#!/usr/bin/env python3
"""
merge_transcripts.py — 合併 FunASR + faster-whisper + Apple Speech 三份逐字稿

策略:
  1. 把三份稿子都正規化(簡轉繁、去標點空白)壓成純文字
  2. 以 faster-whisper 為主軸,跟 FunASR 做字元級 diff
  3. 對每個 diff 區塊,用前後文錨點在 Apple 文字中找對應段落
  4. 三邊都列出來給人/LLM 校對時看

用法:
    python scripts/merge_transcripts.py _transcripts_clean/2026-04-09-1902

輸出:
    _transcripts_clean/2026-04-09-1902_merged.txt

向後相容:
    若 _apple.txt 不存在,自動降級為兩方比對
"""

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

CTX_LEN = 12  # 前後文錨點長度


def strip_punct(text: str) -> str:
    """去掉所有標點和空白,只留中英文字。"""
    return re.sub(r'[\s,。?!,\.\?\!\:\;\-…\—\"\"\'\'\(\)\[\]、·，！？：；「」『』（）《》〈〉【】“”‘’～｜\n\r]', '', text)


def normalize(text: str) -> str:
    """統一繁簡體後去標點,用於比對。"""
    try:
        from opencc import OpenCC
        cc = OpenCC('s2twp')
        text = cc.convert(text)
    except ImportError:
        pass
    return strip_punct(text)


def find_segment_by_context(clean: str, ctx_before: str, ctx_after: str,
                            fallback_pos: int, max_len: int = 60) -> str:
    """在 clean 文字中,用前後文錨點找出對應的段落。

    若找到 ctx_before 和 ctx_after,回傳兩者之間的字串。
    若只找到一個,從那個位置往後/往前抓 max_len 字。
    若都找不到,從 fallback_pos 抓一段。
    """
    # 試找 ctx_before(從 fallback 附近開始找,避免抓到前面的同樣詞)
    search_start = max(0, fallback_pos - 100)
    search_end = min(len(clean), fallback_pos + 100)
    window = clean[search_start:search_end]

    a_start = -1
    a_end = -1

    if ctx_before:
        idx = window.find(ctx_before)
        if idx >= 0:
            a_start = search_start + idx + len(ctx_before)

    if ctx_after and a_start >= 0:
        idx = clean.find(ctx_after, a_start)
        if idx >= 0 and idx - a_start < max_len:
            a_end = idx

    if a_start < 0:
        # 找不到 ctx_before,從 fallback 開始
        a_start = max(0, fallback_pos)
    if a_end < 0:
        # 找不到 ctx_after,抓 max_len 字
        a_end = min(len(clean), a_start + max_len)

    return clean[a_start:a_end]


def find_diffs(faster_raw: str, funasr_raw: str, apple_raw: str | None) -> list[dict]:
    """以 faster 為主軸,跟 funasr diff,並從 apple 取對應段落。"""
    w_clean = normalize(faster_raw)
    f_clean = normalize(funasr_raw)
    a_clean = normalize(apple_raw) if apple_raw else None

    matcher = SequenceMatcher(None, w_clean, f_clean, autojunk=False)
    diffs = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        ctx_before = w_clean[max(0, i1-CTX_LEN):i1]
        ctx_after = w_clean[i2:i2+CTX_LEN]
        diffs.append({
            'pos': i1,
            'end': i2,
            'ctx_before': ctx_before,
            'ctx_after': ctx_after,
            'faster': w_clean[i1:i2],
            'funasr': f_clean[j1:j2],
            'tag': tag,
        })

    if not diffs:
        return diffs

    # 合併相鄰的碎片差異(間距 < 5 字)
    merged_diffs = [diffs[0]]
    for d in diffs[1:]:
        prev = merged_diffs[-1]
        gap = d['pos'] - prev['end']
        if gap < 5:
            bridge_w = w_clean[prev['end']:d['pos']]
            merged_diffs[-1] = {
                'pos': prev['pos'],
                'end': d['end'],
                'ctx_before': prev['ctx_before'],
                'ctx_after': d['ctx_after'],
                'faster': prev['faster'] + bridge_w + d['faster'],
                'funasr': prev['funasr'] + bridge_w + d['funasr'],
                'tag': 'replace',
            }
        else:
            merged_diffs.append(d)

    # 過濾語氣詞差異 + 從 apple 抓對應段落
    filler = set('嗯呃啊喔欸哦哎唔那就是然後的了嘛吧呢啦')
    filtered = []
    for d in merged_diffs:
        def clean(s):
            return ''.join(c for c in s if c not in filler)
        c_w = clean(d['faster'])
        c_f = clean(d['funasr'])

        # 從 apple 抓對應段落
        if a_clean is not None:
            d['apple'] = find_segment_by_context(
                a_clean, d['ctx_before'], d['ctx_after'],
                fallback_pos=d['pos'],
                max_len=max(30, len(d['faster']) * 3),
            )
            c_a = clean(d['apple'])
        else:
            d['apple'] = ''
            c_a = None

        # 三邊去掉語氣詞後一樣 → 跳過
        if c_a is not None:
            if c_w == c_f == c_a:
                continue
            unique = len({c_w, c_f, c_a} - {''})
            d['severity'] = 'high' if unique >= 3 else 'medium'
        else:
            if c_w == c_f:
                continue
            d['severity'] = 'medium'

        filtered.append(d)

    return filtered


def format_merged(faster_raw: str, diffs: list[dict], three_way: bool) -> str:
    """產出合併稿:faster-whisper 原文 + 差異標記在文末。"""
    mode = '三引擎' if three_way else '雙引擎'
    lines = [f'# 合併稿(以 faster-whisper 為主體,{mode}比對)\n']
    lines.append(faster_raw.strip())
    lines.append('\n\n---\n')

    high = sum(1 for d in diffs if d.get('severity') == 'high')
    medium = sum(1 for d in diffs if d.get('severity') == 'medium')

    lines.append(f'# 差異段(共 {len(diffs)} 處,嚴重 {high} 處,輕微 {medium} 處)\n')
    if three_way:
        lines.append('「嚴重 🔴」=三個引擎都不一樣;「輕微 🟡」=兩邊一樣、一邊不同(可採多數決)\n')
    lines.append('')

    for i, d in enumerate(diffs, 1):
        sev = d.get('severity', 'medium')
        sev_mark = '🔴' if sev == 'high' else '🟡'
        lines.append(f'### {sev_mark} 差異 {i}')
        lines.append(f'定位: ...{d["ctx_before"]}**⟨這裡⟩**{d["ctx_after"]}...')
        lines.append(f'  faster:  {d["faster"]}')
        lines.append(f'  funasr:  {d["funasr"]}')
        if three_way:
            lines.append(f'  apple:   {d["apple"]}')
        lines.append('')

    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法: python merge_transcripts.py <base_path>")
        print("  例: python merge_transcripts.py _transcripts_clean/2026-04-09-1902")
        sys.exit(1)

    base = sys.argv[1]
    funasr_path = Path(f"{base}_funasr.txt")
    faster_path = Path(f"{base}_faster.txt")
    apple_path = Path(f"{base}_apple.txt")
    output_path = Path(f"{base}_merged.txt")

    if not funasr_path.exists():
        print(f"找不到: {funasr_path}")
        sys.exit(1)
    if not faster_path.exists():
        print(f"找不到: {faster_path}")
        sys.exit(1)

    funasr_raw = funasr_path.read_text(encoding='utf-8')
    faster_raw = faster_path.read_text(encoding='utf-8')

    f_len = len(strip_punct(funasr_raw))
    w_len = len(strip_punct(faster_raw))
    print(f'→ FunASR: {f_len} 字')
    print(f'→ faster-whisper: {w_len} 字')

    three_way = apple_path.exists()
    if three_way:
        apple_raw = apple_path.read_text(encoding='utf-8')
        a_len = len(strip_punct(apple_raw))
        print(f'→ Apple Speech: {a_len} 字')
    else:
        apple_raw = None
        print('→ Apple Speech: 無(降級為雙引擎比對)')

    diffs = find_diffs(faster_raw, funasr_raw, apple_raw)

    high = sum(1 for d in diffs if d.get('severity') == 'high')
    medium = sum(1 for d in diffs if d.get('severity') == 'medium')
    print(f'→ 差異: {len(diffs)} 處 (嚴重 {high}, 輕微 {medium})')

    merged = format_merged(faster_raw, diffs, three_way)
    output_path.write_text(merged + '\n', encoding='utf-8')
    print(f'✓ 合併完成: {output_path}')


if __name__ == '__main__':
    main()
