#!/usr/bin/env python3
"""
scripts/align_for_finetune.py

把 _proofread/<date>.md 的句子貼回時間戳,產出 fine-tune 訓練語料的對齊清單。
給 Whisper LoRA fine-tune (專案 A) 跟 voice-clone-tts (專案 C) 共用。

Modes:
  hybrid   getnote 文字定位 + faster 時間校正 (推薦,默認)
  faster   _faster.timed.txt 為主軸 (粒度細,文字碎)
  getnote  _getnote.timed.txt 為主軸 (文字準,粒度粗)

數字正規化: 中文/阿拉伯數字統一成 #,讓「四月二十九號」「4月29號」可比對

輸出:
  _alignment/<date>_<source>.tsv
  欄位: start_sec end_sec duration score [coarse_sec] text

不切音檔。切片是 slice_for_finetune.py 的事。

Usage:
  python3 scripts/align_for_finetune.py 2026-04-29-1723           # default: hybrid
  python3 scripts/align_for_finetune.py 2026-04-29-1723 faster
  python3 scripts/align_for_finetune.py 2026-04-29-1723 getnote
"""

import sys
import re
import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROOFREAD = ROOT / "_proofread"
RAW = ROOT / "_transcripts_raw"
OUT = ROOT / "_alignment"

MARKER = re.compile(r"<sup>.*?</sup>|\[\?\]|\[註[：:][^\]]*\]")
PUNCT = re.compile(r"[，。！？、；：「」『』（）【】()…\-—\s]+")
DIGITS = re.compile(r"[0-9０-９]+|[一二兩三四五六七八九十百千萬零]+")
TS = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")
SENT_END = re.compile(r"(?<=[。！？])")


def normalize(s: str) -> str:
    s = MARKER.sub("", s)
    s = PUNCT.sub("", s)
    s = DIGITS.sub("#", s)
    return s


def parse_timed(path: Path):
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = TS.match(ln.strip())
        if m:
            h, mn, s, txt = m.groups()
            rows.append((int(h) * 3600 + int(mn) * 60 + int(s), txt.strip()))
    return rows


def split_sentences(md: str):
    sents = []
    for para in md.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        for chunk in SENT_END.split(para):
            chunk = chunk.strip()
            if chunk:
                sents.append(chunk)
    return sents


def fmt_mmss(sec: int) -> str:
    m, s = divmod(sec, 60)
    return f"{m:02d}:{s:02d}"


def build_concat(timed):
    """從 [(sec, text), ...] 建可搜尋字串 + char→sec 對照"""
    char_times = []
    parts = []
    for sec, txt in timed:
        n = normalize(txt)
        if not n:
            continue
        parts.append(n)
        char_times.extend([sec] * len(n))
    return "".join(parts), char_times


def monotonic_align(sentences, concat, char_times, fallback_sec):
    """單來源 monotonic greedy fuzzy match"""
    cursor = 0
    results = []
    for sent in sentences:
        norm = normalize(sent)
        if not norm:
            continue

        window_end = min(cursor + max(len(norm) * 5, 200), len(concat))
        haystack = concat[cursor:window_end]
        if not haystack:
            results.append({"text": sent, "start_sec": fallback_sec, "score": 0.0})
            continue

        m = difflib.SequenceMatcher(None, haystack, norm, autojunk=False)
        match = m.find_longest_match(0, len(haystack), 0, len(norm))
        score = match.size / max(len(norm), 1)

        if score < 0.2:
            start_sec = char_times[cursor] if cursor < len(char_times) else fallback_sec
            advance = len(norm)
        else:
            absolute = cursor + match.a
            start_sec = char_times[absolute] if absolute < len(char_times) else char_times[-1]
            advance = match.a + match.size

        cursor = min(cursor + advance, len(concat))
        results.append({"text": sent, "start_sec": start_sec, "score": round(score, 2)})
    return results


def refine_with_faster(results, faster_timed, window_before=5, window_after=60):
    """用 faster 校正每個 sentence 的 start_sec(在 coarse ± window 內找 prefix 精確時間)

    保證 monotonic: search window 下限永遠 > 前一句的 fine_sec,
    refine 結果若早於前一句也強制 +1 修正。
    """
    refined_count = 0
    prev_fine = -1
    # 中文口語約 0.3s/字(含停頓重複),用來在 refine 失敗時估算 sentence 推進量
    sec_per_char = 0.3
    for r in results:
        coarse = r["start_sec"]
        norm = normalize(r["text"])
        prefix_len = min(12, max(6, len(norm) // 2))
        prefix = norm[:prefix_len]
        r["coarse_sec"] = coarse

        search_from = max(coarse - window_before, prev_fine + 1)
        search_to = coarse + window_after

        refine_ok = False
        if prefix:
            in_win = [(sec, txt) for sec, txt in faster_timed
                      if search_from <= sec <= search_to]
            if in_win:
                concat, char_times = build_concat(in_win)
                if concat:
                    m = difflib.SequenceMatcher(None, concat, prefix, autojunk=False)
                    match = m.find_longest_match(0, len(concat), 0, len(prefix))
                    if match.size >= len(prefix) * 0.5:
                        new_sec = char_times[match.a]
                        r["start_sec"] = new_sec
                        if new_sec != coarse:
                            refined_count += 1
                        refine_ok = True

        # monotonic 修正: refine 失敗時用估算時長推進(避免擠成 1s)
        if r["start_sec"] <= prev_fine:
            r["start_sec"] = prev_fine + 1
        if refine_ok:
            prev_fine = r["start_sec"]
        else:
            estimated = max(2, int(len(norm) * sec_per_char))
            prev_fine = r["start_sec"] + estimated
    return refined_count


def compute_durations(results, last_anchor_sec):
    for i, r in enumerate(results):
        if i + 1 < len(results):
            r["end_sec"] = max(results[i + 1]["start_sec"], r["start_sec"])
        else:
            r["end_sec"] = last_anchor_sec + 30
        r["duration"] = r["end_sec"] - r["start_sec"]


def write_tsv(results, path: Path, has_coarse=False):
    with path.open("w", encoding="utf-8") as f:
        if has_coarse:
            f.write("start_sec\tend_sec\tduration\tscore\tcoarse_sec\ttext\n")
        else:
            f.write("start_sec\tend_sec\tduration\tscore\ttext\n")
        for r in results:
            safe = r["text"].replace("\t", " ").replace("\n", " ")
            if has_coarse:
                f.write(f"{r['start_sec']}\t{r['end_sec']}\t{r['duration']}\t"
                        f"{r['score']}\t{r.get('coarse_sec', '')}\t{safe}\n")
            else:
                f.write(f"{r['start_sec']}\t{r['end_sec']}\t{r['duration']}\t"
                        f"{r['score']}\t{safe}\n")


def print_report(date, source_label, source_chunks_desc, results, out_path,
                 refined_count=None):
    n = len(results)
    if n == 0:
        print("沒有可對齊的句子")
        return
    high = sum(1 for r in results if r["score"] >= 0.7)
    mid = sum(1 for r in results if 0.4 <= r["score"] < 0.7)
    low = sum(1 for r in results if r["score"] < 0.4)
    durs = [r["duration"] for r in results]
    in_range = sum(1 for d in durs if 5 <= d <= 15)

    print(f"=== {date} ({source_label}) ===")
    print(f"來源: {source_chunks_desc}")
    print(f"proofread 句數: {n}")
    print(f"匹配品質: 高(≥.7) {high}/{n}  中(.4-.7) {mid}/{n}  低(<.4) {low}/{n}")
    print(f"時長分布: 5-15s 區間 {in_range}/{n}, 平均 {sum(durs)/n:.1f}s, "
          f"max {max(durs)}s, min {min(durs)}s")
    if refined_count is not None:
        print(f"faster 校正: {refined_count}/{n} 句的 start_sec 被細時間戳取代")
    print(f"輸出: {out_path.relative_to(ROOT)}")
    print()
    print("--- 前 12 條 sample ---")
    for r in results[:12]:
        preview = r["text"][:60] + ("…" if len(r["text"]) > 60 else "")
        coarse_info = ""
        if "coarse_sec" in r and r["coarse_sec"] != r["start_sec"]:
            coarse_info = f" (coarse={fmt_mmss(r['coarse_sec'])})"
        print(f"  [{fmt_mmss(r['start_sec'])}]{coarse_info} +{r['duration']:>3}s "
              f"s={r['score']:.2f}  {preview}")

    if low > 0:
        print()
        print(f"--- 匹配低分 (<.4) sample (最多 5 條) ---")
        for r in [r for r in results if r["score"] < 0.4][:5]:
            preview = r["text"][:80] + ("…" if len(r["text"]) > 80 else "")
            print(f"  [{fmt_mmss(r['start_sec'])}] s={r['score']:.2f}  {preview}")


def align(date: str, source: str = "hybrid"):
    md_path = PROOFREAD / f"{date}.md"
    if not md_path.exists():
        sys.exit(f"找不到: {md_path}")

    sentences = split_sentences(md_path.read_text(encoding="utf-8"))

    if source == "hybrid":
        gn_path = RAW / f"{date}_getnote.timed.txt"
        ft_path = RAW / f"{date}_faster.timed.txt"
        if not gn_path.exists():
            sys.exit(f"hybrid 需要 _getnote.timed.txt: {gn_path}")
        if not ft_path.exists():
            sys.exit(f"hybrid 需要 _faster.timed.txt: {ft_path}")

        gn_timed = parse_timed(gn_path)
        ft_timed = parse_timed(ft_path)
        if not gn_timed or not ft_timed:
            sys.exit("時間戳檔案為空")

        gn_concat, gn_char_times = build_concat(gn_timed)
        results = monotonic_align(sentences, gn_concat, gn_char_times, gn_timed[-1][0])
        refined = refine_with_faster(results, ft_timed)
        compute_durations(results, ft_timed[-1][0])

        OUT.mkdir(exist_ok=True)
        out_path = OUT / f"{date}_hybrid.tsv"
        write_tsv(results, out_path, has_coarse=True)
        print_report(date, "hybrid (getnote 文字 + faster 時間)",
                     f"{len(gn_timed)} getnote chunks + {len(ft_timed)} faster chunks",
                     results, out_path, refined_count=refined)

    else:
        if source == "faster":
            timed_path = RAW / f"{date}_faster.timed.txt"
        elif source == "getnote":
            timed_path = RAW / f"{date}_getnote.timed.txt"
        else:
            sys.exit(f"source 必須是 'hybrid' / 'faster' / 'getnote',不是 '{source}'")
        if not timed_path.exists():
            sys.exit(f"找不到: {timed_path}")
        timed = parse_timed(timed_path)
        if not timed:
            sys.exit(f"{timed_path} 沒有可用的時間戳行")

        concat, char_times = build_concat(timed)
        results = monotonic_align(sentences, concat, char_times, timed[-1][0])
        compute_durations(results, timed[-1][0])

        OUT.mkdir(exist_ok=True)
        out_path = OUT / f"{date}_{source}.tsv"
        write_tsv(results, out_path)
        print_report(date, source, f"{len(timed)} chunks", results, out_path)


if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        sys.exit(f"用法: python3 {Path(__file__).name} <date> [hybrid|faster|getnote]")
    align(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "hybrid")
