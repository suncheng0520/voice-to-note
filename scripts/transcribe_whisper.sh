#!/usr/bin/env bash
# transcribe.sh - 用 openai-whisper (Python) 轉錄音檔
#
# 用法:
#   ./scripts/transcribe.sh _inbox/2026-04-07.m4a
# 輸出:
#   _transcripts_raw/2026-04-07.txt (已去重連續重複行)
#
# 需求:
#   - openai-whisper 已安裝 (pip install openai-whisper)
#   - yq 已安裝 (brew install yq)
#   - ffmpeg 已安裝

set -euo pipefail

# cron 環境沒有 Homebrew PATH,手動加上
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# === 設定區 ===
WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
LANGUAGE="zh"

# === 路徑 ===
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DICT_FILE="$PROJECT_ROOT/dictionary.yaml"
RAW_DIR="$PROJECT_ROOT/_transcripts_raw"

if [ $# -lt 1 ]; then
  echo "用法: $0 <音檔路徑>"
  exit 1
fi

INPUT_FILE="$1"
BASENAME="$(basename "$INPUT_FILE")"
STEM="${BASENAME%.*}"
OUTPUT_FILE="$RAW_DIR/$STEM.txt"

mkdir -p "$RAW_DIR"

# === 從 dictionary.yaml 組裝 prompt ===
if command -v yq >/dev/null 2>&1 && [ -f "$DICT_FILE" ]; then
  WORDS=$(yq -r '.. | select(type == "!!str")' "$DICT_FILE" \
    | grep -v '^$' \
    | tr '\n' '、' \
    | sed 's/、$//')
  PROMPT="尚承的中文語音筆記,內容可能包含:${WORDS}。"
else
  PROMPT="這是中文語音筆記。"
fi

echo "→ 轉錄: $INPUT_FILE"
echo "→ 模型: $WHISPER_MODEL"
echo "→ Prompt: ${PROMPT:0:80}..."

# === 用 openai-whisper 轉錄 ===
TMP_DIR="$(mktemp -d -t whisper-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT

whisper "$INPUT_FILE" \
  --model "$WHISPER_MODEL" \
  --language "$LANGUAGE" \
  --initial_prompt "$PROMPT" \
  --output_format txt \
  --output_dir "$TMP_DIR" \
  --condition_on_previous_text True \
  --no_speech_threshold 0.6 \
  --compression_ratio_threshold 2.4 \
  --hallucination_silence_threshold 2

# === 去重連續重複行 + 輸出 ===
TMP_TXT="$TMP_DIR/$STEM.txt"
if [ -f "$TMP_TXT" ]; then
  python3 -c "
import sys
from pathlib import Path
lines = Path(sys.argv[1]).read_text().splitlines()
deduped = [lines[0]] if lines else []
for line in lines[1:]:
    if line.strip() and line != deduped[-1]:
        deduped.append(line)
Path(sys.argv[2]).write_text('\n'.join(deduped) + '\n')
print(f'→ 去重: {len(lines)} → {len(deduped)} 行')
" "$TMP_TXT" "$OUTPUT_FILE"
  echo "✓ 完成: $OUTPUT_FILE"
else
  echo "✗ 錯誤: whisper 沒有產出檔案"
  exit 1
fi
