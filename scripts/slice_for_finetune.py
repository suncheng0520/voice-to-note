#!/usr/bin/env python3
"""
scripts/slice_for_finetune.py

讀 _alignment/<date>_<source>.tsv,過濾合格句子,用 ffmpeg 切音檔,
輸出 _corpus_raw/<date>/<idx>.wav + manifest.jsonl,給 fine-tune trainer 用。

過濾條件:
  duration: 5-15s 直接 emit
            <5s  跳過 (太短訊息量不足)
            >15s **long-chunk splitter**:在 faster phrase boundary 切成 5-15s
                 子句,每個子句用 faster 文字 fuzzy 驗證,驗證分 < 0.5 丟棄
  score:    >= 0.5
  text:     不含 [?] / <sup> 標記 (低信心句子不適合訓練)
  conf:     (若 _transcripts_raw/<date>_faster.conf.jsonl 存在) C1 信心閘:
            chunk 時間窗內 whisper avg_logprob 加權平均 < 門檻 (預設 -0.6) 丟棄
            —— 避免把 whisper 自己沒把握的段落當正解餵進訓練

輸出:
  _corpus_raw/<date>/0001.wav, 0002.wav, ...   (16kHz mono PCM s16le)
  _corpus_raw/<date>/manifest.jsonl

每行 manifest 格式:
  {"audio": "0001.wav", "text": "...", "start_sec": 0, "end_sec": 36,
   "duration": 36, "score": 1.0, "split": false}
  (splitter 產出的子句多帶 "verify_score" 欄位)

Usage:
  python3 scripts/slice_for_finetune.py 2026-04-29-1723           # default: hybrid
  python3 scripts/slice_for_finetune.py 2026-04-29-1723 faster
  python3 scripts/slice_for_finetune.py <date> hybrid --no-split        # 關 splitter(對照舊行為)
  python3 scripts/slice_for_finetune.py <date> hybrid --conf-threshold -0.8
"""

import argparse
import json
import csv
import difflib
import re
import subprocess
import sys
from pathlib import Path

# 跟 align_for_finetune.py 共用 normalize / parse_timed / build_concat
sys.path.insert(0, str(Path(__file__).resolve().parent))
from align_for_finetune import normalize, parse_timed, build_concat  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "_inbox"
RAW = ROOT / "_transcripts_raw"
ALIGN = ROOT / "_alignment"
OUT = ROOT / "_corpus_raw"

MIN_DUR = 5
MAX_DUR = 15
MIN_SCORE = 0.5
VERIFY_MIN = 0.5       # splitter 子句的 faster 驗證分下限
SPLIT_TARGET = 12      # splitter 打包目標時長(estimated 秒)
SNAP_TOL = 3           # 邊界吸附到 faster row 的容差(秒)

# 子句切點:逗號層級的停頓(句號層級在 align 就切過了)
CLAUSE_END = re.compile(r"(?<=[，、；…」])")


def find_audio(date: str):
    for ext in (".m4a", ".mp3", ".wav"):
        p = INBOX / f"{date}{ext}"
        if p.exists():
            return p
    return None


def slice_one(input_path: Path, start_sec: int, duration: int, output_path: Path):
    """ffmpeg 切音檔,16kHz mono PCM s16le。-ss 放在 -i 後拿到 sample-accurate seek。"""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(input_path),
        "-ss", str(start_sec),
        "-t", str(duration),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stderr


# ---------------------------------------------------------------- C1 信心閘

def load_conf(date: str):
    p = RAW / f"{date}_faster.conf.jsonl"
    if not p.exists():
        return None
    recs = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            recs.append(json.loads(ln))
    return recs or None


def conf_mean_logprob(start: float, end: float, conf):
    """chunk 時間窗內 whisper avg_logprob 的重疊時長加權平均;無重疊回 None"""
    num = den = 0.0
    for c in conf:
        ov = min(end, c["end"]) - max(start, c["start"])
        if ov > 0:
            num += c["avg_logprob"] * ov
            den += ov
    return (num / den) if den > 0 else None


# ---------------------------------------------------------- A-3 long splitter

def verify_against_faster(text: str, start: int, end: int, faster_timed) -> float:
    """子句文字 vs 該時間窗內 faster 文字的 fuzzy 分(同 align 的 score 口徑)"""
    norm = normalize(text)
    if not norm:
        return 0.0
    in_win = [(sec, txt) for sec, txt in faster_timed if start - 1 <= sec <= end]
    if not in_win:
        return 0.0
    concat, _ = build_concat(in_win)
    if not concat:
        return 0.0
    m = difflib.SequenceMatcher(None, concat, norm, autojunk=False)
    match = m.find_longest_match(0, len(concat), 0, len(norm))
    return match.size / max(len(norm), 1)


def snap_to_boundary(t: float, faster_timed, lo: int, hi: int) -> int:
    """把估算邊界吸附到最近的 faster row 起點(真實 phrase 邊界),限制在 [lo, hi]"""
    candidates = [sec for sec, _ in faster_timed if lo < sec < hi]
    if not candidates:
        return int(round(t))
    best = min(candidates, key=lambda s: abs(s - t))
    return best if abs(best - t) <= SNAP_TOL else int(round(t))


def split_long_row(text: str, start: int, end: int, faster_timed):
    """
    >15s 長句 → 5-15s 子句串。
    策略:子句邊界用文字標點、時長按 normalize 字數比例估算、
          邊界吸附到 faster phrase boundary、每段用 faster 文字驗證。
    回傳 (accepted_subs, n_verify_rejected);無法切回 ([], 0)。
    """
    total_dur = end - start
    clauses = [c for c in CLAUSE_END.split(text) if c.strip()]
    if len(clauses) < 2:
        return [], 0  # 沒有內部標點,切不了

    norm_lens = [max(len(normalize(c)), 1) for c in clauses]
    total_norm = sum(norm_lens)

    # 按字數比例打包 clause → bucket(estimated ≤ SPLIT_TARGET)
    buckets, cur_text, cur_est = [], [], 0.0
    for c, nl in zip(clauses, norm_lens):
        est = total_dur * nl / total_norm
        if cur_text and cur_est + est > SPLIT_TARGET:
            buckets.append(("".join(cur_text), cur_est))
            cur_text, cur_est = [], 0.0
        cur_text.append(c)
        cur_est += est
    if cur_text:
        buckets.append(("".join(cur_text), cur_est))

    # 尾巴太短就併回前一個
    if len(buckets) >= 2 and buckets[-1][1] < MIN_DUR:
        last_t, last_e = buckets.pop()
        prev_t, prev_e = buckets.pop()
        buckets.append((prev_t + last_t, prev_e + last_e))

    # 估算時間 → 吸附 faster boundary → 驗證
    subs, rejected = [], 0
    cursor = float(start)
    prev_cut = start
    for i, (btext, best_) in enumerate(buckets):
        sub_start = prev_cut
        if i + 1 < len(buckets):
            est_cut = cursor + best_
            sub_end = snap_to_boundary(est_cut, faster_timed, sub_start + MIN_DUR, min(end, sub_start + MAX_DUR + 2))
        else:
            sub_end = end
        cursor += best_
        prev_cut = sub_end

        dur = sub_end - sub_start
        if dur < MIN_DUR or dur > MAX_DUR:
            rejected += 1
            continue
        vscore = verify_against_faster(btext, sub_start, sub_end, faster_timed)
        if vscore < VERIFY_MIN:
            rejected += 1
            continue
        subs.append({"text": btext, "start_sec": sub_start, "end_sec": sub_end,
                     "duration": dur, "verify_score": round(vscore, 2)})
    return subs, rejected


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("source", nargs="?", default="hybrid",
                    choices=["hybrid", "faster", "getnote"])
    ap.add_argument("--no-split", action="store_true", help="關 long-chunk splitter(舊行為)")
    ap.add_argument("--conf-threshold", type=float, default=-0.6,
                    help="C1 信心閘:時間窗 avg_logprob 加權平均低於此值丟棄 (預設 -0.6)")
    args = ap.parse_args()
    date, source = args.date, args.source

    tsv_path = ALIGN / f"{date}_{source}.tsv"
    if not tsv_path.exists():
        sys.exit(f"找不到 alignment: {tsv_path}\n"
                 f"先跑: python3 scripts/align_for_finetune.py {date} {source}")

    audio_path = find_audio(date)
    if not audio_path:
        sys.exit(f"找不到 _inbox/{date}.{{m4a,mp3,wav}}")

    # splitter 需要 faster timed 當 phrase boundary
    faster_timed = []
    ft_path = RAW / f"{date}_faster.timed.txt"
    if ft_path.exists():
        faster_timed = parse_timed(ft_path)
    do_split = (not args.no_split) and bool(faster_timed)

    # C1 信心閘(conf.jsonl 不存在就停用)
    conf = load_conf(date)

    out_dir = OUT / date
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.wav"):
        old.unlink()

    rows = []
    with tsv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows.append(row)

    accepted = []
    skipped_short = skipped_long = skipped_low_score = skipped_marker = failed = 0
    split_emitted = split_rejected = 0
    conf_rejected = 0

    def emit(text, start, end, dur, score, split=False, verify_score=None):
        nonlocal failed, conf_rejected
        # C1 閘
        if conf is not None:
            mlp = conf_mean_logprob(start, end, conf)
            if mlp is not None and mlp < args.conf_threshold:
                conf_rejected += 1
                return False
        idx = len(accepted) + 1
        wav_name = f"{idx:04d}.wav"
        ok, err = slice_one(audio_path, start, dur, out_dir / wav_name)
        if not ok:
            failed += 1
            print(f"[{idx:04d}] ffmpeg 失敗 ({start}s +{dur}s): {err.strip()[:200]}")
            return False
        entry = {"audio": wav_name, "text": text, "start_sec": start,
                 "end_sec": end, "duration": dur, "score": score, "split": split}
        if verify_score is not None:
            entry["verify_score"] = verify_score
        accepted.append(entry)
        return True

    for row in rows:
        text = row["text"]
        start = int(row["start_sec"])
        end = int(row["end_sec"])
        dur = int(row["duration"])
        score = float(row["score"])

        if "[?]" in text or "<sup>" in text:
            skipped_marker += 1
            continue
        if score < MIN_SCORE:
            skipped_low_score += 1
            continue
        if dur < MIN_DUR:
            skipped_short += 1
            continue
        if dur > MAX_DUR:
            if do_split:
                subs, n_rej = split_long_row(text, start, end, faster_timed)
                split_rejected += n_rej
                if subs:
                    for s in subs:
                        if emit(s["text"], s["start_sec"], s["end_sec"],
                                s["duration"], score, split=True,
                                verify_score=s["verify_score"]):
                            split_emitted += 1
                    continue
            skipped_long += 1
            continue

        emit(text, start, end, dur, score)

    manifest_path = out_dir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as f:
        for entry in accepted:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    total = len(rows)
    n = len(accepted)
    print(f"=== {date} ===")
    print(f"alignment: {tsv_path.relative_to(ROOT)} ({total} 句)")
    print(f"音檔:      {audio_path.relative_to(ROOT)}")
    print(f"輸出:      {out_dir.relative_to(ROOT)}/  (manifest.jsonl + {n} wav)")
    print()
    print(f"接受: {n}/{total} 句來源(含 splitter 產出 {split_emitted} 個子句)")
    print(f"過濾:")
    print(f"  跳過短 (<{MIN_DUR}s):     {skipped_short}")
    print(f"  跳過長 (>{MAX_DUR}s):     {skipped_long}"
          + ("  (splitter 也救不回)" if do_split else "  (splitter 關閉/無 faster.timed)"))
    print(f"  splitter 子句驗證淘汰:  {split_rejected}")
    print(f"  跳過低分 (<{MIN_SCORE}):  {skipped_low_score}")
    print(f"  跳過 [?]/<sup> 標記:    {skipped_marker}")
    if conf is not None:
        print(f"  C1 信心閘淘汰 (<{args.conf_threshold}): {conf_rejected}")
    else:
        print(f"  C1 信心閘: 停用(無 {date}_faster.conf.jsonl)")
    print(f"  ffmpeg 失敗:           {failed}")
    if accepted:
        durs = [e["duration"] for e in accepted]
        total_audio_sec = sum(durs)
        print()
        print(f"接受 chunks 時長: 平均 {sum(durs)/len(durs):.1f}s, "
              f"min {min(durs)}s, max {max(durs)}s")
        print(f"總音檔時長:       {total_audio_sec}s ({total_audio_sec/60:.1f} 分)")


if __name__ == "__main__":
    main()
