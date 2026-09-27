#!/usr/bin/env bash
# transcribe_faster_whisper.sh - 用 mlx-whisper + whisper-large-v3-turbo 轉錄音檔
#
# 名字保留歷史包袱(輸出檔還是 _faster.txt),但底層已經換成:
# - mlx-whisper:Apple MLX 框架,原生 Metal + Neural Engine,Apple Silicon 上比
#   faster-whisper (CPU only on Mac) 快非常多
# - whisper-large-v3-turbo:OpenAI 2024-10 釋出的官方輕量版,4 個 decoder layer
#   (原本 32 個),~8x 快、品質掉很少、支援多語(含中文)
#
# 用法:
#   ./scripts/transcribe_faster_whisper.sh _inbox/2026-04-08-1718.m4a
# 輸出:
#   _transcripts_raw/2026-04-08-1718_faster.txt (已去重)
#   _transcripts_raw/2026-04-08-1718_faster.timed.txt (時間戳 sidecar)
#
# 第一次跑會從 HuggingFace 下載 mlx-community/whisper-large-v3-turbo (~1.5 GB)。

set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"

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
OUTPUT_FILE="$RAW_DIR/${STEM}_faster.txt"

mkdir -p "$RAW_DIR"

# 組裝 prompt
if command -v yq >/dev/null 2>&1 && [ -f "$DICT_FILE" ]; then
  WORDS=$(yq -r '.. | select(type == "!!str")' "$DICT_FILE" \
    | grep -v '^$' \
    | tr '\n' '、' \
    | sed 's/、$//')
  PROMPT="尚承的中文語音筆記,內容可能包含:${WORDS}。"
else
  PROMPT="這是中文語音筆記。"
fi

echo "→ 轉錄 (mlx-whisper + large-v3-turbo): $INPUT_FILE"

python3 -c "
import sys, time
from pathlib import Path
import mlx_whisper

input_file = sys.argv[1]
output_file = sys.argv[2]
prompt = sys.argv[3]

print('→ 載入模型 + 轉錄中...')
start = time.time()

result = mlx_whisper.transcribe(
    input_file,
    path_or_hf_repo='mlx-community/whisper-large-v3-turbo',
    language='zh',
    initial_prompt=prompt,
    condition_on_previous_text=True,
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,
    hallucination_silence_threshold=2.0,
    word_timestamps=False,
)

elapsed = time.time() - start
print(f'→ 轉錄耗時: {elapsed:.0f} 秒')

# 收 segments
import re

def is_hallucination(text):
    \"\"\"偵測 whisper turbo 在靜音段產生的幻覺\"\"\"
    t = text.strip()
    if not t:
        return True
    # 非中文:重複同一個短詞 3 次以上(如 'Al toil toil toil...')
    words = t.split()
    if len(words) >= 3 and not re.search(r'[\u4e00-\u9fff]', t):
        if len(set(words)) <= 2:
            return True
    # 中文或混合:同一個字佔比超過 70%(如 '鍛鍛鍛鍛...' '我鍛鍛鍛...')
    chars = [c for c in t if not c.isspace()]
    if len(chars) >= 6:
        from collections import Counter
        most_common_char, count = Counter(chars).most_common(1)[0]
        if count / len(chars) > 0.7:
            return True
    return False

import json
lines = []
timed_lines = []
conf_records = []
hallucination_count = 0
for seg in result['segments']:
    text = seg['text'].strip()
    if not text:
        continue
    if is_hallucination(text):
        hallucination_count += 1
        continue
    lines.append(text)
    s = int(seg['start'])
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    timed_lines.append(f'[{h:02d}:{m:02d}:{sec:02d}] {text}')
    conf_records.append({
        'start': round(seg['start'], 2),
        'end': round(seg.get('end', seg['start']), 2),
        'text': text,
        'avg_logprob': round(seg.get('avg_logprob', 0.0), 4),
        'no_speech_prob': round(seg.get('no_speech_prob', 0.0), 4),
        'compression_ratio': round(seg.get('compression_ratio', 0.0), 4),
    })
if hallucination_count:
    print(f'→ 幻覺過濾: {hallucination_count} 段')

# 去重連續重複行
deduped = [lines[0]] if lines else []
for line in lines[1:]:
    if line != deduped[-1]:
        deduped.append(line)

output = '\n'.join(deduped) + '\n'
Path(output_file).write_text(output, encoding='utf-8')
print(f'→ 去重: {len(lines)} → {len(deduped)} 行')
print(f'✓ 完成: {output_file}')

# 時間戳版,給校對時做時間定位用
timed_output = '\n'.join(timed_lines) + '\n'
timed_file = output_file.replace('_faster.txt', '_faster.timed.txt')
Path(timed_file).write_text(timed_output, encoding='utf-8')
print(f'✓ 時間戳版: {timed_file}')

# 信心分數 sidecar,給 C1 低信心定位用
conf_output = '\n'.join(json.dumps(c, ensure_ascii=False) for c in conf_records) + '\n'
conf_file = output_file.replace('_faster.txt', '_faster.conf.jsonl')
Path(conf_file).write_text(conf_output, encoding='utf-8')
print(f'✓ 信心分數版: {conf_file}')
" "$INPUT_FILE" "$OUTPUT_FILE" "$PROMPT"
