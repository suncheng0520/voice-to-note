#!/usr/bin/env bash
# transcribe_funasr.sh - 用 FunASR (Paraformer) 轉錄音檔
#
# 特色:
#   - 自動從 dictionary.yaml 載入熱詞,提高專有名詞辨識率
#   - 輸出自動簡轉繁(OpenCC s2twp)
#
# 用法:
#   ./scripts/transcribe_funasr.sh _inbox/2026-04-08-1718.m4a
# 輸出:
#   _transcripts_raw/2026-04-08-1718_funasr.txt
#
# 需求:
#   pip install funasr modelscope torch torchaudio opencc-python-reimplemented pyyaml
#
# 在 Apple Silicon 上會自動走 MPS (Metal),CPU fallback 可用 FUNASR_DEVICE=cpu 強制

set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW_DIR="$PROJECT_ROOT/_transcripts_raw"
DICT_FILE="$PROJECT_ROOT/dictionary.yaml"

if [ $# -lt 1 ]; then
  echo "用法: $0 <音檔路徑>"
  exit 1
fi

INPUT_FILE="$1"
BASENAME="$(basename "$INPUT_FILE")"
STEM="${BASENAME%.*}"
OUTPUT_FILE="$RAW_DIR/${STEM}_funasr.txt"

mkdir -p "$RAW_DIR"

echo "→ 轉錄 (FunASR): $INPUT_FILE"

python3 -c "
import sys
from pathlib import Path

input_file = sys.argv[1]
output_file = sys.argv[2]
dict_file = sys.argv[3]
scripts_dir = sys.argv[4]

# === 載入熱詞 ===
hotwords = ''
try:
    import yaml
    d = yaml.safe_load(Path(dict_file).read_text(encoding='utf-8')) or {}
    words = []
    for key, val in d.items():
        if isinstance(val, list):
            words.extend([str(v) for v in val if isinstance(v, str)])
    hotwords = ' '.join(words)
    print(f'→ 熱詞: {len(words)} 個')
except Exception as e:
    print(f'→ 熱詞載入失敗: {e}')

# === 選 device(預設 mps,可用 FUNASR_DEVICE=cpu 覆蓋)===
import os
import torch
device = os.environ.get('FUNASR_DEVICE')
if not device:
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
print(f'→ device: {device}')

# === 載入模型 ===
# offline fallback:modelscope.cn 掛掉(502)時,只要模型已 cache 在本地就走本地路徑,
# 不碰網路(見 scripts/modelscope_local.py)。乾淨機器第一次跑仍會上線下載。
sys.path.insert(0, scripts_dir)
from modelscope_local import resolve, missing_local

model_id, vad_id, punc_id = 'paraformer-zh', 'fsmn-vad', 'ct-punc'
missing = missing_local(model_id, vad_id, punc_id)
if missing:
    print('→ 模型來源: 線上 modelscope(以下未在本地 cache,需連網下載;若 modelscope 正在當機本次會失敗):', ', '.join(missing))
else:
    print('→ 模型來源: 本地 cache(offline)')

print('→ 載入模型中...')
from funasr import AutoModel

model = AutoModel(
    model=resolve(model_id),
    vad_model=resolve(vad_id),
    punc_model=resolve(punc_id),
    spk_model=None,
    device=device,
    disable_update=True,
)

# === 轉錄 ===
print('→ 轉錄中...')
kwargs = dict(input=input_file, batch_size_s=300)
if hotwords:
    kwargs['hotword'] = hotwords
res = model.generate(**kwargs)

# === 收集文字 ===
lines = []
for item in res:
    text = item.get('text', '')
    if text.strip():
        lines.append(text.strip())

# === 去重 ===
deduped = [lines[0]] if lines else []
for line in lines[1:]:
    if line != deduped[-1]:
        deduped.append(line)

# === 簡轉繁 ===
print('→ 簡轉繁中...')
from opencc import OpenCC
cc = OpenCC('s2twp')
converted = [cc.convert(line) for line in deduped]

output = '\n'.join(converted) + '\n'
Path(output_file).write_text(output, encoding='utf-8')
print(f'→ 去重: {len(lines)} → {len(deduped)} 行')
print(f'✓ 完成: {output_file}')
" "$INPUT_FILE" "$OUTPUT_FILE" "$DICT_FILE" "$PROJECT_ROOT/scripts"
