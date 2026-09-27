#!/usr/bin/env bash
# compress_inbox.sh
# 把 _inbox/ 的無損音檔(ALAC)就地轉成 AAC 單聲道 ~32kbps,純粹給「回聽 / play.sh」用。
#
# 前提(務必先滿足):
#   無損原檔已由 archive_to_icloud.sh 複製到 iCloud archive 並通過 checksum 驗證。
#   本腳本會「取代」_inbox 裡的原檔,只有在有 archive 備份時才該執行。
#
# 設計:
#   - 沿用 .m4a 容器(AAC),play.sh / transcribe 的副檔名假設不變
#   - 逐檔轉到暫存檔,先驗證「能解碼 + 時長與原檔相差 < 2 秒」,才原子取代
#   - 已是低位元率 AAC 的檔自動跳過(可重複執行)
#   - 任何一步失敗就保留原檔、跳過該檔,不會留下半截檔

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INBOX_DIR="$PROJECT_ROOT/_inbox"
ARCHIVE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/voice-to-note-archive"
BITRATE="32k"

# 安全閥:archive 不存在就拒跑
if [ ! -d "$ARCHIVE_DIR" ]; then
  echo "❌ 找不到 iCloud archive($ARCHIVE_DIR),為避免無備份壓縮,中止。"
  exit 1
fi

dur_of() { ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$1" 2>/dev/null | cut -d. -f1; }

shopt -s nullglob nocaseglob
files=("$INBOX_DIR"/*.{m4a,mp3,wav})
total=${#files[@]}
[ "$total" -eq 0 ] && { echo "_inbox/ 沒有音檔。"; exit 0; }

before=$(du -sk "$INBOX_DIR" | awk '{print $1}')
echo "開始壓縮 $total 個檔(AAC 單聲道 $BITRATE)。原檔無損備份在 archive。"
echo

done_n=0; skip_n=0; fail_n=0; n=0
for src in "${files[@]}"; do
  n=$((n+1))
  base="$(basename "$src")"

  # 已是低位元率 AAC → 視為壓過,跳過
  # 注意:tr 去掉換行後輸出無結尾換行,read 會回傳非零 → set -e 會誤殺,故補 || true
  read codec br < <(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,bit_rate -of default=noprint_wrappers=1:nokey=1 "$src" 2>/dev/null | tr '\n' ' ') || true
  if [ "$codec" = "aac" ] && [ -n "$br" ] && [ "$br" != "N/A" ] && [ "$br" -lt 64000 ] 2>/dev/null; then
    printf "[%d/%d] 跳過(已壓縮):%s\n" "$n" "$total" "$base"
    skip_n=$((skip_n+1)); continue
  fi

  # archive 必須有對應備份才動這個檔
  if [ ! -f "$ARCHIVE_DIR/$base" ]; then
    printf "[%d/%d] ⚠️ 跳過(archive 無備份):%s\n" "$n" "$total" "$base"
    fail_n=$((fail_n+1)); continue
  fi

  stem="${base%.*}"
  tmp="$INBOX_DIR/.${stem}.compressing.m4a"
  rm -f "$tmp"

  printf "[%d/%d] 壓縮:%s ... " "$n" "$total" "$base"
  if ! ffmpeg -nostdin -v error -y -i "$src" -ac 1 -c:a aac -b:a "$BITRATE" -movflags +faststart "$tmp" 2>/dev/null; then
    echo "✗ ffmpeg 失敗,保留原檔"; rm -f "$tmp"; fail_n=$((fail_n+1)); continue
  fi

  d_src=$(dur_of "$src"); d_tmp=$(dur_of "$tmp")
  if [ -z "$d_tmp" ] || [ $(( d_src > d_tmp ? d_src - d_tmp : d_tmp - d_src )) -gt 2 ]; then
    echo "✗ 時長不符(原 ${d_src}s / 壓 ${d_tmp:-?}s),保留原檔"; rm -f "$tmp"; fail_n=$((fail_n+1)); continue
  fi

  # 取代原檔(原檔副檔名可能不是 m4a → 一併處理)
  rm -f "$src"
  mv "$tmp" "$INBOX_DIR/${stem}.m4a"
  echo "✓"
  done_n=$((done_n+1))
done

after=$(du -sk "$INBOX_DIR" | awk '{print $1}')
echo
echo "完成:壓縮 ${done_n}、跳過 ${skip_n}、失敗/略過 ${fail_n}。"
printf "_inbox:%s → %s(省 %s)\n" \
  "$(echo "scale=1;$before/1024/1024"|bc)G" \
  "$(echo "scale=1;$after/1024/1024"|bc)G" \
  "$(echo "scale=1;($before-$after)/1024/1024"|bc)G"
