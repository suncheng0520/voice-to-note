#!/usr/bin/env python3
"""
transcribe_faster_whisper_chunked.py — mlx-whisper turbo,長音檔切片版。

何時用此版本而非 transcribe_faster_whisper.sh:
    - 音檔 ≥ ~30 分:mlx-whisper turbo 在長段音檔會 catastrophic loop
      (典型「中文字幕志愿者 李宗盛」YouTube footer 幻覺、或「你吃的」x N)
    - 切 30 分鐘 chunks 重跑可大幅改善覆蓋率(實測 19% → 64%);
      模型只載一次,FOOTER_PATTERNS 過濾 chunk 內仍崩的 segments
    - ⚠️ turbo 還有一種「seek 卡死」:mlx-whisper 0.4.3 transcribe.py 在視窗最後一個
      timestamp 位置為 0 時 seek 不前進,同一視窗無限重解(2026-09-15 檔卡 3 天)。
      本腳本自己擋不了,靠 transcribe.sh 的 run_with_timeout watchdog 殺掉

用法:
    python3 scripts/transcribe_faster_whisper_chunked.py _inbox/<音檔>

輸出(跟 .sh 版本同檔名,直接覆蓋):
    _transcripts_raw/<stem>_faster.txt        去重後純文字
    _transcripts_raw/<stem>_faster.timed.txt  時間戳 sidecar
"""
import atexit
import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import mlx_whisper
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = PROJECT_ROOT / "dictionary.yaml"
RAW_DIR = PROJECT_ROOT / "_transcripts_raw"
CHUNK_SECS = 1800  # 30 分鐘

# 已知的 footer / loop hallucination 模式,字符多樣化所以無法靠「同字佔比 > 70%」抓到
# 全部 pattern 都做簡繁兼容(Whisper turbo 內部 token 是簡體,輸出可能簡繁混雜)
FOOTER_PATTERNS = [
    re.compile(r"中文字幕志[愿願]者"),
    re.compile(r"字幕志[愿願]者"),
    re.compile(r"^[\s中文字幕]+$"),                 # 「中文字幕 中文字幕 ...」(沒有志愿者後綴)整段都是這四字
    re.compile(r"明[鏡镜][與与][點点][點点][欄栏]目"),
    re.compile(r"打[賞赏]支持"),                    # 「打賞支持X」YouTube 樂捐 chain
    re.compile(r"[請请]不吝[點点][讚赞贊]"),         # 「請不吝點讚」YouTube footer
]


def collect_words(d, out):
    if isinstance(d, str):
        out.append(d)
    elif isinstance(d, list):
        for v in d:
            collect_words(v, out)
    elif isinstance(d, dict):
        for v in d.values():
            collect_words(v, out)


def is_hallucination(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    for p in FOOTER_PATTERNS:
        if p.search(t):
            return True
    # 同一字佔比 > 70%(「鍛鍛鍛...」這種)
    chars = [c for c in t if not c.isspace()]
    if len(chars) >= 6:
        _, count = Counter(chars).most_common(1)[0]
        if count / len(chars) > 0.7:
            return True
    # 非中文重複短詞(「Al toil toil toil...」)
    parts = t.split()
    if len(parts) >= 3 and not re.search(r"[一-鿿]", t):
        if len(set(parts)) <= 2:
            return True
    return False


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <音檔路徑>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)

    stem = src.stem

    # 熱詞
    words = []
    if DICT_FILE.exists():
        try:
            collect_words(yaml.safe_load(DICT_FILE.read_text(encoding="utf-8")), words)
        except Exception as e:
            print(f"→ 熱詞載入失敗: {e}")
    prompt = (
        f"尚承的中文語音筆記,內容可能包含:{'、'.join(words)}。"
        if words
        else "這是中文語音筆記。"
    )

    # ffmpeg 切片成 16kHz mono wav(mlx-whisper 標準輸入規格)
    # 不用 stream copy 因為 m4a (AAC) → mp3 跨 codec 不能 -c copy(實測 status 234)
    work = Path(tempfile.mkdtemp(prefix="voice_chunks_"))
    # 被 transcribe.sh 的逾時 watchdog TERM 掉時也要清暫存
    # (2026-09-15 檔 seek 卡死 3 天,留了 122MB 孤兒切片在 /var/folders)
    atexit.register(shutil.rmtree, work, ignore_errors=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    print(f"→ 切片到 {work}")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-ar", "16000", "-ac", "1",
            "-f", "segment", "-segment_time", str(CHUNK_SECS),
            str(work / "chunk_%02d.wav"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    chunks = sorted(work.glob("chunk_*.wav"))
    print(f"→ 切成 {len(chunks)} 段")

    all_lines, all_timed, all_conf = [], [], []
    total_halluc = 0

    for idx, chunk in enumerate(chunks):
        offset = idx * CHUNK_SECS
        print(f"\n→ chunk {idx + 1}/{len(chunks)} (offset {offset}s)")
        t0 = time.time()
        result = mlx_whisper.transcribe(
            str(chunk),
            path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
            language="zh",
            initial_prompt=prompt,
            condition_on_previous_text=True,
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            hallucination_silence_threshold=2.0,
            word_timestamps=False,
        )
        elapsed = time.time() - t0
        n_halluc = 0
        for seg in result["segments"]:
            text = seg["text"].strip()
            if not text:
                continue
            if is_hallucination(text):
                n_halluc += 1
                continue
            s = int(seg["start"]) + offset
            h, m, sec = s // 3600, (s % 3600) // 60, s % 60
            all_lines.append(text)
            all_timed.append(f"[{h:02d}:{m:02d}:{sec:02d}] {text}")
            all_conf.append(
                {
                    "start": round(seg["start"] + offset, 2),
                    "end": round(seg.get("end", seg["start"]) + offset, 2),
                    "text": text,
                    "avg_logprob": round(seg.get("avg_logprob", 0.0), 4),
                    "no_speech_prob": round(seg.get("no_speech_prob", 0.0), 4),
                    "compression_ratio": round(seg.get("compression_ratio", 0.0), 4),
                }
            )
        total_halluc += n_halluc
        print(
            f"  耗時 {elapsed:.0f}s,{len(result['segments'])} 段,過濾幻覺 {n_halluc}"
        )

    # 去重連續重複行
    deduped = [all_lines[0]] if all_lines else []
    for line in all_lines[1:]:
        if line != deduped[-1]:
            deduped.append(line)

    RAW_DIR.mkdir(exist_ok=True)
    out_txt = RAW_DIR / f"{stem}_faster.txt"
    out_timed = RAW_DIR / f"{stem}_faster.timed.txt"
    out_conf = RAW_DIR / f"{stem}_faster.conf.jsonl"
    out_txt.write_text("\n".join(deduped) + "\n", encoding="utf-8")
    out_timed.write_text("\n".join(all_timed) + "\n", encoding="utf-8")
    out_conf.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in all_conf) + "\n",
        encoding="utf-8",
    )

    print(f"\n→ 幻覺過濾總計: {total_halluc} 段")
    print(f"→ 去重: {len(all_lines)} → {len(deduped)} 行")
    print(f"✓ {out_txt}")
    print(f"✓ {out_timed}")
    print(f"✓ {out_conf}")

    shutil.rmtree(work)


if __name__ == "__main__":
    main()
