#!/usr/bin/env python3
"""Measure recurring delivery markers in raw transcript files.

This is intentionally simple: it does not judge writing quality, it only gives
stable numbers that can be rerun across time windows.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_TERMS = [
    "就是",
    "其實",
    "然後",
    "我覺得",
    "我不知道",
    "應該說",
    "有點",
    "可能",
    "為什麼",
    "這件事情",
    "但是",
    "所以",
    "嗯",
    "呃",
]

DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})-(?P<m>\d{2})-(?P<d>\d{2})"),
    re.compile(r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日"),
]


@dataclass(frozen=True)
class Transcript:
    path: Path
    day: date | None
    text: str


def parse_date(name: str) -> date | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(name)
        if not match:
            continue
        try:
            return date(
                int(match.group("y")),
                int(match.group("m")),
                int(match.group("d")),
            )
        except ValueError:
            return None
    return None


def find_files(root: Path, engine: str) -> list[Path]:
    if engine == "all":
        candidates = sorted(root.glob("*.txt"))
    else:
        candidates = sorted(root.glob(f"*_{engine}.txt"))

    files: list[Path] = []
    for path in candidates:
        name = path.name
        if any(marker in name for marker in (".timed.", ".lowconf.", ".conf.")):
            continue
        if name.startswith("朋友聚餐聊天"):
            continue
        if parse_date(name) is None:
            continue
        files.append(path)
    return files


def load_transcripts(
    root: Path,
    engine: str,
    since: date | None,
    until: date | None,
) -> list[Transcript]:
    transcripts: list[Transcript] = []
    for path in find_files(root, engine):
        day = parse_date(path.name)
        if since and day and day < since:
            continue
        if until and day and day > until:
            continue
        transcripts.append(
            Transcript(path=path, day=day, text=path.read_text(encoding="utf-8"))
        )
    return transcripts


def count_terms(text: str, terms: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for term in terms:
        counts[term] = text.count(term)
    return counts


def density(count: int, chars: int) -> float:
    if chars == 0:
        return 0.0
    return count * 1000 / chars


def analyze(transcripts: list[Transcript], terms: list[str]) -> dict:
    total_chars = sum(len(item.text) for item in transcripts)
    total_counts: Counter[str] = Counter()
    monthly_chars: dict[str, int] = defaultdict(int)
    monthly_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for item in transcripts:
        counts = count_terms(item.text, terms)
        total_counts.update(counts)
        month = item.day.strftime("%Y-%m") if item.day else "unknown"
        monthly_chars[month] += len(item.text)
        monthly_counts[month].update(counts)

    return {
        "files": len(transcripts),
        "chars": total_chars,
        "terms": [
            {
                "term": term,
                "count": total_counts[term],
                "per_1k_chars": round(density(total_counts[term], total_chars), 2),
            }
            for term in terms
        ],
        "monthly": [
            {
                "month": month,
                "files": sum(
                    1
                    for item in transcripts
                    if item.day and item.day.strftime("%Y-%m") == month
                ),
                "chars": monthly_chars[month],
                "terms": [
                    {
                        "term": term,
                        "count": monthly_counts[month][term],
                        "per_1k_chars": round(
                            density(monthly_counts[month][term], monthly_chars[month]),
                            2,
                        ),
                    }
                    for term in terms
                ],
            }
            for month in sorted(monthly_chars)
        ],
        "files_included": [str(item.path) for item in transcripts],
    }


def render_markdown(result: dict, root: Path, engine: str, since: str, until: str) -> str:
    lines = [
        "# Delivery Marker Metrics",
        "",
        f"- Source: `{root}`",
        f"- Engine filter: `{engine}`",
        f"- Date range: `{since or 'beginning'}` to `{until or 'latest'}`",
        f"- Files: {result['files']}",
        f"- Characters: {result['chars']}",
        "",
        "## Overall",
        "",
        "| marker | count | per 1k chars |",
        "|---|---:|---:|",
    ]
    for row in result["terms"]:
        lines.append(f"| {row['term']} | {row['count']} | {row['per_1k_chars']:.2f} |")

    lines.extend(["", "## Monthly", ""])
    for month in result["monthly"]:
        lines.extend(
            [
                f"### {month['month']}",
                "",
                f"- Files: {month['files']}",
                f"- Characters: {month['chars']}",
                "",
                "| marker | count | per 1k chars |",
                "|---|---:|---:|",
            ]
        )
        for row in month["terms"]:
            lines.append(
                f"| {row['term']} | {row['count']} | {row['per_1k_chars']:.2f} |"
            )
        lines.append("")

    lines.extend(["## Files Included", ""])
    lines.extend(f"- `{path}`" for path in result["files_included"])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze recurring delivery markers in raw transcripts."
    )
    parser.add_argument("--root", default="_transcripts_raw", type=Path)
    parser.add_argument(
        "--engine",
        default="faster",
        help="Transcript engine suffix to scan, e.g. faster, sensevoice, funasr, apple, or all.",
    )
    parser.add_argument("--since", help="Start date, YYYY-MM-DD.")
    parser.add_argument("--until", help="End date, YYYY-MM-DD.")
    parser.add_argument(
        "--terms",
        help="Comma-separated marker list. Defaults to the delivery baseline markers.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--output", type=Path, help="Write output to this path.")
    args = parser.parse_args()

    since = date.fromisoformat(args.since) if args.since else None
    until = date.fromisoformat(args.until) if args.until else None
    terms = [term.strip() for term in args.terms.split(",")] if args.terms else DEFAULT_TERMS

    transcripts = load_transcripts(args.root, args.engine, since, until)
    result = analyze(transcripts, terms)
    if args.json:
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    else:
        output = render_markdown(result, args.root, args.engine, args.since, args.until)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
