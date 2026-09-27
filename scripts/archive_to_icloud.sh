#!/usr/bin/env bash
# archive_to_icloud.sh
# 把 _inbox/ 的原始音檔(無損 ALAC 等)「複製」到 iCloud Drive 的封存資料夾,
# 當作 fine-tune / 聲音克隆訓練素材的真理來源(source of truth)。
#
# 設計原則:
#   - 用 cp 複製,不是 mv —— 本地 _inbox 在驗證完成前保持原樣
#   - 跳過已存在且 checksum 相同的檔(可重複執行、續傳)
#   - 複製後立刻用 shasum 逐檔比對來源/目的,任何不符就 fail-loud
#   - 維護 archive 端 SHA256SUMS 清單,日後可隨時 `shasum -c` 稽核
#
# 注意:複製進 iCloud 資料夾只保證「本地副本正確」,真正上雲是 iCloud 背景作業。
#       在 iCloud 確實上傳完成前,不要清掉 / 壓縮本地 _inbox(見 scripts/icloud_upload_status.sh)。

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INBOX_DIR="$PROJECT_ROOT/_inbox"
ARCHIVE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/voice-to-note-archive"
MANIFEST="$ARCHIVE_DIR/SHA256SUMS"

mkdir -p "$ARCHIVE_DIR"

shopt -s nullglob nocaseglob
files=("$INBOX_DIR"/*.{m4a,mp3,wav})
total=${#files[@]}
if [ "$total" -eq 0 ]; then
  echo "_inbox/ 沒有音檔,結束。"
  exit 0
fi

echo "準備封存 $total 個音檔 → $ARCHIVE_DIR"
echo

copied=0; skipped=0; n=0
for src in "${files[@]}"; do
  n=$((n+1))
  base="$(basename "$src")"
  dst="$ARCHIVE_DIR/$base"

  src_sum="$(shasum -a 256 "$src" | awk '{print $1}')"

  # 已存在且 checksum 相同 → 跳過(支援續傳 / 重跑)
  if [ -f "$dst" ]; then
    dst_sum="$(shasum -a 256 "$dst" | awk '{print $1}')"
    if [ "$src_sum" = "$dst_sum" ]; then
      printf "[%d/%d] 跳過(已存在且一致):%s\n" "$n" "$total" "$base"
      skipped=$((skipped+1))
      continue
    fi
    echo "⚠️  目的已存在但 checksum 不同:$base — 覆蓋為來源版本"
  fi

  printf "[%d/%d] 複製:%s\n" "$n" "$total" "$base"
  cp -p "$src" "$dst"

  dst_sum="$(shasum -a 256 "$dst" | awk '{print $1}')"
  if [ "$src_sum" != "$dst_sum" ]; then
    echo "❌ checksum 不符,複製失敗:$base"
    echo "   src=$src_sum"
    echo "   dst=$dst_sum"
    exit 1
  fi
  copied=$((copied+1))
done

echo
echo "重建 checksum 清單:$MANIFEST"
( cd "$ARCHIVE_DIR" && shasum -a 256 *.{m4a,mp3,wav} 2>/dev/null > SHA256SUMS )

echo
echo "完成:複製 ${copied}、跳過 ${skipped},共 ${total} 個檔。"
echo "封存大小:$(du -sh "$ARCHIVE_DIR" | awk '{print $1}')"
echo
echo "下一步:跑 scripts/icloud_upload_status.sh 確認 iCloud 上傳完成,"
echo "        全部上傳完才可以壓縮 / 清理本地 _inbox。"
