#!/usr/bin/env python3
"""
strip_proofread_speaker.py — 把 _proofread/<檔名>.md 的雅婷 speaker ID 拿掉,
變成「純對話流 + 時間戳 anchor」格式。

何時用:
    多人對話音檔。雅婷(或其他 ASR diarization 服務)對相近聲線、長對話的
    speaker 跟蹤不可靠,標籤會 drift。把 ID 拿掉、讓使用者讀的時候自己對
    應到誰,比強塞錯標更誠實。

用法:
    python3 scripts/strip_proofread_speaker.py _proofread/<stem>.md
輸出:
    同目錄 <stem>_no_speaker.md

格式變化:
    **語者X** `[00:00:00]`\\n\\n內容    →    `[00:00:00]` 內容
    **語者X**(接話/笑)\\n\\n內容          →    內容(獨立段)
    孤兒 **語者X**\\n                        →    刪除
保留:
    `> *備註*` 引用塊內的「語者X」字樣(供查回原檔對應位置)
    時間戳、字面修正、`<sup>?</sup>`、停頓 ……、HTML 註釋、`---` 分段
"""
import re
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <_proofread/檔名.md>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"找不到檔案: {src}")
        sys.exit(1)

    dst = src.with_name(src.stem + "_no_speaker.md")
    text = src.read_text(encoding="utf-8")
    orig_size = len(text)

    # 1. **語者X[註]** [括號註]? `[時間]`\n\n  →  `[時間]` (時間戳合併到段首)
    text = re.sub(
        r"\*\*語者[123][^*]*\*\*\s*(?:\([^)]*\))?\s*(`[^`]+`)\s*\n\n",
        r"\1 ",
        text,
    )
    # 2. **語者X**(接話/笑) 沒時間戳的 → 直接刪 header,讓內容自成段
    text = re.sub(
        r"\*\*語者[123]\*\*\s*\([^)]*\)\s*\n\n?",
        "",
        text,
    )
    # 3. 孤兒 **語者X**(自己一行,沒時間戳沒括號) → 刪
    text = re.sub(
        r"^\*\*語者[123]\*\*\s*$\n\n?",
        "",
        text,
        flags=re.MULTILINE,
    )

    dst.write_text(text, encoding="utf-8")
    print(f"原檔 {orig_size} bytes → 新檔 {len(text)} bytes")
    print(f"✓ {dst}")


if __name__ == "__main__":
    main()
