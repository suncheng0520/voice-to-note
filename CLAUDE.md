# Voice-to-Note 工作流

這個專案把 iOS 語音備忘錄(或其他來源的中文錄音)轉成校對過的逐字稿。
使用者是尚承,寫作風格屬於「暗流隨筆」,口語自述為主,
語氣、停頓、重複都是素材的一部分,**不能潤稿**。

## 資料夾用途

- `_inbox/` — 音檔工作副本。**注意:這裡是 AAC 單聲道 32k 的「壓縮回聽版」,不是無損原檔**
  (省空間,給 play.sh 回聽/校對對齊用)。無損原檔(ALAC)的真理來源在
  **iCloud `voice-to-note-archive/`**,已 evict 成 placeholder、本地不佔空間,讀取時自動下載。
  - **要做 fine-tune / 聲音克隆訓練時,從 iCloud archive 取無損原檔,不要用 _inbox 的壓縮版**
  - 重轉錄理論上也該用無損版;但壓縮版對轉錄品質影響小,既有逐字稿夠用就別重轉
  - 管理腳本:`scripts/archive_to_icloud.sh`(複製+checksum 驗證)、
    `scripts/compress_inbox.sh`(壓縮回聽版)、`scripts/icloud_archive.sh [--evict]`(查上傳/釋放本地)
  - 新音檔流程:`sync_from_icloud.sh` 搬進 _inbox 後會**自動**呼叫 archive 備份;
    要省空間時再手動跑 compress_inbox.sh + icloud_archive.sh --evict
- `_transcripts_raw/` — 各引擎原始輸出(.txt),本地引擎為主(三主力 + FireRed 第三票 + Qwen3 sidecar,檔名沿用 `_faster`),Get 筆記為可選外部來源
- `_transcripts_clean/` — 套用 rules.yaml 後的版本(.txt)
- `_proofread/` — LLM 校對結果,每份音檔產出**一個檔**:`<檔名>.md`
  - 低信心處用 `<sup>字?</sup>` 輕度標記(視覺上幾乎看不到)
  - 嚴重語意分歧 / 未知專有名詞用 `[?]`(沒有編號),文末加 1–2 行斜體說明
  - 真實停頓 / 情緒卡住 → `……`,不標記
  - `[註:...]` 是**使用者事後手動補**的 meta-note(澄清、延伸思考),
    Claude 一律保留原樣,**不自己新增、不修改、不刪除**
- `_polished/` — proofread 的可讀版本(保留語意與節奏,刪 stutter 不潤稿)
  - 由 `/polish <檔名>` 觸發,詳細規則在 `.claude/commands/polish.md`
  - 同檔名不同資料夾,檔頭 HTML 註解寫 polish 來源
  - 用途:給未來自己重讀、給 RAG 檢索;不是摘要、不是潤稿

## 核心檔案

- `dictionary.yaml` — 個人專有詞典(人名、產品、概念),餵給 FunASR(sidecar 的 Qwen3 無熱詞)
- `rules.yaml` — 同音錯誤對照表(精確替換 + regex)
- `prompts/proofread.md` — LLM 校對 prompt
- `scripts/transcribe.sh` — 主入口,同時呼叫三主力引擎(FunASR / Apple / SenseVoice)
  + FireRedASR2 補詞第三票 + sidecar(2026-09-25 起預設 Qwen3-ASR + ForcedAligner,
  `SIDECAR_ENGINE=whisper` 可切回舊的 mlx-whisper turbo;角色分工見 `docs/sensevoice_vs_faster.md` 開頭與文末);
  sidecar 有逾時 watchdog(音檔長度 2 倍、最少 15 分),卡死會被整組殺掉不擋主流程
- `scripts/transcribe_funasr.sh` — FunASR 轉錄(含熱詞 + 簡轉繁,Apple Silicon 自動走 MPS)
- `scripts/transcribe_sensevoice.py` — SenseVoice 轉錄(**補詞主力**,自帶標點、句末斷行,長檔穩零崩)
- `scripts/transcribe-apple.swift` — Apple SpeechAnalyzer 轉錄(macOS 26+ 本地引擎,跟 iOS 語音備忘錄同源,自帶標點,`--lang` 切語言)
- `scripts/transcribe_firered.py` — FireRedASR2-AED(MLX)**補詞第三票**(無標點無熱詞、50s 低能量切片、附 per-chunk 信心分數;弱音節/陌生專有名詞常是唯一對的引擎;限制見 `docs/sensevoice_vs_faster.md`)
- `scripts/transcribe_qwen3.py` — Qwen3-ASR-1.7B-8bit + Qwen3-ForcedAligner-0.6B-8bit(**sidecar**,輸出沿用 `_faster.txt` / `_faster.timed.txt`:句級時間戳(逐字對齊取句首)+ 中英混講/英文專名備援 + 完整稿備援;**不參與一般多數決**;無熱詞、數字寫中文、無信心分數)
- `scripts/transcribe_faster_whisper.sh` / `transcribe_faster_whisper_chunked.py` — 舊 sidecar mlx-whisper turbo,`SIDECAR_ENGINE=whisper` 時才跑(loop / seek 卡死是本性;唯一會產 `_faster.conf.jsonl` 給 flag_low_confidence.py)
- `scripts/apply_rules.py` — 把 rules.yaml 套到原始稿上
- `scripts/merge_transcripts.py` — 把三個引擎的稿子做字元級 diff,輸出合併稿
- `scripts/clean_getnote.py` — 清洗 Get 筆記 `audio.original` 欄位(去說話人/時間戳/日文幻覺 + 簡轉繁),把它變成跟本地引擎同格式的純逐字稿;**同時輸出 `<檔名>_getnote.timed.txt` 保留每段時間戳**,給校對時做時間戳定位用
- `scripts/play.sh` — 從指定時間戳開始播放 `_inbox/` 裡的音檔(用 mpv 或 ffplay),校對後若想回去聽某段,查 `_transcripts_raw/<檔名>_getnote.timed.txt` 或 `_faster.timed.txt` 拿時間戳後直接跳播

## 標準工作流程

當使用者指定音檔或說「處理 inbox」時:

1. 對每個音檔執行:
   a. 跑 `scripts/transcribe.sh <音檔>`,同時啟動三主力 + 第三票 + sidecar
      (角色分工 2026-07-05 收斂,詳見 `docs/sensevoice_vs_faster.md`):
      - **FunASR (Paraformer)**(主力) → `_transcripts_raw/<檔名>_funasr.txt`
        中文專精,自帶標點,輸出已簡轉繁,有熱詞;
        在 Apple Silicon 上自動走 MPS(Metal),56 分音檔約 30 秒
      - **Apple SpeechAnalyzer**(主力,結構骨架) → `_transcripts_raw/<檔名>_apple.txt`
        macOS 26+ 本地引擎(跟 iOS 語音備忘錄背後同一個 SpeechAnalyzer + zh-TW 模型),
        自帶標點,本地跑很快(50 分鐘音檔約 15-30 秒),沒有雲端逾時問題。
        第一次跑某個 locale 會 prompt 下載模型(zh-TW 約幾百 MB,只下載一次)
      - **SenseVoice**(主力,**補詞主力**) → `_transcripts_raw/<檔名>_sensevoice.txt`
        自帶標點、句末斷行,可當第二骨架對照;A/B 7/7 難檔較準且零崩後升格
      - **FireRedASR2**(補詞第三票,失敗不擋主流程) → `_transcripts_raw/<檔名>_firered.txt` + `.timed.txt` + `.conf.jsonl`
        無標點無熱詞,只當補詞票;弱音節/陌生專有名詞常是唯一對的
        (巴納姆效應/西西弗斯/掉書袋),主力全崩段優先看它
      - **Qwen3-ASR + ForcedAligner**(sidecar,失敗不擋主流程) → `_transcripts_raw/<檔名>_faster.txt` + `_faster.timed.txt`
        檔名沿用 `_faster` 是歷史包袱(2026-09-25 前是 mlx-whisper turbo)。**不參與一般多數決**;
        留三個用途:`.timed.txt` 句級時間戳(aligner 逐字對齊取句首,play.sh 跳播)、
        **中英混講/英文專名備援**(唯一會把整句英文吐出來的引擎,例:Fable 5)、完整稿備援(CER 僅次 Apple)。
        數字它寫中文(四百/十二),數字/日期改看 Apple/SenseVoice;無熱詞、無信心分數(lowconf 定位停用)
   b. **(可選) 第 4 來源:Get 筆記**。如果使用者在請求裡同時給你
      **檔名 + Get 筆記標題**(例:「處理 2026-04-10-1541,標題是『对长期被忽视感受...』」),
      就把它拉進來當第 4 個來源:
      - 用 `mcp__getnote__list_notes`(必要時翻頁)找到標題對得上的 note,拿它的 `note_id`(字串型,不能用 `id`,精度會炸)
        - **翻頁鐵律**:下一頁的 `since_id` 用 `notes[-1].note_id`(string),**不要用回傳的 `next_cursor`**——
          `next_cursor` 在 server 端是 number,19 位 ID 在 JS Number 末 3 位會被四捨五入,
          照它翻頁會跳過或抓到不存在的筆記。同一個原因,`get_note` 永遠傳 `note_id` 字串,不要傳 `id` 數字
      - 用 `mcp__getnote__get_note` 抓 `audio.original` 欄位
      - 用 Write 工具把欄位內容**原樣**寫到 `_transcripts_raw/<檔名>_getnote.txt`
      - 跑 `python3 scripts/clean_getnote.py _transcripts_raw/<檔名>_getnote.txt`
        (就地清洗純文字版 + 同時輸出 `<檔名>_getnote.timed.txt` 給時間戳定位用。
        **一定要用 `python3`**,miniconda 的 `python` 沒裝 opencc,會 fail-loud 終止)
      - 把回傳的 `note.content` 欄位(智能總結 + 章節概要 + 金句)記在心上,
        校對時當作語意層級的對照(它有時會反過來抓出本地引擎集體判錯的地方)
   c. 對所有結果都跑 `scripts/apply_rules.py`,
      輸出到 `_transcripts_clean/<檔名>_{funasr,apple,sensevoice,firered,faster}.txt`(以及 `_getnote.txt` 如有)
   d. 讀 `prompts/proofread.md`,**同時讀所有 clean 稿交叉比對**:
      - **結構骨架用 Apple Speech**(自帶標點、斷句;SenseVoice 也有標點可當第二骨架對照;
        FunASR 輸出一整段、firered 每行碎片,都不適合當骨架;faster(Qwen3)雖一句一行但角色是 sidecar,不當骨架)
      - **SenseVoice + FunASR 補詞、修同音字**:三主力一致的直接填;
        兩個一致、Apple 不同,用多數決;Apple 獨家但語意明顯對,用 Apple
      - **FireRed 當補詞第三票**:主力有歧異時看它;弱音節/陌生專有名詞它常是
        唯一對的;主力全崩段優先用它的版本(但仍照低信心標記原則)
      - **faster(Qwen3 sidecar)不參與一般多數決**,只在**中英混講/英文專名**處查它的版本;
        數字/日期改看 Apple/SenseVoice(Qwen 寫中文數字);其餘場合它的獨行版本不算票
      - **三主力多數決**產出 best-guess,**直接填進校對稿,不加標記**
      - **低信心的字**(單引擎孤證、引擎互相矛盾、語意有張力)用 `<sup>字?</sup>` 輕度標記
      - **真實停頓**(講卡、情緒來了、話題跳、長空檔)用 `……` 帶過,不標記
      - **Get 筆記分兩種情況處理**(常有漏字、偶有日語幻覺):
        - 🟢 **音檔品質正常**:當 tie-breaker 用,只在主力引擎有輕微歧異時參考,
          同時參考它的智能總結/章節概要當語意層級對照
        - 🔴 **音檔品質差**(使用者感冒/咳嗽、錄音環境吵、50 min+ 抽象內容):
          **Get 筆記完全不參與校對**。低品質輸入下 Get 的 AI 補完幻覺會暴增,
          產出「文法通順、語意合理但不是使用者講的」文字,會污染知識庫且難以察覺
          (案例:2026-04-21-1613 L65 被 Get 補尾覆蓋)。
          寧可多標 `<sup>字?</sup>` 讓使用者耳對齊,也不要用 Get 填空
        - **⚠️ 保險絲規則:三主力全崩時不要當 tie-breaker**(先看 FireRed 第三票,
          它也崩才算全崩),此時應該標 `[?]` + 文末說明(prompts/proofread.md 有完整說明)。
          全崩的段落通常是語速快/情緒強/口音重的段,最需要人耳確認,
          Get 筆記雖然有時能撈到語意但可能是 AI 補完的「似是而非」版本
        - **專有名詞鐵律**:Get 筆記沒有 dictionary.yaml 加持,
          凡是 dictionary.yaml 已收錄的人名/產品/概念,
          **永遠以 dictionary.yaml + 主力引擎為準**,不要因為 Get 筆記版本不同就改
      - **只有這兩種情況才標 `[?]`**(沒有編號,一份稿子通常 0–2 個):
        - 🔴 **嚴重語意分歧**:不同來源給出完全不同的語意,影響整段情緒方向
        - 🔴 **陌生專有名詞**:明顯是人名/產品/概念但 dict.yaml 沒收,各引擎又不一致
        → 這兩種在文末加 `---` + 1–2 行斜體說明,例:
          `*「想要陪伴」處 Get 筆記版聽成「比較偏」,~37:32,建議重聽*`
   e. 校對結果輸出**一個檔**:`_proofread/<檔名>.md`
      - 正文(best-guess 填好、低信心 `<sup>字?</sup>`、停頓 `……`)
      - 如有 `[?]` 嚴重分歧 / 未知專有名詞,在文末加 `---` + 1–2 行斜體說明
      - **不產出 `_questions.md` 檔**
      - **不在校對稿裡寫時間戳**
      - 如果使用者之後想重聽某一段,自己查 `_transcripts_raw/<檔名>_getnote.timed.txt`
        或 `_faster.timed.txt` 拿時間戳,再用 `./scripts/play.sh <檔名> <HH:MM:SS>` 跳播
2. 全部處理完後,**停下來**告訴使用者:
   「處理完 N 個檔案,校對稿在 `_proofread/<檔名>.md`。如有 `[?]` 標記的嚴重分歧,文末會說明。」

## 校對的不可違反原則

讀 `prompts/proofread.md` 取得完整規則。重點摘要:

- **不要潤稿**。口語的重複、繞、停頓全部保留
- 語氣詞(嗯、欸、就是、然後、我覺得)全部保留
- 只修明顯的同音字辨識錯誤、補標點、分段
- **預設直接 best-guess 填入**,不要動不動就標 `[?]`
- 低信心用 `<sup>字?</sup>`;真實停頓用 `……`;只有嚴重分歧或陌生專有名詞才用 `[?]`
- `[註:...]` meta-note 是**使用者專屬**,Claude 只讀不寫(避免污染知識庫)
- 目標是「帶質感的半成品」,不是「語法完美的逐字稿」。可以帶錯字,不追求 100%

## 規則演化

當使用者說「這次又錯了 X→Y,加進規則」時:

1. 先判斷這個錯誤屬於哪一類:
   - **專有名詞**(人名、產品、概念) → 加進 `dictionary.yaml`
   - **無歧義同音字**(例如「貼杵→鐵杵」,「貼杵」本身不是常用詞)
     → 加進 `rules.yaml` 的 `simple_replacements`
   - **有歧義同音字**(例如「被→背」,兩個字都常用)
     → 加進 `rules.yaml` 的 `context_replacements`,並寫好 regex 限定上下文
2. 修改前先 `view` 看現在的內容,避免重複
3. 修改後告訴使用者加在哪一節,並說明判斷理由

## 不要做的事

- 不要修改 `_proofread/` 裡使用者已經人工校對過的檔案
- 不要在沒問過的情況下刪除 `_inbox/` 的音檔
- **不要過度標記**。方向 B 的精髓是「視覺上滿眼乾淨」— 如果校對稿上有一堆 `[?]`,代表 prompt 沒貫徹
- 不要設定 cron 或自動排程 — 使用者手動觸發
