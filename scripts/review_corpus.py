#!/usr/bin/env python3
"""
scripts/review_corpus.py — A-1 人耳驗證對齊精度(fine-tune 前置閘)

從 _corpus_raw/<date>/manifest.jsonl 隨機抽 N 條,逐條播放 wav + 顯示文字,
你用耳朵判斷「音檔跟文字對不對得上」。結果存 _corpus_raw/<date>/review.json。

判定標準(寬鬆即可,這是抓「對齊錯位」不是抓「錯字」):
  ✓ (y) — 音檔講的就是這段文字(個別字輕微出入 OK)
  ✗ (n) — 對不上:音檔是別段話 / 頭尾切到別句的一大段 / 文字明顯不是這段音
  r     — 重播一次
  s     — 跳過這條(不計分)
  q     — 提前結束(已答的會存檔)

目標:正確率 ≥ 95% 才放心全量跑。低於 95% 代表對齊有系統性問題,
先回頭調 align_for_finetune.py 再繼續。

Usage:
  python3 scripts/review_corpus.py 2026-04-29-1723            # 預設抽 20 條
  python3 scripts/review_corpus.py 2026-04-29-1723 -n 30
  python3 scripts/review_corpus.py 2026-04-29-1723 --splits-only   # 只抽 splitter 產的子句
"""

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "_corpus_raw"


def play(path: Path):
    subprocess.run(["afplay", str(path)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("-n", type=int, default=20, help="抽樣數 (預設 20)")
    ap.add_argument("--seed", type=int, default=42, help="抽樣 seed(可重現)")
    ap.add_argument("--splits-only", action="store_true",
                    help="只抽 splitter 產出的子句(splitter 剛上線時建議先驗這批)")
    args = ap.parse_args()

    corpus_dir = OUT / args.date
    manifest_path = corpus_dir / "manifest.jsonl"
    if not manifest_path.exists():
        sys.exit(f"找不到 {manifest_path}\n先跑 slice_for_finetune.py")

    entries = [json.loads(l) for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.splits_only:
        entries = [e for e in entries if e.get("split")]
        if not entries:
            sys.exit("manifest 裡沒有 split 條目")

    rng = random.Random(args.seed)
    sample = rng.sample(entries, min(args.n, len(entries)))

    print(f"=== 人耳驗證: {args.date} — 抽 {len(sample)} 條 ===")
    print("y=對得上  n=對不上  r=重播  s=跳過  q=結束\n")

    results = []
    for i, e in enumerate(sample, 1):
        wav = corpus_dir / e["audio"]
        tag = " [split]" if e.get("split") else ""
        print(f"--- {i}/{len(sample)}  {e['audio']}{tag}  "
              f"({e['start_sec']}s +{e['duration']}s)")
        print(f"    文字:「{e['text']}」")
        play(wav)
        while True:
            ans = input("    ✓/✗ [y/n/r/s/q]: ").strip().lower()
            if ans == "r":
                play(wav)
                continue
            if ans in ("y", "n", "s", "q"):
                break
            print("    輸入 y / n / r / s / q")
        if ans == "q":
            break
        if ans == "s":
            continue
        results.append({"audio": e["audio"], "text": e["text"],
                        "split": e.get("split", False), "ok": ans == "y"})
        print()

    if not results:
        sys.exit("沒有答任何一條,不存檔")

    n_ok = sum(1 for r in results if r["ok"])
    acc = n_ok / len(results) * 100
    review_path = corpus_dir / "review.json"
    review_path.write_text(json.dumps({
        "date": args.date, "n_reviewed": len(results), "n_ok": n_ok,
        "accuracy_pct": round(acc, 1), "seed": args.seed,
        "splits_only": args.splits_only, "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 結果: {n_ok}/{len(results)} 對得上 ({acc:.1f}%) ===")
    if acc >= 95:
        print("✓ ≥95%,可以放心全量跑")
    else:
        print("✗ <95%,對齊有系統性問題,先看對不上的條目共通點(都是 split?都在檔尾?)")
        for r in results:
            if not r["ok"]:
                print(f"  ✗ {r['audio']}{' [split]' if r['split'] else ''}: {r['text'][:50]}")
    print(f"已存: {review_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
