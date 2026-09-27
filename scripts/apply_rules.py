#!/usr/bin/env python3
"""
apply_rules.py — 把 rules.yaml 的規則套用到一份逐字稿上。

用法:
    python scripts/apply_rules.py _transcripts_raw/2026-04-07.txt
輸出:
    _transcripts_clean/2026-04-07.txt

需要:
    pip install pyyaml
"""

import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_FILE = PROJECT_ROOT / "rules.yaml"
CLEAN_DIR = PROJECT_ROOT / "_transcripts_clean"


def load_rules() -> dict:
    if not RULES_FILE.exists():
        return {"simple_replacements": [], "context_replacements": []}
    with RULES_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def apply_simple(text: str, rules: list) -> tuple[str, list[str]]:
    """無歧義全域取代。"""
    log = []
    for rule in rules or []:
        src = rule["from"]
        dst = rule["to"]
        if src in text:
            count = text.count(src)
            text = text.replace(src, dst)
            log.append(f"  [simple] {src} → {dst} (×{count})")
    return text, log


def apply_context(text: str, rules: list) -> tuple[str, list[str]]:
    """有歧義 regex 替換。"""
    log = []
    for rule in rules or []:
        pattern = rule["pattern"]
        replace = rule["replace"]
        note = rule.get("note", "")
        new_text, n = re.subn(pattern, replace, text)
        if n > 0:
            text = new_text
            log.append(f"  [regex]  {pattern} → {replace} (×{n}) {note}")
    return text, log


def main():
    if len(sys.argv) < 2:
        print("用法: python apply_rules.py <原始稿路徑>")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"找不到檔案: {input_path}")
        sys.exit(1)

    rules = load_rules()
    text = input_path.read_text(encoding="utf-8")

    print(f"→ 套用規則: {input_path.name}")

    text, log_simple = apply_simple(text, rules.get("simple_replacements", []))
    text, log_ctx = apply_context(text, rules.get("context_replacements", []))

    all_log = log_simple + log_ctx
    if all_log:
        print("套用了以下規則:")
        for line in all_log:
            print(line)
    else:
        print("  (沒有任何規則命中)")

    CLEAN_DIR.mkdir(exist_ok=True)
    output_path = CLEAN_DIR / input_path.name
    output_path.write_text(text, encoding="utf-8")
    print(f"✓ 完成: {output_path}")


if __name__ == "__main__":
    main()
