---
description: 處理 _inbox/ 的音檔(三主力轉錄 + FireRed 第三票 + faster sidecar + 可選 Get 來源 + 套規則 + 多來源交叉比對),產出 _proofread/<檔名>.md
argument-hint: <檔名,如 2026-05-08-1616> [Get 筆記標題]
---

# Proofread 一份音檔

把 `_inbox/<檔名>.m4a` 走完整的轉錄 + 校對 pipeline,產出 `_proofread/<檔名>.md`。

## 輸入解析

`$ARGUMENTS` 的第一個 token 是**檔名**(不含副檔名,例如 `2026-05-08-1616`)。
之後若有更多文字,當作 **Get 筆記標題**(完全或部分匹配都可)。

範例:
- `/proofread 2026-05-08-1616` — 只跑本地引擎
- `/proofread 2026-05-08-1616 5月8日《不能承受的生命之轻》阅读心得` — 本地引擎 + Get 來源

若 `$ARGUMENTS` 為空,列出 `_inbox/` 裡的音檔讓使用者選,**不要自己挑最新的就動手**。

## 流程

### 1. 確認音檔存在

```bash
ls -la _inbox/<檔名>.*
```

如果找不到,停下來告訴使用者並列出 `_inbox/` 內容。

### 2. 平行啟動本地引擎轉錄(背景)+(可選)抓 Get 筆記

兩件事**同時**做,不要 sequence:

**a) 本地引擎轉錄(背景跑)**
```bash
bash scripts/transcribe.sh _inbox/<檔名>.<ext>
```
用 `run_in_background=true`,timeout 設長一點(建議 30 分鐘)。三主力很快(FunASR 30 秒、Apple 15–30 秒、SenseVoice 幾分鐘),但腳本會等最慢的 FireRed(K/V 快取後 ~5.5x 即時,63 分音檔約 12 分鐘)才結束;可以先讀已產出的主力稿開始比對,FireRed 稿出來再補第三票。

**長音檔(2+ 小時)**:不要直接跑 `transcribe.sh`,改照記憶 [長音檔轉錄引擎優先級] 先 FunASR 主跑,Apple 次之,sidecar 用 `scripts/transcribe_qwen3.py`(自帶 4 分鐘切片,長檔穩)。

**b) Get 筆記(若使用者給了標題)**
1. 用 `mcp__getnote__list_notes(since_id=0)` — 結果可能太大,會直接寫到 tool-results 檔
2. 在那個檔裡 `grep` 標題的關鍵詞,撈出 `"note_id":"..."` 字串(**用 note_id 不要用 id**,id 會精度爆炸)
3. `mcp__getnote__get_note(id=<note_id>)` 拿完整 note
4. 檢查 `_transcripts_raw/<檔名>_getnote.txt` 是否已存在 — 若存在,Read 確認是同一個 note 即可,不重寫
5. 若不存在,用 Write 工具把 `note.audio.original` 欄位**原樣**寫入 `_transcripts_raw/<檔名>_getnote.txt`
6. 跑 `python3 scripts/clean_getnote.py _transcripts_raw/<檔名>_getnote.txt`(**一定要用 `python3`**,miniconda 的 `python` 沒裝 opencc 會 fail-loud)
7. 把 `note.content`(智能總結 / 章節概要 / 金句)記在心上,校對時當語意層級對照

### 3. 套 rules.yaml(等三引擎跑完後)

對所有產出的 raw 檔跑:
```bash
for src in funasr apple sensevoice firered faster getnote; do
  [ -f "_transcripts_raw/<檔名>_${src}.txt" ] && python3 scripts/apply_rules.py "_transcripts_raw/<檔名>_${src}.txt"
done
```

(哪個來源沒產出,迴圈會自動跳過)

### 4. 讀 prompt 與 dictionary

讀進來看清楚規則:
- `prompts/proofread.md`(完整校對規則)
- `dictionary.yaml`(專有名詞最終真理)

### 5. 讀 clean 稿做交叉比對

讀(角色分工 2026-07-05 收斂,詳見 `docs/sensevoice_vs_faster.md` 開頭):
- `_transcripts_clean/<檔名>_apple.txt` — **主力:結構骨架**(自帶標點 / 斷句)
- `_transcripts_clean/<檔名>_funasr.txt` — **主力:補詞 + 修同音字**(中文專精、熱詞)
- `_transcripts_clean/<檔名>_sensevoice.txt` — **主力:補詞主力**(自帶標點、句末斷行,可當第二骨架對照)
- `_transcripts_clean/<檔名>_firered.txt`(如有) — **補詞第三票**(無標點無熱詞;弱音節/陌生專有名詞常是唯一對的)
- `_transcripts_clean/<檔名>_faster.txt` — **sidecar(2026-09-25 起 Qwen3-ASR,檔名沿用),不參與一般多數決**;只在中英混講/英文專名處查它;數字/日期改看 Apple/SenseVoice
- `_transcripts_clean/<檔名>_getnote.txt`(如有) — Get 外部來源

**C1 低信心定位(只在 `SIDECAR_ENGINE=whisper` 跑過的檔才有 `_faster.conf.jsonl`;Qwen3 sidecar 不產信心分數,預設略過此步)**:跑 `python3 scripts/flag_low_confidence.py <檔名>` 先看 whisper 自己沒把握的時間段,這些段落優先用耳朵/多引擎確認,sup 放得更有原則(注意:粒度是 ~30s window,非逐字)。

**比對策略**:
- 結構用 Apple(SenseVoice 當第二骨架對照)
- SenseVoice + FunASR 補詞、修同音字:三主力一致直接填;兩個一致、Apple 不同 → 多數決;Apple 獨家但語意明顯對 → 用 Apple
- **FireRed 當第三票**:主力有歧異時看它;主力全崩段優先用它的版本(掉書袋/西西弗斯/巴納姆效應型案例);但它無熱詞,dict 詞條仍以 dict + 主力為準
- **faster(Qwen3 sidecar)只有三個用途**:`.timed.txt` 句級時間戳、**中英混講/英文專名備援**(Fable 5 型案例)、完整稿備援。數字/日期改看 Apple/SenseVoice。它的其他獨行版本不算票
- **dict.yaml 收錄的專有名詞永遠以 dict.yaml 為準**(例:這次特蕾莎 / 弗蘭茨 / 吳軍 / 不能承受的生命之輕,各引擎寫法不一致時強制用 dict)
- **Get 筆記分情況處理**:
  - 🟢 音檔品質正常 → 當 tie-breaker,語意層級對照(智能總結/章節概要)
  - 🔴 音檔品質差(感冒、咳嗽、吵雜、長抽象內容) → **Get 完全不參與校對**,寧可多標 `<sup>字?</sup>` 讓使用者耳對齊
  - ⚠️ 主力全崩段不要當 tie-breaker(保險絲規則;先看 FireRed,連它也崩才標),標 `[?]` + 文末說明

### 6. 撰寫 `_proofread/<檔名>.md`

照 `prompts/proofread.md` 的規則寫:
- 預設**直接 best-guess 填入**,不要動不動就標 `[?]`
- 低信心用 `<sup>字?</sup>`(全形 ?),真實停頓用 `……`,只有嚴重分歧或陌生專有名詞才用 `[?]`
- `[註:...]` meta-note **只讀不寫** — 使用者寫的保留原樣,Claude 不主動加
- 段間空一行,不寫前言後記,不寫時間戳
- 一份稿子的 `[?]` 通常 0–2 個,寧可沒有不要濫用

### 7. 全形標點自查(必跑)

寫完後跑這個 Python 腳本檢查:

```bash
python3 - <<'EOF'
forbidden = {0x002C: ",", 0x002E: ".", 0x003A: ":", 0x003B: ";",
             0x003F: "?", 0x0021: "!", 0x0028: "(", 0x0029: ")"}
path = "_proofread/<檔名>.md"
with open(path) as f:
    text = f.read()
counts = {cp: 0 for cp in forbidden}
for ch in text:
    cp = ord(ch)
    if cp in counts:
        counts[cp] += 1
for cp, c in counts.items():
    print(f"U+{cp:04X} ({forbidden[cp]}): {c}")
EOF
```

任何一個 count > 0 都要修。例外只有 `<sup>` HTML tag 和 markdown 語法本身 — 但這份稿子預期都是 0。

**常見地雷**:LLM 寫稿時鍵盤輸入會夾雜 ASCII `,` `?` `(` `)`。修法:

```bash
python3 - <<'EOF'
path = "_proofread/<檔名>.md"
with open(path) as f:
    t = f.read()
trans = {0x002C: "，", 0x003F: "？", 0x0028: "（", 0x0029: "）",
         0x0021: "！", 0x003B: "；"}
t = t.translate(trans)
with open(path, "w") as f:
    f.write(t)
EOF
```

`U+002E (.)` 和 `U+003A (:)` 通常出現在檔名(`dictionary.yaml`)或時間戳(`43:54`)— 改寫成「dictionary 詞典」、「43 分 54 秒」之類避開,不要用全形句點/冒號替換(會難看)。

`<sup>?</sup>` 內的 `?` 也必須是全形 U+FF1F,`[?]` 同樣。上面 translate 會自動處理。

### 8. 結尾回報

告訴使用者:
- 「處理完了,校對稿在 `_proofread/<檔名>.md`」
- 若有 `[?]`,用一兩句話說明它在哪、為什麼標(對應文末斜體說明)
- 若標了 dict.yaml 沒收的專有名詞,提一下建議補進詞典
- 提醒使用者:手改之前先 commit 這版校對稿(Claude 原版進版控後,
  之後 `/recheck` 才能用 `git diff` 精確定位手改的位置,跨 session 也不會抓瞎)

**不要**自己跑 `/polish` 或主動修改使用者已校對的檔案 — proofread 結束就停下,polish 是另一個獨立步驟。

## 標記哲學提醒

方向 B 的精髓是「視覺上滿眼乾淨」。如果校對稿上有一堆 `[?]` 或 `<sup>?</sup>`,代表 prompt 沒貫徹。低信心其實大多可以 best-guess + 不標,留 `<sup>?</sup>` 給真的兩可、留 `[?]` 給真的崩。
