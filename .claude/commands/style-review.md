---
description: 重跑文風/口條整體回饋,跟上一份基準相比,輸出到 docs/文風回饋-<期間>.md
argument-hint: [輸出標籤,例如 2026-Q3 或 2026-09。省略則用今天日期 YYYY-MM]
---

# 文風回饋 — 定期重跑

把全部 `_proofread/` 校對稿重新做一次風格/口條分析,並**跟上一份基準相比**,看作者「尚承」的文風在這段時間怎麼移動。

基準檔(baseline)就是 `docs/文風回饋-*.md`。第一份是 `docs/文風回饋-2026-Q2.md`(2026-06-30 建立,涵蓋 2026-04-07 ~ 2026-06-29)。

**這是手動觸發的,不排程**(遵守 CLAUDE.md「不要設定 cron 或自動排程」)。建議節奏:每月或每季跑一次。

## 操作步驟

1. 決定輸出標籤:`$ARGUMENTS` 有給就用它;沒給就用今天的 `YYYY-MM`。輸出檔為 `docs/文風回饋-<標籤>.md`。
   - 若該檔已存在,先問使用者要覆蓋還是換標籤。

2. 用 **Workflow 工具**跑既有工作流(`.claude/workflows/style-review.js`):
   ```
   Workflow({ name: "style-review" })
   ```
   它會自己:列出全部 `_proofread/*.md` → 找最近一份 `docs/文風回饋-*.md` 當上次基準 →
   14 組讀者分批精讀 → 5 維度分析 → 整合 + 〈與上一份基準相比〉+ 對抗式複核。
   回傳 `{ final, dimensions, quotes, compare, prev_baseline_path, corpus_count, date_range }`。

3. 把回傳內容組成 Markdown 寫到 `docs/文風回饋-<標籤>.md`,結構照第一份基準:
   - 檔頭 metadata(分析範圍、`corpus_count` 篇、`date_range`、產出方式、上一份基準路徑、生成日期)
   - 正文 = `final`
   - `# 附錄 A — 五維度深度分析` = `dimensions`
   - `# 附錄 B — proofread vs polished 對照` = `compare` 的四個欄位
   - `# 附錄 C — 精選逐字金句` = `quotes` 逐句列點
   - 若 `final` 開頭有 agent 殘留的英文 meta 行(如 "All quotes verified..."),寫檔前刪掉。

4. 寫完後**停下來**告訴使用者:輸出在哪、涵蓋幾篇、以及〈與上一份基準相比〉那節的 2-3 句重點摘要。

## 不要做的事

- 不要改 `_proofread/` 或既有的 `docs/文風回饋-*.md` 基準檔(它們是歷史快照,要留著比對)。
- 不要潤稿、不要把回饋寫成「糾錯清單」——成長空間是關於思考與表達,不是把話講通順。
- 不要設 cron / 排程 / 提醒。永遠等使用者手動下 `/style-review`。
