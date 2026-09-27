#!/usr/bin/env python3
"""
flag_low_confidence.py — C1「信心分數收割」消費端。

讀 whisper 引擎吐的 `_faster.conf.jsonl`(每段 avg_logprob / no_speech_prob /
compression_ratio),依門檻挑出「低信心段」,輸出帶時間戳的清單給校對用。

⚠️ 2026-09-25 起 sidecar 預設是 Qwen3-ASR(scripts/transcribe_qwen3.py),不產 conf.jsonl;
只有 SIDECAR_ENGINE=whisper 跑過的檔才有料。FireRed beam search 逐 token 信心可望接手(待做)。

用途:校對時不再只靠「三引擎彼此不一致」的啟發式猜哪裡該標 <sup>?</sup>,
而是先看這份 lowconf 清單 —— whisper 自己對哪幾段「沒把握」,直接浮出來,
再去跟其他引擎交叉比對,sup 放得更有原則。

門檻語意(whisper 慣例):
    - avg_logprob   每 token 平均對數機率,越接近 0 越有信心;< 門檻 = 低信心
                    (典型好段 ~ -0.1 ~ -0.3,難段 < -0.6)
    - no_speech_prob 這段是「非語音/靜音」的機率,> 門檻 = 可能是幻覺/環境音
    - compression_ratio 文字壓縮比,> 門檻 = 重複/碎念/可能幻覺

用法:
    python3 scripts/flag_low_confidence.py _transcripts_raw/<stem>_faster.conf.jsonl
    python3 scripts/flag_low_confidence.py <stem>          # 自動補路徑
    # 可調門檻:
    python3 scripts/flag_low_confidence.py <stem> --avg-logprob -0.5 --no-speech 0.6 --compression 2.4

輸出:
    _transcripts_raw/<stem>_faster.lowconf.txt
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "_transcripts_raw"


def hms(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def resolve_conf_path(arg: str) -> Path:
    p = Path(arg)
    if p.exists():
        return p
    # 當成 stem 處理
    cand = RAW_DIR / f"{arg}_faster.conf.jsonl"
    if cand.exists():
        return cand
    # 給了 stem_faster 或別的尾巴也試一下
    cand2 = RAW_DIR / f"{Path(arg).stem}_faster.conf.jsonl"
    if cand2.exists():
        return cand2
    print(f"找不到 conf 檔: {arg}")
    print(f"  試過: {p}, {cand}, {cand2}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("conf", help="conf.jsonl 路徑,或 stem")
    ap.add_argument("--avg-logprob", type=float, default=-0.55,
                    help="avg_logprob 低於此值算低信心 (預設 -0.55)")
    ap.add_argument("--no-speech", type=float, default=0.6,
                    help="no_speech_prob 高於此值算可疑 (預設 0.6)")
    ap.add_argument("--compression", type=float, default=2.4,
                    help="compression_ratio 高於此值算可疑 (預設 2.4)")
    args = ap.parse_args()

    conf_path = resolve_conf_path(args.conf)
    records = []
    for line in conf_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    flagged = []
    for r in records:
        reasons = []
        if r["avg_logprob"] < args.avg_logprob:
            reasons.append(f"logprob={r['avg_logprob']}")
        if r["no_speech_prob"] > args.no_speech:
            reasons.append(f"no_speech={r['no_speech_prob']}")
        if r["compression_ratio"] > args.compression:
            reasons.append(f"compress={r['compression_ratio']}")
        if reasons:
            flagged.append((r, reasons))

    # 最沒把握的排前面
    flagged.sort(key=lambda x: x[0]["avg_logprob"])

    total = len(records)
    n_flag = len(flagged)
    pct = (n_flag / total * 100) if total else 0.0

    out_lines = [
        f"# 低信心段 (whisper) — {conf_path.name}",
        f"# 門檻: avg_logprob<{args.avg_logprob}  no_speech>{args.no_speech}  compression>{args.compression}",
        f"# 共 {total} 段,標記 {n_flag} 段 ({pct:.1f}%)",
        "",
    ]
    for r, reasons in flagged:
        out_lines.append(f"[{hms(r['start'])}] {r['text']}")
        out_lines.append(f"    ↳ {', '.join(reasons)}")

    out_path = RAW_DIR / f"{conf_path.name.replace('_faster.conf.jsonl', '')}_faster.lowconf.txt"
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    print(f"→ {total} 段,低信心 {n_flag} 段 ({pct:.1f}%)")
    print(f"✓ {out_path}")
    # 同步在 stdout 印前 15 段最可疑的,方便當場看
    for r, reasons in flagged[:15]:
        print(f"  [{hms(r['start'])}] {r['text'][:30]}  ↳ {', '.join(reasons)}")


if __name__ == "__main__":
    main()
