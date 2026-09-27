#!/usr/bin/env bash
# sync_from_icloud.sh
# 把 iCloud Drive 中繼資料夾的音檔搬到本地 _inbox/

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

ICLOUD_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/voice-to-note-inbox"
INBOX_DIR="$PROJECT_ROOT/_inbox"
LOG_FILE="$PROJECT_ROOT/scripts/sync.log"

mkdir -p "$INBOX_DIR"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"
}

# iCloud 的 placeholder 檔案會以 .icloud 結尾(隱藏檔)
# 例如還沒下載完的 "錄音.m4a" 會出現為 ".錄音.m4a.icloud"
# 我們要先強制下載,等下載完再搬

# 1. 對所有 .icloud placeholder 觸發下載
find "$ICLOUD_DIR" -name "*.icloud" -type f 2>/dev/null | while read -r placeholder; do
  log "觸發下載: $placeholder"
  # brctl download 會強制 iCloud 下載這個檔案
  brctl download "$placeholder" 2>/dev/null || true
done

# 2. 等最多 30 秒讓下載完成
for i in {1..30}; do
  remaining=$(find "$ICLOUD_DIR" -name "*.icloud" -type f 2>/dev/null | wc -l | tr -d ' ')
  if [ "$remaining" = "0" ]; then
    break
  fi
  sleep 1
done

# 3. 搬移所有音檔
shopt -s nullglob nocaseglob
moved=0
for file in "$ICLOUD_DIR"/*.{m4a,mp3,wav}; do
  if [ -f "$file" ]; then
    basename=$(basename "$file")
    target="$INBOX_DIR/$basename"

    # 如果目標已存在,加時間戳避免覆蓋
    if [ -e "$target" ]; then
      stem="${basename%.*}"
      ext="${basename##*.}"
      target="$INBOX_DIR/${stem}-$(date +%s).${ext}"
    fi

    mv "$file" "$target"
    log "搬移: $basename → $target"
    moved=$((moved + 1))
  fi
done

if [ "$moved" -gt 0 ]; then
  log "完成,共搬移 $moved 個檔案"

  # 立刻把新進音檔「複製」一份到 iCloud archive 當備份真理來源。
  # 暫存區是 mv(清空),但 _inbox 本身只是工作副本 ——
  # 唯有 archive 那份(cp + checksum 驗證)才是不可重現的訓練素材備份。
  # archive_to_icloud.sh 會跳過已存在且一致的檔,所以這裡重跑安全。
  if [ -x "$PROJECT_ROOT/scripts/archive_to_icloud.sh" ]; then
    log "觸發 archive_to_icloud.sh 備份新音檔"
    "$PROJECT_ROOT/scripts/archive_to_icloud.sh" >> "$LOG_FILE" 2>&1 || log "⚠️ 封存失敗,請手動檢查"
  fi

  # 順便發 macOS 通知
  osascript -e "display notification \"$moved 個音檔已進 inbox\" with title \"voice-to-note\"" 2>/dev/null || true
fi