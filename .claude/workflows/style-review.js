export const meta = {
  name: 'style-review',
  description: '分析尚承全部語音隨筆的風格、口條,給出該保留與可改進的回饋,並與上一份基準相比',
  whenToUse: '想定期回顧自己的文風/口條演化時,由 /style-review 手動觸發(不排程)',
  phases: [
    { title: 'Discover', detail: '列出全部 _proofread/*.md,找出上一份基準' },
    { title: 'Read', detail: '分批精讀全部校對稿,抽取口條/風格/主題觀察(含逐字引文)' },
    { title: 'Analyze', detail: '五個維度各自跨全 corpus 深度分析' },
    { title: 'Synthesize', detail: '整合成回饋 + 與上次相比 + 對抗式複核修訂' },
  ],
}

// ---- 共用素材 ----
const ARTIFACT_NOTE = `重要校準:這些是「校對稿」,有一些是轉錄流程的標記,不是使用者的文體,分析時要區分:
- \`<sup>字?</sup>\` 和 \`[?]\` 是「轉錄不確定」標記,不是使用者風格,忽略它們當風格訊號。
- \`……\` 是「真實停頓/情緒卡住」,這是風格訊號,要計入(代表他講話的呼吸與遲疑)。
- \`[註:...]\` 是使用者事後手寫的 meta-note,代表他的後設思考習慣,可以當風格訊號但要標明這是書面補註不是口語。
- 文末 \`---\` 之後的斜體是校對者的提示,不是使用者的話,忽略。
逐字引文必須一字不差照抄原文,嚴禁改寫、潤飾或杜撰。`

const READER_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['date_range', 'files', 'delivery', 'style_strengths', 'style_tics_or_limits', 'recurring_themes', 'emotional_register', 'intellectual_references', 'standout_quotes', 'within_batch_trajectory'],
  properties: {
    date_range: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    delivery: {
      type: 'object', additionalProperties: false,
      required: ['filler_words', 'sentence_structure', 'repetition_patterns', 'self_correction', 'pacing_pauses', 'notable_verbal_tics'],
      properties: {
        filler_words: { type: 'array', items: { type: 'string' } },
        sentence_structure: { type: 'string' },
        repetition_patterns: { type: 'string' },
        self_correction: { type: 'string' },
        pacing_pauses: { type: 'string' },
        notable_verbal_tics: { type: 'array', items: { type: 'string' } },
      },
    },
    style_strengths: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['observation', 'quote'], properties: { observation: { type: 'string' }, quote: { type: 'string' } } } },
    style_tics_or_limits: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['observation', 'quote'], properties: { observation: { type: 'string' }, quote: { type: 'string' } } } },
    recurring_themes: { type: 'array', items: { type: 'string' } },
    emotional_register: { type: 'string' },
    intellectual_references: { type: 'array', items: { type: 'string' } },
    standout_quotes: { type: 'array', items: { type: 'string' } },
    within_batch_trajectory: { type: 'string' },
  },
}

const COMPARE_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['redundancy_signal', 'what_polish_strips', 'what_survives', 'examples'],
  properties: {
    redundancy_signal: { type: 'string' },
    what_polish_strips: { type: 'string' },
    what_survives: { type: 'string' },
    examples: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['proofread', 'polished'], properties: { proofread: { type: 'string' }, polished: { type: 'string' } } } },
  },
}

const DISCOVER_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['proofread_paths', 'prev_baseline_path', 'prev_baseline_main'],
  properties: {
    proofread_paths: { type: 'array', items: { type: 'string' }, description: '全部 _proofread/*.md 的絕對路徑,依檔名(日期)排序' },
    prev_baseline_path: { type: ['string', 'null'], description: '最近一份既有基準檔(docs/文風回饋-*.md)的絕對路徑;若無則 null' },
    prev_baseline_main: { type: 'string', description: '上一份基準「# 附錄」之前的正文(沒有則空字串)' },
  },
}

function chunkContiguous(arr, size) {
  const out = []
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size))
  if (out.length > 1 && out[out.length - 1].length <= 2) {
    const tail = out.pop()
    out[out.length - 1] = out[out.length - 1].concat(tail)
  }
  return out
}

// ---- Discover ----
phase('Discover')
const disc = await agent(
  `你在 voice-to-note 專案根目錄。請用 Bash 完成兩件事並回傳結構化結果:
1. 列出全部校對稿的絕對路徑:執行 \`ls -d "$PWD"/_proofread/*.md\`,依檔名排序,放進 proofread_paths。
2. 找最近一份既有文風基準:執行 \`ls -t "$PWD"/docs/文風回饋-*.md 2>/dev/null | head -1\`。
   - 若有,把它的絕對路徑放進 prev_baseline_path,並用 Read 讀它,把「# 附錄」之前的正文放進 prev_baseline_main。
   - 若沒有(首次跑),prev_baseline_path 設 null,prev_baseline_main 設空字串。`,
  { label: 'discover', phase: 'Discover', schema: DISCOVER_SCHEMA }
)

const paths = disc.proofread_paths
const batches = chunkContiguous(paths, 6)

// 動態挑早/中/晚三篇做 proofread vs polished 對照
const pick = (frac) => paths[Math.min(paths.length - 1, Math.max(0, Math.round((paths.length - 1) * frac)))]
const tripletProof = [pick(0.12), pick(0.5), pick(0.88)]
const tripletBase = tripletProof.map((p) => p.split('/').pop())

// ---- Read ----
phase('Read')
const readerThunks = batches.map((batch, i) => () =>
  agent(
    `你是一位中文母語、熟悉台灣口語與當代華語散文/私散文的文體分析師。請完整精讀以下 ${batch.length} 篇語音隨筆校對稿(用 Read 工具逐一讀完整檔,絕對路徑如下),分析作者「尚承」的口條與文風。

${batch.map((p) => '- ' + p).join('\n')}

${ARTIFACT_NOTE}

分析重點:
1. 口條(delivery):語助詞與口頭禪、句子結構、重複模式、邊講邊修正的方式、節奏與停頓、招牌句式。要具體、要附逐字例。
2. 文風強項:他獨特、動人、有洞察力或有質感的地方,各附一字不差的逐字引文。
3. 慣性/侷限(成長空間,不是缺點):反覆出現的句式依賴、思路打轉、抽象懸空、結論迴避等,各附逐字例。
4. 反覆主題、情緒底色、提到的書/人/概念。
5. 挑出這批裡最有質感、最像他、最動人的 4-7 句(逐字照抄)。
6. 這批時間先後內部有無變化。

全部用繁體中文(台灣用語)。引文一律逐字照抄,不可改寫或杜撰。`,
    { label: `read:batch${i + 1}`, phase: 'Read', schema: READER_SCHEMA }
  )
)
const readersPromise = parallel(readerThunks)

const comparePromise = agent(
  `你要比較同一篇隨筆的「校對稿」(_proofread/,保留口語停頓重複)與「可讀版」(_polished/,刪 stutter 不潤稿)的差異,藉此量化作者口語的「訊號/雜訊比」。請用 Bash/Read,在專案根目錄處理早/中/晚三組對照(檔名如下;若某篇在 _polished/ 沒有對應檔,改挑日期最接近、且 _polished/ 有的那一篇):

早期:_proofread/${tripletBase[0]}  對  _polished/${tripletBase[0]}
中期:_proofread/${tripletBase[1]}  對  _polished/${tripletBase[1]}
後期:_proofread/${tripletBase[2]}  對  _polished/${tripletBase[2]}

觀察:polish 主要砍掉哪些東西?砍完留下的核心密度如何?贅字/重複大約佔多少比例?附 2-3 組逐字對照(proofread 原句 → polished 後)。全部用繁體中文。`,
  { label: 'read:proofread-vs-polished', phase: 'Read', schema: COMPARE_SCHEMA }
)

const readerResults = (await readersPromise).filter(Boolean)
const compareResult = await comparePromise

const allQuotes = readerResults.flatMap((r) => (r && r.standout_quotes) || [])
const corpusJSON = JSON.stringify(readerResults)
const compareJSON = JSON.stringify(compareResult)

// ---- Analyze ----
phase('Analyze')
const DIMENSIONS = [
  { key: 'delivery', title: '口條與語言質地', prompt: '你是文體分析師。請只聚焦「口條/delivery」這個維度,跨全部校對稿做一份深入分析:語助詞與口頭禪的依賴、句子結構(繞句/長句/主謂斷裂)、重複與疊字、邊講邊修正的招牌句式、節奏與停頓。指出哪些是有魅力的口語質地(該保留),哪些是會稀釋表達力的填充慣性(可練習)。大量引用結構化觀察裡的逐字例。' },
  { key: 'voice', title: '聲音與文風(他之所以是他)', prompt: '你是文體分析師。請聚焦「獨特的聲音/文風」:他的比喻與意象、他凝視內在的方式、誠實與脆弱的程度、有沒有幽默、語言的觸感。指出最像他、別人模仿不來的特質。大量引用逐字例。' },
  { key: 'thought', title: '思考結構與「暗流」質感', prompt: '你是文體分析師。請聚焦「思考如何被建構」:他怎麼從一個事件滑進一個念頭、會不會繞回來、結論是收束還是懸置、岔題與聯想的方式、那種「暗流隨筆」的潛流感是怎麼形成的。指出這套思考方式的強項與盲點。引用逐字例。' },
  { key: 'themes', title: '主題地景與情緒軌跡', prompt: '你是文體分析師兼敏感的讀者。請聚焦「主題與情緒」:反覆出現的執念、情緒的範圍與起伏、心理弧線。指出哪些主題在深化、哪些在原地打轉。引用逐字例。' },
  { key: 'evolution', title: '這段時間的演化軌跡', prompt: '你是文體分析師。請聚焦「時間上的變化」:比較最早、中段、最近的稿子,他的口條、自我覺察、句子掌控度、主題處理有沒有變化?是在成長、收斂、還是穩定重複?用各批的 date_range 與 within_batch_trajectory 拼出趨勢。引用逐字例佐證變化。' },
]
const dimResults = await parallel(
  DIMENSIONS.map((d) => () =>
    agent(
      `${d.prompt}

以下是各批讀者對全部校對稿的結構化觀察(JSON,含逐字引文);另附一份 proofread vs polished 的對照分析。請以此為唯一證據基礎,寫一節有深度、具體、引文充足的繁體中文分析(約 500-900 字)。每個論點都要有逐字引文撐住。不要寫「我現在開始分析」這類開場白,直接給內容。

=== 讀者觀察(JSON) ===
${corpusJSON}

=== proofread vs polished 對照 ===
${compareJSON}`,
      { label: `analyze:${d.key}`, phase: 'Analyze', effort: 'high' }
    )
  )
)
const dimText = DIMENSIONS.map((d, i) => `## ${d.title}\n\n${dimResults[i]}`).join('\n\n')
const quotesBlock = allQuotes.map((q) => '- ' + q).join('\n')

// ---- Synthesize ----
phase('Synthesize')
const baselineBlock = disc.prev_baseline_main && disc.prev_baseline_main.trim()
  ? `\n\n另外,以下是「上一份基準」的正文。請在回饋中加入一節 \`## 七、與上一份基準相比的變化\`,具體指出:口條、主題、成長空間這幾條上,這次跟上次相比有什麼移動(更收斂了?某個慣性改善了?新冒出什麼?),用這次的逐字引文佐證。\n\n=== 上一份基準正文 ===\n${disc.prev_baseline_main}`
  : `\n\n(這是首份基準,沒有前次可比;不要寫「與上一份相比」那一節。)`

const draft = await agent(
  `你要為「尚承」寫一份關於他語音隨筆的整體回饋。他是寫作者,風格屬「暗流隨筆」,核心倫理是「不潤稿」——口語的重複、停頓、繞,都是素材,不是要被修掉的瑕疵。所以「改進」絕對不是叫他講得更通順、更正確;而是關於「思考與表達的成長」。

請用溫暖、誠實、具體、不諂媚的語氣(像一個真的讀完他全部稿子、很懂他的人),寫一份繁體中文回饋,結構:
1. **整體畫像**(三四句精準描出他的聲音)
2. **口條觀察**(質地、節奏、招牌句式;哪些是魅力,哪些是慣性)
3. **什麼該繼續保留**(用他自己的逐字句子當證據)
4. **什麼可以嘗試(成長空間)**(3-5 點,具體、可操作、尊重「不潤稿」倫理,每點說清楚「為什麼這對你有意義」)
5. **這段時間的軌跡**
6. 結尾用他自己最好的一兩句話收束
${baselineBlock}

證據材料:

=== 五維度分析 ===
${dimText}

=== 精選逐字引文 ===
${quotesBlock}`,
  { label: 'synth:draft', phase: 'Synthesize', effort: 'high' }
)

const critique = await agent(
  `你是嚴格的審稿人。下面是要給寫作者尚承的文風回饋草稿。請挑毛病:哪些論點空泛沒有逐字引文撐住?有沒有把轉錄標記/口語特性誤當風格問題?「可以嘗試」那節有沒有違反「不潤稿」倫理(變成叫他講更通順/更正確)?有沒有諂媚或太苛刻?引文有沒有像被竄改?給具體可執行的修改清單。繁體中文。

=== 草稿 ===
${draft}

=== 可用證據(五維度) ===
${dimText}`,
  { label: 'synth:critic', phase: 'Synthesize', effort: 'high' }
)

const final = await agent(
  `根據審稿意見修訂這份給尚承的文風回饋。保留溫暖誠實具體的語氣,補上缺的逐字引文,刪掉空泛論點,確保「可以嘗試」那節完全尊重「不潤稿」倫理。輸出最終版,繁體中文,Markdown,可直接給使用者看。不要加「以下是修訂版」這類開場白,直接給內容。

=== 原草稿 ===
${draft}

=== 審稿意見 ===
${critique}

=== 精選逐字引文(可再取用) ===
${quotesBlock}`,
  { label: 'synth:final', phase: 'Synthesize', effort: 'high' }
)

return {
  final,
  dimensions: dimText,
  quotes: allQuotes,
  compare: compareResult,
  prev_baseline_path: disc.prev_baseline_path,
  corpus_count: paths.length,
  date_range: `${(paths[0] || '').split('/').pop().replace('.md', '')} ~ ${(paths[paths.length - 1] || '').split('/').pop().replace('.md', '')}`,
}
