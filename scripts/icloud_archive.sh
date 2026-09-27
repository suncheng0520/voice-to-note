#!/usr/bin/env bash
# icloud_archive.sh — 查 iCloud archive 上傳狀態 / 上傳完成後釋放本地副本
#
# 用法:
#   ./scripts/icloud_archive.sh            # 只看狀態:已上傳 / 待上傳 / 本地實佔
#   ./scripts/icloud_archive.sh --evict    # 把「已上傳」的檔釋放成 placeholder,騰出本地空間
#                                          # (需要時 Finder 點一下或任何讀取會自動重新下載)
#
# evict 只會動「已確認上傳完成」的檔;還在上傳的檔一律保留,確保不會弄丟唯一副本。

set -euo pipefail

ARCHIVE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/voice-to-note-archive"
MODE="${1:-status}"

[ -d "$ARCHIVE_DIR" ] || { echo "找不到 archive:$ARCHIVE_DIR"; exit 1; }

# brctl status 會列出仍 needs-upload 的檔名(被混淆成 2{13}3.m4a 之類,無法精準對應),
# 所以這裡改用可靠的「待上傳檔數」當門檻:全部上傳完才允許整批 evict。
pending=$(brctl status "$ARCHIVE_DIR" 2>/dev/null | grep -c 'needs-upload' || true)
total=$(find "$ARCHIVE_DIR" -type f \( -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.wav' \) | wc -l | tr -d ' ')
placeholders=$(find "$ARCHIVE_DIR" -name '*.icloud' | wc -l | tr -d ' ')
local_size=$(du -sh "$ARCHIVE_DIR" 2>/dev/null | awk '{print $1}')

echo "archive: $ARCHIVE_DIR"
echo "  音檔總數:        $total"
echo "  仍待上傳:        $pending"
echo "  已釋放(placeholder): $placeholders"
echo "  本地實佔空間:    $local_size"
echo

if [ "$MODE" != "--evict" ]; then
  if [ "$pending" -eq 0 ]; then
    echo "✅ 全部已上傳。可跑 ./scripts/icloud_archive.sh --evict 釋放本地空間。"
  else
    echo "⏳ 還有 $pending 個檔在上傳,先別 evict。稍後再查。"
  fi
  exit 0
fi

# --evict 模式
if [ "$pending" -ne 0 ]; then
  echo "⛔ 還有 $pending 個檔沒上傳完,為避免弄丟唯一副本,拒絕 evict。等全部上傳完再來。"
  exit 1
fi

echo "開始釋放本地副本(已雲端備份,讀取時會自動重新下載)..."
evicted=0
while IFS= read -r f; do
  if brctl evict "$f" 2>/dev/null; then
    evicted=$((evicted+1))
  fi
done < <(find "$ARCHIVE_DIR" -type f \( -iname '*.m4a' -o -iname '*.mp3' -o -iname '*.wav' \))

echo "完成:釋放 $evicted 個檔。"
echo "archive 本地實佔:$(du -sh "$ARCHIVE_DIR" 2>/dev/null | awk '{print $1}')"
