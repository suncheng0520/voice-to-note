#!/usr/bin/env bash
# transcribe.sh - 同時用 FunASR + Apple Speech + SenseVoice + FireRedASR2 + Qwen3-ASR(sidecar)轉錄音檔,供交叉比對
#
# 用法:
#   ./scripts/transcribe.sh _inbox/2026-04-08-1718.m4a
# 輸出:
#   _transcripts_raw/<檔名>_funasr.txt       ← 主力:補詞 + 修同音字(熱詞、中文專精)
#   _transcripts_raw/<檔名>_apple.txt        ← 主力:結構骨架(標點/斷句)
#   _transcripts_raw/<檔名>_sensevoice.txt   ← 主力:補詞主力(2026-07-05 A/B 收斂升格,7/7 難檔較準且零崩)
#   _transcripts_raw/<檔名>_firered.txt      ← 補詞第三票(弱音節/陌生專有名詞;失敗不擋主流程)
#   _transcripts_raw/<檔名>_faster.txt       ← sidecar(2026-09-25 起 Qwen3-ASR + ForcedAligner,檔名沿用):timestamp + 中英混講備援
#
# 角色分工與 A/B 決策記錄詳見 docs/sensevoice_vs_faster.md 開頭「已收斂」節。
# sidecar 不參與一般多數決。2026-09-25 起 sidecar 預設 Qwen3-ASR + ForcedAligner
# (scripts/transcribe_qwen3.py);舊的 mlx-whisper turbo 路徑保留,SIDECAR_ENGINE=whisper 可切回做 A/B。

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ $# -lt 1 ]; then
  echo "用法: $0 <音檔路徑>"
  exit 1
fi

INPUT_FILE="$1"
BASENAME="$(basename "$INPUT_FILE")"
STEM="${BASENAME%.*}"

echo "→ 五引擎轉錄: $INPUT_FILE"
echo ""

echo "=== [主力 1/3] FunASR ==="
bash "$PROJECT_ROOT/scripts/transcribe_funasr.sh" "$INPUT_FILE" &
PID_FUNASR=$!

echo "=== [主力 2/3] Apple Speech ==="
swift "$PROJECT_ROOT/scripts/transcribe-apple.swift" "$INPUT_FILE" &
PID_APPLE=$!

echo "=== [主力 3/3] SenseVoice ==="
python3 "$PROJECT_ROOT/scripts/transcribe_sensevoice.py" "$INPUT_FILE" &
PID_SV=$!

# 補詞第三票:FireRedASR2(2026-07-05 收斂後留任)。
# 最慢(K/V 快取 patch 後 ~5.5x 即時,原版 ~3x)且無標點無熱詞,失敗只警告不擋主流程
echo "=== [第三票] FireRedASR2 ==="
python3 "$PROJECT_ROOT/scripts/transcribe_firered.py" "$INPUT_FILE" &
PID_FIRERED=$!

# sidecar(不參與一般多數決):產出 _faster.txt + _faster.timed.txt 時間戳,校對時作中英混講/英文專名備援。
# 預設 Qwen3-ASR + ForcedAligner(2026-09-25 起,26x 即時、CER 排第二、逐字時間戳、不 loop)。
# 舊路徑 mlx-whisper turbo(SIDECAR_ENGINE=whisper):30+ 分會 catastrophic loop(tally #2/#3),
# 長檔走 chunked 版;短檔也會小 loop(tally #7);還會 seek 卡死(2026-09-18 事件)。
DURATION="$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 "$INPUT_FILE" 2>/dev/null | cut -d. -f1)"
FASTER_CHUNK_THRESHOLD=1500  # 25 分,留安全邊際

# 逾時保險絲(2026-09-18 加):turbo 除了 loop 幻覺還會「seek 卡死」——
# mlx-whisper 0.4.3 transcribe.py 在視窗最後一個 timestamp 位置為 0 時 seek 不前進,
# 同一個 30s 視窗永遠重解、每圈跑完 6 個溫度。2026-09-15 檔的 sidecar 就這樣
# 佔著 GPU 82% 跑了 3 天沒人發現。turbo 正常 ~10x 即時,給音檔長度 2 倍、最少 15 分鐘。
# macOS 沒有 GNU timeout,用 perl 做 watchdog,整個 process group 一起殺(含 bash 包的 python)。
FASTER_TIMEOUT=$(( ${DURATION:-0} * 2 ))
if [ "$FASTER_TIMEOUT" -lt 900 ]; then FASTER_TIMEOUT=900; fi
run_with_timeout() {  # 用法: run_with_timeout <秒> <指令...>;逾時 exit 124(同 GNU timeout)
  perl -e '
    my $secs = shift @ARGV;
    my $pid = fork();
    if ($pid == 0) { setpgrp(0, 0); exec @ARGV or exit 127; }
    $SIG{ALRM} = sub {
      kill "TERM", -$pid;
      for (1..5) { last if waitpid($pid, 1) != 0; sleep 1; }  # 最多等 5 秒讓它清暫存
      kill "KILL", -$pid;
      exit 124;
    };
    alarm $secs;
    waitpid($pid, 0);
    exit(($? & 127) ? 128 + ($? & 127) : ($? >> 8));
  ' "$@"
}

SIDECAR_ENGINE="${SIDECAR_ENGINE:-qwen3}"
if [ "$SIDECAR_ENGINE" = "qwen3" ]; then
  echo "=== [sidecar] Qwen3-ASR + ForcedAligner (逾時 ${FASTER_TIMEOUT}s) ==="
  run_with_timeout "$FASTER_TIMEOUT" python3 "$PROJECT_ROOT/scripts/transcribe_qwen3.py" "$INPUT_FILE" &
elif [ "${DURATION:-0}" -ge "$FASTER_CHUNK_THRESHOLD" ] 2>/dev/null; then
  echo "=== [sidecar] faster-whisper (長檔 ${DURATION}s,走 chunked,逾時 ${FASTER_TIMEOUT}s) ==="
  run_with_timeout "$FASTER_TIMEOUT" python3 "$PROJECT_ROOT/scripts/transcribe_faster_whisper_chunked.py" "$INPUT_FILE" &
else
  echo "=== [sidecar] faster-whisper (逾時 ${FASTER_TIMEOUT}s) ==="
  run_with_timeout "$FASTER_TIMEOUT" bash "$PROJECT_ROOT/scripts/transcribe_faster_whisper.sh" "$INPUT_FILE" &
fi
PID_FASTER=$!

# 三主力任一失敗就擋;第三票與 sidecar 失敗只警告
FAIL=0
wait $PID_FUNASR || FAIL=1
wait $PID_APPLE || FAIL=1
wait $PID_SV || FAIL=1
wait $PID_FIRERED || echo "⚠ FireRedASR2(第三票)失敗,不影響主流程"
wait $PID_FASTER || {
  rc=$?
  if [ "$rc" -eq 124 ]; then
    echo "⚠ sidecar(${SIDECAR_ENGINE})逾時 ${FASTER_TIMEOUT}s 已強制終止,不影響主流程(將缺 _faster.txt/.timed.txt)"
  else
    echo "⚠ sidecar(${SIDECAR_ENGINE})失敗,不影響主流程(將缺 _faster.txt/.timed.txt)"
  fi
}

if [ $FAIL -eq 1 ]; then
  echo "✗ 有主力引擎失敗,請檢查上方錯誤訊息"
  exit 1
fi

echo ""
echo "✓ 轉錄完成:"
echo "  FunASR(主力):     _transcripts_raw/${STEM}_funasr.txt"
echo "  Apple Speech(主力): _transcripts_raw/${STEM}_apple.txt"
echo "  SenseVoice(主力):  _transcripts_raw/${STEM}_sensevoice.txt"
echo "  FireRedASR2(三票): _transcripts_raw/${STEM}_firered.txt"
echo "  sidecar(${SIDECAR_ENGINE}): _transcripts_raw/${STEM}_faster.txt + .timed.txt"
