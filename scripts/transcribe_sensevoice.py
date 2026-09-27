#!/usr/bin/env python3
"""
transcribe_sensevoice.py — 用 FunAudioLLM SenseVoice-Small 轉錄音檔。

定位:作為「turbo → SenseVoice」的替換引擎(中文 benchmark 勝 whisper-large-v3,
非自迴歸、快很多),補上 whisper turbo 在難段掉字的弱點。先當「新引擎」跑,
跟 _faster.txt 做 A/B,確認台灣口音/繁體 OK 之後再決定要不要在 transcribe.sh 換掉 turbo。

特色(對齊現有 funasr 腳本慣例):
    - fsmn-vad 切句 → 長音檔可跑
    - rich_transcription_postprocess 去掉 SenseVoice 的 <|zh|><|NEUTRAL|>… 富文字標籤
    - use_itn=True 開逆文字正規化(數字/標點)
    - OpenCC s2twp 簡轉繁(SenseVoice 的 zh 輸出是簡體)
    - dictionary.yaml 熱詞(SenseVoice 若不支援會自動略過,不影響)

用法:
    python3 scripts/transcribe_sensevoice.py _inbox/<音檔>
輸出:
    _transcripts_raw/<stem>_sensevoice.txt

device:預設 mps(Apple Silicon Metal),可用 FUNASR_DEVICE=cpu 強制。
第一次跑會下載 iic/SenseVoiceSmall(~900MB)。
"""
import os
import re
import sys
from pathlib import Path

# SenseVoice 富文字標籤(語言/情緒/事件/itn 旗標),例 <|zh|><|NEUTRAL|><|Speech|><|woitn|>
# 直接整段拔掉 —— 不走 rich_transcription_postprocess(它會把情緒 token 轉成 😔 emoji)
TAG_RE = re.compile(r"<\|[^|]*\|>")
# 斷句:在 。！？ 後切行,保留標點
SENT_SPLIT_RE = re.compile(r"(?<=[。！？])")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DICT_FILE = PROJECT_ROOT / "dictionary.yaml"
RAW_DIR = PROJECT_ROOT / "_transcripts_raw"


def load_hotwords() -> str:
    try:
        import yaml

        d = yaml.safe_load(DICT_FILE.read_text(encoding="utf-8")) or {}
        words = []
        for val in d.values():
            if isinstance(val, list):
                words.extend([str(v) for v in val if isinstance(v, str)])
        print(f"→ 熱詞: {len(words)} 個")
        return " ".join(words)
    except Exception as e:
        print(f"→ 熱詞載入失敗: {e}")
        return ""


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <音檔路徑>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)

    stem = src.stem
    RAW_DIR.mkdir(exist_ok=True)
    out_file = RAW_DIR / f"{stem}_sensevoice.txt"

    hotwords = load_hotwords()

    import torch

    device = os.environ.get("FUNASR_DEVICE")
    if not device:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"→ device: {device}")

    # offline fallback:modelscope.cn 掛掉(502)時,只要模型已 cache 在本地就走本地路徑,
    # 不碰網路(見 scripts/modelscope_local.py)。乾淨機器第一次跑仍會上線下載。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from modelscope_local import resolve, missing_local

    model_id, vad_id = "iic/SenseVoiceSmall", "fsmn-vad"
    missing = missing_local(model_id, vad_id)
    if missing:
        print("→ 模型來源: 線上 modelscope(以下未在本地 cache,需連網下載;若 modelscope 正在當機本次會失敗): " + ", ".join(missing))
    else:
        print("→ 模型來源: 本地 cache(offline)")

    print("→ 載入模型中 (iic/SenseVoiceSmall)...")
    from funasr import AutoModel

    model = AutoModel(
        model=resolve(model_id),
        vad_model=resolve(vad_id),
        vad_kwargs={"max_single_segment_time": 30000},
        device=device,
        disable_update=True,
    )

    print("→ 轉錄中...")
    kwargs = dict(
        input=str(src),
        cache={},
        language="zh",
        use_itn=True,
        batch_size_s=300,
        merge_vad=True,
        merge_length_s=15,
    )
    if hotwords:
        kwargs["hotword"] = hotwords
    try:
        res = model.generate(**kwargs)
    except TypeError:
        # 某些版本不吃 hotword,退回不帶熱詞
        kwargs.pop("hotword", None)
        res = model.generate(**kwargs)

    # 拔標籤 → 合併 → 依句末標點斷行
    full = "".join(TAG_RE.sub("", item.get("text", "")) for item in res)
    lines = [s.strip() for s in SENT_SPLIT_RE.split(full) if s.strip()]

    # 去重連續重複行
    deduped = [lines[0]] if lines else []
    for line in lines[1:]:
        if line != deduped[-1]:
            deduped.append(line)

    print("→ 簡轉繁中...")
    from opencc import OpenCC

    cc = OpenCC("s2twp")
    converted = [cc.convert(line) for line in deduped]

    out_file.write_text("\n".join(converted) + "\n", encoding="utf-8")
    print(f"→ 去重: {len(lines)} → {len(deduped)} 行")
    print(f"✓ 完成: {out_file}")


if __name__ == "__main__":
    main()
