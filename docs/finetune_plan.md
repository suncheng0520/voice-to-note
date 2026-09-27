# B. Fine-tune 詳細規劃

> 目標:在尚承自己的**台灣口音 + 慣用詞 + 專有名詞**上,把 ASR 錯誤系統性降低,
> 讓 proofread 階段的 `<sup>?</sup>` / `[?]` 更少、best-guess 更準。
> 這是「換公開模型」做不到的——公開模型只給你「別人平均最好的」,fine-tune 給你「你的」。
>
> 現況基礎:align+slice PoC 已於 2026-05-11 完成(見 `scripts/align_for_finetune.py`、
> `scripts/slice_for_finetune.py`、`_corpus_raw/`)。本文把「剩下要做的」展開成可執行步驟。

---

## 0. 成功標準(先定義,否則只能憑感覺)

必須先有一個**客觀 eval**,不然 fine-tune 前後好壞講不清:

- **主指標:CER(字錯率)** ——在一個 held-out 驗證集(留 N 份 proofread 永不進訓練)上,
  finetuned 模型相對 base 模型的 CER 下降幅度。中文 CER 要先斷字(用 jiwer + 逐字切)。
- **副指標:專有名詞召回** ——`dictionary.yaml` 裡的詞在 held-out 音檔出現時,有沒有被認對。
- **務實指標:proofread 工作量** ——同一份音檔,finetuned 稿需要我改的字數 / 標的 sup 數。

> 沒有 eval harness 之前,不要急著訓練。第 5 步會建這個 harness。

---

## 1. 選哪個 base 來 fine-tune?(關鍵決策)

| 路線 | 優 | 缺 | 資料門檻 |
|---|---|---|---|
| **A. Whisper large-v3 + LoRA**(PoC 現有方向) | mlx/transformers+PEFT 在 Apple Silicon 友好;LoRA 輕量、數小時語料就可見效;生態最成熟 | whisper 中文非最強;**turbo 層太少不適合 finetune,要用 full large-v3**;繁體靠後處理 | 低(~2–4h) |
| **B. Paraformer / SenseVoice (FunASR) finetune** | 中文起點最強、跟你主力引擎一致;FunASR 有官方 train 腳本 | 全參數 finetune 資料需求大;LoRA 支援不如 whisper 成熟 | 中–高(~10h+) |
| **C. Qwen3-ASR finetune** | LLM-based、最新 SOTA、能吃 context(dict 當 prompt)、**附 forced-alignment model 可強化對齊** | 最重、Apple Silicon finetune 成本高、生態最新踩雷風險高 | 高 |

**建議:先走 A(Whisper LoRA)**——資料門檻最低,最快驗證「在你聲音上 finetune 到底有沒有效」這個根本問題。
- base 起點別用 turbo,用 **full large-v3**,或直接拿中文 finetune 版(如 Belle-whisper-large-v3-zh)當起點再疊 LoRA。
- 確認 A 有效(CER 真的降)再評估要不要投 B/C。
- 台灣口音的 gap 對 B 這種大陸 base 反而收益可能最大,但那是第二階段的事。

---

## 2. 資料管線(沿用 PoC + 補強)

現況:`align_for_finetune.py` + `slice_for_finetune.py` → `_corpus_raw/<date>/{0001.wav…, manifest.jsonl}`。
實測 1 份 21min → 23 chunks / 220s,接受率 30%。閘:5–15s、score≥.5、無 `[?]`/`<sup>`。

要做的(依 CP 值排):

1. **【A-3】Long-chunk splitter ← CP 值最高,優先做**
   救回 41%(31/76)被跳過的 >15s 長句。用 faster 的 phrase boundary 在 5–15s 找切點。
   救回後語料**可能翻倍**——對 LoRA 來說資料量直接決定效果,這步比換模型都重要。

2. **【新】C1 信心分數當資料閘** ← C1↔B 的綜效
   用剛做好的 `_faster.conf.jsonl`,**只收高信心段**當乾淨標註,低信心段排除。
   避免把「whisper 自己都沒把握的字」當正解餵進訓練(那會教模型學錯)。
   做法:slice 時 join whisper conf,丟掉 avg_logprob < 門檻 的 chunk。

3. **【A-1】人耳驗證對齊精度**
   隨機抽 20 段 `_corpus_raw/*.wav`,用 `play.sh` 跳播 + 印 manifest text,人工標 ✓/✗。
   對齊正確率要 >95% 才能放心全量(對齊錯 = 音檔配錯字 = 毒資料)。
   建議寫個 `scripts/review_corpus.py` 抽樣播放。

4. **【A-2】全量跑 40+ 份 proofread**
   加上 splitter 後估 ~4h 語料(原估 38 份 ~2.3h)。可能踩 edge case,逐份看 log。

5. **held-out 切分**:留最後 ~10% 的日期當 eval 集,**永不進訓練**。

---

## 3. 訓練(A-4 trainer)

- **框架**:mlx-whisper 沒有現成 trainer → 用 `transformers` + `PEFT`(LoRA)跑 MPS;
  或既有的 whisper-finetune repo。
- **LoRA 設定起點**:r=8 或 16,target = attention 的 q/v proj;lr ~1e-4;epochs 3–5;
  batch 看記憶體(MPS 上保守)。
- **資料格式**:HF `datasets` 直接吃 `manifest.jsonl`(PoC 已對齊此格式)。
- **產出**:LoRA adapter,推論時掛回 large-v3。
- **Apple Silicon 注意**:MPS 訓練比 CUDA 慢,2–4h 語料的 LoRA 估數小時/epoch,可接受;
  跑不動就上雲(租一張卡跑幾小時)。

---

## 4. 評估與上線

1. **eval harness**(第 0 步的成功標準落地):held-out 集算 CER(jiwer + 中文斷字)+ 專有名詞召回。
2. **比較**:base large-v3 vs finetuned,同一 held-out 集。
3. **上線**:若 finetuned 勝,把 `transcribe_faster_whisper*.py` 的 `path_or_hf_repo`
   指向 finetuned 模型(或推論時掛 LoRA)。**保留 base 當 fallback**。
4. **月度迭代**:proofread 資料每天在長,每月用新資料重訓一次。

---

## 5. 與 C 專案(voice-clone-tts)的關係

`../voice-clone-tts` 會跨 repo 讀 `_corpus_raw` 當 F5-TTS 訓練語料。
**同一份語料雙用**(ASR finetune + TTS clone),所以第 2 步的資料品質投資(A-1 對齊驗證、
A-3 splitter、C1 信心閘)兩個專案都受益——這是優先把資料管線做紮實的最大理由。

---

## 6. 建議執行順序(可勾選)

- [x] **A-3** long-chunk splitter(2026-06-11 完成。0429 實測 220s→586s,2.7 倍;
      子句邊界吸附 faster phrase boundary + faster 文字 fuzzy 驗證 ≥0.5 才收,
      驗證淘汰的子句寧可丟。`--no-split` 可回舊行為)
- [x] **C1 資料閘** 整合進 slice(2026-06-11 完成。時間窗 avg_logprob 加權平均
      < −0.6 丟棄,`--conf-threshold` 可調;無 conf.jsonl 自動停用。
      0604 實測擋掉 7 條,包含 17:44 中英混雜的 highlight 句 lp=−0.86,擋得正確)
- [ ] **A-1** 人耳驗證 20 段(`scripts/review_corpus.py <date>`,工具已就緒,
      **需要使用者耳朵**;建議先 `--splits-only` 驗 splitter 產的子句)
- [ ] **A-2** 全量跑 40+ 份 → 估 >1h 語料(0429+0604 兩份已 26.5 分)
      ⚠️ 大多數檔沒 getnote → 用 faster mode,低分句多是正常的(0604: 111/221 被低分濾掉)
- [ ] **eval harness**(CER + 專名召回,先能跑才有得比)
- [ ] **A-4** LoRA trainer → 訓練 → 跟 base 比
- [ ] 勝出則上線 `transcribe_faster_whisper*.py` + 排月度重訓

---

*建立於 2026-06-05。相關 PoC 工件追蹤見記憶 `project_finetune_pipeline.md`;
本文是「剩下要做的」的展開,動工前請 verify 上面 file:line 仍與現況相符。*
