#!/usr/bin/env python3
"""
clean_getnote.py — 把 Get 筆記 `audio.original` 欄位的原始文字清洗成純逐字稿,
                   並同時輸出帶時間戳的版本給校對時做時間戳定位用。

Get 筆記輸出特徵:
  - 每段前面有「🔵 我(我) [HH:MM:SS]」說話人/時間戳行
  - 內文偶爾夾雜 /sil 標記
  - 偶爾出現純日文行(辨識幻覺,使用者錄音實為純中文)
  - 簡體中文

清洗規則:
  1. 從段首「🔵 我(我) [HH:MM:SS]」擷取時間戳(只在 .timed.txt 保留)
  2. 移除 /sil 標記
  3. 含平假名/片假名的整段內容刪除(辨識幻覺)
  4. 簡轉繁(opencc s2twp)
  5. 連續空行壓成單一空行

輸出:
  輸入  _transcripts_raw/<檔名>_getnote.txt
  輸出  _transcripts_raw/<檔名>_getnote.txt        (純文字版,跟其他三引擎同格式)
  輸出  _transcripts_raw/<檔名>_getnote.timed.txt  (每段前綴 [HH:MM:SS],給時間戳定位用)

用法:
    python3 scripts/clean_getnote.py _transcripts_raw/2026-04-10-1541_getnote.txt

注意:**必須用 `python3`**(/usr/local/bin/python3),不要用 `python`。
miniconda 的 python 通常沒裝 opencc,會導致簡轉繁失敗。
"""

import re
import sys
from pathlib import Path

# 平假名 U+3040-U+309F、片假名 U+30A0-U+30FF
JP_KANA_RE = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]')

# 任何以 [HH:MM:SS] 結尾的行 → 說話人/時間戳行,順便擷取時間戳
TIMESTAMP_LINE_RE = re.compile(r'\[(\d{1,2}:\d{2}:\d{2})\]\s*$')


def parse_blocks(text: str):
    """把原文切成 [(timestamp_str_or_None, [content_lines]), ...]。

    每段以「以 [HH:MM:SS] 結尾的行」開頭。開頭沒有時間戳的內容會掛在 timestamp=None
    區塊(實務上 Get 筆記不會這樣,但 defensively 處理)。
    """
    blocks = []
    current_ts = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = TIMESTAMP_LINE_RE.search(line.rstrip())
        if m:
            # 上一段先收尾
            if current_lines or current_ts is not None:
                blocks.append((current_ts, current_lines))
            current_ts = m.group(1)
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines or current_ts is not None:
        blocks.append((current_ts, current_lines))

    return blocks


def clean_block(lines: list[str]) -> tuple[str | None, dict]:
    """把單一 block 的內容行清洗成一段文字。

    回傳 (cleaned_text, stats);若整段被刪掉(全空 / 全日文幻覺)回傳 (None, stats)。
    """
    stats = {"jp": 0, "sil": 0}
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if '/sil' in line:
            stats["sil"] += line.count('/sil')
            line = line.replace('/sil', '').strip()
            if not line:
                continue
        if JP_KANA_RE.search(line):
            stats["jp"] += 1
            continue
        out.append(line)

    if not out:
        return None, stats
    # block 內若有多行,join 成一行(符合「一個 timestamp 對應一段語音」的語意)
    return ' '.join(out), stats


def clean(text: str) -> tuple[str, str, dict]:
    """主清洗函式。

    Returns (plain_text, timed_text, stats):
      plain_text: 純文字版,每段一段(段間空行),適合做為第 4 來源
      timed_text: 每段前綴 [HH:MM:SS] text,給時間戳定位用
    """
    blocks = parse_blocks(text)
    stats = {
        "speaker": 0,
        "jp": 0,
        "sil": 0,
        "blocks_in": len(blocks),
        "blocks_out": 0,
    }

    plain_paragraphs: list[str] = []
    timed_lines: list[str] = []

    for ts, lines in blocks:
        if ts is not None:
            stats["speaker"] += 1
        cleaned, delta = clean_block(lines)
        stats["jp"] += delta["jp"]
        stats["sil"] += delta["sil"]
        if cleaned is None:
            continue
        stats["blocks_out"] += 1
        plain_paragraphs.append(cleaned)
        if ts:
            timed_lines.append(f'[{ts}] {cleaned}')
        else:
            timed_lines.append(cleaned)

    plain = '\n\n'.join(plain_paragraphs) + '\n'
    timed = '\n'.join(timed_lines) + '\n'

    # 簡轉繁(必須成功,否則跟其他引擎比對會繁簡混雜)
    try:
        from opencc import OpenCC
    except ImportError:
        print(
            '✗ opencc 未安裝。請改用 python3 (/usr/local/bin/python3),'
            '或裝 opencc-python-reimplemented 到目前環境。',
            file=sys.stderr,
        )
        sys.exit(2)
    cc = OpenCC('s2twp')
    plain = cc.convert(plain)
    timed = cc.convert(timed)

    return plain, timed, stats


def main():
    if len(sys.argv) < 2:
        print('用法: python3 clean_getnote.py <檔案路徑>', file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f'找不到檔案: {path}', file=sys.stderr)
        sys.exit(1)

    raw = path.read_text(encoding='utf-8')
    plain, timed, stats = clean(raw)

    # 純文字版就地改寫(跟其他三引擎同格式)
    path.write_text(plain, encoding='utf-8')

    # 時間戳版輸出到 sibling 檔
    timed_path = path.with_name(path.stem + '.timed' + path.suffix)
    timed_path.write_text(timed, encoding='utf-8')

    raw_lines = len(raw.splitlines())
    print(f'✓ {path.name}: {raw_lines} 行 → 純文字 {stats["blocks_out"]} 段、時間戳版 {stats["blocks_out"]} 行')
    print(f'  Get 筆記說話人段:    {stats["speaker"]}')
    print(f'  日文幻覺刪除段:      {stats["jp"]}')
    print(f'  /sil 標記移除:        {stats["sil"]}')
    print(f'  → {timed_path.name}')


if __name__ == '__main__':
    main()
