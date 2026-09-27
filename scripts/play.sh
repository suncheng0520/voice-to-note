#!/usr/bin/env bash
# play.sh — 從指定時間戳開始播放 _inbox/ 裡的音檔
#
# 用途:
#   配合 `_proofread/<檔名>_questions.md` 裡每個 [?N] 旁邊的 (~HH:MM:SS) 註記,
#   一鍵跳到該時間點聽原音,逐個確認 / 修正不確定的字。
#
# 用法:
#   ./scripts/play.sh _inbox/2026-04-10-1541.m4a 28:05
#   ./scripts/play.sh _inbox/2026-04-10-1541.m4a 00:28:05
#   ./scripts/play.sh 2026-04-10-1541 28:05         # 自動找 _inbox/<檔名>.{m4a,mp3,wav}
#
# 需要 mpv 或 ffplay(任一):
#   brew install mpv      # 推薦,有 OSD 顯示時間
#   brew install ffmpeg   # 含 ffplay,fallback

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ $# -lt 1 ]; then
  echo "用法: $0 <音檔或檔名> [HH:MM:SS]"
  echo ""
  echo "  例:"
  echo "    $0 2026-04-10-1541 28:05"
  echo "    $0 _inbox/2026-04-10-1541.m4a 00:28:05"
  exit 1
fi

INPUT="$1"
SEEK="${2:-0}"

# 如果 INPUT 不是現有檔案路徑,試著當成檔名在 _inbox/ 裡找
if [ ! -f "$INPUT" ]; then
  for ext in m4a mp3 wav; do
    candidate="$PROJECT_ROOT/_inbox/${INPUT}.${ext}"
    if [ -f "$candidate" ]; then
      INPUT="$candidate"
      break
    fi
  done
fi

if [ ! -f "$INPUT" ]; then
  echo "✗ 找不到音檔: $1"
  echo "  試過 $PROJECT_ROOT/_inbox/$1.{m4a,mp3,wav}"
  exit 1
fi

echo "→ 播放: $INPUT"
echo "→ 起點: $SEEK"
echo "→ 按 q 結束"

# 優先用 mpv(有 OSD 顯示當下時間,方便判斷是不是聽到目標位置),沒裝再 fallback ffplay
if command -v mpv >/dev/null 2>&1; then
  exec mpv --start="$SEEK" --no-video --osd-level=3 --term-osd-bar "$INPUT"
elif command -v ffplay >/dev/null 2>&1; then
  exec ffplay -ss "$SEEK" -nodisp -autoexit "$INPUT"
else
  echo "✗ 需要安裝 mpv 或 ffplay:"
  echo "    brew install mpv      (推薦)"
  echo "    brew install ffmpeg   (含 ffplay)"
  exit 1
fi
