#!/usr/bin/env python3
"""
merge_yating.py — 把雅婷逐字稿的碎句按 speaker 連續合併。

雅婷的原始輸出:每個發言獨立分行,很多 1-3 字短句(「對」「就是」「文學的」)
獨立成行,讀起來像 4790+ 行的條列清單。此腳本只動視覺結構,字面不變。

用法:
    python3 scripts/merge_yating.py _transcripts_raw/<stem>_雅婷.txt
輸出:
    同目錄 <stem>_雅婷_merged.md
        - 連續同 speaker 段落合併
        - 標題 `**語者X** \\`[HH:MM:SS]\\``
        - 字面、標點完全照雅婷不動
"""
import re
import sys
from pathlib import Path

SPEAKER_RE = re.compile(r"^語者(\d+)\s+(\d+(?::\d+){1,2})$")


def normalize_time(t: str) -> str:
    parts = [int(p) for p in t.split(":")]
    if len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    elif len(parts) == 3:
        h, m, s = parts
    else:
        return t
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_segments(lines):
    segs = []
    i = 0
    while i < len(lines):
        m = SPEAKER_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        speaker, time_str = m.group(1), m.group(2)
        i += 1
        parts = []
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            if SPEAKER_RE.match(line):
                break
            parts.append(line)
            i += 1
        content = "".join(parts)
        if content:
            segs.append((speaker, time_str, content))
    return segs


def merge_consecutive(segs):
    merged = []
    for speaker, t, content in segs:
        if merged and merged[-1][0] == speaker:
            ps, pt, pc = merged[-1]
            merged[-1] = (ps, pt, pc + content)
        else:
            merged.append((speaker, t, content))
    return merged


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <雅婷檔.txt>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)

    dst = src.with_name(src.stem + "_merged.md")

    lines = src.read_text(encoding="utf-8").splitlines()
    segs = parse_segments(lines)
    merged = merge_consecutive(segs)

    out = [
        f"<!-- 來源: {src.name} -->",
        "<!-- 純格式整理:連續同 speaker 合併,字面照雅婷不動,標點未補 -->",
        "",
    ]
    for speaker, t, content in merged:
        out.append(f"**語者{speaker}** `[{normalize_time(t)}]`")
        out.append("")
        out.append(content)
        out.append("")

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"原始 {len(segs)} 段 → 合併後 {len(merged)} 段")
    if segs:
        print(f"壓縮率 {len(merged) / len(segs) * 100:.1f}%")
    print(f"✓ {dst}")


if __name__ == "__main__":
    main()
