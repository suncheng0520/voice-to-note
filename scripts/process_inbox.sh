#!/usr/bin/env bash
# process_inbox.sh — 掃描 _inbox/ 裡未處理的音檔,跑四引擎轉錄 + 規則替換
#
# 判斷「已處理」的方式:_transcripts_raw/ 裡四引擎 raw 稿
# (funasr/faster/apple/sensevoice)都在才跳過
# LLM 校對不在這裡跑,需要在 Claude Code 裡手動執行

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

# 防止 cron 重複執行:用 lock file
LOCK_FILE="/tmp/voice-to-note-process.lock"
if [ -f "$LOCK_FILE" ]; then
  # 檢查持鎖的進程是否還在
  LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
  if kill -0 "$LOCK_PID" 2>/dev/null; then
    echo "另一個 process_inbox 正在執行 (PID $LOCK_PID),跳過"
    exit 0
  fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INBOX="$PROJECT_ROOT/_inbox"
RAW_DIR="$PROJECT_ROOT/_transcripts_raw"
CLEAN_DIR="$PROJECT_ROOT/_transcripts_clean"
TRANSCRIBE="$PROJECT_ROOT/scripts/transcribe.sh"
APPLY_RULES="$PROJECT_ROOT/scripts/apply_rules.py"
LOG_FILE="$PROJECT_ROOT/_inbox/.process_log"

mkdir -p "$RAW_DIR" "$CLEAN_DIR"

COUNT=0

for AUDIO in "$INBOX"/*.{m4a,mp3,wav}; do
  [ -f "$AUDIO" ] || continue

  STEM="$(basename "${AUDIO%.*}")"

  # 已處理過就跳過(四引擎 raw 稿都要在,否則視為未完成,重跑)
  if [ -f "$RAW_DIR/${STEM}_funasr.txt" ] \
     && [ -f "$RAW_DIR/${STEM}_faster.txt" ] \
     && [ -f "$RAW_DIR/${STEM}_apple.txt" ] \
     && [ -f "$RAW_DIR/${STEM}_sensevoice.txt" ]; then
    continue
  fi

  echo "[$(date '+%Y-%m-%d %H:%M')] 處理: $STEM" | tee -a "$LOG_FILE"

  # 四引擎轉錄
  if bash "$TRANSCRIBE" "$AUDIO"; then
    # 對所有產出的引擎結果都套用規則(沒產的自動跳過)
    for SUFFIX in _funasr _faster _apple _sensevoice _getnote; do
      RAW_FILE="$RAW_DIR/${STEM}${SUFFIX}.txt"
      if [ -f "$RAW_FILE" ]; then
        python3 "$APPLY_RULES" "$RAW_FILE"
      fi
    done
    echo "[$(date '+%Y-%m-%d %H:%M')] 完成: $STEM" | tee -a "$LOG_FILE"
    COUNT=$((COUNT + 1))
  else
    echo "[$(date '+%Y-%m-%d %H:%M')] 失敗: $STEM" | tee -a "$LOG_FILE"
  fi
done

if [ "$COUNT" -gt 0 ]; then
  echo "共處理 $COUNT 個新檔案,請在 Claude Code 裡跑校對"
else
  echo "沒有新檔案需要處理"
fi
