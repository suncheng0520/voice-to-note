#!/usr/bin/env python3
"""
transcribe_qwen3.py — Qwen3-ASR-1.7B(MLX 8-bit)+ Qwen3-ForcedAligner-0.6B,sidecar 引擎。

角色(2026-09-25 起取代 mlx-whisper turbo 當 sidecar):
    輸出檔名沿用 `_faster.*`(歷史包袱,play.sh / proofread / process_inbox 流程都不用改):
    _transcripts_raw/<stem>_faster.txt         簡轉繁,一句一行(依 。!?;… 斷句)
    _transcripts_raw/<stem>_faster.timed.txt   [HH:MM:SS] 每句起點(aligner 逐字時間戳取句首字)
    不再產出 _faster.conf.jsonl —— Qwen 沒有信心分數,lowconf 定位這條先停
    (FireRed beam search 內部有逐 token 信心,之後可補位)。

為什麼換掉 turbo:
    - turbo 兩種失控模式(loop 幻覺、seek 卡死無限迴圈)都是本性,圍堵不了
      (2026-09-15 檔的 sidecar 卡死 3 天佔 GPU,見 transcribe.sh 逾時保險絲註解)
    - 2026-09-15-143157(66 分)實測:Qwen3-ASR 152s(26x 即時),對校對稿 CER 6.32%
      (Apple 5.48 / FireRed 6.79 / SenseVoice 7.45 / FunASR 9.50),零 loop 零截尾;
      中英混講段落是唯一把整句英文吐出來的引擎
    - ForcedAligner 對 50s 段 0.4s 回逐字時間戳,比 whisper 段落級細一個量級,
      而且能對任何文字對齊(將來可直接對校對稿做)

限制(讀稿時要知道):
    - 無熱詞:dictionary.yaml 不加持,專有名詞以 dict + 主力引擎為準
    - 數字寫中文(四百/十二),不像 Apple/SenseVoice 給阿拉伯數字
    - 無信心分數
    - 角色不變:**仍不參與一般多數決**;用途 = 時間戳、中英混講/英文專名備援、完整稿備援
    - aligner 一次最多 5 分鐘 → 這裡切 4 分鐘(低能量邊界),Qwen 與 aligner 共用同一切片
    - Qwen 是 LLM decoder,理論上也可能 loop → transcribe.sh 的 run_with_timeout 仍包著它

環境變數:
    QWEN3_CHUNK_SECS=240   切片上限秒數(aligner 上限 300,別超過)
    QWEN3_ALIGN=0          跳過 aligner(timed.txt 退化成 chunk 內按字數線性內插)

用法:
    python3 scripts/transcribe_qwen3.py _inbox/<音檔>
第一次跑會下載 mlx-community/Qwen3-ASR-1.7B-8bit(~2GB)與 Qwen3-ForcedAligner-0.6B-8bit(~0.7GB)。
"""
import atexit
import gc
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "_transcripts_raw"
ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
ALIGN_MODEL = "mlx-community/Qwen3-ForcedAligner-0.6B-8bit"
CHUNK_SECS = float(os.environ.get("QWEN3_CHUNK_SECS", "240"))
USE_ALIGNER = os.environ.get("QWEN3_ALIGN", "1") != "0"
# 句末斷行:中英句號問號驚嘆號分號、省略號(Qwen 會吐「……」或「...」)
SENT_SPLIT = re.compile(r"(?<=[。!?！？;；])|(?<=…{2})|(?<=\.{3})")


def hms(sec: float) -> str:
    s = int(sec)
    return f"[{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}]"


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENT_SPLIT.split(text)]
    return [p for p in parts if p and re.search(r"\w", p)]


def sentence_starts_from_alignment(sentences, items, tokenize) -> list[float] | None:
    """用 aligner 的逐字 items 對回每句:依同一套 tokenizer 數每句 token 數,依序消耗 items。
    對不上就回 None(呼叫端退化成線性內插)。"""
    starts, pos = [], 0
    for sent in sentences:
        toks = tokenize(sent)
        if not toks:
            starts.append(starts[-1] if starts else 0.0)
            continue
        chunk_items = items[pos : pos + len(toks)]
        if len(chunk_items) != len(toks) or "".join(t.text for t in chunk_items) != "".join(toks):
            return None
        starts.append(chunk_items[0].start_time)
        pos += len(toks)
    return starts


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <音檔路徑>")
        sys.exit(1)
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)
    if CHUNK_SECS > 300:
        print(f"QWEN3_CHUNK_SECS={CHUNK_SECS:.0f} 超過 aligner 上限 300s")
        sys.exit(1)

    stem = src.stem
    RAW_DIR.mkdir(exist_ok=True)
    out_txt = RAW_DIR / f"{stem}_faster.txt"
    out_timed = RAW_DIR / f"{stem}_faster.timed.txt"

    # m4a → 16k mono wav;被 transcribe.sh 的逾時 watchdog TERM 掉也要清暫存
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_path = Path(tf.name)
    atexit.register(lambda: wav_path.unlink(missing_ok=True))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(wav_path)],
        check=True,
    )

    import mlx.core as mx
    from mlx_audio.stt import load
    from mlx_audio.stt.models.qwen3_asr.qwen3_asr import split_audio_into_chunks
    from mlx_audio.stt.utils import load_audio

    wav = np.array(load_audio(str(wav_path), sr=16000))
    total_sec = len(wav) / 16000
    chunks = split_audio_into_chunks(wav, 16000, chunk_duration=CHUNK_SECS)
    print(f"→ 低能量邊界切片: {len(chunks)} 段(≤{CHUNK_SECS:.0f}s,共 {total_sec:.0f}s)")

    # ---- 第一階段:Qwen3-ASR 逐 chunk 轉錄 ----
    print(f"→ 載入 ASR ({ASR_MODEL})...")
    asr = load(ASR_MODEL)
    texts = []
    t0 = time.time()
    for i, (chunk, offset) in enumerate(chunks):
        r = asr.generate(
            chunk,
            language="Chinese",
            chunk_duration=CHUNK_SECS + 1,  # 已切好,別再切
            max_tokens=32768,  # mlx-audio 預設 8192 是整檔總預算,會默默截尾
        )
        texts.append(r.text.strip())
        print(f"  ASR {i + 1}/{len(chunks)} [{hms(offset)[1:-1]}] {len(texts[-1])} 字,{time.time() - t0:.0f}s")
    asr_secs = time.time() - t0
    print(f"→ ASR 耗時 {asr_secs:.0f}s({total_sec / max(asr_secs, 1):.1f}x 即時)")
    del asr
    gc.collect()
    mx.clear_cache()

    # ---- 第二階段:ForcedAligner 逐字時間戳 → 句首時間 ----
    records = []  # (start_sec, sentence_simplified)
    aligned_chunks = 0
    aligner = tokenize = None
    if USE_ALIGNER:
        try:
            from mlx_audio.stt.models.qwen3_asr.qwen3_forced_aligner import ForceAlignProcessor

            print(f"→ 載入 aligner ({ALIGN_MODEL})...")
            aligner = load(ALIGN_MODEL)
            tokenize = ForceAlignProcessor().tokenize_chinese_mixed
        except Exception as e:  # noqa: BLE001
            print(f"⚠ aligner 載入失敗({e}),timed.txt 退化成線性內插")
            aligner = None
    t0 = time.time()
    for (chunk, offset), text in zip(chunks, texts):
        if not text:
            continue
        sents = split_sentences(text)
        if not sents:
            continue
        chunk_dur = len(chunk) / 16000
        starts = None
        if aligner is not None:
            try:
                items = list(aligner.generate(chunk, text=text, language="Chinese").items)
                starts = sentence_starts_from_alignment(sents, items, tokenize)
                if starts is not None:
                    aligned_chunks += 1
            except Exception as e:  # noqa: BLE001
                print(f"⚠ aligner 在 {hms(offset)} 失敗({e}),此段線性內插")
        if starts is None:  # 退化:按字元位置線性內插
            n = max(len(text), 1)
            pos, starts = 0, []
            for s in sents:
                starts.append(chunk_dur * (text.find(s, pos) if text.find(s, pos) >= 0 else pos) / n)
                pos += len(s)
        for s, st in zip(sents, starts):
            records.append((offset + st, s))
    if aligner is not None:
        print(f"→ aligner 耗時 {time.time() - t0:.0f}s,{aligned_chunks}/{len(chunks)} 段逐字對齊成功")

    # ---- 簡轉繁 + 輸出 ----
    print("→ 簡轉繁中...")
    from opencc import OpenCC

    cc = OpenCC("s2twp")
    lines = [cc.convert(s) for _, s in records]
    timed = [f"{hms(st)} {line}" for (st, _), line in zip(records, lines)]
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_timed.write_text("\n".join(timed) + "\n", encoding="utf-8")
    print(f"✓ 完成: {out_txt}({len(lines)} 句,{sum(len(l) for l in lines)} 字)")
    print(f"✓ 時間戳版: {out_timed}")


if __name__ == "__main__":
    main()
