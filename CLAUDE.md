# always-stock 專案記憶

## 正式推薦卡片 4 項改動 + 發現 P4 「legacy 基線不完整」永久卡在 CAUTION 的落差（2026-08-10 第五輪）

### UI 改動（`recommendations/page.tsx`）
1. `TrackingSummary` 卡片內第一行改成「首次抓到 {first_seen_date}（第 N 個交易日）」，
   不用點進 archive 才看得到是幾號抓到的
2. 新增排序 chip：推薦排序（預設，沿用 `recommendation_rank`）／抓到日期（近到遠，用
   `archiveByStock.get(stock)?.first_seen_date`）／報酬率（高到低，`return_pct` 為 null
   的排最後，不讓 null 混進數字比較）
3. `COMMON_STOCK`（一般股）不再顯示 `SignalAssetBadge`；`FINANCIAL`／`ETF` 維持顯示——
   只在這個頁面的呼叫端加條件（`item.asset_type !== "COMMON_STOCK"`），**沒有動
   `SignalAssetBadge.tsx` 共用元件本身**，因為它同時被 L0 `DailySignalsPanel`／
   observations／outcomes／`SignalNotSelectedSection`／`SignalRemovedSection` 共用，
   改共用元件會波及沒被要求的頁面
4. `TrackingSummary` 從只在正式版顯示，改成正式版／工程版都顯示；工程版額外多顯示
   `P4 Episode {uuid}・首次推薦 {date}` 那行（兩者疊加，不是二選一）

### 查證使用者發現的疑問：2618／6533 追蹤超過 11 個交易日為何還在推薦榜
**P3「今日正式推薦」跟 P4「觀察生命週期」是兩套完全獨立的機制，沒有交集**：
- P3（Global Selector）**每天從零重新評估**候選池，不管一檔股票已經被推薦幾天，只要
  今天的一次性全體比較它還是贏，就會再被選一次——archive 的 `tracking_day_index`
  （21／11 個交易日）只是「這一輪追蹤窗口存在多久」，`hit_count`（4／9）才是「這期間
  真的被 P3 選中幾次」，兩者本來就不必相等。**沒有「追蹤超過 N 天就該離開推薦榜」這條
  規則**——這是設計上刻意的：P3 每天都是全新判斷，不是「一旦上榜就沿用舊決定」
- P4（`SignalObservation`）才有「離開」概念（`OBSERVING/CAUTION → STOPPED`），但實際
  查證 `decide_observation_action()`（`backend/app/signals/observation_lifecycle.py:791`）
  發現使用者原本假設的「警戒 3 次就移出」**不準確**：真正觸發 STOP 的唯一「持續警戒」
  路徑是 `prior_decision == CAUTION` 且**同一組核心維度（MOMENTUM_STRUCTURE／
  PARTICIPATION 相關）連續兩次 review 都失效**（line 893-913），**且這條路徑被一個前置
  條件整個擋住**：`not baseline_incomplete`——只要 `baseline_quality == "LEGACY_INCOMPLETE"`
  （P4 系統上線前就存在、缺完整基線資料的舊觀察），這個 STOP 判斷**完全不會執行**
- 實測 2618／6533 兩檔的 `baseline_quality` 都是 `LEGACY_INCOMPLETE`，`consecutive_
  caution_count` 都已經連續 13 次（curl 直接查 production `/api/signals/observations`
  證實），卻因為上述前置條件被跳過而完全無法透過「持續警戒」路徑 STOP——只剩 hard
  exclusion／TRACKING_INVALIDATED／外部 THESIS_INVALIDATED 三條路徑能讓它們離開，這三
  條都沒被目前的警戒原因觸發，所以會**無限期停在 CAUTION**
- `STOP_CONFIRM_THRESHOLD=3`／`stop_confirm_count` 這組欄位**不是**「警戒 3 次觸發
  STOP」——是反過來：**已經 STOP 之後**，還要連續 3 天重新 review 確認沒有恢復，才會
  真正從每日 review 清單移除（`run_daily_observation_reviews` line 969-978 的查詢條件），
  是「確認停止」不是「觸發停止」。上一輪（第三輪 CLAUDE.md 條目）的研究摘要把這兩個機制
  混在一起講成「連續 3 天警戒觸發 STOP」，**是不準確的簡化，這次已釐清**
- **這是一個真實的系統設計落差**（非本輪職權範圍內修改，已回報給使用者定奪）：
  `LEGACY_INCOMPLETE` 基線的觀察會被「持續警戒」機制永久豁免，導致這類舊觀察即使警戒
  訊號一直在累積也無法自然 STOP，只能等外部條件觸發

### 「警戒後 STOP 是否該記錄進負報酬」的既有機制
`SignalObservationArchive`（`models.py:464-504`）在 STOPPED 時**已經**會記
`entry_price`/`exit_price`/`return_pct`（由 `_settle_pending_archive_exits` 晚一天補
上），並回饋進 outcomes 頁「既有觀察的停止品質」區的 `stop_before_big_loss_rate`
（真正大跌前有沒有提早 STOP）與 `premature_stop_candidate_count`（STOP 後又漲回來，
疑似停太早）兩個彙總指標——**但目前只有彙總比例，沒有逐檔清單**可以直接看到「這幾檔是
因為警戒 STOP、事後虧了多少」。算是部分做到，未完全滿足使用者想要的逐檔可見度，這輪
沒有動這塊（屬於 outcomes 頁 P4 停止品質區塊的延伸功能，需要另外規劃）。

## 結果分析頁項導番（drill-down）+ 修復路由重構遺留的測試 regression（2026-08-10 第四輪）

### 背景
使用者接著問：摘要數字（如「大跌比例／大漲抓取率 50.0%／0.0%」）跟圖表上的「中性 8
檔」「大跌 8 檔」，要怎麼知道具體是哪幾檔？沒有地方可以點。順便發現目前顯示的資料是
哪一天的區間也沒有標示清楚。

### 改動
- **日期區間移到頁面顯眼位置**：原本只藏在「趨勢圖表」子標題裡，現在在
  `{summary && (...)}` 區塊最上方加一個永久橫幅：「目前顯示區間：2026-07-20 ～
  2026-08-09（可用上方「開始日期」「結束日期」調整）」
- **項導番機制**（`goToDetail` helper，`outcomes/page.tsx`）：把既有「逐筆明細」表格
  （本來就有 `outcome_label`／`p3_decision` 篩選＋分頁）當成唯一的「哪幾檔」答案來源，
  任何摘要數字/圖表/表格列被點擊時，統一呼叫 `goToDetail({ outcomeLabel?, decision? })`
  → 設篩選 + `requestAnimationFrame` 後 `scrollIntoView`。**沒有新增任何 API 或資料抓
  取**，純粹是「幫使用者把篩選條件填好、捲到看得到的地方」
- 套用位置：「大跌比例／大漲抓取率」卡片下方加「查看大跌名單 →」／「查看大漲名單 →」；
  `OutcomeDistributionChart`（`components/OutcomeCharts.tsx`）新增可選的 `onSelect`
  prop，用 `echarts-for-react` 的 `onEvents={{ click }}` 把長條圖三個分類變成可點擊；
  Backend Rank 分布表三個列標籤（被 AI 正式推薦／被 AI 排除／最後大漲達標）改成按鈕；
  既有「查看股票明細」按鈕（NOT_SELECTED 後成為 Winner 區）也統一走同一個 helper
- 「逐筆明細」表格 header 加「目前篩選：XXX・清除篩選」提示，讓使用者知道自己現在看到
  的是被篩過的子集，不是全部
- **Backend Rank 表格cell（4×3 矩陣）本身沒有做到逐格點擊**：現有 API 篩選參數只有
  `outcome_label`／`p3_decision`，沒有 rank 區間參數，逐格點擊需要後端加新篩選欄位，
  這輪先只做到列級（整類）點擊，是刻意的範圍取捨

### 意外抓到的 regression（不是這輪引入，是兩輪前搬檔案時漏掉的）
第一輪（正式版／工程版 toggle）把 5 個頁面搬進 `(product)/` route group 時，只跑了
`tsc --noEmit`（用 `grep -v "__tests__"` 過濾掉雜訊）跟 `eslint`，**從未跑過
`npm test`**——`src/__tests__/components/Signal{Outcomes,Observations,Recommendations}Page.test.tsx`
三個測試檔案的 `import ... from "@/app/signals/xxx/page"` 全部還指向搬移前的舊路徑，
測試套件直接 `Could not locate module` 掛掉；額外因為這幾頁後來加了 `useSignalsViewMode()`
（P3/P4 兩頁）跟 `fetchSignalArchive()`（P3 頁），測試也需要對應補 mock。**教訓**：
`tsc --noEmit` 不會檢查測試檔案本身有沒有跟著搬移（尤其是這個專案的既有慣例會把
`__tests__` 的 tsc 錯誤直接 filter 掉，很容易把真的 regression 跟已知雜訊一起濾掉）；
移動/重構任何被測試 import 的檔案後，**必須額外跑一次 `npx jest` 全套件**，不能只靠
tsc/eslint。修法：
- 三個測試檔案的 import path 改成 `@/app/signals/(product)/xxx/page`
- Observations／Recommendations 兩個測試因為頁面組件內部呼叫 `useSignalsViewMode()`
  需要 Provider；這兩份測試的意圖本來就是在驗證「工程稽核內容」（TRACKING_SELECTION_
  CONFLICT、Episode 歷史、NOT_SELECTED/REMOVE 分區），所以用
  `jest.mock("@/lib/signalsViewMode", () => ({ useSignalsViewMode: () => ({ isEngineering:
  true, ... }) }))` 直接鎖定工程版，不測 toggle 機制本身（避免依賴真實 Provider 的
  localStorage 讀取在 jsdom 裡的行為）
- Recommendations 測試補上 `fetchSignalArchive` mock（回傳空 `items: []`），否則會呼叫
  真實的 `apiFetch` 在 jsdom 無網路環境掛掉
- `OUTCOME_LABELS.NEUTRAL` 文字從 `"中性結果"` 改 `"持平"` 那次真的是新規在，
  `SignalP6Components.test.tsx` 的斷言也同步更新

### Gotcha
- 這個環境的 Bash tool cwd 不穩定跨 tool call（第三輪就記過一次），這輪又發生兩次：
  下指令前務必 `pwd` 或每次都明確 `cd frontend/`
- `npx eslint "src/app/signals/(product)/xxx.tsx"` 這種帶括號的具體路徑會被 eslint 的
  glob 引擎當成找不到檔案（跟 bash 的 quoting 無關，是 eslint 自己的 minimatch 行為）；
  改用萬用字元 `"src/app/signals/**/*.tsx"` 才吃得到

## 結果分析頁全中文化 + 白話說明（2026-08-10 第三輪）

同一天第三輪：使用者發現 archive 卡片顯示某檔報酬率 20%+，但 outcomes 頁卻沒算它是
Winner，追問原因；同時要求 outcomes 頁「全中文＋每個 section 附白話說明與例子」。

**根因（不是 bug，是語意落差）**：`backend/app/signals/outcome_metrics.py:57-64`
`classify_day10_return()` 的 WINNER 判定是 `return_pct >= 10.0`（BIG_LOSER `<= -10.0`），
但這個 `return_pct` 是 `(exit_price - entry_price) / entry_price`，`exit_price` 固定抓
**「推薦後第 10 個交易日收盤」單一時間點**（`EXIT_PRICE_DEFINITION`，line 33-34），**不是
最高點、也不是即時報酬率**。archive 卡片顯示的 `return_pct` 是即時/當下算的（每天更新），
兩個是完全不同的數字來源。所以「archive 顯示 20%+」跟「outcomes 沒算 Winner」不衝突：
可能是還沒滿 10 個交易日（`IMMATURE`，`_nth_subsequent_trade_date` 找不到對應交易日就是
這個狀態）、或是滿 10 天那天股價已經拉回到 +10% 以下（那天的快照就是最終定論，之後即使
股價又漲回去也不會回頭改判定）。已把這條說明寫進頁面 header（永久顯示，不是一次性回答）。

**Rank Override（越級推薦）精確定義**（`global_selector.py:666-721`）：**不是**比較
`recommendation_rank` vs `backend_priority_rank`；而是「這檔被 RECOMMEND，但同一天有一檔
`backend_priority_rank` 數字更小（排序更前面）的股票卻被 NOT_SELECTED」——觸發條件是跟
「當天被排除股票裡最好的排序」比較，不是跟自己的排序比較。

**Backend Rank 分布表**（`outcome_metrics.py:1011-1040`）：「winner」那列是**跨決策**
統計——不管當天是 RECOMMEND 還是 NOT_SELECTED，只要最後結果是 WINNER 就算進對應排序區間，
是獨立於 recommend/not_selected 兩列的第三個聚合，不是前兩列的子集。

**改動**：`frontend/src/lib/signalP6Presentation.ts` 的 `OUTCOME_LABELS`／
`REVIEW_CATEGORY_LABELS` 拔掉殘留英文（`"Winner／正向結果"` → `"大漲達標"` 等）；
`outcomes/page.tsx` 全部下拉選單／表格欄位翻中文，每個 section 加 `SectionExplainer`
（白話說明＋具體數字例子，用實際門檻/公式寫例句，不是空泛形容詞）。純內容改動，沒有動
任何資料流或計算邏輯。

**Gotcha**：這環境的 Bash tool 有時候 `cd` 不會跨 tool call 持續生效（同一輪對話裡遇到
兩次 `pwd` 回到非預期目錄），每次要跑 `npx eslint`/`npx tsc` 前最好先 `pwd` 確認或每次都
明確 `cd` 到 `frontend/`，不要假設前一個 Bash call 設的 cwd 還在。

> **完整選股邏輯 / hard exclusion / pipeline step-by-step 說明**：
> [docs/plans/魚尾選股邏輯與排除規則說明.md](docs/plans/魚尾選股邏輯與排除規則說明.md)
> （2026-07-22，含 legacy + Phase 2 兩條路徑完整對照，給要查「某天某檔股票被剔除在哪一關」的人）

## 正式推薦借用魚尾 archive 資料顯示報酬率 + 結果分析收進工程版（2026-08-10 第二輪）

### 背景
- 上一輪加了正式版／工程版 toggle 後，使用者發現 `/signals/recommendations` 每張卡片顯示
  「追蹤中：自 X 起持續觀察」不直覺，希望比照 `/signals/archive`（魚尾 30 日追蹤頁）顯示
  報酬率與預期價格
- 使用者也回饋：連上一輪已簡化過的「結果分析」頁（10 日後達標率／成熟樣本數／圖表）都看
  不懂在講什麼（不知道日期區間、不知道哪些股票是中性或大幅負報酬）——確認這頁對他沒價值，
  應收進工程版

### 查證：P3-P7（v7 pipeline）跟 M23 舊系統的資料關係
- **P3 RECOMMEND 清單本來就有完整報酬率資料，純前端就能接**：`persist_signal_watch_hits`
  （`backend/app/signals/archive.py:128`）在同一次 `run_signal_pipeline_sync` 裡把 P3 的
  `final_payload["watchlist"]` 寫進 `signal_watch_hits`，所以 archive 頁的
  `return_pct`／`max_positive_return_pct`／`latest_close_price`／`daily_change_pct`／
  `tracking_day_index`／`hit_count`（`SignalArchiveSummaryItem`，`frontend/src/lib/api.ts:1603`）
  對 P3 股票本來就存在，**不需要後端新工作**
- **`signal_expectation_prices`（保守價／夢想價，M26）也已經對 P3 股票生效**：
  `generate_for_new_signals()`（`backend/app/signals/expectation_price.py:982`）純粹以
  `SignalWatchHit.snapshot_date == today` 找候選，不分是哪個 pipeline 寫入
- **P4（`SignalObservation`）自己完全沒有 return_pct／price 欄位**，P5/P6 的 `day10_return`
  是滿 10 個交易日後的一次性快照、不是即時報酬率——這正是為什麼直接借用 archive 頁既有的
  `signal_watch_hits` 資料，而不是在 P4 系統裡重造一套即時報酬追蹤
- **「新／舊選股」用既有 `prompt_version` 欄位判斷即可，不需要新增 DB 欄位**：v7 pipeline
  上線後（commit `86a159b`，2026-07-29）寫入的 `prompt_version` 一律是 `"v7_..."` 開頭；
  此前／手動 replay 才會是裸 `v1/v2/v4/v5/v6/v6.1`。純字串前綴判斷：任一版本 token 以
  `"v7"` 開頭 → 新選股

### 改動
- **結果分析頁完全收進工程版**：`(product)/page.tsx` 的「結果分析」nav card 加
  `engineeringOnly: true`；`SignalProductNav.tsx` 的 `ENGINEERING_ONLY_HREFS` 加
  `/signals/outcomes`；`outcomes/page.tsx` 拿掉上一輪加的所有 `isEngineering` 條件
  render，還原成單一份完整內容（比照 Debug 頁「內容不分版本，只是 nav 不連過去」）——避免
  維護一個正式版永遠不會被看到、且已證實對使用者無意義的簡化版本。總覽頁的「10 日後達標
  率」統計卡也一併收進工程版（4 顆變 3 顆）
- **正式推薦卡片改魚尾風格**（`recommendations/page.tsx`）：額外呼叫
  `fetchSignalArchive()`（預設 `limit=0` 已不限筆數）建 `Map<stock_id, SignalArchiveSummaryItem>`；
  正式版卡片拿掉「追蹤中：自 X 起」，改用新元件 `TrackingSummary`（收盤價＋當日漲跌幅、
  報酬率＋已追蹤 N 個交易日、保守／夢想價；archive 查無資料時顯示「今日新入選，尚無追蹤
  數據」）；「查看完整分析」dialog 內加一行「查看完整追蹤紀錄 →」連到
  `/signals/archive?q={stock}`。工程版不動
- **觀察生命週期頁 STOPPED 加追蹤紀錄連結**（`observations/page.tsx`）：detail 面板對
  `status === "STOPPED"` 的項目加「查看完整追蹤紀錄 →」連到 `/signals/archive?q={stock}`；
  **只加在 detail 面板，不加在清單卡片**——清單卡片整張是 `<button>`，`<a>` 巢狀在
  `<button>` 裡是無效 HTML（同 2026-07-16 K 線圖 popup 那次踩過的坑）
- **archive 頁加 `?q=` deep-link + 新／舊選股 flag**：`activeSearch`／`completedSearch`
  初始值改讀 `searchParams.get("q") ?? ""`（單純 mount 時讀一次當初始值，不雙向同步回
  URL，沿用本頁搜尋框刻意不進 URL 的既有決定）；新增 `PipelineFlagChip`（緊鄰
  `VersionChip` 放，不改 `VersionChip` 本身）用同一個 `prompt_version` 字串判斷新舊選股，
  套用在 active 卡片 detail dialog 與 completed 卡片兩處（這是全檔案僅有的兩處
  `VersionChip` 用量）

### Gotcha
- `git add` 用多個 pathspec 一次下指令時，若其中一個路徑打錯（`fatal: pathspec ... did
  not match any files`），**整條指令會直接中斷、完全不 staged 任何檔案**（不是「跳過壞的
  路徑、其餘照常」）——上一輪（正式版／工程版 toggle 那次）因此第一次 commit 漏掉
  CLAUDE.md／prompt／SignalProductNav.tsx 三個檔案，而且 5 個搬移的頁面只有 rename 被
  記錄、內容編輯反而遺漏，靠 `git status --short` 的 `RM`／` M` 前綴仔細分辨才抓到（`RM`
  = 已 staged 的 rename + 額外 unstaged 修改，不是「已完全 staged」）。commit 前務必
  `git diff --cached --stat` 確認變動量級合理，而不是只看 `git status` 有沒有列出檔名
- `useSyncExternalStore` 取代 `useEffect`+`setState` 讀 localStorage 這個 pattern（見上
  一輪 CLAUDE.md 條目）在這輪繼續沿用，本輪沒有新增類似需求
- 本機驗證受工具限制：`chromium-cli`／Playwright 在此環境都不可用，只能用 `curl` 確認
  路由 200／dev server log 無編譯錯誤（整站包在 client-side `<SiteGate>` 密碼閘門後，
  未解鎖前 HTML 永遠只有「載入中…」殼層，RSC payload 裡的函式名也是編譯後的 chunk 參照，
  grep 不到真正的字串內容）；`tsc --noEmit` + `eslint` 全綠

## /signals/* 產品頁：正式版／工程版 toggle（2026-08-10）

### 背景
- `/signals/*`（總覽／正式推薦／觀察生命週期／結果分析／Debug，P3-P7 v7 pipeline 的前端）
  混雜大量工程診斷資訊（raw UUID、JSON dump、prompt/selection/score 版本字串、Funnel、
  NOT_SELECTED/REMOVE 稽核清單），使用者反映「看不懂在幹嘛」，只想看「今天推薦了什麼、
  為什麼」。需求：加一個正式版／工程版 toggle（預設正式版），正式版只留真正會用到的內容、
  全部中文；工程版維持現狀。同時修掉推薦理由裡混雜英文 enum 的問題、隱藏 Debug 入口。

### 架構：view-mode toggle
- 新模組 [frontend/src/lib/signalsViewMode.tsx](frontend/src/lib/signalsViewMode.tsx)：
  `SignalsViewModeProvider` + `useSignalsViewMode()`，state 存 localStorage
  `always-stock:signals:view-mode`（預設 `"production"`）
  - **用 `useSyncExternalStore` 而非 `useEffect`+`setState`**：後者會撞上
    `eslint-plugin-react-hooks` 的 `react-hooks/set-state-in-effect` 規則（同樣是「mount
    後讀 localStorage 覆寫預設值」的模式，`archive/page.tsx` 的 `completedCollapsed` 舊
    寫法沒被這條規則抓到，但新寫的 code 會被抓——不確定是規則的啟發式判斷差異還是版本
    差異，總之新規劃一律改用 `useSyncExternalStore`：`getServerSnapshot` 固定回
    `"production"`（SSR 安全）、`getSnapshot` 讀 localStorage、寫入時透過極簡 module-level
    pub-sub（`Set<() => void>`）通知同分頁內的其他 subscriber——原生 `storage` 事件只在
    **其他分頁**才會觸發，同分頁呼叫 `setItem` 不會自動通知自己，這是這個模式最容易忽略
    的坑
- 新增 [frontend/src/app/signals/(product)/layout.tsx](frontend/src/app/signals/(product)/layout.tsx)：
  套 `SignalsViewModeProvider` + 統一 render 一次 `SignalProductNav`
  - **用 `(product)` route group 隔離**：Next.js layout 會套用到整個子樹，若直接加在
    `signals/layout.tsx` 會連 `archive/`、`phase2/` 兩個獨立風格頁面也一起被包住（多一條
    不該出現的 nav bar）。用 route group 把總覽/正式推薦/觀察生命週期/結果分析/Debug 這
    5 個頁面移進 `(product)/` 資料夾，`archive`、`phase2` 留在外面當 sibling，URL 完全
    不變（route group 資料夾名不進 URL）
- Toggle 按鈕做在 [SignalProductNav.tsx](frontend/src/components/SignalProductNav.tsx)：
  nav 列右側切換鈕；`isEngineering` 時 nav 才顯示「Debug」連結（正式版隱藏入口，但
  `/signals/debug` 本身不做存取限制，直接輸入網址仍可進）

### 各頁面改動模式
- 所有「正式版隱藏、工程版顯示」統一用 `{isEngineering && (...)}` 條件 render 包住，不拆
  兩份 component
- **正式推薦頁**：Funnel／未列入今日推薦／明確移除／技術失敗四區全部移到工程版；正式版
  每張卡片拿掉 `Theme {cluster}`／`Backend Rank {n}` 工程標籤、拿掉 raw `P4 Episode {uuid}`
  （改顯示「追蹤中：自 {date} 起持續觀察」）、拿掉版本 footer 行；新增
  `RecommendationDialog`（`@base-ui/react/dialog`，仿 `StockChartDialog` 樣式）取代原本
  `<details>` accordion，同時修掉「thesis/relative_advantage 在卡片本體與 details 各印一
  次」的重複顯示
- **觀察生命週期頁**：隱藏 Asset Type／Episode ID 篩選、`EvidenceBlock` raw JSON dump ×2、
  Review Timeline 版本行；`stop_reason_code`／`episode.status` 等 enum 值一律經既有翻譯
  字典（`OBSERVATION_STATUS_LABELS`／`observationStatusLabel()`）轉中文再顯示
- **結果分析頁**：大幅精簡，正式版只留日期區間篩選 + 2 張圖表 + 2 顆核心卡片（10 日後
  達標率／成熟樣本數）；版本篩選 5 個下拉、CSV 匯出、Backend Rank 分布表、
  NOT_SELECTED→Winner 區塊、Outcome 明細表、人工檢查清單、raw JSON「版本與定義」全部移
  到工程版

### Prompt 修正：英文 enum 洩漏進中文推薦理由
- 根因：[recommendation-reason-v7.md](backend/app/prompts/recommendation-reason-v7.md)
  原本逐條要求 LLM 在中文 bullet 裡「引用」`Role／Tracking State`、`Backend Rank`、
  `Entry State`、`Technical Status` 等**內部欄位名稱**，LLM 因此把
  `ACTIVE_TREND`／`backend_priority_rank`／`sector_rotation_status` 這類 enum/欄位名原樣
  抄進中文句子（真實案例：威強電推薦理由出現「後端為 ACTIVE_TREND，且
  backend_priority_rank 為 2」）
- 修法：把「引用欄位名稱」改成「描述欄位所代表的中文語意」，並加一條全文通則：「一律用
  繁體中文描述語意，不得在句子中出現任何英文欄位名稱、程式變數名、enum 代碼或底線命名」
- **只影響之後新產生的 snapshot**；已存在資料庫的舊快照不回溯清洗（維持本專案一貫「不
  回溯造假資料」原則，使用者已確認接受）

### 資料流確認（非改動，純回報）
- 使用者問「/signals 下的股票有沒有放到魚尾（/signals/archive）」——**答案是有**：
  `app/signals/pipeline.py` 在同一個 `run_signal_pipeline_sync` 呼叫裡，`_persist_snapshot()`
  存新版 P3-P7 資料後緊接著呼叫
  `signal_archive.persist_signal_watch_hits(db, target_date, final_payload, job_id)`，
  把同一份 `final_payload["watchlist"]`（RECOMMEND 清單）寫進舊版 `signal_watch_hits`。
  兩邊資料同源，不需要額外接線

### Gotcha
- `git mv` 對「本次 session 內剛建立、尚未 `git add` 的檔案」會報
  `fatal: not under version control`（本次移動 `layout.tsx` 時踩到）；改用一般 `mv` 即可，
  下次 `git add` 會自動偵測成 rename
- 用 `{isEngineering && (<section/><section/>)}` 包多個相鄰 JSX 元素會編譯錯誤（JSX 只能有
  一個根節點），要包 `<>...</>` Fragment
- 型別檢查前先 `rm -rf .next`：`next dev`/`next build` 產生的 `.next/types/validator.ts`
  會快取舊路徑（`src/app/signals/debug/page.js` 等），檔案搬進 `(product)/` 後這些快取型
  別檔案會報 `Cannot find module`，是 stale cache 不是真的錯誤
- 本機驗證受工具限制：`chromium-cli`／Playwright 在此環境都不可用，只能用
  `curl` 檢查 SSR HTML（因整站包在 client-side `<SiteGate>` 密碼閘門後，未解鎖前 HTML
  永遠只有「載入中…」殼層）+ dev server log 確認 200／無編譯錯誤 + RSC payload 內文字
  確認新模組正確被引用（`signalsViewMode.tsx`／`SignalProductNav.tsx` 有出現在
  `/signals` 的 payload、`archive` 頁的 payload 裡沒有——證實 route group 隔離確實生效）；
  沒有做到真正點擊 toggle／開 dialog 的互動驗證，`tsc --noEmit` + `eslint` 全綠

## daily_signals workflow：非交易日跳過 + LLM 契約重試加倍（2026-08-10）

### 背景
- 使用者回報兩個問題：(1) `Daily Signals Generation` GitHub Action 常態性失敗、(2) 週末/國定假日也在跑選股
- 查最近 10 次執行：6 成功 4 失敗（非「固定」失敗，約 40% 機率），且 08-01（週六）/08-02（週日）確實都完整跑了一次 LLM pipeline

### 問題 2 根因：非交易日短路檢查從未真正實作
- `.github/workflows/daily_signals.yml` 註解宣稱「國定假日／一般週末因當日無交易資料而 no_data pass」，但 `candidate_pool.ingest_data` 抓交易日資料一律用 `trade_date <= target_date`（找「不超過目標日的最近交易日」），不會因為 `target_date` 剛好是週末而回空——週末直接沿用上個交易日（通常週五）資料重新跑一次完整 pipeline，多燒一次 OpenAI 額度，也多一次觸發問題 1 的機會
- **修法**（[backend/run_daily_signals.py](backend/run_daily_signals.py) `main()`）：建立 `SignalGenerationJob` 之前，先查 `DailyPrice` 是否有 `trade_date == target_date`（精確相等，非 `<=`）的任何一筆；沒有就直接 `return EXIT_NO_DATA`，不建 job、不呼叫 pipeline。維持 cron 每天觸發（涵蓋補班六），但只有真的有資料的那天才會往下跑

### 問題 1 根因：兩處 LLM 契約修正重試都只有 1 次，且 P3 一個分支根本沒進到重試邏輯
- **P4 每日觀察回顧**（[observation_lifecycle.py](backend/app/signals/observation_lifecycle.py) `run_tracking_assessments`）：對「已追蹤中」股票逐一呼叫 LLM 做當日回顧判斷，輸出不符合嚴格 schema（例如宣稱 `THESIS_INVALIDATED` 卻沒附可追溯來源）時只重試 1 次；重試仍失敗 → 該檔進 `technical_failures` → job 標記 `partial_failure`，即使 P3 主要推薦清單早已正常存進 DB
- **P3 全體候選比較**（[global_selector.py](backend/app/signals/global_selector.py) `run_global_selection`）：135 檔候選塞進單一 prompt 做一次性「全體一對一比較」，`for attempt in range(2 ...)` 名義上有 1 次重試，但 `payload is None`（LLM 沒回傳合法 JSON）這個分支的 `raise GlobalSelectionError(...)` 寫在 for 迴圈的 try/except 保護範圍**之外**，完全沒機會重試就直接整批失敗（0 檔推薦）
- **修法**：
  1. `global_selector.run_global_selection`：`max_attempts` 從 2 改 3（1 次重試→2 次）；把 `payload is None` 的分支也納入同一套重試判斷（`attempt < max_attempts - 1` 才重試，否則才 raise）
  2. `observation_lifecycle.run_tracking_assessments`：新增整批呼叫層級的重試（`max_batch_attempts=3`，涵蓋例外與格式不符兩種情況，原本 0 次重試）；單檔契約修正重試從 1 次改 2 次（`max_contract_retries=2`，用 for 迴圈取代原本單次 try）

### Gotcha
- `run_daily_signals.py` 的交易日檢查用 `SessionLocal()` 建立獨立連線，發生在建立 job **之前**；DB 連線本身失敗會回 `EXIT_DB_ERROR`（3），不會誤判為非交易日
- 測試（[backend/tests/test_run_daily_signals.py](backend/tests/test_run_daily_signals.py)）monkeypatch `app.database.SessionLocal`（非 `runner_module.SessionLocal`，因為 `main()` 內是函式內 local import，要 patch 被 import 的來源模組屬性，call 時才會拿到新值）
- P3/P4 重試次數提升後單次 pipeline 執行時間會拉長（原本失敗直接放棄，現在最多多打 1~2 次 LLM call 才放棄）；`daily_signals.yml` 既有 `timeout-minutes: 120` 未調整，先觀察是否足夠
- 全 backend suite 驗證：新增 2 個測試（非交易日短路 + 有資料正常往下走）全 pass；既有 20 個 baseline fail（site-passwordless 認證相關）不變，零新增失敗

## Phase 2：魚尾 Canonical Momentum Pipeline（**production cutover 完成，2026-07-22**）

> **狀態**：deterministic 決策層（sector context → role taxonomy → sector cluster →
> entry/tracking state → regime gate → explain trace → funnel metrics）已完整實作，
> 用真實 2026-07-20 資料 replay 驗證過落差，並累積 20 個交易日（06-22~07-17,
> 07-20）historical replay 報酬率比對後，**2026-07-22 正式切換為 production**：
> `SIGNALS_PIPELINE_MODE` 預設值從 `legacy` 改為 `phase2`，Phase 2 存活者現在就是
> 真正送進 LLM、寫進 `signal_snapshots`/`signal_watch_hits` 的候選來源。legacy chain
> 仍照跑，只當 fail-safe fallback（Phase 2 丟例外時退回）與持續監控比較基準。
> LLM v6 完整重寫、`/signals/archive` 等其他前端 surface 的 canonical 遷移仍未做
> （見下方「尚未做」）。

### 為什麼要做 Phase 2
- Phase 1（canonical 分類）完成後，用漢翔/台虹/台化/長榮/星宇等真實案例回溯
  發現：問題不是「分類不準」，是「產業被當成股票生死的硬條件」——單一樣本
  sector（漢翔）、弱 sector 裡的強股（台虹）、沒有完美 formal LEADER 的整個
  航運族群、追蹤中股票每天重新選秀（台化）、hit_count 當 RISK_OFF 硬門檻
  （統一）都是同一個根因的不同症狀
- 使用者要求：**不要**只把 `industry_name` 換成 `primary_sector`（會立刻撞上
  `AEROSPACE_DEFENSE=1` 這種新的樣本數問題）；要把「產業」從硬條件改成
  「描述股票所處環境的一組 context」

### 2.0 資料正確性修復（已上 production，非 shadow）
- **`monthly_revenue.yoy_pct` 全市場全月份 NULL 的根因修復**
  （[backend/etl/finmind_monthly_revenue_sdk.py](backend/etl/finmind_monthly_revenue_sdk.py)）：
  舊版在 45 天 fetch frame 內 `groupby.shift(12)` 回算 YoY，frame 內從無 12 個月前
  資料，永遠算出 NaN。新增 `recompute_missing_yoy_mom()`：改查 **DB 既有歷史**
  （回溯到 2019）算去年同期/上個月，COALESCE 保護不覆寫既有值、算不出來時誠實
  留 NULL。9 個新 regression test。**D 基本面通道從上線起首次真正有資料可用**
- **`momentum_score` missing≠bad 語意修正**（`app/signals/momentum.py`）：
  基本面資料常態性缺席（月營收公告時間差）不應永遠把分數封頂在 90——改用
  available-weight normalization 讓核心四項（30+25+20+15=90）滿分時仍可 rescale
  到 100。**這條會實際改變 LEADER≥70 門檻的過關名單，behind flag**：
  `SIGNALS_MOMENTUM_SCORE_AVAILABLE_WEIGHT`（預設 `false`=v1 舊行為）。
  新增 `momentum_score_version` / `feature_coverage` / `score_confidence` 三個
  診斷欄位（已寫進 `build_signal_metrics`，不論 flag 開關都會出現）
  - **Gotcha（差點捅出的簍子）**：一開始把這條改動直接寫死進
    `compute_momentum_score()`，沒有 flag——這會在下一次 cron 直接影響真實選股
    結果，完全繞過使用者要求的 shadow 驗證流程。發現後補上 flag，default 完全
    等同修改前的行為

### 新模組 `backend/app/signals/phase2/`（純 shadow，legacy 零 import 依賴）
- `sector_context.py`（§D/§E）：hierarchical peer_scope
  `SUB_SECTOR → PRIMARY_SECTOR → MARKET_ONLY`（樣本 < `MIN_PEER_SAMPLE=5` 就
  fallback，**不會**讓 1 檔的 sector 產生 100 percentile 假訊號）；
  `sector_strength_percentile_20d`（整個 sector 強不強，vs 其他 sector 比）與
  `peer_rs_percentile_20d`（個股在自己 peers 裡強不強）明確拆開，不再共用一個
  `industry_rs_percentile`；`canonical_mapping_usable`（Phase 1 confidence
  HIGH/MEDIUM=可用，LOW=不可用於硬分組）
- `sector_cluster.py`（§K）：`sector_momentum_cluster`（ACTIVE/NEUTRAL/COOLING/
  FAILED/UNAVAILABLE），讓 FOLLOWER 路徑可以在「沒有完美 formal LEADER」時，
  靠整個 sector 的強度/法人流向/強勢股數量打開（解航運全滅）
- `entry_state.py`（§J）：`pullback_atr_multiple`（距高點回落幅度 / 14 日 ATR）
  取代 `distance_to_20d_high >= -3%` 固定 cliff；NEAR_HIGH/NORMAL_PULLBACK/
  DEEP_PULLBACK/REACCELERATING/STRUCTURE_DAMAGED，**與 role 完全分離**（不是
  LEADER 判定的一部分）
- `roles.py`（§G/§H/§I/§L/§M）：base momentum eligibility 與 role annotation
  分離（不合格不等於刪除，回 `role=None` 繼續往下走）；LEADER 改
  evidence-count（6 項證據，非 6-way AND）：`SECTOR_LEADER`(>=5)/`CO_LEADER`(>=4)；
  新增 `INDEPENDENT_LEADER`（sector context 不可用/不強時，個股 market RS>=90 +
  score>=75 + 足夠獨立確認的替代路徑，解漢翔）；`EMERGING_MOMENTUM`（RS 排名
  快速改善但尚未到頂）；`UNCLASSIFIED_MOMENTUM`（base 合格但無明確角色，
  **不等於死亡**，繼續往 risk/regime 走）
- `tracking_state.py`（§N）：`is_tracked=True` 的候選走 continuation state
  （ACTIVE_TREND/HEALTHY_PULLBACK/REACCELERATING/DETERIORATING/INVALIDATED），
  **不重新參加角色選秀**（解台化：問「原本的強勢邏輯還在嗎」而非「今天是不是
  新 LEADER」）
- `regime_gate.py`（§O/§P/§Q）：`hit_count` 從任何 regime 的 hard gate 改為純
  `conviction`（high/medium/low）增強項，不再單獨造成 REMOVE（解統一：
  hit_count=2 但 formal LEADER + RS 91.5，Phase 2 下存活）；`is_true_hard_exclusion`
  只保留真正定義性排除（ETF/金融 universe、liquidity、volume deadline、
  failed_follow_through、**distribution**、structure_damaged、
  risk_gate_action=EXCLUDE、overheat/extended+法人反轉）——**刻意不搬** legacy
  條件 #2（法人 5 日流出且非 ROTATION_LAGGARD），因為那正是依賴要拿掉的
  `prelim_type` 硬分類
- `explain_trace.py`（§R）+ `funnel_metrics.py`（§S）：每檔候選完整決策追蹤
  （candidate_channels/sector_context/role/tracking_state/entry_state/
  hard_exclusion/regime_gate/final_stage/first_exclusion_reason）+ 每日 funnel
  統計（candidate→eligible→role→risk→regime→llm 各層留存數 + 異常偵測：
  `classification_survival_low`/`sector_lockout_detected`/`sent_to_llm_zero`/
  `no_output_day`）——`7/20 那種「120→14→1→0」的崩潰現在可以立刻定位在哪一關`
- `pipeline_v2.py`：整合入口。**Candidate Discovery 完全沿用 legacy**
  （`candidate_pool.build_candidate_pool` 等，Phase 2 不重新定義候選池怎麼來）；
  `build_phase2_pool(raw_pool)` 是關鍵 helper——**必須吃 legacy
  `classification.classify_stocks()` 之前的原始候選池**，不能吃 `after_soft`
  （已被 legacy 三選一硬刪除過），否則 shadow 完全驗證不到 Phase 2 要解決的東西
  （這是 replay 時抓到的第一版 bug，已修正）

### Shadow Mode 接線（`app/signals/pipeline.py`）
- `SIGNALS_PIPELINE_MODE` env（預設 `legacy`）：`legacy` 時 `_run_phase2_shadow`
  直接 return，**對 legacy pipeline 零 import、零執行**；`phase2_shadow` 時額外
  跑一次 Phase 2 決策層，寫進獨立表 `signal_shadow_snapshots`（`app/models.py`），
  完全不影響 `signal_snapshots`/`signal_watch_hits`（真正驅動使用者看到的訊號）
- Phase 2 pipeline 任何例外都被 `_run_phase2_shadow` 吞掉只 log，不
  propagate——3 個 shadow wiring 測試明確驗證：預設模式零寫入 / shadow 模式
  寫入且不動 legacy 輸出 / phase2 例外不拖垮 legacy

### 用真實 2026-07-20 資料 Replay 驗證（`backend/run_phase2_replay.py`）
```
候選池（Candidate Discovery，兩邊共用）：120 檔
[Legacy] classify_stocks（三選一）後：1 檔 → regime gate 後：0 檔
[Phase 2] 定義性 hard exclusion 後：32 檔 → regime gate 後：7 檔
   2527/5434/1515/2912(統一)/00715L/00665L/3532：全部 INDEPENDENT_LEADER
```
- **統一（2912）被正確救回**：INDEPENDENT_LEADER + conviction=medium，通過
  RISK_OFF gate——證實 hit_count 移出硬門檻確實解決了incumbency bias
- **漢翔（2634）/星宇（2646）正確得到 `tracking_state=HEALTHY_PULLBACK`**
  （不再是legacy 的「今天沒有 role 就死」），但**目前仍在 regime_gate 的
  RISK_OFF 分支被排除**——因為 `apply_regime_gate_v2` 判斷「formal leader」只看
  `role` 欄位，tracked 候選的 role 是 None（分類權交給 tracking_state）。
  **這是已知、需要下一輪修的落差**：RISK_OFF 存活條件要能接受
  `tracking_state in (ACTIVE_TREND, REACCELERATING, HEALTHY_PULLBACK)` 視同
  formal leader 的替代條件，不是只認 `role`
- **台化(1326)/台虹(8039)/長榮(2603)/慧洋(2615)/台塑化(6505) 全部被
  `distribution` soft hint 判定為 true hard exclusion 剔除**：spec §P 明確把
  `distribution` 列為「真正定義性排除」之一，但這是與 legacy 行為的重大差異
  （legacy 只把它當 LLM 的軟性提示，不曾當硬刪除條件）。5 檔命中同一個原因，
  影響一半的目標驗證案例，**需要使用者決定**：(a) 維持 spec 原意，`distribution`
  繼續當 hard exclusion，只是這幾檔那天真的有派發嫌疑；或 (b) 把 `distribution`
  降級回 soft signal（只影響 conviction，不影響存活），改用其他方式表達
  「真正的結構性崩壞」（如 entry_state=STRUCTURE_DAMAGED 已經有）
- **台達電（2308）正確地連候選池都沒進**：負面對照組驗證通過——RS 太低，
  Phase 2 沒有因為基本面强而把它硬救回來

### 第二輪：distribution 定調 + regime gate 落差修正 + Comparison Debug View（2026-07-21）

第一輪 replay 找到兩個具體落差，這輪處理掉，並把 shadow 結果打通到前端：

- **`distribution` 決定降級為 soft signal**（使用者確認）：不再是
  `regime_gate.is_true_hard_exclusion()` 的條件，改成只影響
  `compute_conviction()`（命中時信心度降一級：high→medium→medium→low）。
  同時發現 `roles.is_base_momentum_eligible()` 原本**獨立**也擋 distribution
  （跟 regime_gate 是兩條不同程式碼路徑），只修一邊等於沒修——兩處都已同步移除
- **RISK_OFF regime gate 認 tracking_state**：`apply_regime_gate_v2()` 的
  RISK_OFF 存活條件從「只認 `role` 是 formal leader」改成「`role` 是 formal
  leader **或** `tracking_state` 是 ACTIVE_TREND/REACCELERATING/
  HEALTHY_PULLBACK 之一」，market RS >= 90 門檻不變
- **重跑 7/20 replay 驗證兩個修正的實際效果**（真實 DB 資料，非模擬）：
  存活數從 7 檔增加到 16 檔——統一/漢翔（tracking_state 修正生效）/台塑化
  （distribution 降級生效）都正確被納入；長榮/星宇/慧洋因 market RS 73~81
  沒到 RISK_OFF 的 90 門檻正確維持排除（門檻本身沒被誤觸發，是合理結果）；
  台化因 `momentum_phase="weakening"` 正確判定資格不符（真實轉弱，不是誤殺）；
  台虹撞到另一條獨立的既有規則 `extended_with_institution_selling`（10 日漲
  27.8% 且當日法人轉賣，跟 distribution 蠟燭圖案完全是不同訊號，暫不調整）
- **新增 Backend API**：`GET /api/signals/phase2/shadow-dates`（列出已跑
  replay 的日期）+ `GET /api/signals/phase2/shadow/{date}`（單日完整
  funnel_metrics + explain_traces + legacy/phase2 比較摘要），新 router
  `app/routers/phase2_debug.py`，純讀 `signal_shadow_snapshots`
- **新增前端 Comparison Debug View**（`/signals/phase2`）：日期選單 + 統計卡片
  （候選池/legacy 存活/phase2 存活/動能資格通過率）+ 角色分布 + 逐檔可展開的
  explain trace（搜尋 + 「只看判斷不同的股票」篩選）；從 `/signals/archive`
  header 加一個連結入口
- **真實瀏覽器端到端驗證抓到一個真 bug**：`TraceRow` 元件原本把股票代號的
  `<Link>` 包在整列的 `<button>` 裡面——`<a>` 巢狀在 `<button>` 裡是無效 HTML，
  瀏覽器 parser 行為不可預期，用 Chrome + CDP（`ws` 套件手刻最小 client，因為
  環境沒裝 playwright/puppeteer）實際點擊測試才發現點擊會被導去股票頁而不是
  展開列。修法：整列改用 `<div>` 包裝，股票代號文字與「個股頁 →」`<Link>`、
  「展開/收合」`<button>` 三者是平行的兄弟元素，不互相巢狀
- 全 backend test suite：999 pass / 20 fail（既有 baseline，zero 新增失敗）

### 20 天 historical replay 報酬率驗證（2026-07-21，先於 cutover）
- 用 `run_phase2_replay.py --persist` 對 2026-06-22 ~ 2026-07-20 共 20 個交易日
  重跑（1 天因本機資料庫連線卡住失敗，19 天成功），每天輸出 legacy 存活數 /
  Phase 2 存活數 / regime / 候選池大小到 `signal_shadow_snapshots`
- `backend/analyze_phase2_replay_returns.py`：對每天 legacy/Phase 2 各自的存活
  名單，用「命中當天收盤 → 2026-07-21 收盤」算簡單報酬率，並對照 production
  真實 `signal_watch_hits` 當天是否也抓到、抓到的話報酬率是多少
- **關鍵發現**：漢翔（2634）在 legacy 掛零存活的 4 天（07-13/07-16/07-17/07-20）
  全部被 Phase 2 抓到，且 4 次相對 07-21 收盤報酬率全為正
  （+12.07%/+10.85%/+16.83%/+10.00%）——直接證實 Phase 2 解決了本輪要解決的
  核心問題（單一樣本 sector 不該讓強股被判死）
- Phase 2 候選池是 legacy 的 ~15 倍大（20 天合計 677 筆 vs legacy 遠小得多），
  整體平均報酬率與 legacy 相近略遜（本輪只有 20 天樣本、且未接 LLM 過濾，
  這是預期中「候選池變寬但決策權還在下一段」的中繼結果，不是最終使用者會看到
  的品質）
- 逐筆明細（含全部 677 筆 + legacy/production 對照欄位）：
  `/tmp/phase2_replay_returns_20days_0721.csv`（本機暫存檔，非版控）

### Production Cutover（2026-07-22）
- **`SIGNALS_PIPELINE_MODE` 預設值改為 `"phase2"`**（`app/signals/pipeline.py`）：
  不需要在 Render / GitHub Actions 額外設定 env var——改程式碼預設值同時涵蓋
  Render web service 的 BackgroundTasks 路徑（前端「重新產生」按鈕）與
  `daily_signals.yml` workflow_dispatch 路徑。要臨時退回 legacy，設定 env var
  `SIGNALS_PIPELINE_MODE=legacy` 即可，不需要改代碼或 revert commit
- **LLM 相容層**（`pipeline_v2.role_to_prelim_type()`）：Phase 2 存活者沒有
  legacy `prelim_type`（LEADER/FOLLOWER/ROTATION_LAGGARD），新增映射：
  - 三種 formal leader（SECTOR_LEADER/CO_LEADER/INDEPENDENT_LEADER）→ LEADER
  - SECTOR_FOLLOWER → FOLLOWER；ROTATION_LAGGARD → `"ROTATION_LAGGARD"`
    字串（沿用既有 `_normalize_prelim_type` 再轉 LAGGARD 的邏輯）
  - EMERGING_MOMENTUM → FOLLOWER 桶；UNCLASSIFIED_MOMENTUM → LAGGARD 桶
    （最保守分類，避免灌水）
  - 已追蹤股（role=None，改用 tracking_state）：ACTIVE_TREND/REACCELERATING
    → LEADER；HEALTHY_PULLBACK → FOLLOWER；DETERIORATING/INVALIDATED → LAGGARD
  - 這是工程判斷，非 spec 硬性規定；**§X v6 prompt 完整重寫仍未做**，這只是
    讓既有 v1/v4 prompt 契約能吃 Phase 2 輸出的最小相容層
- **`llm_caller._to_evidence_view` 新增 3 個 optional 欄位**：`phase2_role` /
  `phase2_tracking_state` / `phase2_entry_state`（legacy 候選這三個永遠 None，
  零影響）；prompt（v1 + v4）加一小段說明，要求 reason 優先引用這些具體狀態，
  不要為了套用「產業有龍頭、這檔在跟」敘事而虛構不存在的龍頭股
- **`pipeline.py` 真實分支**：`SIGNALS_PIPELINE_MODE=="phase2"` 時，Phase 2
  存活者（映射過 prelim_type、套用同一個 `LLM_INPUT_HARD_LIMIT=50` 上限）
  **取代** legacy 算出來的 `after_regime`/`conviction_by_stock`/
  `signal_metrics_by_stock`；legacy chain 仍照跑（成本低，純 deterministic，
  無 LLM），只當 fail-safe fallback 與 shadow snapshot 比較基準。Phase 2
  pipeline 任何例外 → log + rollback + 退回 legacy 輸出，cron 不會因新程式碼
  的 bug 整包失敗
- **conviction 欄位別名**：phase2 的信心度欄位叫 `conviction`
  （`regime_gate.py`），legacy 是 `regime_conviction`（`filters.py`）；
  evidence view 讀的是後者的 key 名，phase2 分支裡多寫一行別名
  （`c["regime_conviction"] = c.get("conviction")`），否則 LLM 看到的
  regime_conviction 全部是 null
- **shadow snapshot 持續監控**：切換後 `signal_shadow_snapshots` 仍每天寫入，
  `comparison_summary.legacy_survivor_ids` 現在代表「若還在用 legacy 今天
  會抓到誰」，`/signals/phase2` Debug View 頁面繼續有意義，不是只有 cutover
  前才有用
- **前端零改動**：LLM 輸出契約（`type`=LEADER/FOLLOWER/LAGGARD、`conviction`、
  `watch_intensity`、`regime`）完全沒變，只是候選來源換了——`SignalTypeChip`
  等既有元件不需要任何修改就能正確顯示 Phase 2 驅動的訊號卡片
- **成本影響（需觀察）**：Phase 2 存活數明顯高於 legacy（19 天 replay 平均
  ~37 檔 vs legacy 個位數），送進 LLM 的候選數量會顯著增加、逼近甚至常態性
  觸頂 `LLM_INPUT_HARD_LIMIT=50`，OpenAI 呼叫成本（research + decision +
  watch_reason 三段 batch）預期會有感上升，需要接下來幾天觀察實際花費
- 15 個新單元測試（`role_to_prelim_type` 映射 7 個 + pipeline.py phase2 真實
  分支 2 個：使用 phase2 候選送進 LLM / phase2 例外 fail-safe 退回 legacy）；
  全 backend suite 1008 pass / 20 fail（既有 baseline），零新增失敗

### Production 真實重跑驗證（2026-07-22，`run_daily_signals.py 2026-07-21`）
- 直接對 production DB 跑一次真正的 pipeline（非 replay，會真的呼叫 OpenAI +
  覆寫 `signal_snapshots`/`signal_watch_hits`）；選 2026-07-21 是因為 07-22 當天
  ETL 尚未跑（18:00 台北才有資料），且 07-21 legacy 原本產出剛好是 **0 檔
  watchlist**，覆寫無「毀掉既有好結果」風險
- **結果**：候選池 120 檔 → Phase 2 定義性 hard exclusion 後 56 檔 → regime gate
  （RISK_OFF）後存活 13 檔（含 2634 漢翔 / 2912 統一超 / 1326 台化 等）→
  LLM 決策後最終 WATCH 2 檔：**1810 和成**、**2912 統一超**（皆 LEADER /
  conviction=medium / watch_intensity=cautious）；`signal_shadow_snapshots.
  comparison_summary` 同時記到 `legacy_survivor_count=0`，證實這天若還在用
  legacy 選股，使用者會看到完全空的清單
- **統一超（2912）正是最初驅動 Phase 2 的案例之一被真實 production 選中**
  （spec 動機：hit_count 曾被 legacy 當 RISK_OFF 硬門檻擋下，Phase 2 移除該
  門檻後正確存活）；漢翔進了候選池但沒進最終 LLM WATCH 名單（LLM 決策層
  這次判斷不足以 WATCH，屬正常篩選，不代表 Phase 2 候選池機制沒生效）
- 順手修正一個純 log 訊息 bug（不影響任何 persisted 資料）：production 分支
  的「legacy would have produced N survivors」log 曾誤讀已被 phase2 覆寫後的
  `conviction_by_stock`，改用覆寫前就先存好的 `legacy_survivor_ids` 變數

### Hard Exclusion 重構（2026-07-22 第二輪，`regime_gate.is_true_hard_exclusion`）

> 起因：production cutover 後想查「8039 台虹哪幾天進候選池」，發現它在 7/14
> （3 日漲幅 +32.5%）被 `overheat_3d` 直接剔除——過熱 ≠ 失敗，這條規則跟其他
> 幾條單一 % 門檻一樣，把「entry risk 高」錯當「momentum failure」處理。

**核心原則**：Hard Exclusion 只能代表「即使後續題材/角色/LLM 驗證多好，也不應
再進入 WATCH 評估」的真正失效情況；「漲很多」「短線過熱」「法人單日小幅轉賣」
「單日跌幅較大」都只是 entry risk 高，不是失敗證明。

**重構後 TRUE HARD EXCLUSION 只剩 6 種**（原本 9 條規則）：
`MANUAL_BLACKLIST` / `FAILED_FOLLOW_THROUGH_CURRENT_EPISODE` /
`STRUCTURE_DAMAGED` / `COMPOSITE_RISK_EXCLUDE`（原 `risk_gate_action=EXCLUDE`）/
`LIQUIDITY_FAILURE` / `REVERSAL_FAILURE`（新）。

**取消的舊硬剔除，全部降級為 risk_warning（不再剔除，只標記）**：
- ETF / 金融股資產類型（`is_etf`/`is_financial` 不再是排除理由，只有人工黑名單
  `manual_blacklist` 才是；`candidate_pool.py` Step 1 候選池建立仍會濾掉 ETF/金融，
  這次刻意沒動，屬於已知落差，見下）
- 近 3 日漲幅 > 15% → `EXTENDED_3D` warning
- 股價級距 × 日張數門檻 → `LOW_RAW_VOLUME` warning（raw 張數不分股價高低的舊
  bug，25 天 replay 顯示這是**最大的誤殺來源**，996/3000 檔命中）
- 近 10 日漲幅 >25% + 法人轉賣 → `EXTENDED_PROFIT_TAKING_WARNING`
- 3D 法人正 + 今日反轉賣 + 跌 >1.5% → `INSTITUTION_REVERSAL_WARNING`

**新增 `REVERSAL_FAILURE`**：取代上面兩條粗糙規則，改成三條件同時成立才 Hard：
(A) 法人反轉具實質性 `institution_reversal_ratio`（今日賣超 / 前段扣除今日的
累積買超）≥0.5、(B) 相對大盤明顯轉弱 `excess_return_vs_market`（個股當日報酬 -
大盤當日報酬，非絕對報酬）≤-1.5、(C) 至少再一個獨立 family 的 deterioration
confirmation（PRICE_STRUCTURE/VOLUME_PRICE/SECTOR_ROTATION 任一）。大盤 -5%、
個股 -2%（相對 +3%）不再被誤判出貨——這正是舊規則的根因。`market_regime.py`
新增 `return_1d_pct`（純新增欄位，不改 `classify_regime` 判斷邏輯）供這裡使用。

**COMPOSITE_RISK_EXCLUDE**（原 `risk_gate_action=EXCLUDE`）沿用既有兩條路徑
（distribution+institution_flow_reversal，或 failed_rotation+weakening），本輪
只補 `evidence_families` 標記確認本來就跨兩個獨立維度，判斷邏輯沒重寫。

**重構過程中發現並順手修正的 2 個既有 bug**（都直接跟 hard exclusion 相關，
非額外功能，故在授權範圍內修）：
1. `build_phase2_pool` 呼叫 `attach_deterministic_signals` 的時機早於
   `_detect_soft_hints` 設定 `soft_hints`，導致 `distribution`/`weakening`/
   `retail_overheated` 三個訊號衍生的 `deterministic_signals` 欄位從未被正確
   套用過（永遠讀到空 hints list）
2. 舊版 `risk_gate_action == EXCLUDE` 讀扁平欄位 `candidate["risk_gate_action"]`，
   實際資料在巢狀 `candidate["deterministic_signals"]["risk_gate_action"]`，
   這條 hard exclusion **從未在 production 真正觸發過**

**Explain trace / funnel 不再 silent delete**：`build_phase2_pool` 新增
`excluded_out` 參數收集每檔被剔除的候選（含 reason/risk_warnings/
evidence_families），`run_phase2_pipeline` 合併進 `explain_traces` 並統計
`funnel_metrics.hard_exclusion_reason_counts`（每種原因剔除了幾檔，一眼看到）+
`hard_exclusion_version="phase2_new_hard_gate"`（供 historical snapshot 區分
新舊規則）。

**Regression 驗證**：
- 8039（台虹 7/14）：不再被 3D>15% 誤殺，`excluded=false`，只標 `EXTENDED_3D`，
  角色判為 `INDEPENDENT_LEADER`，正確送進 LLM
- 6243（迅杰 7/21）：確認未被誤剔除
- ETF/金融/人工黑名單/低張數但金額足夠/failed_follow_through episode-scoped：
  16 個新單元測試（`tests/test_phase2_hard_exclusion_v2.py`）全通過
- 全 backend suite：1022 pass / 20 fail（既有 baseline，零新增失敗）

**25 天 historical replay（06-15~07-21，總候選池 3000 筆）**：
```
舊規則命中：OLD_VOLUME_TIER 996 / OLD_3D_OVERHEAT 412 / OLD_10D_EXTENDED_SELL 97
           / OLD_3D_FLOW_REVERSAL 88；舊規則下總存活 1407/3000
新規則命中：LIQUIDITY_FAILURE 817 / COMPOSITE_RISK_EXCLUDE 181 /
           FAILED_FOLLOW_THROUGH_CURRENT_EPISODE 8；新規則下總存活 1994/3000
```
多放行 587 檔（+41.7%）。**`REVERSAL_FAILURE` 在這 25 天真實資料中 0 命中**——
不代表機制失效（單元測試已用合成數據證明三條件同時成立時能正確觸發），比較
可能是門檻本來就設計成「只抓真正嚴重」的罕見情況，需要更長 replay 觀察，
**不得為了讓某天有命中而調鬆門檻**。

**已檢查、確認不需要改的部分**：`entry_state.py` 的 `STRUCTURE_DAMAGED` 判斷
本來就要求「距高點 ≥4 倍 ATR（結構性、非固定 %）」+「RS 排名同時惡化（相對
維度）」雙重確認，符合本輪「絕對價格弱勢 + 結構/相對確認」的原則，未修改。

**已知、刻意不動的落差**：`candidate_pool.py`（Step 1 候選池建立，本次任務明確
排除）仍用 `should_exclude()` 在源頭排除 ETF/金融股，所以牠們現在還是進不了
候選池——這次只解決「Phase 2 hard exclusion 這一關不再把 ETF/金融當排除理由」，
要讓 ETF/金融真的流過完整 pipeline 需要另外處理 Step 1，超出本次授權範圍。

### 尚未做（下一輪接手指引）
1. LLM v6（§X：backend 唯一 deterministic authority，LLM 只做業務/題材/供應鏈
   驗證 + 降級/REMOVE，不可重跑 threshold）**完全未動**——現有 v1/v4/v5 prompt
   ×regime 路由邏輯不受本輪影響，只是額外看得到 3 個 phase2_* 欄位
2. `/signals/archive`（30 日追蹤正式頁）與 watchlist/StockList 等其他前端 surface
   的 canonical 遷移（§W）仍未做——目前只有 debug-only 的 Comparison Debug View
3. Sector momentum cluster / role evidence 門檻都是**工程起始值**，只用 20 天
   歷史 replay 觀察過（原 spec §Y.16 建議 60~120 天），明確標記「待更多 replay
   校準」，不得為了讓特定案例過關硬調
4. **cutover 後第一週應密切觀察**：(a) 前端每日訊號清單的品質是否符合預期
   （candidate 變寬後 LLM 決策層要扛起更多篩選責任）、(b) OpenAI 每日花費、
   (c) `/signals/phase2` Debug View 的 legacy vs phase2 存活數比較是否穩定、
   (d) `signal_watch_hits`/30 日追蹤的實際命中報酬率走勢
5. 本地測試用的 `SITE_GATE_PASSWORD=localtest123` 只在手動驗證時透過環境變數
   臨時帶入單次 uvicorn 進程，**沒有寫進任何 `.env` 或程式碼**，重啟後即失效，
   不影響任何持久化設定

### LLM v6 Contract Alignment：backend 是唯一 candidate eligibility authority（2026-07-22）

> 完整規格：本輪 spec「魚尾 Phase 2 → LLM v6 Contract Alignment」（對話中，未落地為獨立文件）

**背景**：Phase 2 deterministic 決策層（sector context / role / tracking state / entry
state / hard exclusion / regime gate / conviction）已完整實作，但 LLM prompt（v1/v4/v5）
仍保留「重新判斷動能門檻」的權責，容易出現「backend 已判定合格，LLM 卻用自己的一套
RS/momentum 門檻再刪一次」的矛盾。本輪目標：LLM 從「選股者」降為「外部事實驗證 +
否決 + 中文解釋層」，backend 是唯一的 candidate eligibility authority。

**候選池 ETF/金融修復**（`candidate_pool.py` Step 1、`momentum.py` universe）：ETF/金融股
不再單獨被排除（只有人工黑名單才排除）；新增 `asset_type`（COMMON_STOCK/FINANCIAL/ETF）
供 LLM 決定研究流程，本身不可成為 REMOVE 理由。真實 3 天驗證（07-14/07-20/07-21）證實
2 檔金融股（2886 兆豐金、5871 中租-KY）成功走完整流程進入最終 WATCH。

**新 prompt** [watch-list-stock-v6.md](backend/app/prompts/watch-list-stock-v6.md)：
- 明確宣告 backend authoritative 清單，LLM 絕不能重建/覆寫/取代
- `internal_role`（`phase2_role`/`phase2_tracking_state`/`phase2_entry_state`）與
  `display_type`（映射後 LEADER/FOLLOWER/LAGGARD，僅供 UI 相容）並存傳給 LLM，禁止用
  `display_type` 重新套用 legacy 角色資格規則（例如「FOLLOWER 必須有 formal leader」）
- 移除 LLM 端 Momentum Gate / regime 數字門檻重判
- 新增驗證三態 `business_validation`/`theme_validation`/`supply_chain_validation`：
  `VERIFIED`/`UNCONFIRMED`/`MISMATCH`，`UNCONFIRMED ≠ MISMATCH`（缺新聞不是矛盾證據）
- 新增 `veto_reason` enum（`BUSINESS_MISMATCH`/`THEME_MISMATCH`/`FALSE_SUPPLY_CHAIN_LINK`/
  `MATERIAL_NEGATIVE_EVENT`/`DATA_CONTRADICTION`/`BACKEND_MAX_REMOVE`）：任何 REMOVE
  必須有明確理由，禁止「漲太多」「entry risk 高」等偽理由

**`backend_max_decision` 天花板**（[llm_caller.py](backend/app/signals/llm_caller.py)
`_run_decision_chunk`）：程式碼層強制執行（不只是 prompt 宣稱）——`backend_max_decision
=REMOVE` 時，即使 LLM 誤判 WATCH，也強制覆寫回 REMOVE + `veto_reason=BACKEND_MAX_REMOVE`。
**這條規則回溯適用所有 prompt 版本**（v1/v4/v5 皆套用同一段合併邏輯），因為 v5 prompt
STEP 7.5 本就宣稱這個規則，只是從未被程式碼驗證過。

**`_normalize_prelim_type` fallback 修正**：unknown/缺值 fallback 從 `"LEADER"` 改為
`"LAGGARD"`（最保守桶）+ 加 `logger.warning`——避免資料缺漏的候選被系統性灌水成最高
優先桶。

**LLM_INPUT_HARD_LIMIT 截斷排序**（`pipeline.py::_phase2_llm_priority_key`）：Phase 2
候選（有 `role`/`tracking_state` 欄位）改用 `conviction → momentum_score →
rs_market_percentile_20d → risk_warning 數量` 數字排序，取代舊的 `prelim_type` 角色桶
排序——避免 `EMERGING_MOMENTUM`/`UNCLASSIFIED_MOMENTUM` 這類映射到較弱顯示桶的角色被
系統性犧牲截斷名額。Legacy 候選（無 `role` 欄位）排序邏輯完全不變。

**意外發現並修正的既有 bug**：`_build_stage_prompt`（A4 prompt 分段優化，2026-05-18）
邊界判斷有誤——每個 STEP 標題是「==== / STEP N / ====」三明治結構，舊版在**第一個**
符合的 `====` 邊界（正是標題正下方那條）就 `break`，導致 research/decision/
watch_reason 三個 stage 過去只收到 STEP **標題文字**，內文完整消失。**v1/v4/v5/v6
全部受影響**（自 A4 上線起）。修法：不要在第一個符合邊界就 break，保留最後一個符合
邊界（最靠近下一個 STEP 標題正上方那條）。已修正 + 補 regression test。

**真實 3 天驗證**（`run_v6_llm_validation.py`，真呼叫 OpenAI，只寫本機 scratch 檔、
不動任何 production 表）：

| 日期 | Regime | 候選池 | 送 LLM | backend REMOVE→LLM WATCH 違反 | LLM 主動否決 | 最終 WATCH |
|---|---|---|---|---|---|---|
| 2026-07-20 | RISK_OFF | 120 | 25 | 0 | BUSINESS_MISMATCH×2 | 23（含 2 檔金融股） |
| 2026-07-14 | RISK_OFF | 120 | 36 | 0 | BUSINESS_MISMATCH×4 | 32 |
| 2026-07-21 | RISK_OFF | 120 | 20 | 0 | 無 | 20 |

天花板 3 天合計 0/81 違反（誠實揭露：這 3 天 backend 送進 LLM 的候選 `backend_max_decision`
全部是 WATCH，天花板在這 3 天真實資料中沒被自然觸發；覆寫生效是靠合成情境單元測試
`test_backend_max_decision_remove_forces_final_remove_even_if_llm_says_watch` 驗證）；
全 3 天 0 筆「REMOVE 但無 veto_reason」的靜默否決。

**已記錄未修（超出本次範圍）**：`exclusions.is_etf()` regex 認不出「00665L」一類帶字母
後綴的槓桿/反向 ETF；Phase 1 canonical classification 已修過同類問題但未反向對齊。

**版本**：`PROMPT_VERSION_MOMENTUM`/`PROMPT_VERSION_BULL`/`PROMPT_VERSION_VOLATILE` 均
指向 v6；`SIGNALS_FORCE_PROMPT_VERSION=v5` 可強制回跑舊版做對照。

---

### Phase 2.5：Momentum Freshness + Final Watch Quality Layer（2026-07-23）

**背景**：Phase 2（含 v6 LLM contract）已解決「不要過早刪掉真正強股」，但下一步問題是
「不要讓所有勉強符合 Momentum 的股票都變成 WATCH」——`Candidate Eligible ≠ High-quality
WATCH`。本輪在 Regime Gate 通過後、送進 LLM 之前，新增一層 deterministic 品質過濾。

**新模組**：
- [momentum_freshness.py](backend/app/signals/phase2/momentum_freshness.py)：
  `compute_momentum_freshness()` 用**相對報酬優先於絕對報酬**（大盤跌 5%、個股跌 2% 是
  相對抗跌）+ 多維度證據聚合（非單一固定門檻）判斷 `FRESH_STRONG`/`FRESH_STABLE`/
  `HEALTHY_PULLBACK`/`STALE`/`DETERIORATING` 五種狀態。刻意不新增獨立
  `REACCELERATING` 狀態（避免與既有 `entry_state.ENTRY_REACCELERATING` 混淆），改
  作為 FRESH_STRONG 的一項證據。
- [watch_quality.py](backend/app/signals/phase2/watch_quality.py)：
  `compute_watch_quality()` 用 7 個獨立 evidence family（MOMENTUM_STRENGTH /
  FRESHNESS / RELATIVE_STRENGTH / PARTICIPATION / SECTOR_CONFIRMATION /
  INSTITUTION_CONFIRMATION / PRICE_STRUCTURE）+ freshness state + role/tracking_state
  （輔助調整，非唯一決定）判斷 `READY`/`SETUP`/`RESERVE`。`EMERGING_MOMENTUM`/
  `UNCLASSIFIED_MOMENTUM` 預設 `RESERVE`，只有證據足夠強才升級（不可只因排名改善或
  base eligible 就 READY）；`EXTENDED_3D` 等 risk warning **不自動降級**（EXTENDED ≠
  FAILED）；`tracking_state=DETERIORATING` 強制 `RESERVE`。

**Pipeline 接線**（`pipeline_v2.run_phase2_pipeline`，regime gate 之後）：新增
`WATCH_QUALITY_MODE` 環境變數（`off`/`shadow`/**`shadow`（預設）**/`production`），
沿用本專案「新 gate 先 shadow 觀察，滿意後再切 production」慣例：
- `off`：完全不算（行為與加這層之前逐 byte 相同）
- `shadow`（**預設**）：照算 freshness/watch_quality，寫進 explain_trace/funnel_metrics
  供觀察，但**不過濾**送進 LLM 的候選（`llm_eligible` = 全部 regime gate 存活者，與
  cutover 前行為一致）
- `production`：只有 `READY`/`SETUP` 進 `llm_eligible`，`RESERVE` 保留在 `survivors`
  （供 debug / 未來 re-entry 觀察）但不送 LLM

`run_phase2_pipeline()` 回傳新增 `llm_eligible`（真正該送 LLM 的子集）與
`watch_quality_mode`；`survivors` key 語意不變（向後相容既有 caller）。`pipeline.py`
的 production 分支從讀 `phase2_result["survivors"]` 改讀 `phase2_result["llm_eligible"]`
建 `_cap_llm_input`。`explain_trace.py` 新增 `STAGE_WATCH_QUALITY` + `llm_eligible`
欄位；`RESERVE` 不計入 `first_exclusion_reason`（§37 RESERVE ≠ FAILED，只是「今天證據
不足以進正式 WATCH」，明天可重新升級，逐日重算天然支援 re-entry）。

**v6 prompt 同步更新**：新增 STEP 6.5「Final Quality 否決」+ 5 種 quality veto reason
（`INSUFFICIENT_CONFIRMATION`/`MOMENTUM_NOT_FRESH`/`WEAK_PARTICIPATION`/
`CATALYST_TOO_WEAK`/`EVIDENCE_NOT_COHERENT`），**只能在 `phase2_watch_quality_state`
有值時使用**、且必須引用 backend 提供的 `quality_evidence`/`momentum_freshness` 具體
欄位值，禁止自行判斷「今天下跌所以轉弱」；decision 輸出新增 `quality_assessment`
四維度整體判斷（`momentum_quality`/`participation_quality`/`catalyst_quality`/
`evidence_coherence`）。

**Deterministic-only replay（`run_phase25_replay_analysis.py`，不呼叫 OpenAI，2026-04-13
~ 2026-07-07 共 60 個交易日，N=617 去重候選，10 交易日遠期報酬 evaluation-only）**——
本輪最重要的誠實發現，記錄於
[docs/plans/phase25_future_recommendations.md](docs/plans/phase25_future_recommendations.md)
第 3 節：

- 全體去重候選（regime gate 存活者）：正報酬率 58.2%、平均報酬 +4.81%、跌超 10% 比例
  10.7%（regime 分布 BULL_TREND 32 天 / VOLATILE_RANGE 22 天 / RISK_OFF 僅 6 天，
  偏多頭視窗）
- **現行門檻下 `RESERVE` 只攔下 11/617（1.8%）候選，且這一小群事後平均報酬反而是
  +15.68%（優於全體平均）**；對 66 檔真正大虧股（跌超 10%）只抓到 0 檔——目前的 7 項
  evidence family 在這個視窗裡幾乎沒有區分力
- 離線試算更嚴格門檻（`ready_min=6, setup_min=5`）：RESERVE 擴大到 21.9%、可攔到
  18.2% 大虧股，但會把 regression 案例 8039 也推到 RESERVE，且 RESERVE cohort 事後
  平均報酬仍是正的 +5.71%——找不到能同時「顯著降低左尾」又「不誤殺真贏家」的門檻組合
- **根因假設**：候選池本身已是動能/法人資金篩選過的子集，本次設計的 evidence family
  與「已經進入候選池」高度相關，天生區分力有限；且視窗以多頭/震盪為主，RISK_OFF 樣本
  太少，無法驗證 spec 原始關切的「退潮盤品質層是否有用」情境
- **決策**：維持程式碼內建工程起始門檻不變（不為了讓數字好看硬調）；
  `WATCH_QUALITY_MODE` 維持 `shadow`（現行預設），**不建議近期切換至 `production`**
- 6505/8039/6414/1810 四檔 regression winner 在整個 60 天視窗中**從未被推到 RESERVE**

**測試**：28 個新測試（momentum_freshness 12 / watch_quality 13 / pipeline wiring
模式 4）全 pass；全 backend suite 1065 pass / 20 fail（既有 baseline，零新增失敗）。

**尚未做（下一輪接手指引）**：
1. `WATCH_QUALITY_MODE` 維持 `shadow`，**不建議近期切 `production`**（見上方 replay
   結論）——除非重新設計 evidence family 或補齊 RISK_OFF 樣本後重跑
2. 若要讓這層真正發揮作用，下一輪應該重新設計 evidence family 本身（例如接入 M25
   peer_rank 機制），而非調整既有門檻數字——本次已證實調門檻無法解決根本的區分力問題
3. Quality veto reason 的 LLM 遵循度需要 production 觀察（見
   phase25_future_recommendations.md 第 4 點）
4. `exclusions.is_etf()` regex 對齊（見 phase25_future_recommendations.md 第 2 點）

## Phase 1：魚尾 Canonical Market Classification System 完成（2026-07-21）

> **交付檔案目錄**：[docs/plans/canonical_classification/](docs/plans/canonical_classification/)
> （current_industry_data_flow / canonical_sector_taxonomy.json / stock_sector_mapping.csv /
> etf_classification.csv / sector_mapping_manual_review.csv / catch_all_remap_report.csv /
> sector_mapping_validation_report.md / future_phase2_recommendations.md）

### 背景與範圍
- 起因：追查漢翔（2634）/台虹（8039）/台化（1326）/長榮 vs 星宇等案例時發現，
  FinMind `industry_name` 對這些股票分組嚴重失真（漢翔掛「其他」、台虹沒有 PCB 細分、
  台化混在「紡織」）——魚尾選股的產業層 RS 條件因此系統性冤枉部分強勢股（見對話中
  漢翔/台虹/台化/長榮/星宇的逐關重放分析）
- Phase 1 **只做顯示層**：建 canonical 分類（primary_sector/sub_sector + ETF taxonomy）+
  DB + API + 前端顯示；**完全沒有**修改魚尾選股 pipeline（`app/signals/*` 零 diff，
  `industry_daily_flow`/L0-L1 產業排行零改動）。Phase 2 才會決定是否讓選股邏輯吃
  canonical 分類

### 落地內容
- **新模組 [backend/app/classification/](backend/app/classification/)**：
  - `taxonomy.py`：49 個 `primary_sector`（不照抄 TWSE 33 類，consolidate FinMind 重複命名
    如 `半導體`/`半導體業`；金融統一 `primary_sector=FINANCIAL` + 6 個固定 sub_sector）
  - `asset_type.py`：`COMMON_STOCK/ETF/ETN/PREFERRED_STOCK/DR/REIT/INDEX_BENCHMARK/OTHER`
  - `industry_mapping.py`：raw `industry_name`（含歷史重複命名批次）→ primary_sector 系統性
    映射；混雜類別標 `NEEDS_OVERRIDE` 交個股層處理
  - `stock_overrides.py`：~260 檔個股層 override（其他 117 + 電子工業 42 + TDR 36 + 金融
    72 + 汽車工業/食品生技/化學生技醫療等混合類別 + 5 個 regression case）；沿用既有市場
    知識分類，無把握者誠實標 `confidence=LOW` + `review_required`（非逐檔即時網路查證）
  - `etf_mapping.py`：ETF/ETN 關鍵字規則引擎（region/asset_class/strategy/themes，槓桿反向
    後綴 L/R、主動式前綴「主動」、平衡型「平衡」）+ 20 檔旗艦 ETF override
  - `build.py`：`classify_security()` / `classify_all()` 整合入口
- **新表**（`backend/app/models.py`）：`security_classification`（1321 筆）+
  `etf_classification`（292 筆）；`main.py` lifespan `_ensure_classification_tables()`
  自動 idempotent 建表
- **Backfill**：`backend/run_classification_backfill.py`（`--dry-run` 可預覽不寫 DB）；
  同時輸出 Phase 1 全部交付 CSV/JSON/MD 到 `docs/plans/canonical_classification/`
- **Backend API**（additive，全部向後相容）：
  - 新 router `GET /api/classification/{stock_id}` / `GET /api/classification?stock_ids=`
  - `GET /api/stocks/{stock_id}/history` 加 `canonical` 欄位
  - `GET /api/signals/latest` / `snapshot/{date}` 的 watchlist/removed 每筆 item 加
    `canonical` 欄位（`_attach_canonical_classification` 用 item 的 `stock` key 查表，
    注意魚尾 watchlist item 的股票代號欄位是 `stock` 不是 `stock_id`）
- **前端**：新元件 `CanonicalSectorTag.tsx` + `classificationLabels.ts` 中文字典；掛在
  `StockChartDialog.tsx`（K 線 popup header）與 `DailySignalsPanel.tsx`（魚尾卡片 subtitle
  + 詳情 popup）；ETF/ETN 顯示 region/strategy/主題，不顯示公司產業

### 驗證結果
- 普通股分類覆蓋 1290 檔：HIGH 87.4% / MEDIUM 6.1% / LOW（review_required）6.5%
- 5 個 regression case（2634/1326/8039/2603/2646）+ 金融三子類 + ETF 全部驗證正確
- 全 backend test suite：932 pass / 20 fail（與既有 baseline 完全一致，zero 新增失敗）
- `app/signals/` 目錄 `git diff` 為零（選股 pipeline 完全未受影響）

### Gotcha
- **ETF 判斷 regex 曾漏判 ~130 檔**：舊 pattern 只吃純數字（`^00\d{2,}$`），2023 年後新
  掛牌的主動式/槓桿反向/平衡型 ETF 用字母後綴（`00400A`/`00631L`/`00981T`）不會命中；
  已修正為 `^00\d{2,6}[A-Za-z]?$`。**未來若 FinMind 資料源新增其他 ETF 代號慣例，
  先檢查這個 regex**
  - **`智慧電網`（19 檔）與 `再生醫療`（4 檔）曾完全遺漏**在 `industry_mapping.py`
  對照表外——這兩個 `industry_name` 在初版統計清單裡就存在，純粹是撰寫時漏看；dry-run
  後才被 `catch_all_remap_report.csv` 的「generic fallback」訊號揪出。**新增
  industry_name 對照時，務必對照 `stocks_master` 實際 distinct 值全表，不要只挑「看起來
  眼熟」的**
  - **魚尾 watchlist item 的股票代號欄位是 `stock` 不是 `stock_id`**：與其他多數 schema
  不同，串接時容易寫錯 key 名稱導致查表永遠回 None（本次開發時就踩過一次）
  - **`stocks_master.industry_name` 永遠不能覆寫**：`source_industry` 就是它的原樣快照；
  canonical 分類存在獨立表，兩者平行不交會

## 魚尾 v2.2 × watch-list-stock-v5 結合（2026-07-16）

> **Canonical spec + 進度**：[docs/plans/fishtail_momentum_upgrade_spec.md](docs/plans/fishtail_momentum_upgrade_spec.md)（v2.1 / 資料前置 / v2.2 全完成；v2.3 待開始）
> **v5 prompt**：[backend/app/prompts/watch-list-stock-v5.md](backend/app/prompts/watch-list-stock-v5.md)（PROMPT_VERSION=v5，所有 regime 預設走 v5；v1/v4 保留給對照實驗）
> **改版前後完整差異**：[docs/plans/fishtail_v5_before_after_diff.md](docs/plans/fishtail_v5_before_after_diff.md)（候選池 / 特徵 / 分類 / gate / LLM 決策權 / 資料層逐項對照 + 7/15 實測）

### v5 設計（與 backend 的分工契約）
- **價格動能 / 相對強度是第一優先**；題材與法人只是確認訊號（核心原則 16~22）
- LLM 判斷順序固定：Momentum Gate（STEP 7.8，score<50 / rs<40 等原則 REMOVE）→
  Regime Gate（STEP 8，三態各自 WATCH 硬條件）→ Risk Cap（STEP 7.5）→ 題材驗證
- `momentum_signals` / `deterministic_signals` / `market_regime` / `regime_conviction` /
  `tracking_status` **全部 backend deterministic，LLM 只能原樣採用**；缺值 = 資料不足不可幻想
- STEP 0 改「只查外部風險背景」（external_risk_context），盤勢判斷完全交 backend；
  `market_state` 變 legacy 欄位固定 `BACKEND_REGIME_AUTHORITATIVE`

### backend 補齊內容（本輪）
1. **momentum_signals**（[momentum.py](backend/app/signals/momentum.py)）：新增
   `atr_pct_14d`（TR14 均 / 收盤 ×100）、`up_down_volume_ratio_20d`（漲日量/跌日量，
   無跌日 → None）、`momentum_grade`（A>=75/B>=60/C>=45/D）、`momentum_phase`
   （優先序 weakening > extended > accelerating > trending > emerging；rs percentile
   缺值 → None）；`build_momentum_signals()` 以 **v5 命名** 組 nested dict
   （`rs_rank_change_5d` ← rs_rank_improvement_5d、`distance_to_high_20d_pct` ←
   distance_to_20d_high…），candidate_pool 掛進每筆候選 → llm_caller
   `_momentum_signals_view` 看到現成 dict 直接用（不落入它的 fallback 對照）
2. **market_breadth.py**（新模組，spec §7.1/§7.2）：從 momentum frame 的 `_` 內部欄位
   聚合（`_above_ma20/_above_ma60/_ret_1d/_new_high_20d/_new_low_20d`），**不重複查
   全市場**；`breadth_score` 0~100（權重 MA20 30% / MA60 20% / AD 20% / 新高低 15% /
   強產業比 15%，子項缺值以中性 50 計）；樣本 <100 → 全 None（breadth 不可信 → 不加嚴）
3. **deterministic_signals.py**（新模組；v5 STEP 6/7/7.5 後端化，補 M27 refinement #6
   「延後」債）：8 欄位全 deterministic；EXCLUDE=（distribution+法人反轉）或
   （failed_rotation+weakening）；MAX_B=散戶過熱/急拉追高；任一旗標=DOWNGRADE；
   `theme_maturity` 刻意不做（需外部資訊，v5 對缺欄位有 LLM fallback）
4. **episode（spec §7.4）**：`_episode_counts`——未命中 >=5 交易日 → 新 episode、
   4 天模糊帶歸同 episode；`consecutive_hit_count`（最新 episode 內命中數）/
   `independent_hit_count`（episode 總數）進 tracking_status + signal_metrics；
   無 schema 改動（on-the-fly 從 signal_watch_hits 算）
5. **regime gate v2.2**（spec §7.3）：pipeline 算 breadth →
   `resolve_regime_detail`（BULL + breadth<50 → NARROW_BULL）→
   `apply_regime_gate(..., regime_detail=)`：BROAD_BULL 剔 score<50；NARROW_BULL 只留
   （LEADER 且 score>=65）或（score>=70 且無 distribution）；VOLATILE 加剔
   RS 排名 5 日掉 >50 名；BULL 高信心新增 score>=75 且 independent_hit>=2 路徑
6. **candidate_pool**：新增 `industry_flow_1d/3d`（industry_daily_flow 聚合，
   canonical 名稱 normalize 後比對）給 sector_rotation_status；
   `build_candidate_pool` 加 optional `momentum_frame` param（pipeline 先算一次
   與 breadth 共用；未傳自算，測試向後相容）

### 前端動能顯示（2026-07-17）
- [api.ts](frontend/src/lib/api.ts)：`SignalMomentumBlock`（v5 LLM 回填含 momentum_reason）+
  `SignalMetrics`（deterministic）型別；`SignalWatchlistItem.momentum / signal_metrics`、
  `SignalMarketContext.breadth_score / market_regime_detail`
- [DailySignalsPanel.tsx](frontend/src/components/DailySignalsPanel.tsx)：
  - `MomentumChip`（卡片：「動能 A・82」，grade 配色 A 綠/B 藍/C 琥珀/D 紅）
  - `MomentumPanel`（popup：分數+grade+phase chip、6 格 metric（RS 大盤/產業百分位、
    排名 5 日變化、20 日報酬、距高點、趨勢效率/ATR）、v5 的 4 條 momentum_reason bullet）
  - `BreadthChip`（header：「廣度 55」，>=60 綠 / <45 紅 / 中間琥珀）
  - phase 中文：emerging 啟動 / accelerating 加速 / trending 趨勢延續 / extended 過熱 / weakening 轉弱
- **資料優先序**：`resolveMomentum` 先吃 deterministic `signal_metrics`、缺值再吃 LLM
  `momentum` 區塊；兩者皆無（v5 之前舊快照）→ chip / panel 整個不渲染（向後相容）
- 30 日追蹤頁尚未顯示 momentum（archive API 未回 signal_metrics，屬後端 serializer 改動，留待下一輪）

### Gotcha
- **4 態 regime 只存在 deterministic gate / snapshot 觀察欄位**：對 LLM 的
  `market_context.market_regime` 契約維持 3 態（v5 enum 固定），
  `market_regime_detail`（BROAD_BULL/NARROW_BULL）存 market_context 與
  `signal_metrics.market_regime_detail`，30 日追蹤可歸因
- **spec §7.5「LLM facts-only」不實作**：v5 的 deterministic Risk Cap + Momentum Gate
  已把決策上限交給程式，LLM 只在 cap 內判斷——同 intent 不同機制，別再重做
- **momentum_phase 的 residual 落在 emerging**：中後段停滯股會被標 emerging，
  靠 score gate 把關品質（emerging 不代表可 WATCH）
- **breadth 樣本 guard**：`MIN_SAMPLES_FOR_BREADTH=100`；in-memory 測試要 seed 100+ 檔
  或直接測 pure function
- **pipeline 測試 stub**：`build_candidate_pool` 的 monkeypatch lambda 要收 `**kw`
  （momentum_frame kwarg）
- 全 suite 930 pass / 20 fail = 既有 baseline（site-passwordless），零新增失敗

## 魚尾 v2.2 資料前置：營收 ETL 修復 + 基本面動能 D 通道 + 市值 ETL（2026-07-15 第二輪）

### monthly_revenue ETL 缺口修復（根因兩層）
- **症狀**：monthly_revenue 最新只到 2026-03、04~06 全空、02/03 只有 ~837 檔（正常 ~1075）
- **根因 1**：FinMind `TaiwanStockMonthRevenue` 把「N 月營收」**全部掛在「N+1 月 1 號」單一 date key**（6 月營收 2316 檔全是 date=2026-07-01；date **不是**公告日），且公司次月上旬陸續公告、FinMind 陸續補進同一個 key
- **根因 2**：該 dataset 的 v4 **dataset-level fetch 只回 start_date 當日資料**（實測 2026-01-01~04-01 只回 date=1/1 的 2282 筆；與 margin_trade 同款陷阱）
- 舊 daily ETL 用 start=end=target_date 單日抓 → 只有「每月 1 號恰為交易日且 ETL 成功」抓得到、且只抓到當天已公告的少數公司，之後永遠不回補
- **修法**（[backend/etl/finmind_monthly_revenue_sdk.py](backend/etl/finmind_monthly_revenue_sdk.py)）：從 start_date 回看 45 天，對範圍內**每個「月 1 號」key 各打一次**（start=end=key）；daily 模式 = 2 key = 2 quota/日；每日重抓 + upsert 冪等 → 公告陸續自動補齊。helper `_month_first_days_between` + regression test 鎖行為
- **回補已執行**：remote 2026-01~06 每月 1072~1083 檔、yoy/mom 100% 有值

### 基本面動能 D 通道上線（spec §6.1 D）
- **available_date 規則**（[momentum.py](backend/app/signals/momentum.py) `revenue_available_date`）：**revenue_month 次月 10 日**（台灣法規公告截止日）；不加 DB 欄位、不改 ETL。frame 只吃 `available_date <= target_date` 的月份 → 無資料穿越。**`ingested_at` 不可當 proxy**（backfill 的 ingested_at 是回補時間）
- frame 新欄位：revenue_yoy / revenue_mom / revenue_yoy_acceleration / revenue_yoy_accel_2m / revenue_yoy_turned_positive / revenue_yoy_percentile / revenue_yoy_industry_percentile / revenue_month_used（audit）
- D 通道條件（任一）：yoy > 15 且連兩月加速 / yoy 由負轉正 / 產業內 yoy percentile >= 80；**上限 CHANNEL_D_LIMIT=20**；候選 flag `in_fundamental_pool`
- momentum_score 基本面 10 分啟用：`10 × (0.6 × yoy_percentile/100 + 0.2 × 加速為正 + 0.2 × mom 為正)`；無營收資料 → detail.fundamental=None（貢獻 0）
- signal_metrics 增存 revenue_yoy / revenue_yoy_acceleration / revenue_month_used

### 發行股數 / 市值 ETL（spec §4.2 institution_buy_to_market_cap 前置）
- **資料源**：FinMind `TaiwanStockShareholding` 的 `NumberOfSharesIssued`（dataset-level 單日全市場 2357 檔、1 quota/日；**只回 start_date 當日**，同 margin_trade）
- 新表 `stock_shares_outstanding (trade_date, stock_id, shares_issued, foreign_shares_ratio)`（[models.py](backend/app/models.py)；`_ensure_m23_tables` lifespan 自動建）
- 新 ETL：[backend/etl/finmind_shareholding_sdk.py](backend/etl/finmind_shareholding_sdk.py)（鏡像 margin 模組；多日 backfill 逐 daily_price 交易日呼叫）+ client `fetch_shareholding_dataset` + `run_finmind_etl_sdk.py` **step 8（non-CRITICAL，已進 DEFAULT_STEPS）**
- **回補已執行**：remote 2026-07-01~14 共 9 交易日 × 1350 檔 = 12,150 筆
- momentum frame 新欄位：`shares_issued / market_cap（shares × close）/ institution_buy_to_market_cap_2d`——**只出欄位，不進 momentum_score、不進分類門檻**（spec §6.1 A 延後項；等資料累積後再決定啟用）；快照缺日往回找 10 天內最近一筆

### Gotcha
- **FinMind dataset-level「只回 start_date」名單擴大**：margin_trade、TaiwanStockMonthRevenue、TaiwanStockShareholding 都是。新接 dataset 前先實測（拉一段區間看 distinct date）
- **月營收 date ≠ 公告日**：date 是「營收次月 1 號」佔位鍵；要時間對齊一律用 `revenue_available_date`（次月 10 日）
- ETL raw SQL 的 trade_date 在 PostgreSQL 回 date、SQLite（測試）回 str：`d.strftime(...) if hasattr(d, "strftime") else str(d)`
- 全 suite 20 個 pre-existing fail baseline 不變；本輪新增 shareholding 4 + momentum 9 + monthly_revenue 2 測試全綠

## 魚尾 v2.1 動能選股升級（2026-07-15）

> **Canonical spec + 實作進度**：[docs/plans/fishtail_momentum_upgrade_spec.md](docs/plans/fishtail_momentum_upgrade_spec.md)（v2.1 完成 / v2.2 v2.3 待開始）

### 需求
- 把魚尾從「法人異常訊號主導」升級成「動能選股系統」；解「法人有買但股票本身不強」的問題
- 三階段：v2.1 動能特徵 + momentum_score（本輪）→ v2.2 breadth/regime 四態 + episode → v2.3 回測持有管理

### v2.1 落地內容
- **新模組 [backend/app/signals/momentum.py](backend/app/signals/momentum.py)**：
  - `compute_market_momentum_frame(db, target_date, masters)`：全市場（active、非 ETF/金融/黑名單，與候選池排除規則一致）近 66 交易日特徵：return_5d/20d/60d、rs_market_percentile_20d、rs_industry_percentile_20d、rs_rank_improvement_5d（1=最強的名次制，正值=進步）、distance_to_20d/60d_high（收盤 rolling high，0=創新高）、distance_to_ma20、trend_efficiency_20d、institution_buy_to_turnover_2d + 市場 percentile、industry_rs_percentile_20d（產業層級）
  - `select_momentum_candidates(frame)`：候選池 B（價格動能）/ C（動能加速）通道；**上限 B=40 / C=20**（spec 未定，工程決策：rs>=85 在 ~1800 檔 universe 會命中 200+ 檔，不 cap 會灌爆 POOL_HARD_LIMIT=120）
  - `compute_momentum_score(candidate)`：0~100，percentile-based；權重 價格 30 / RS 25 / 法人 20 / 量價 15 / 基本面 10（**v2.1 恆 0**，monthly_revenue 缺 announcement_date 不接主流程避免資料穿越）；風險扣分（爆量長上影 -10 / RS 排名 5 日掉 200+ -10 / 3 日漲幅 >12% -5）
  - `build_signal_metrics(candidate, regime_info)`：spec §9.2 第一批欄位 → JSON（全 float/str/None，無 date 物件）
- **candidate_pool.py**：四通道聯集（A 法人既有 + B + C；D 未上線）；每檔 merge frame 特徵（`_` 開頭中間欄位過濾掉）+ `in_price_momentum_pool` / `in_acceleration_pool` flag + momentum_score；截斷排序從 flow_3d proxy 改 momentum_score
- **classification.py**：v2.1 規則重寫；`LAGGARD_CANDIDATE` 改名 `ROTATION_LAGGARD`（`PRELIM_TYPE_LAGGARD_CANDIDATE` 保留為 alias，值同 `"ROTATION_LAGGARD"`）
  - LEADER：industry_rs_percentile>=70 + 產業內 RS>=80 + score>=70 + （連買 2 日 OR inst_buy_to_turnover percentile>=80）+ 量比>=1.3 + 距 20 日高點 <=3%
  - FOLLOWER：同產業有 LEADER + score 55~69（`< 70` 上界）+ 5 日漲幅低於 LEADER + rs_rank_improvement>0 + 3d 法人正 + 無爆量長上影
  - ROTATION_LAGGARD：同產業有 LEADER + 產業強勢（industry_rs>=70 或 in_top_industries_3d）+ 20 日落後產業平均>=5pct + RS 改善 + （法人 1d 轉正且 5d<=0 或 vol_1d/5d>1.2）+ （站回 10MA 或收盤創 20 日新高）+ score>=50
- **filters.py**（spec §6.4）：hard exclusion #8 `rs_market_percentile_20d<40 且 rs_rank_improvement_5d<=0`；regime gate 震盪盤 `momentum_score<60` 剔除、退潮盤 `rs_market_percentile_20d<90` 剔除（三者缺值都不觸發，向後相容）
- **persistence**：`SignalWatchHit.signal_metrics` JSON column（nullable；`signal_watch_schema.py` idempotent ALTER）；pipeline 在 regime gate 後建 `signal_metrics_by_stock`，assemble 後 deterministic 蓋回每筆 watchlist item → snapshot JSON + watch hit 都存
- **llm_caller / pipeline**：`_normalize_prelim_type` 接受 `ROTATION_LAGGARD` → 對外仍映射 `LAGGARD`（前端 領漲/跟漲/補漲 標籤零改動）；LLM prompt / evidence card **刻意未動**（spec §10 Step 7 最後才改）

### Gotcha
- **percentile 樣本 guard**：全市場 >=20 檔、產業內 >=3 檔、產業數 >=5 才算 percentile，不足回 None → B/C 通道與新分類條件自動不觸發。既有小樣本測試因此不受影響；新整合測試要 seed >=20 檔（見 `_seed_momentum_market`）
- **market benchmark 用 universe 中位數**（非 TAIEX）：percentile 對常數 benchmark 平移不變，rs_market_percentile_20d 數學上 = return_20d 全市場 percentile；TAIEX 不在 stocks_master 也不用特別撈
- **rolling high 用收盤價**（非盤中 high）：`distance_to_20d_high >= 0` = 收盤創 20 日新高；LEADER 的 `<=3%` 與 LAGGARD 的突破條件都以此為準
- **缺資料股 score 偏低是刻意的**：momentum_score 缺 percentile 子項給 0 分（不硬給中性 50）→ 新上市/資料缺漏股在震盪盤 score gate（>=60）自然被擋；但 hard/regime gate 對「單一欄位缺值」不觸發剔除（沿用資料缺漏不清池慣例）
- **prompt_version 未 bump**：本輪只動 deterministic 選股層、prompt 檔案零改動；30 日追蹤要歸因 v2.1 前後差異，用 `signal_watch_hits.signal_metrics` 是否為 NULL 區分（v2.1 上線後的 hit 都有 momentum_score）
- **`momentum_score` 對 frame 的 `_return_20d_prev5` / `_volume_5d_to_60d` 等 `_` 開頭欄位**：僅 frame 內部使用，merge 進 candidate 時被過濾，不會進 snapshot
- **測試 baseline**：全 backend suite 20 個 pre-existing fail（site-passwordless 未同步的 auth/watchlist/rate-limit/test_database）與本輪無關；v2.1 改動 0 新增失敗

### v2.2 / v2.3 待辦（給接手的人）
- v2.2：新模組 `market_breadth.py`（pct_above_ma20/60、advance_decline、new high/low count；universe 與 candidate pool 一致）→ regime 升四態（BROAD_BULL / NARROW_BULL）→ §7.3 score-based 收斂 → `hit_count` 拆 episode（`consecutive_hit_count` / `independent_hit_count`，命中間隔 <=3 交易日同 episode、>=5 日未命中才算新 episode；**會動到 archive.py carry 邏輯，小心 autoflush=False 的舊坑**）→ LLM facts-only + 程式 final_score 決策
- v2.2 前置：`build_signal_metrics` 已留 `breadth_score: None` 佔位；市場 regime 詳情已存 `signal_metrics.market_regime_detail`
- 基本面動能（§6.1 D）上線前必須先給 `monthly_revenue` 補 `announcement_date` 或 `available_date`（spec §9.4），否則有公告前偷看風險
- `institution_buy_to_market_cap` 延後：缺流通市值欄位；已用 `institution_buy_to_turnover_2d` 替代

## K 線圖全站改 popup（2026-07-14）

### 需求
- K 線圖固定只顯示**近 6 個月**，因此拿掉日期選擇（1M/3M/6M/1Y/All + 自訂區間）與自訂 MA 輸入
- K 線圖**全站改 popup**（含 L2 個股頁、回測頁），圖上不再有 dataZoom 拖拉
- 保留 10/20/60 MA 與外資/投信/自營累積買超線的 toggle

### 新元件 [frontend/src/components/StockChartDialog.tsx](frontend/src/components/StockChartDialog.tsx)
- base-ui Dialog（樣式比照 DailySignalsPanel 的 SignalDetailDialog）；props `{ stockId: string | null, stockName?, onClose }`，`stockId=null` = 關閉
- **抓 365 天、顯示切近 6 個月**：MA 在完整序列上計算再 slice，讓 MA60 在窗口第一天就有值（只抓 180 天會讓 MA60 前 3 個月全 null）
- **法人累積線 re-baseline**：切窗後扣掉「窗口前一天」的累積值，讓線代表「顯示窗內累積」從 0 附近起算
- ECharts 用 `dynamic(() => import("echarts-for-react"), { ssr: false })` 只在 popup 開啟時載入
- 即時報價保留在 popup header；`useRealtimeQuotes(stockId ? [stockId] : [])` 關閉時不打 API
- 無 dataZoom、無 broker 副圖、無自訂 MA / 日期選擇

### 各入口改動
- **L2 個股頁**：常駐 `<StockChart>` 移除；header 加「K線圖（近 6 個月）」按鈕 + `StockSignalSummaryPanel` 的「看 K 線圖」改吃 `onOpenChart` callback 觸發同一 popup。`?chart_days=` URL param 與回測 `?start=&end=` dateRange 連動一併移除；**FinancialsPanel 的 `chartDays` 固定 180**（原本跟 K 線天數連動）
- **回測頁**：上方常駐 StockChart 移除，header 右側加 K線圖按鈕；`backtestRange` state / `onDateRangeChange` 連動 / handleBack 帶 start&end 全拔（BacktestPanel 的 `onDateRangeChange` prop 是 optional，元件本身沒動）
- **30 日追蹤頁**：詳情 popup 內與紀錄卡片的「K線圖」Link 改為開 StockChartDialog（詳情 popup 內另補「個股頁 →」link 保留 L2 導航）；chart popup 疊在詳情 popup 之上（兩個都 z-50，後 mount 的 portal 在 DOM 後面 → 蓋上面）

### 手機版調整（同日第二輪，使用者反映「有點窄」）
- popup 手機（< sm）改 `inset-0` 全螢幕、內距縮小；桌機維持置中卡片
- 手機拔掉圖表外框/內距 + **左側股價 y 軸刻度整排隱藏**（`grid.left` 38 → 8，數值靠 tooltip，使用者要「有線看趨勢就好」）；右側累積張數刻度保留
- 手機圖高 52vh → 62vh

### 手機橫向捲動（第三輪）
- 繪圖內容手機做寬到 `w-[150vw]`，外層 `overflow-x-auto` 讓使用者左右滑；載入完成後 `scrollLeft = scrollWidth` 預設捲到最右（最新 K 棒）；≥sm 恢復 `w-full` 無捲動

### prod daily_price 2026-02-18 髒資料刪除（2026-07-14）
- **症狀**：K 線 popup 上 2/18 有一根價格暴跌的 K 棒把 y 軸拉爆（2330 那天寫 227 元，前後日都 ~1900）
- **根因**：2026-02-18 是春節休市日（資料 2/12~2/20 全空、TWSE MI_INDEX 當天回「沒有符合條件的資料」），但 `daily_price` 有 1020 筆 `source='twse'` 的**代號錯位**垃圾（2330 的 227 元實為 0057 的價格；63% 檔偏離前後日 >30%）— 舊 TWSE parser 寫入
- **修法**：直接 `DELETE FROM daily_price WHERE trade_date='2026-02-18'`（使用者確認後執行，1020 筆）；其他表（inst_flow / valuation / margin / broker）那天本來就 0 筆不需處理
- **順帶影響（皆為修正）**：追蹤天數計算（`_count_tracking_days` 數 daily_price distinct trade_date）、`_resolve_nth_trade_date`、回測都不再把 2/18 當交易日
- **教訓**：發現 K 線莫名深 V／天量偏離時，先懷疑「休市日被寫入錯位資料」；驗證法 = 對照前後交易日收盤偏離比例 + 檢查該日是否落在連續空窗（假期）內

### Gotcha
- **`StockChart.tsx` 保留未刪**（依 BrokerPanel 慣例：程式碼保留、只拔入口）；現在全站無人 import，未來要復活完整互動圖直接掛回
- **StockSignalSummaryPanel 的 `onOpenChart` 是 optional**：沒傳時 fallback 回原本 `#stock-chart` 錨點連結（但 L2 已無該錨點，實際上 L2 一定會傳）
- L2 舊書籤帶 `?chart_days=` / `?start=&end=` 不會壞，params 被忽略

## 30 日追蹤頁分類顯示大改版（2026-07-13）

### 需求
- 追蹤中清單一次 show 全部太多；改成 **4 個互斥分類 chip**，一次只顯示一種排序、各只顯示**前 15 名**：
  - 追蹤日期（first_seen_date 最早在前）/ 最多報酬率（return_pct desc）/ 最低報酬率（return_pct asc）/ 抓到次數（hit_count desc）
- 卡片極簡化：只留**代號+名稱 / 收盤價 / 當日漲跌幅**；其餘所有資訊（類型/版本 chip、預測價、最大正負報酬、報告時間軸…）移進每檔的「查看更多」**popup**（base-ui Dialog，比照 DailySignalsPanel 的 SignalDetailDialog 樣式）
- 每類清單底部「查看更多（共 N 檔）」展開全部；「追蹤期滿移出紀錄」維持卡片網格但**預設收合**

### 後端（[backend/app/signals/archive.py](backend/app/signals/archive.py)）
- `ArchiveSummaryItem` 新增 `latest_close_price` / `daily_change_pct`（**當日漲跌幅**，相對前一交易日收盤）
- 新 helper `_load_latest_close_and_change(db, stock_ids, as_of_trade_date)`：兩個輕量查詢（先找 as_of 的前一個全市場交易日，再一次撈兩天 close_price batch）；個股 as_of 停牌 → (None, None)、前一日缺值或 0 → 漲跌幅 None
- `list_archive_summary` + `get_archive_detail` 都會填；router `SignalArchiveSummaryItemResponse` 加同名 Optional 欄位（detail response 繼承自動帶入）
- **為何不用 `latest_eval_price`**：它在 baseline 未建立（第一天抓到）時是 None，卡片收盤價必須任何追蹤日都有值

### 前端（[frontend/src/app/signals/archive/page.tsx](frontend/src/app/signals/archive/page.tsx) 大重寫）
- 分類排序**純前端**（一次 `fetchSignalArchive({ limit: 0 })`，client-side sort）；原本打後端的 6 選項排序下拉（`sort_by` URL param）**移除**，改 URL param `?view=`（`first_seen`(default 不寫 URL) / `return_desc` / `return_asc` / `hit_count`）
- null return_pct（第一天抓到）在兩種報酬排序都用 `?? ±Infinity` 排最後
- 搜尋框保留：輸入時**忽略前 15 限制直接搜全部**；「查看更多」展開狀態在切分類時 reset（`useEffect([view])`）
- popup：`StockDetailDialog`（`@base-ui/react/dialog`），資料 header/metrics 直接吃 summary item（開窗即顯示），報告時間軸等 `fetchSignalArchiveDetail` 載入；`popupStockId` state 取代原 inline expand 的 `selectedStockId`
- 紀錄區 `completedCollapsed` 預設 `useState(true)`；localStorage 只在存過 `"false"`（使用者展開過）才自動展開

### Gotcha
- **completed fetch effect 的 deps 刻意只有 `[selectedPeriodStart]`**：`setSelectedPeriodStart` 的 identity 隨任何 URL 變動（含點分類 chip）改變，放進 deps 會讓切分類時 completed 區白白重新 fetch；加 eslint-disable 註記
- 極簡卡片整張是 `<button>` 開 popup；popup 內 K線圖入口保留
- 舊 `?sort_by=` URL 不再被讀取（向後不相容，直接落回預設分類）；`SignalArchiveSortBy` 型別與 API 端 `sort_by` param 仍在（後端契約沒動）

## 30 日追蹤頁響應式卡片改版（2026-07-09）

### 背景
- 使用者手機上看 `/signals/archive` 要一直左右滑（active 12 欄 / completed 10 欄大表，手機寬度必然橫向捲動）
- 需求：「手機看跟電腦看的感覺一樣」，兩端都不必滑來滑去

### 修法（純前端，[frontend/src/app/signals/archive/page.tsx](frontend/src/app/signals/archive/page.tsx)）
- 兩張 `<Table>` 全部改成**響應式卡片網格**：`grid grid-cols-1 gap-3 md:grid-cols-2`（手機單欄堆疊 / 桌機兩欄），沿用自選清單 `WatchlistTradeQualityTable` 2026-05-03 的卡片前例
- 用 **CSS breakpoint 而非 UA 偵測**：同一份 markup 兩端共用，資訊結構完全一致，無判斷錯誤風險
- 卡片結構：header（股票名 + SignalTypeChip + VersionChip）→ 警示 chips → `Metric` 標籤/值小網格（手機 2 欄 / ≥sm 3 欄）→ footer 動作列（K線圖 + 展開報告）
- inline expand 保留：展開報告的卡片加 `col-span-full` 撐滿整列，報告時間軸 markup 原封不動搬進卡片內
- 新增小元件 `Metric({ label, children })`；active 卡的「首次/最近抓到」合併成一格 `formatShortDate(a) → formatShortDate(b)`
- 移除 `STICKY_FIRST_COL_*` 凍結欄 hack 與 `Table` import（卡片化後不再需要）

### Gotcha
- **active 清單「最新類型」從純文字改用 `SignalTypeChip`**：與 completed 表一致（chip 對未知值 fallback 顯示原文字，不會壞）
- **搜尋空結果從 colSpan row 改成置頂 `<p>`**：卡片網格沒有 colSpan 概念
- **展開卡 `col-span-full` 會讓後面卡片 reflow**：grid auto-flow 正常行為，一次只展開一檔所以視覺可接受
- 排序（sort_by）仍由後端決定順序，卡片依「左→右、上→下」閱讀順序呈現

### 第二輪：全站其餘表格手機收斂（同日 2026-07-09）
使用者確認「全改」後，把剩下會在手機遺失資料或橫向捲動的表格一次處理：
- **HotMoneyList**：產業 / 子產業 / 外資 / 投信 / 自營欄統一改 `hidden lg:table-cell`；< lg 時在「個股」格下方顯示 `產業 · 子產業` 小字 + `InstMiniLine`（外/投/自 三個帶色數值）；`#` 欄手機隱藏（`hidden sm:table-cell`）、清單欄 `w-16 lg:w-28` 省寬度。**原本 mobile 是直接看不到法人明細，現在資料不遺失**
- **IndustryDashboard**：外資/投信/自營欄維持 `hidden sm:table-cell`，新增共用 `InstMiniLine`（`sm:hidden`）塞在產業名稱與子產業名稱下方；手機仍可用 Tabs（合計/外資/投信/自營）換排序
- **StockList `SummaryTable`**（L1 子產業彙總）：原本 6 欄完全沒手機處理會橫向捲；外資/投信/自營欄加 `hidden sm:table-cell` + 名稱格下方小字列。手機犧牲那三欄的點頭排序（合計/趨勢排序仍在）
- **FinancialsPanel 財報矩陣**：`hidden sm:block` 保留桌機「項目 × 季度」矩陣；`sm:hidden` 改渲染「每季一張小卡」`grid grid-cols-2`（最新季在前，「基本每股盈餘」在卡內縮寫 EPS）
- **不動**：`KeyFactorsTimeline`（小圓點矩陣寬度小 + 已有 StickyHorizontalScroll）、`BacktestPanel`（無表格）、StockList 個股卡片 / DailySignalsPanel / watchlist 卡片（本來就響應式）
- Gotcha：HotMoneyList 的手機資訊列要先算 label 字串再決定 render（L1 情境 `industryName` 已知，industry 被 filter 掉後 sub 為 null 時不要渲染出多餘的「—」）

## 魚尾 30 日追蹤跨 cycle carry bug 修復（2026-07-08）

### 症狀
- 股票完成 30 個交易日 → 封存進歷史區 → 之後又被抓到時，新一輪 cycle 的最大正報酬 / 最大負報酬 / baseline 會**帶入上一輪的值**，兩個獨立事件被錯接成一段。

### Root cause（`autoflush=False` 專屬）
- `persist_signal_watch_hits`（[backend/app/signals/archive.py](backend/app/signals/archive.py)）舊順序：先 `_load_latest_return_state_by_stock` 載入 carry → 建立帶 carry 的新 hit（**pending insert，未 flush**）→ 才 `refresh_completed_signal_cycles` 封存刪除舊 cycle。
- production session 是 `autoflush=False`（[backend/app/database.py](backend/app/database.py)）；`refresh` 內 bulk `delete(synchronize_session=False)` **不會先 flush pending insert** → 刪不到完成日那筆新 hit → commit 後殘留成孤兒，帶著上一輪 baseline / 極值，變成新 cycle 種子。
- 既有測試用預設 `autoflush=True`（會意外先 flush 再刪）→ 遮住 bug。Regression test 必須顯式 `autoflush=False`。
- `synchronize_session="evaluate"` **對 pending insert 無效**（只處理 identity map 內 persistent 物件），光改 delete 參數救不了。

### 修法（兩處）
1. **`persist_signal_watch_hits` 把 `refresh_completed_signal_cycles` 移到載入 carry 之前**：完成 30 日的舊 cycle 先被封存刪除，carry 讀不到 → 完成日再被抓到視為全新獨立事件（first_seen=完成日、無 carry）。仍在 30 日窗內未完成的 cycle 不被刪，carry 照常延續同一段追蹤。順帶消除 pending-insert race（refresh 時尚無新 hit）。
2. **`refresh_completed_signal_cycles` 的 active-hits delete 從 `synchronize_session=False` → `"evaluate"`**：在 `update_signal_watch_returns` 路徑那些 row 上游已被改 dirty，autoflush=False 下 False 不移出 session → commit 對已刪 row 發 UPDATE → StaleDataError（同 M23 2026-06-15 早退修正類別）。

### 行為改變（更好）
- 完成日的 re-catch 現在同一天就以全新 cycle 出現（first_seen=完成日），不再遺失那天或延到隔天才 fresh。

### Regression test
- `test_persist_recatch_after_cycle_completion_starts_fresh_cycle`（[backend/tests/test_signal_archive_returns.py](backend/tests/test_signal_archive_returns.py)）：顯式 `autoflush=False`，seed 完成 cycle → 完成日 persist re-catch → 斷言新 hit baseline/max 皆 None + 舊 cycle 正確封存（帶自己這輪極值）。原 code 會 fail（新 hit 帶 baseline 2026-05-02）。

### Gotcha — production 既有髒資料不會自癒
- 本修法只擋未來。prod DB 內已被污染的 active cycle（tell-tale：`baseline_trade_date < 自己的 first_seen_date`）不會自我修正，`update_signal_watch_returns` 每日重算仍吃被污染的 baseline → 極值橫跨兩 cycle。
- 但 retention 只有 30 交易日，污染 cycle 會在 ~6 週內自然完成 / 汰換掉；若要立即修正需另寫一次性 cleanup（尚未做）。

## 工作流程規範

- 每次完成一輪修改後，**自動更新 README、CLAUDE.md、memory 並直接 commit & push**，不需要使用者每次重新提醒

## Claude Code Skills

- `.claude/skills/always-stock-backend/SKILL.md` — 後端開發規範（FinMind ETL 欄位對照 / bulk upsert / 回測引擎 / prompt 管理 / Daily Brief 模式 / L1 產業 fallback）。修改 `backend/` 下的 Python 檔案時自動觸發
- `.claude/skills/always-stock-frontend/SKILL.md` — 前端開發規範（時區日期 / Panel toggle / 圖表 null 處理 / API 邊界 / L2-L3 頁面結構 / TradeQuality 輸入）。修改 `frontend/` 下的檔案時自動觸發

## 部署相關文件

- `docs/architecture/architecture_overview.md` — 技術選擇、通訊方式、部署架構總覽
- `docs/operations/deployment_guide.md` — 日常部署操作手冊
- `infra/render/render.yaml.template` — Render blueprint 範本

## 專案概述

**always-stock**：台股產業別三大法人資金流向分析儀表板（原名 tw-stock-dashboard，2026-04 更名）。

## 技術堆疊
- **Backend**: FastAPI + SQLAlchemy, Python 3.9+
- **Frontend**: Next.js + Tailwind CSS + shadcn/ui + ECharts
- **DB**: PostgreSQL（Render Managed）；本地開發可用 SQLite（`backend/db/tw_stock.db`）
- **ETL 資料來源**: FinMind 為主（價、法人、估值、月營收、財報、券商分點、產業分類），TWSE/TPEX 僅保留備援或校驗；Fugle 已全面下線（2026-04-21）
- **Bot**: Telegram Bot（long-polling）+ OpenAI GPT 籌碼分析
- **排程**:
  - macOS launchd（本地）
  - Render Cron Job（雲端，週一至五）
  - GitHub Actions：`daily_etl_update.yml`（台北週一~五 18:00 全量 ETL）、`broker_trade_backfill.yml`（每小時 broker_trade_agg backfill）、`margin_trade_backfill.yml`（台北週一~五 22:30 補抓 margin_trade，自動掃描近 14 個交易日缺漏）
- **部署**: Render（後端 API + Bot + ETL + Postgres）+ Vercel（前端）

## FinMind 決策記憶

- 2026-04-11 起，專案方向已確認為「全面改用 FinMind 提供的資料重做」
- FinMind API 以 `https://api.finmindtrade.com/api/v4` 為主，使用 `Authorization: Bearer {token}`
- 依使用者提供的最新規格，token rate limit 為 `600 req/hour`，未帶 token 為 `300 req/hour`
- 若要維持目前這種「每日全市場 ETL」模式，實務上應規劃 `Backer` 或 `Sponsor`
- 若要取代目前 `broker_trade` 的 TWSE BSR parser，應優先使用：
  - `TaiwanStockTradingDailyReportSecIdAgg`：適合現有 BrokerPanel 聚合場景
  - `TaiwanStockTradingDailyReport`：適合未來逐價分點分析
- `TaiwanStockTradingDailyReport` / `TaiwanStockTradingDailyReportSecIdAgg` 為 `Sponsor` 資料，且歷史起點為 `2021-06-30`
- 切換 FinMind 後，資料庫主幹表大多可保留，但需要 migration：
  - `stocks_master` 保留並加 `market/source`
  - `daily_price` 保留並加 `spread/trading_turnover/source`
  - `inst_stock_flow` 保留，改以 FinMind `name` 映射 `foreign/trust/dealer`
  - `industry_daily_flow` 可沿用
  - `broker_trade` 可沿用，但建議未來拆 raw / agg
- 切換 FinMind 後應新增：
  - `daily_valuation` <- `TaiwanStockPER`
  - `monthly_revenue` <- `TaiwanStockMonthRevenue`
  - `financial_statement_*` <- FinMind 基本面資料集
- 工程策略是「先 migration + backfill + 驗證，再淘汰 TWSE parser / ETL」，不要一開始就直接刪舊程式
- `broker_trade` 是用來存「某檔股票、某一天、各券商分點買賣超」的表，主要支撐 L2 個股頁的關鍵券商 / 分點面板
- 現行 `broker_trade` schema 為聚合後結果：`trade_date / stock_id / broker_id / broker_name / buy_shares / sell_shares / net_shares`
- 切到 FinMind 後，`broker_trade` 的資料來源應優先改為：
  - `TaiwanStockTradingDailyReportSecIdAgg`：最符合現有 BrokerPanel 聚合需求
  - `TaiwanStockTradingDailyReport`：若未來要做逐價分點分析再補
- `broker_trade` 這個表的概念可以保留，不一定要砍掉重建；但長期建議拆成：
  - `broker_trade_raw`
  - `broker_trade_daily_agg`
- schema 命名不應混用 TWSE / FinMind 原始欄位名；應採「內部 canonical naming」
- 原則：
  - ETL 層負責把 FinMind / TWSE 原始欄位映射成內部命名
  - DB schema、API schema、前端、回測引擎只使用專案自己的欄位名
- 例如：
  - FinMind `Trading_Volume` -> DB `volume`
  - FinMind `Trading_money` -> DB `turnover`
  - FinMind `max` -> DB `high_price`
  - FinMind `min` -> DB `low_price`
- 不要把外部資料源 naming 直接散落到全系統，避免未來 ETL 切換時 schema 混亂
- FinMind 為**唯一產業分類來源**（2026-04-21 完成切換）：
  - `TaiwanStockIndustryChain`：`stock_id / industry / sub_industry / date`（`Backer/Sponsor`）
  - Fugle CSV mapping 已全面下線，`chain`（上游/中游/下游）欄位永久捨棄
  - `stocks_master.industry_name` / `sub_industry` 由 FinMind 寫入；`chain` 欄位保留但永遠 NULL
  - `industry_daily_flow` 的 `industry_name` 已重建為 FinMind 細分類（53 個產業/日）

## Milestones 進度

### 已完成（截至 2026-04-20）
- M1~M4: ETL pipeline、FastAPI API、Next.js 三層 drill-down 儀表板
- M5: Telegram Bot 個股籌碼查詢
- M6: 8 年歷史資料 backfill（2019-01 ~ 2026-04），僅 5 天 OHLC 資料源缺漏
- M7: K 線圖（L2 candlestick + 法人累積買超，舊資料自動 fallback 折線圖）
- M8: 財報面板（估值 PER/PBR/殖利率、月營收+YoY、季財報 EPS 等）— API + 前端完成
- M9: AI 籌碼分析（`/ai` 指令，接 OpenAI GPT）
- M10: 雲端部署（Render + Vercel）
- M11: 回測程式（DSL + AI mapping + equity curve + 策略建議；2026-04 擴充 4 欄位改版 + 9 K棒型態 + 6 技術型態 + 報酬率%回撤圖）
- M16: AI 盤前摘要（Daily Brief，2026-04-20 起改由 Telegram Bot `/brief` 提供）
- M17: 交易質量 AI 分析（Trade Quality Analysis，5 階評級 + 四象限 + 目標價）
- M18: 使用者註冊系統（Email/password + server-side session + RequireAuth；M17 公開但分層 rate limit；admin email / password 由 Render env var `ADMIN_EMAIL` / `ADMIN_PASSWORD` 設定）
- M19: 關注買進清單（單一清單上限 30 檔，加入 popup 填買進日/均價；L0 HotMoneyList、L1 StockList、L2 個股頁右下「加入清單」；Navbar「我的清單」；/watchlist 顯示未實現損益 + trade quality 卡片 + 個股頁報告入口；資料綁 user_id）
- M22: 熱錢湧入個股排行（L0 底部 Top 20 / L1 頂部 Top 10，近 N 日三大法人累計買超；spec 在 [docs/plans/hot_money_list_spec.md](docs/plans/hot_money_list_spec.md)）
- M21: Trade Quality Context 資料管線（6 個 section 預聚合 JSON：industry/chip/peer_rank/fundamental/price_structure/news_stub；deterministic + no hindsight；入口 `build_trade_quality_context(db, stock_id, buy_date)`；`GET /api/analysis/context` 需登入；實作 [docs/plans/m21_context_pipeline_implementation.md](docs/plans/m21_context_pipeline_implementation.md)）
- M25: 自選清單 trade quality 快照表 + key_factors 條列指標（2026-05-02 完工；新表 `watchlist_trade_quality_snapshots` 三入口共用：cron / on_demand / manual；`/watchlist` 顯示卡片、L2 個股頁顯示報告；`<TradeQualityAnalysis />` 加 `KeyFactorsList`（A 綠 / B 黃 / C 紅 + delta 箭頭）；`run_watchlist_trade_quality.py` 串在 daily_etl_update.yml ETL 之後；trade_quality.md prompt 加 `key_factors` 6 category 強制欄位；spec [docs/plans/M25.md](docs/plans/M25.md)）

### 進行中
- M13 關鍵券商分點：ETL 模組與 `broker_trade_agg` backfill 已完成；L2 券商面板在 2026-04-19 主動隱藏（產品優先序下調），未來視需要復活

### 待開始
- M12 自然語言策略
- M14 輿情分析
- M15 Telegram 電子報
- M20 交易分析擴充（預期 45% 報酬率加碼建議 + 風報比 1:1.75）
- M23 每日異常訊號清單（**改為使用者手動觸發**；前端 `DailySignalsPanel`「重新產生」按鈕 → POST `/api/signals/regenerate` → FastAPI BackgroundTasks 跑 pipeline；deterministic filter 建候選池 + LLM 上網查公司業務／集團／龍頭；最終只保留 top 3 檔，輸出 LEADER / FOLLOWER / LAGGARD 三類；另有 `/signals/archive` 的 40 交易日追蹤總表，並新增 `/api/signals/archive/completed` 封存移出 40 日後的 cycle 摘要；不預測報酬、不出買賣建議；GitHub Actions cron 已停用，`workflow_dispatch` 保留作管理備援；spec [docs/plans/m23_daily_signals_spec.md](docs/plans/m23_daily_signals_spec.md)）
- M24 自訂進出場策略回測（M11 擴充；使用者自設分層進場 / 追價 / 攤平 / 停損停利規則，引擎回測 edge；LLM 為現場判斷層，trigger 觸發時依當下籌碼/產業/技術給「適合執行 yes/no」提示，不替使用者寫規則）

> M18 → M19 → M20 依序執行。M19 已完工（2026-04-23），M20 擴充建立在 M19 卡片帶入 context 之上。
> M21 與 M20 平行但互補：M20 改 prompt、M21 改 backend context 組裝，兩者合起來才能讓 M17 分析真正精準。
> M23 LLM 是「資料翻譯員」（解釋觸發訊號）；M24 LLM 是「現場提醒員」（trigger 當下給判斷）。LLM 拔掉系統還能跑（filter / 回測結果還在）— 是輔助層不是核心，**有它更好、沒它也不殘**。

## 開發注意事項
- 優先考慮資料正確性與 TWSE API rate limiting
- 前端以深色主題為主
- Brian 的個人專案，目標是從法人籌碼面輔助台股交易決策

## 魚尾追蹤清單顯示上限移除（2026-06-03）
- 症狀：`/signals/archive` 的「30 日追蹤」一直卡在 `200 / 200 檔`，使用者反映魚尾應該有多少就顯示並留存多少
- 根因：`GET /api/signals/archive` 與 `GET /api/signals/archive/completed` 的 `limit` Query 預設 `200`（且 `le=500`），前端 [archive/page.tsx](frontend/src/app/signals/archive/page.tsx) 又硬傳 `limit: 200`，被後端 `ordered[:limit]` 截斷
- 修法：兩個 endpoint `limit` 改 `default=0, ge=0, le=5000`，`0` 代表不限筆數（沿用既有 `if limit > 0` 才切片的邏輯）；前端 `fetchSignalArchive` / `fetchCompletedSignalArchive` 改傳 `limit: 0`
- 不動 30 個交易日 retention（active 清單仍是滾動追蹤窗，滿期才封存到 completed）；「留存」靠 completed archive，本次只拔掉顯示截斷
- Gotcha：前端 `if (params?.limit != null)` 下 `0 != null` 為真會帶上 `limit=0`，後端 `ge=0` 接受；不要改成不傳 limit（會與未來想顯式關閉上限的語意混淆）

## M25 完工（2026-05-02）

### 範圍
- 兩個前端大改 + 後端快照表 + cron 串接 + prompt 結構化欄位
- 全 17 個 M25 backend tests pass + 既有 56 個相關 tests 不退步
- canonical 計畫：[docs/plans/M25.md](docs/plans/M25.md)

### 後端
- 新表 `watchlist_trade_quality_snapshots`：UNIQUE `(user_id, stock_id, buy_date, snapshot_trade_date)`；存完整 TradeQualityResponse 欄位 + `key_factors` JSON + status `ok|failed` + source `manual|on_demand|cron`
- 新模組 `app/trade_quality_cache.py`：`resolve_snapshot_trade_date` / `load_snapshot` / `load_latest_ok_snapshot` / `save_snapshot_ok` / `save_snapshot_failed` / `snapshot_to_response_dict`
- `routers/analysis.py` 抽出 `run_trade_quality_for_user(db, user, stock_id, buy_date_input, persist_source)` 給 endpoint / cron / refresh 三方共用；in-memory 5min cache 維持給匿名使用者用
- `routers/analysis.py` `analyze_trade_quality` + `analyze_trade_quality_stream` 都接 DB cache：登入使用者命中 → 不打 OpenAI；source 標 `cache`
- `routers/watchlist.py` 新增 `GET /api/watchlist/trade-quality`（一次回全清單最新快照 + previous + change_pct）+ `POST /api/watchlist/trade-quality/refresh`（單檔 on-demand 補洞）
- `backend/run_watchlist_trade_quality.py` cron 入口；exit 0 ok / 1 partial / 2 all_failed / 5 holiday；個別失敗 try/except 寫 `status='failed'` 不中斷整個 job
- `DELETE /api/watchlist/{entry_id}` 與 `DELETE /api/watchlist` 會同步刪除該使用者對應的 `watchlist_trade_quality_snapshots`，避免股票移出清單後舊快照繼續殘留

### Prompt 修改（trade_quality.md）
- `PART 1` JSON schema 新增 `key_factors` 強制欄位（6 個 category 全部必填：industry / industry_heat / return / chip / technical / fundamental）
- 每項含 `level`（A/B/C）+ `trend`（improving/stable/weakening/deteriorating）+ `note`（10~25 字）
- 一致性規則：classification=A → 至少 4 項 level=A；classification=C → 至少 3 項 level=C
- 鏡像 `docs/trade_quality_prompt.md` 同步更新（標記 canonical 在 backend 那份）

### 前端
- 新元件 `KeyFactorsList`：6 條 A/B/C 燈號 + 趨勢箭頭 + 與 `previousFactors` 比對顯示「上次 X → 本次 Y」
- 新元件 `WatchlistTradeQualityCards`：`/watchlist` 直接顯示個股卡片；欄位 = 個股 / 收盤 / 漲跌幅 / 未實現 / 動作建議 5 階徽章 + 保留摘要 + 重試按鈕；對 `latest=null` 的 row 自動 fire-and-forget on-demand refresh；可直接點進個股頁看完整報告
- `TradeQualityAnalysis` 在 Summary 之後、Price info 之前插入 `KeyFactorsList`（首版不接 delta，未來從 watchlist context 帶 previousFactors 進來即可）
- L2 個股頁新增 `StockSignalSummaryPanel` + `StockWatchlistTradeQualityPanel`：先顯示 M23 市場狀態 / 題材契合 / 資金籌碼燈號 / 保留理由，再接 M25 自選清單報告
- 首頁 `DailySignalsPanel` 改為乾淨卡片：不再顯示 `removed` 分頁，只保留 top 3 訊號卡片與保留理由
- `lib/api.ts` 加 `KeyFactor` / `WatchlistTradeQualityItem` / `WatchlistSnapshotPayload` / `fetchWatchlistTradeQuality` / `refreshWatchlistTradeQuality`；`TradeQualityResponse.source` 加 `"cache"` enum

### GitHub Actions
- `daily_etl_update.yml` 新增 step `Run watchlist trade quality refresh (M25)`；條件 `etl_final.outputs.final_exit in (0, 1)` 才跑（partial 也跑、quota / error / holiday 跳過）
- `timeout-minutes` 240 → 300（多預留 60 min 給 wtq）
- wtq 失敗不影響整個 workflow（exit 0 包住）

### Cron 時機決策
- ETL workflow 維持台北 18:00 不動（既有 cron `0 10 * * 1-5` UTC）
- watchlist trade quality 串在同一 workflow ETL 之後（避免兩個 workflow 同時打 OpenAI / DB）
- `snapshot_trade_date` resolver 用 `ETL_DONE_TIME = 20:00` 當截斷點：20:00 前打 trade quality → 用前一交易日；之後 → 用當日
- 19:00 跑 ETL 不可行：FinMind 同步慢（會大量 no_data retry）+ holiday short-circuit 會誤判（>= 22:00 才允許判 holiday，目前邏輯沒這個條件）

### Gotcha
- **舊客戶端忽略 `key_factors`**：`_normalize_response` 對 invalid enum / 非 dict row 直接 drop（不 raise）；全空 → 回 None 而非 []，讓前端用 `factors && factors.length > 0` 當渲染條件
- **匿名使用者不寫 DB 快照**：沒 user_id 可關聯；走原本 5 分鐘 in-memory cache + 每次重打 OpenAI；登入後才會累積歷史快照供 delta 比對
- **`save_snapshot_failed` 會清掉舊 ok payload**：caller 用 `load_latest_ok_snapshot` 從更早的快照 fallback；UI 標記 `is_stale=True` 提示「資料較舊」
- **同 stock_id 在 watchlist 唯一**：M19 既有約束；同檔不同 buy_date 會把舊的擋掉（這是 watchlist 的設計）
- **OpenAI 成本控制**：每使用者 30 檔 × $0.05 ≈ $1.5/天/使用者；觀察期若太貴改成每 3 天跑一次或拔 cron
- **`analyze_trade_quality` 對外契約凍結**：`rating` / `classification` enum 不變；`key_factors` 只是新增 optional 欄位
- **重複跑 same-day → cache hit 不重打 OpenAI**：`(user_id, stock_id, buy_date, snapshot_trade_date)` 命中即 return，cron / refresh / manual endpoint 都共用這條 fast path
- **股票移出 watchlist 後快照也要一起刪**：否則資料表會留孤兒快照，且之後使用者回頭看個股頁可能誤以為仍在追蹤

## 最近重要修正（2026-05-02）

- M23 `DailySignalsPanel` 不再顯示 `removed` 候選；後端最終只保留 top 3 `watchlist`，前端卡片直接顯示保留理由
- M25 自選清單 trade quality 已**重新掛回首頁**（`<TradeQualityAnalysis />` 之後、`<DailySignalsPanel />` 之前）；同時 `/watchlist` 與 L2 個股頁仍保留報告入口；元件未登入時自身回 `null`，不需在外層判斷登入狀態
- 訊號相關 UI 全面中文化：`signalPresentation.ts` 新增 `signalDecisionLabel`（LEADER → 領漲 / FOLLOWER → 跟漲 / LAGGARD → 轉弱）+ `VALUE_LABELS` 字典翻譯 market_state / VIX / 期貨 / 籌碼狀態（強多 / 結構偏多 / 盤整 / 散戶過熱 / 籌碼集中 / 出貨…）；`signalValueLabel` 命中字典就直譯，未命中才 fallback 到原 `_` → 空白 + upper case
- `useRealtimeQuotes` 修暴衝 bug：原本 `useMemo(() => [...stockIds], [stockIds])` + `useEffect(..., [ids])` 用 array reference 當 dep，父層每次 render 傳 `[stockId]` 新 literal 導致 effect 不停重啟，盤後仍每秒被打 60+ 次。改用 `idsKey = stockIds.join(",")` stable string + effect 內 `idsKey.split(",")` 重組；同步在 prod log 看見 `/api/realtime/quotes` 上游 502 / RemoteDisconnected 是 TWSE mis 偶發問題，但根因是前端打太頻繁讓問題放大
- L2 `<StockSignalSummaryPanel />` 新增「風險提示」（吃 `summary.risk_note`）與「保留摘要」（吃 `item.business_summary`）兩塊；先前點進個股頁只剩 `decision` 徽章與 reason，現在保留理由與 LLM research 的業務摘要都會帶下去
- 首頁 boot loading overlay 改加權進度條：`BOOT_TASK_WEIGHTS` 給每個任務不同權重（signals 28 / industries 24 / tradeDate 20 / hotMoney 16 / job 12）+ `BOOT_LOADING_CREDIT=0.58` 給 in-flight 任務假性進度，避免 0 → 100 跳變；副字顯示「另外 N 項同步處理中」，項目列各自帶迷你 spinner
- `DELETE /api/watchlist/{entry_id}` / `DELETE /api/watchlist` 會同步刪掉 `watchlist_trade_quality_snapshots`
- 本輪已順手清掉數個 React hooks / memoization lint 問題（`useRealtimeQuotes`、`BrokerPanel`、`FinancialsPanel`、`StockChart`）

### 下一步（M25 上線後）
- 觀察 prod cron 第一次跑（隔日早上 18:30 後檢查 Render log + DB row 數）
- 觀察使用者 30 檔 OpenAI cost；若超預算考慮：(a) 排除某 rating 的 row 不每天重跑、(b) 改成週一 / 週四只跑兩次、(c) 拔 cron 改純 on-demand
- 若 prompt 對 6 個 category level/trend 一致性還是偶爾飄，可在 user message 內把 M21 enum 直接 echo 回去當 anchor

## 最近重要修正（2026-04-09）

- L2 頁面 `StockChart` 與 `BrokerPanel` 必須共用同一個 `date` query param，避免同頁不同日期資料混用
- L0 / L1 前端預設日期必須用 `Asia/Taipei`，不可用 `toISOString().slice(0, 10)`，否則台灣凌晨會落到前一天
- 即時報價 API `/api/realtime/quotes` 單次上限 50 檔；前端若要查整個產業，必須自動分 batch，不能假設所有股票可一次取回
- `industry_daily_flow` 仍是 L0 主查詢來源；不要把產業聚合搬回 API 臨時計算或前端計算
- L2 個股頁的「回測程式」與「關鍵券商」已拆成兩個獨立 toggle，且會記住使用者上次的顯示偏好；被隱藏的 panel 不應 render，也不應觸發後續 API

## 最近重要修正（2026-04-27）

- L0 `/api/industries` 已加 `resolved_date` 粒度的 60 秒 server-side cache；同一天短時間重複請求不可再重跑整段產業查詢
- `industry_daily_flow.streak` 已下沉為 ETL 持久化欄位；L0 不可再 request-time 回掃最近 31 個交易日計算 streak
- `industry_daily_flow.streak` 的 schema 演進要靠顯式 ensure（`ALTER TABLE ... ADD COLUMN streak`）；`Base.metadata.create_all()` 只建新表，不會替既有表補欄位
- 若要修正歷史 streak，應優先跑 `rebuild_industry_flow.py`（升序重建），不要只改 API 端計算
- M23 Step 0 / research 改為顯式走 OpenAI Responses API `tools=[{"type":"web_search"}]`；不能只靠 prompt 文字寫「請上網查」
- `market_context.taiex_change_pct` / `otc_change_pct` 改為 backend authoritative：從 DB snapshot deterministic 帶入，LLM 不可改寫或補 0
- M23 OpenAI client 必須顯式設 `timeout=120`、`max_retries=1`；否則單一 `responses.create()` 卡住時，job 會一直停在 `llm_research` 或 `llm_explain` 的同一個 batch 進度
- M23 batch 建議拆開調：`research` 可維持 8，`explain` 應降到 4；因為 explain prompt 較長，較容易在單次 call 卡住
- M23 explanation 已改成兩階段：
  - 全候選先做短 decision（`WATCH/REMOVE + short_reason`）
  - 只對最後 `WATCH` 名單補長理由（250-350 字）
- M23 現在對 Responses API 顯式帶 `prompt_cache_key`（market / research / decision / watch-reason 分開），利用固定長 prompt 前綴降低 latency
- M23 模型分層已接好：`OPENAI_SIGNALS_MARKET_MODEL` / `OPENAI_SIGNALS_RESEARCH_MODEL` / `OPENAI_SIGNALS_DECISION_MODEL` / `OPENAI_SIGNALS_REASON_MODEL`
- M23 任何 LLM fallback 都必須保留 `llm_diagnostic`，至少含 `stage / model / status / use_web_search / prompt_cache_key`
- `llm_diagnostic.status` 目前標準值：`ok` / `api_key_missing` / `openai_exception` / `empty_output` / `invalid_json`
- Step 0 market fallback 文案不可再籠統寫成「OpenAI 服務不可用」；必須帶出較精確原因（例如 API key 缺失、OpenAI 例外、空回應、非 JSON）
- research / decision / watch-reason 三段若 fallback，也要把診斷掛回各股票項目，避免 snapshot 成功但無法判斷是哪一層退回保守結果
- M23 現在走 Responses API + `web_search` tool；預設 fallback 不可再用 `gpt-4o-search-preview`，避免線上帳號回 `404 Model not found`
- M23 pipeline 的 research / decision / watch-reason batch 現在允許有限度並行（concurrency=2）；若要再加速，優先調這個並行度，不要先無限制放大 batch
- `DailySignalsPanel` 卡片要提供明確的個股/K線入口，不要只剩股票代號文字 link
- M23 候選池目前採較保守來源範圍：`TOP_INDUSTRIES_LIMIT=6`、`TOP_STOCKS_LIMIT=30`、`TOP_STOCKS_INNER=6`
- M23 laggard 候選雖仍是 `hits >= 2`，但新增硬條件 `total_institution_flow_1d > 0`，避免把純量價轉強但法人尚未回補的邊緣股送進 LLM
- M23 在 `after_hard` 後新增 `LLM_INPUT_HARD_LIMIT=50`；排序優先序是 `LEADER > FOLLOWER > LAGGARD_CANDIDATE`，同類內再看 `in_top_stocks_3d / in_top_industries_3d / total_institution_flow_3d / total_institution_flow_1d / price_change_5d`
- 目前預設配置：
  - `OPENAI_SIGNALS_MARKET_MODEL=gpt-5.4-mini`
  - `OPENAI_SIGNALS_RESEARCH_MODEL=gpt-5.4-mini`
  - `OPENAI_SIGNALS_DECISION_MODEL=gpt-5.4`
  - `OPENAI_SIGNALS_REASON_MODEL=gpt-5.4-mini`
- M17 / Trade Quality Analysis 現在對同 stock_id + buy_date 有 5 分鐘 in-memory cache；重複查同一標的時應先命中 cache 再考慮重新打 OpenAI
- 首頁首屏效能：`TradeQualityAnalysis` 保持先載，`DailySignalsPanel` / `HotMoneyList` / `IndustryDashboard` 改 deferred mount，避免首次同時打多支 API
- 觀察清單上限已調整為 30；前後端文案、capacity 常數、API 限制需同步

## 資料狀態（2026-04-10）

- 本地 backfill 已重新補跑大部分歷史缺口
- `inst_stock_flow` / `industry_daily_flow` 仍缺 3 天：`2019-04-04`、`2023-04-03`、`2026-02-18`
- 上述 3 天重抓時，TWSE `MI_INDEX` 回傳「沒有符合條件的資料」，暫列為資料源特殊日
- `daily_price` 仍有 5 天 `OHLC` 缺漏：`2023-05-05`、`2023-09-19`、`2024-01-17`、`2024-02-29`、`2024-07-11`
- 這 5 天已重抓一次，`close_price` 與後續 flow 可更新，但 `open/high/low` 仍為空，推測是資料源回傳本身缺欄位
- 已從 Fly.io 遷移至 Render（Postgres）+ Vercel（前端）
- Fly.io 資源已停用，可待驗證完成後刪除

## 最近重要修正（2026-04-12）

- L3 回測 MVP 第一批已落地：
  - 後端新增 `/api/backtest/templates`
  - 後端新增 `/api/backtest/interpret`
  - 後端新增 `/api/backtest/run`
  - 後端新增 `/api/backtest/advice`
- 回測引擎目前範圍固定為：
  - 單一股票 / ETF
  - 日線資料
  - long-only
  - 訊號以當日收盤判斷、次日開盤成交
  - 同時間單一部位
  - 成本模型固定為 `0`
- 第一批 parser / DSL 僅保證支援：
  - `收盤價站上 N 日均線`
  - `收盤價跌破 N 日均線`
  - `成交量高於 N 日均量`
  - `外資 / 投信 / 自營商 連買 N 天`
  - `外資 / 投信 / 自營商 轉賣 / 賣超`
- 回測標準輸出目前已包含：
  - `total_return_pct`
  - `annual_return_pct`
  - `win_rate_pct`
  - `max_drawdown_pct`
  - `sharpe_ratio`
  - `trade_count`
  - `ending_equity`
  - `benchmark_return_pct`
  - `excess_return_pct`
  - `avg_trade_return_pct`
  - `avg_holding_days`
  - `profit_factor`
  - `avg_gain_pct`
  - `avg_loss_pct`
- 前端 `BacktestPanel` 已從假資料改成真 API 串接，並支援：
  - 策略模板載入
  - 策略文字手動編輯
  - `interpret -> preview -> run -> advice` 流程
  - 顯示 quick metrics
  - 顯示正式 equity curve chart
  - 顯示最近交易紀錄
  - 顯示最新交易日建議
  - 顯示策略建議卡片
  - 顯示 warnings / validation error
  - 顯示 `unsupported_conditions`
  - 顯示更細的 422 中文錯誤訊息
  - 從交易紀錄 / 最新訊號跳回 L2 研究頁
- 邊界處理已補上：
  - 空白策略文字
  - 開始日大於結束日
  - 部分不支援條件的 `interpret` 回應
  - `run` 對不支援條件的拒絕執行
  - lookback 不足 warnings
  - 開盤價缺失 fallback warnings
- UX 已補上：
  - strategy preview loading state
  - advice loading skeleton
  - partial-support preview
- 已完成（2026-04-12 全部完工，對齊 docs/plans/l3_manual_strategy_backtest_spec.md）：
  - DSL 條件：停損停利、均線交叉、突破高低點、volume_ratio（倍數量能）
  - 三大法人完整支援：net_positive / net_negative、consecutive_buy / consecutive_sell、all_inst_net
  - 完整 summary / period analysis（月/季/年度報酬）
  - AI mapping 流程（backtest_ai_mapping.py 接入 interpret，回傳 ai_mapped_conditions）
  - 前端顯示 AI 補充解析來源標記（天藍色提示區塊）
  - strategy templates 7 個，對齊 spec 15.1.1：4 個核心 + 3 個延伸
  - 使用範例文件：docs/guides/backtest_strategy_examples.md

## 最近重要修正（2026-04-17）

- **環境定位確認（重要）**
  - 本機 `localhost:8000` 當下執行中的 backend 進程環境變數 `DATABASE_URL` 指向 Render PostgreSQL（非本地 SQLite）。
  - 本地 `backend/db/tw_stock.db` 的 `daily_valuation` 目前是 0 筆；本地與雲端資料差異需先確認連線目標。

- **L1 產業名稱 fallback（已於 2026-04-21 全面移除）**
  - 舊設計（TWSE `industry_daily_flow` ↔ Fugle `stocks_master` 名稱不一致）需要 `INDUSTRY_NAME_FALLBACKS` 硬映射 + 後綴剝離。
  - 2026-04-21 切換後，`industry_daily_flow.industry_name` 與 `stocks_master.industry_name` 皆由 FinMind 寫入，名稱一致，fallback 已全部刪除。

- **L3 回測頁視覺修正**
  - 修正回測頁右側 `BacktestPanel` 高度策略：`h-full` 改為 `min-h-full`，避免內容展開時底色不延伸造成「破圖感」。
  - 回測頁容器補 `min-h-0` 與 pane 背景，確保雙欄滾動與背景覆蓋一致。

- **L2 財報顯示修正**
  - 估值圖：`PER <= 0` 視為 N/A（顯示 `null` 不畫線），避免誤讀為有效 0 值。
  - 月營收圖：當 `yoy_pct` 無資料時，不顯示 YoY 線與圖例，並提示「目前僅顯示月營收」。
  - 原因確認：Render `monthly_revenue` 目前 `COUNT(yoy_pct)=0`、`COUNT(mom_pct)=0`。

- **monthly_revenue ETL 根因與修補**
  - 根因：`etl/finmind_monthly_revenue_sdk.py` 先前僅讀特定欄位名（`revenue_year_difference_per` / `revenue_month_difference_per`），遇到 SDK 欄位名差異時全部寫成 `NULL`。
  - 已修：支援多欄位名 fallback，並在資料源未提供 YoY/MoM 時以營收序列回算（同股月序列計算 YoY/MoM）。
  - 另修：`revenue_month` 可能是整數月份（1~12），需搭配 `revenue_year` 轉月末日期。
  - 已新增測試：`backend/tests/test_finmind_monthly_revenue_sdk.py`。
  - 當日回補嘗試結果：FinMind 配額超限（`6352/6000`），ETL 回傳 `INSUFFICIENT_QUOTA`，DB 尚未補回 YoY/MoM（仍為 0 筆非空）。

## 回測引擎設計規範（2026-04-12 整理）

### normalized_text 生成方式
- 必須從解析後的 `entry_rules`/`exit_rules` AST 重建，不可用 naive `str.replace()` 修改原文
- 由 `backtest_parser._rule_to_text(rule)` 負責 rule → 可讀文字的映射
- 停損/停利附加在 exit 段尾端（不可混入 entry 段）

### 語義正確性
- `profit_factor`：無虧損交易時應回傳 `None`（不是 `0.0`）
- `avg_gain_pct`：無獲利交易時應回傳 `None`
- `avg_loss_pct`：無虧損交易時應回傳 `None`
- 前端顯示 `null` 時用 `—` 代替，不可直接 `toFixed()`

### Sharpe Ratio 年化係數
- 使用 `_TRADING_DAYS_PER_YEAR = 252`（美股慣例，與 Zipline/Backtrader 對齊）
- 台股實際約 245 天，但改動會影響可比性，暫不修改

### 前端預設值
- `startDate` 預設為台北時區一年前，使用 `Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Taipei" })`
- 不可用 `new Date().toISOString()` 或寫死日期字串
- 策略文字預設值由後端 `/api/backtest/templates` 第一筆決定，前端不另存常數

## FinMind SDK ETL 集成（2026-04-13 完成）

### 核心改進
- **架構升級**：從 REST API per-stock/per-day（400K+ calls）→ FinMind SDK async batch（50-100 calls）
- **Bulk upsert**：所有 ETL 模組改用 `INSERT ON CONFLICT DO UPDATE`，batch size 1000，速度提升 450x（row-by-row ~4 rec/sec → bulk ~1800 rec/sec）
- **防斷線機制**：HTTP 402（Payment Required）檢測 + 配額預警 + SDK 自動重試
- **雙軌並行**：所有 DB 寫入加上 `source` 欄位（twse | finmind），支持驗證期間並行跑舊新系統

### 已完成的程式碼（2026-04-13）

#### 第一層：SDK 客戶端
- **`etl/finmind_sdk_client.py`**
  - FinMind Python SDK 封裝層
  - `__init__()` 自動初始化 + token 驗證
  - `_refresh_quota()` 配額管理（HTTP 402 詳細解析）
  - `can_proceed()` gate-keeper（防超額）
  - 7 大 batch 查詢（均返回 pandas DataFrame）：
    - `fetch_taiwan_stock_price(...)` → 日股價
    - `fetch_institutional_investors(...)` → 三大法人
    - `fetch_per(...)` → P/E, P/B, dividend_yield
    - `fetch_month_revenue(...)` → 月營收 + YoY/MoM
    - `fetch_financial_statements(...)` → 財報
    - `fetch_broker_trade_agg(...)` → 券商分點聚合（Sponsor）

#### 第二層：ETL 模組（均支持 batch 處理 + bulk upsert）

1. **`etl/finmind_daily_price_sdk.py`**
   - 欄位映射（注意：FinMind 實際回傳欄位名）：
     - `max` → `high_price`、`min` → `low_price`
     - `Trading_Volume` → `volume`、`Trading_money` → `turnover`
   - Upsert：`ON CONFLICT (trade_date, stock_id) DO UPDATE`

2. **`etl/finmind_inst_flow_sdk.py`**
   - FinMind 回傳 5 種法人類型 → 需先 `groupby().agg()` 合併後再 upsert（否則 unique constraint 違反）
   - 映射（在 `finmind_utils.py` 的 `FINMIND_INST_TYPES_MAPPING`）：
     - `Foreign_Investor` + `Foreign_Dealer_Self` → `foreign`
     - `Investment_Trust` → `trust`
     - `Dealer_self` + `Dealer_Hedging` → `dealer`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_flow_date_stock_inst DO UPDATE`

3. **`etl/finmind_daily_valuation_sdk.py`**
   - 新表 `daily_valuation`：`(trade_date, stock_id, per, pbr, dividend_yield, source, ingested_at)`
   - Upsert：`ON CONFLICT (trade_date, stock_id) DO UPDATE`

4. **`etl/finmind_monthly_revenue_sdk.py`**
  - 月營收：`revenue_year`/`revenue_month` 欄位轉換為月末日期（用 `calendar.monthrange()`）
  - YoY/MoM：優先吃 FinMind 回傳欄位（多欄位名 fallback），若資料源無提供則以同股營收序列回算
  - Upsert：`ON CONFLICT ON CONSTRAINT uq_revenue_month_stock DO UPDATE`

5. **`etl/finmind_financial_statement_sdk.py`**
   - 財報：`origin_name` → `item_name`、`type` → `item_code`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_finstatement_date_stock_item DO UPDATE`

6. **`etl/finmind_broker_trade_sdk.py`**
   - 券商分點聚合（Sponsor，資料起點 2021-06-30）
   - `start_date < 2021-06-30` 時自動調整；`start_date > end_date` 後跳過（回傳 `status: skipped`）
   - 映射：`securities_trader_id` → `broker_id`、`securities_trader` → `broker_name`
   - Upsert：`ON CONFLICT ON CONSTRAINT uq_broker_agg_date_stock_broker DO UPDATE`

#### 第三層：協調與監控
- **`run_finmind_etl_sdk.py`**
  - 6 步協調流程：
    - [1/6] daily_price
    - [2/6] inst_flow
    - [3/6] daily_valuation
    - [4/6] monthly_revenue
    - [5/6] financial_statement
    - [6/6] broker_trade_agg（2019/2020 自動跳過）
  - 支援：單日模式 `--date`、區間模式 `--start-date/--end-date`、預設昨天

- **`scripts/backfill_finmind.sh`**
  - 以年為單位逐年 backfill（2019 → 2026）
  - `START_YEAR=YYYY bash scripts/backfill_finmind.sh` 可從斷點繼續
  - checkpoint 記錄至 `backend/logs/backfill_checkpoint.txt`
  - 每年日誌寫入 `backend/logs/backfill_YYYY.log`

- **`test_finmind_sdk_integration.py`**
  - 5 階段集成測試，用法：`python test_finmind_sdk_integration.py --config test_small`
  - 注意：`hasattr(client, "api")` 是正確檢測（SDK 使用 `self.api`，不是 `self.client`）

### FinMind 欄位對照（重要 gotcha）

| FinMind 原始欄位 | DB 欄位 | 說明 |
|----------------|---------|------|
| `max` | `high_price` | 不是 `high` |
| `min` | `low_price` | 不是 `low` |
| `Trading_Volume` | `volume` | 不是 `volume` |
| `Trading_money` | `turnover` | 不是 `money` |
| `open` | `open_price` | 一致 |
| `close` | `close_price` | 一致 |

### 配額消耗 (Sponsor 6000 req/hour)

> **2026-04-27 修正**：先前估算「SDK `stock_id_list=[...]` async batch = 1 req」是錯的。SDK 內部對每個 `data_id` 都打一次 v4 endpoint，仍是 per-stock 計費（1592 檔 ≈ 1500 req / 步）。daily_etl_update workflow 實測：
> daily_price 1466 → inst_flow +1708 → daily_valuation +1591 → monthly_revenue 已 6012/6000 超標 → 後續 financial / broker 全跳過。
>
> 已改為走 **dataset-level batch**：純 v4 REST，**不帶 `data_id`**、僅帶 `dataset` + `start_date` / `end_date`，單次拉全市場該區間資料，**1 quota per dataset**。

| 場景 | 舊（per-stock SDK list） | 新（dataset-level REST） | 節省 |
|------|----------------------|----------------------|-----|
| 單日 daily_price | ~1500 req | 1 req | **-99.9%** |
| 單日完整 ETL（5 dataset） | ~7500 req（必爆） | ~5 req | **-99.9%** |
| 一年 backfill | ~365K req | ~5 × 12 = 60 req | **-99.98%** |

**dataset 對應**：
- `daily_price` → `TaiwanStockPrice`
- `inst_flow` → `TaiwanStockInstitutionalInvestorsBuySell`（**舊名 `TaiwanStockInstitutionalInvestors` 已被 v4 enum 拒收**）
- `daily_valuation` → `TaiwanStockPER`
- `monthly_revenue` → `TaiwanStockMonthRevenue`
- `financial_statement` → `TaiwanStockFinancialStatements`
- `margin_trade` → `TaiwanStockMarginPurchaseShortSale`（**v4 dataset-level fetch 只回 `start_date` 當日資料**，必須逐交易日呼叫；ETL 模組內部 loop daily_price.trade_date，每天 1 quota）

實作位置：`backend/etl/finmind_sdk_client.py` 的 `_fetch_dataset_for_range()` + 6 個 `fetch_*_dataset()` wrapper；ETL 模組拿到全市場 DataFrame 後以 `df[df["stock_id"].isin(stock_ids)]` 過濾到 stocks_master 範圍。

**broker_trade_agg 例外**：`TaiwanStockTradingDailyReport` 必須帶 `data_id` 不接受 dataset-level 呼叫，仍是 per-stock。已從 `run_finmind_etl_sdk.py` 預設步驟拔掉；`broker_trade_backfill.yml`（每小時 cron）獨立處理。要強制跑時用 `--steps broker_trade_agg`。

### 待辦事項

#### Backfill（配額充足後執行）
- ⬜ 執行 `bash scripts/backfill_finmind.sh` 全量 backfill 2019-2026
- ⬜ 確認各年 log 均為 `✓ XXXX 完成`
- ⬜ 驗證新舊資料一致性（`source='twse'` vs `source='finmind'`）

#### 第二階段（切換）
- ⬜ 配額足夠 → 切換為 FinMind 為主
- ⬜ TWSE ETL 改為 fallback / 校驗用途

#### M8-M13 相依
- M8 財報：✅ 已完成（API + 前端面板，2026-04-17）
- M13 券商分點：ETL 模組已完成（`finmind_broker_trade_sdk.py`，Agg 版），`broker_trade_agg` 表已支援，GitHub Actions 每小時自動 backfill

## 前端功能更新（2026-04-14）

### 新增功能
1. **今日觀察重點**（AI 盤前摘要）
   - 後端：`backend/app/routers/market.py`
     - 共用函式 `build_daily_brief(db, requested_date)` — HTTP endpoint 與 Telegram bot 共用，確保兩個入口輸出一致
     - HTTP endpoint `GET /api/market/daily-brief` 僅負責 `ValueError → HTTPException` 轉換
   - 收集 DB 法人流向資料 + Yahoo Finance（VIX、WTI、USD/TWD）→ OpenAI 生成盤前摘要
   - `_resolve_trade_date()` 確保一定落在有資料的交易日（非假日/非休市日）
   - `_top_industries_3d()` 使用 DB 實際有資料的 3 個交易日，不依曆法推算
   - 曝光入口（2026-04-20 調整）：
     - Telegram Bot `/brief`（主要入口，handler 在 `backend/app/telegram_bot.py::brief_handler`）
     - 前端 `DailyBrief.tsx` 元件保留但已從首頁移除；若未來要重新掛回再 import 即可

2. **BrokerPanel 改版**（買進 / 賣出排行 + 標籤）
   - 後端新增 `GET /api/stocks/{stock_id}/brokers/ranked`：返回 `buy_top` / `sell_top` 各 10 筆
   - `BrokerTradeItem` 新增 `categories: List[str]` 欄位（舊分類以標籤顯示）
   - 前端 `BrokerPanel.tsx` 改為兩 tab：「買進 Top10 / 賣出 Top10」，附舊分類標籤（顏色標示）

3. **點擊券商 → 買賣超走勢圖**
   - 後端新增 `GET /api/stocks/{stock_id}/brokers/{broker_id}/history?start=&end=`
   - 前端新增 `frontend/src/components/BrokerBarChart.tsx`（ECharts 長條圖）
   - L2 個股頁點擊 BrokerPanel 中的券商 → StockChart 下方顯示該券商逐日淨買超長條圖
   - StockChart 新增 `onDaysChange` prop，讓 L2 頁追蹤當前 K 線時間範圍

### UI 調整
4. **背景/卡片調淺**：body `bg-zinc-800` → `bg-zinc-600`，卡片 `bg-zinc-900` → `bg-zinc-700`，border `zinc-700` → `zinc-600`
5. **K 線圖放大**：StockChart `60vh / min 400px` → `70vh / min 500px`；BacktestEquityChart `240px` → `380px`

## M8 財報面板（2026-04-17 完成）

### 後端 API（`backend/app/routers/financials.py`）
- `GET /api/stocks/{stock_id}/valuation` — PER/PBR/殖利率走勢（預設一年）
- `GET /api/stocks/{stock_id}/revenue` — 月營收 + YoY/MoM（預設 24 個月）
- `GET /api/stocks/{stock_id}/financials` — 財報項目，支援 `item_names` 篩選、`quarters` 參數

### 前端（`frontend/src/components/FinancialsPanel.tsx`）
- 三 tab：估值 / 月營收 / 財報
- 估值：PER + PBR 折線（左軸）+ 殖利率折線（右軸），ECharts
- 月營收：柱狀圖（營收，億元）+ YoY% 折線（右軸），ECharts
- 財報：EPS、營業收入、淨利、毛利、營業利益 的季度橫向對照表
- 位置：L2 個股頁，toggle 列下方、券商面板上方
- `chartDays` prop：三個子元件隨 K 線天數連動（估值=天數、營收=天數÷30 月、財報=天數÷90 季）
- PER <= 0 視為 N/A，全期間不適用時顯示提示文字

### Bug 修復
- `finmind_monthly_revenue_sdk.py`：月份解析 bug，`revenue_month` 為單位數（2~9）時 `mo_str[-2:]` 長度判斷錯誤，導致全部被歸到 1 月
- 修正後 monthly_revenue 從 29,349 → 74,354 筆，全 12 個月覆蓋

### GitHub Actions 優化
- `broker_trade_backfill.yml`：batch 從 calendar days 改為 trading days 計算，跳過週末，效率提升 ~30%

## 回測圖表改版（2026-04-17）

### BacktestEquityChart 改善
- Y 軸從絕對金額改為**報酬率 %**（`+10.5%` 取代 `$1,105,000`）
- 新增**回撤副圖**（drawdown % 紅色面積圖），上下圖聯動
- 標記**進出場點**：買入 ▲ 紅色三角、賣出 pin（獲利黃/虧損綠）
- tooltip 整合策略報酬、Buy & Hold、回撤三項數值
- 移除圖下方冗餘的 equity point 數字列表
- Props 新增 `trades?: BacktestTrade[]`，用於繪製進出場標記

## L2 個股頁 UX 改版（2026-04-17）

### 功能 toggle 列
- 原「功能顯示」獨立 section 改為 K 線圖下方的**緊湊 pill 列**（`ToggleChip` 元件）
- 三項目橫排：`回測程式 →`（連結）、`財報`（toggle）、`關鍵券商`（toggle）
- 兩個 toggle 存 `localStorage`（`always-stock:show-financials-panel` / `always-stock:show-broker-panel`）
- 關閉的 panel 不 render、不觸發 API

### 券商面板 retry 上限
- `BrokerPanel` 新增 `emptyRefreshCount` 狀態
- 當 API 回傳 `is_refreshing: true` 但 `buy_top` / `sell_top` 為空時計數 +1
- **超過 3 次**後停止 auto-refresh polling，顯示「此日期無券商交易紀錄」
- 切換股票/日期時自動重設計數器

### FinancialsPanel 日期連動
- 新增 `chartDays` prop，三個子元件隨 K 線天數變化重新載入
- 估值：直接用 `chartDays` 計算 `startDate` / `endDate`
- 月營收：`chartDays ÷ 30`（最少 6、最多 120 月）
- 財報：`chartDays ÷ 90`（最少 4、最多 20 季）

### PER 不適用提示
- 當全期間 PER <= 0（EPS 為負），圖表下方顯示「此期間 EPS 為負值或不適用，本益比無法顯示」
- FinMind 回傳 PER=0 即代表 EPS 為負值，非 ETL 錯誤

## L3 回測 4 欄位改版與 K 棒型態擴充（2026-04-19）

### 策略輸入改為 4 欄位
- 原單一 `strategy_text` textarea → **四欄位分離**：買進條件（entry_text）、賣出條件（exit_text）、停損 %（stop_loss_pct）、停利 %（take_profit_pct）
- 後端 `BacktestRunRequest` / `BacktestInterpretRequest` 皆新增 optional 欄位，保留 `strategy_text` 做向後相容
- `backtest_parser.parse_strategy()` 優先使用 entry/exit 分段；未提供時 fallback 解析 `strategy_text`
- `stop_loss_pct` / `take_profit_pct` 優先序：顯式參數 > entry 文字 > exit 文字 > AI mapping
- 自由文字無格式限制，parser 無法匹配的條件會走 OpenAI AI mapping fallback

### K 棒 / 技術型態擴充（backend/app/backtest_patterns.py）
- K 棒型態（OHLC-based）：
  - 紅三兵 `candle_three_white_soldiers`、三隻烏鴉 `candle_three_black_crows`
  - 錘子線 `candle_hammer`、吊人 `candle_hanging_man`
  - 十字星 `candle_doji`
  - 多頭吞噬 `candle_bullish_engulfing`、空頭吞噬 `candle_bearish_engulfing`
  - 晨星 `candle_morning_star`、夜星 `candle_evening_star`
- 技術型態（peak/trough via `_find_local_peaks` / `_find_local_troughs`, radius=3）：
  - 頭肩頂/底 `pattern_head_shoulders_top` / `pattern_head_shoulders_bottom`
  - 雙頂 M `pattern_double_top`、雙底 W `pattern_double_bottom`
  - V 型反轉 `pattern_v_reversal`、A 型反轉/倒 V `pattern_a_reversal`
- **Gotcha**：`detect_head_shoulders_*` / `detect_double_*` guard 須用 `if i < lookback - 1`，不可寫 `if i < lookback`（V/A 用後者，因為 n 天資料 index 0..n-1，lookback=n 時 i=n-1 合法）

### 可用條件目錄分組（backend/app/backtest_catalog.py）
- `CapabilityCatalog.groups` 新增 high-level 分組：
  - 外資買賣、投信買賣、自營商買賣
  - 均線 / MA（站上/跌破/黃金交叉/死亡交叉）
  - K 棒型態、技術型態
  - 風險控制（停損 / 停利 / 突破高低點 / 量能倍數）
- 前端 `BacktestPanel` 加入可收合的「查看可用條件列表」，以 `CatalogGroups` 元件依 `groups` 渲染；後端未提供 `groups` 時退回 flat 顯示

### L2 關鍵券商面板暫時隱藏
- `frontend/src/app/stocks/[stockId]/page.tsx`：從 `SIDEBAR_ITEMS` 移除 `broker` 項目，不再載入 `BrokerPanel`、不再讀寫 `always-stock:show-broker-panel` localStorage
- 程式碼保留（`components/BrokerPanel.tsx`、`components/BrokerBarChart.tsx`、對應 API）僅隱藏入口
- 理由：使用者希望優先聚焦「策略回測」與「主動推薦」，券商分點面板待產品優先序再決定是否復活

## Phase 2：交易質量 AI 分析（規劃中，2026-04-19 啟動）

### 需求背景
- `docs/trade_quality_prompt.md` 是使用者沉澱下來的買方分析師 prompt：輸入 `{stock, buy_date}`，輸出 A/B/C 分類 + JSON + 中文分析報告
- 線上 backend 實際部署的 canonical prompt 應放在 `backend/app/prompts/trade_quality.md`，因為 Render Web Service `rootDir=backend`，不保證 repo 根目錄 `docs/` 會被帶進 production artifact
- 核心規則：no hindsight bias、只用 buy_date 當日及以前的資訊、target price 必須自己推導、資料不足要明講「無法建立有效交易判斷」
- 此 phase 將 prompt 接成首頁的互動功能

### 功能落點
- **位置**：首頁（`frontend/src/app/page.tsx`）`TradeQualityAnalysis` section（2026-04-20 起為首頁頂部，DailyBrief 已移至 Telegram Bot）
- **輸入**：
  - 股票代號 / 名稱（autocomplete，user 打字即時 filter 下拉選單）
  - 買進日期（空白時預設為 DB 最近一個交易日）
- **輸出**：
  - 5 階顏色評級：強烈推薦（深綠）/ 推薦（綠）/ 中立（黃）/ 再看看（橘）/ 快跑（紅）
  - Summary（一段話原因）
  - 預估目標價區間
  - 「詳細」按鈕 → 展開 PART 2 完整中文分析報告

### 設計決策（2026-04-19）
- **5 階由 prompt 直接輸出** `rating` 欄位（不在後端做 A/B/C → 5 階映射，避免 JSON 與 PART 2 不一致）
- **第一版不接新聞資料**：prompt 裡註明「本次分析無 10 天內新聞」；依規則 15，缺新聞時分析師應趨向保守判斷（C/快跑或中立），這是刻意的行為 —— 日後接 Google News / 輿情 ETL 再補
- **context 組裝**：後端會把 buy_date 前可觀察資料（近 10 交易日 OHLC、法人、最近一次月營收 YoY/MoM）塞進 user message，再把 `backend/app/prompts/trade_quality.md` 作為主要 system prompt；repo `docs/trade_quality_prompt.md` 保留給人讀與編輯

### API 設計
- `POST /api/analysis/trade-quality`
  - Request: `{ stock_id: str, buy_date?: date }`（buy_date 空白時 fallback 到 latest trade date）
  - Response: `{ rating, rating_label, summary, target_price_low, target_price_high, classification, action, report_markdown, ... }`
- 支援端點（若尚未存在則新增）：
  - `GET /api/stocks/search?q=...` — 股票 autocomplete
  - `GET /api/market/latest-trade-date` — DB 最新交易日

## Daily ETL 穩定性修正（2026-04-21）

### 問題
- 2026-04-20 的 scheduled run（台北 21:00 觸發，實際因 Actions cron 延遲在 22:54 才跑）被標 `error`、GitHub Actions fail
- 根因：FinMind `TaiwanStockInstitutionalInvestorsBuySell` 與 `TaiwanStockPER` 同步比 broker_trade_agg 慢；22:54 時 inst_flow / daily_valuation 還拿不到全市場資料
- inst_flow 在空資料時直接回傳 `status: "error"`，因為它屬於 `CRITICAL_STEPS`，整包被拖倒

### 修正
1. **cron 推遲**：`.github/workflows/daily_etl_update.yml`
   - `0 13 * * 1-5`（台北 21:00）→ `0 15 * * 1-5`（台北 23:00）
   - `timeout-minutes: 45 → 75`（預留 30 分鐘給 critical step retry）
2. **no_data 語義拆分**：`backend/etl/finmind_inst_flow_sdk.py`
   - 空資料時 `status: "error" → "no_data"`（與真的 exception 區分）
3. **CRITICAL step retry**：`backend/run_finmind_etl_sdk.py`
   - 新增 `NO_DATA_RETRY_SCHEDULE = [600, 1200]`（10 / 20 分鐘）
   - `_run_critical_step_with_retry()`：CRITICAL step 回 `no_data` 時依排程重試最多 2 次
   - `daily_price` / `inst_flow` 兩個 step 呼叫改用 helper
4. **整體狀態判定**：
   - `RESUMEABLE_STEP_STATUSES` 加入 `no_data`（非 CRITICAL 的空資料視為正常，例如月營收 / 財報）
   - CRITICAL step 最終仍 `no_data`（retry 用盡）→ 整包 `error`（觸發 workflow fail，提醒人工檢查）

### 測試
- `backend/tests/test_run_finmind_etl_sdk.py` 新增兩個測試案例：
  - CRITICAL step `no_data` 最終仍 no_data → error
  - 非 CRITICAL step `no_data` → ok

## Daily ETL 配額重試 + 假日自動跳過（2026-04-22）

### 背景
- 2026-04-22 00:15（台北）的排程又卡配額（6362/6000），workflow 因 `insufficient_quota` 被當成 pass 但其實全沒跑
- 假日（國定假日）cron 不會排除，ETL 會一路跑每個 step 才發現空資料，浪費時間與配額

### 改動
1. **`backend/etl/finmind_daily_price_sdk.py`**
   - `daily_price` 回空資料時依 `client.quota_info` 判斷：
     - 配額健康（`ok` / `warning`）→ `status: "holiday"`（非交易日）
     - 配額 `critical` → `status: "no_data"`（交給既有 CRITICAL retry）
   - 原本會回 `error`，現在區分假日 vs FinMind 慢同步

2. **`backend/run_finmind_etl_sdk.py`**
   - `daily_price` 回 `holiday` → 直接短路，後續 5 個 step 全標 `skipped_holiday`
   - `RESUMEABLE_STEP_STATUSES` 加入 `holiday` / `skipped_holiday`
   - `determine_overall_status()`：daily_price holiday → 整體 `holiday`
   - `main()` 新增 **exit code 5 = holiday**（workflow 視為 pass、不 retry）
   - `_run_critical_step_with_retry()` 維持原邏輯：只對 `no_data` retry

3. **`.github/workflows/daily_etl_update.yml`**
   - `timeout-minutes: 75 → 240`（首次 75 + sleep 90 + 重試 75 + buffer）
   - 三段 step：`etl1` →（僅在 exit 2 時）`sleep 5400` → `etl2`
   - 最終狀態評估：`etl2.etl_exit || etl1.etl_exit`
   - exit `0 / 1 / 5` → pass；`2 / 3` → fail
   - `insufficient_quota` 不再被當成靜默 pass；1.5h 後重試一次；假日自動跳過不 retry

4. **`backend/tests/test_run_finmind_etl_sdk.py`** 新增 3 案例：
   - `daily_price holiday` → 整體 `holiday`
   - CRITICAL step 回 `holiday` 不觸發 `no_data` retry
   - `skipped_holiday` 為 resumeable status

### Gotcha
- Holiday 偵測用「配額健康」當 signal；配額 critical 時即使 daily_price 空，也退回 `no_data` 走原 retry 路徑
- 若真實交易日遇到 FinMind 10+h 延遲導致 23:00 還沒資料，會誤判為假日 → 可接受的 trade-off
- workflow exit code 語義：`0 ok / 1 partial / 2 quota / 3 error / 5 holiday`

## 產業分類全面切換至 FinMind（2026-04-21）

### 背景
- 舊架構：`industry_daily_flow` 用 TWSE 名稱（`水泥工業` 等），`stocks_master` 用 Fugle 名稱（`水泥` 等），L1 API 靠 `INDUSTRY_NAME_FALLBACKS` + 後綴剝離硬接
- 舊架構的 `chain`（上游/中游/下游）是 Fugle 特有三層結構，FinMind 沒有
- 決策：**全面切 FinMind `TaiwanStockIndustryChain`**，徹底移除 Fugle 與 `chain` 欄位

### 執行步驟
1. **`backend/etl/fetch_stock_master.py` 重寫**：移除 Fugle CSV，改吃 `TaiwanStockInfo` + `TaiwanStockIndustryChain`；`chain` 寫 NULL、`source="finmind"`
2. **`backend/run_daily_etl.py` / `run_backfill.py`**：移除 `--fugle-mapping` argparse 與 `fugle_mapping_path` 參數
3. **`backend/run_finmind_etl_sdk.py` 新增 step 0**：`stocks_master`（non-CRITICAL），每日 ETL 自動 refresh 產業分類
4. **`backend/app/routers/industries.py`**：移除 `INDUSTRY_NAME_FALLBACKS` + `_candidate_industry_names`；`StockFlowItem` / `SubIndustrySummaryItem` schema 拔掉 `chain`
5. **`backend/app/ai_analyst.py` + `telegram_bot.py`**：移除 `stock.chain` 輸出（`供應鏈位置` / `⛓ 供應鏈`）
6. **`frontend/src/lib/api.ts`**：TS 型別拔掉 `chain`
7. **`frontend/src/components/StockList.tsx`**：移除 `CHAIN_ORDER` / `chainSortKey`；卡片由 `chain` 分組改為 `sub_industry` 分組，SummaryTable 移除「鏈」欄位
8. **`rebuild_industry_flow.py`** 全量重建：清空 `industry_daily_flow` 1672 個交易日 + 逐日 re-aggregate

### DB 欄位保留政策
- `stock_master.chain` 欄位**不做 migration**（避免 schema 震盪），ETL 永遠寫 NULL
- `source` 欄位統一寫 `finmind`
- 前端 / API schema / 測試 **完全不再引用** `chain`

### 重建後資料
- `industry_daily_flow` 從 267 筆（舊 TWSE 粗分類） → ~53 個產業 × 1672 天 ≈ 88,000 筆（FinMind 細分類）
- Render Postgres 端執行，每天 aggregate 約 5 秒，全量耗時約 2 小時
- L0→L1 drill-down 100% 對應（53/53 產業都能在 stocks_master 找到股票）

### 環境變數命名對齊
- 本地 `.env` 跟 Render dashboard 對齊：`TARGET_DATABASE_URL` → `DATABASE_URL`、`FINMIND_API_TOKEN` → `FINMIND_TOKEN`
- 2026-04-21 commit `bc51dc9` 已修完 `.env.example` / `backend/.env.example` / `infra/render/render.yaml.template` / `docs/operations/security_and_secrets.md` / `docs/architecture/architecture_overview.md`
- `backend/migrate_sqlite_to_postgres.py` / `backend/validate_migrated_data.py` 保留 `TARGET_DATABASE_URL`（migration 工具區別來源/目的，有 fallback 到 `DATABASE_URL`）

## Phase 3：使用者註冊 + 關注清單 + 加碼建議（規劃中，2026-04-21 啟動）

三個相依的 milestone，依 **M18 → M19 → M20** 順序執行。

### M18 使用者註冊系統
- **認證方式**：第一階段僅支援 Gmail OAuth（未來可能加其他 provider）
- **Admin local auth**：帳號 / 密碼由 Render env var `ADMIN_EMAIL` / `ADMIN_PASSWORD` 設定（給開發者繞過 Gmail 用）
- **Gating 範圍**：
  - 未登入：全站頁面可 render，但互動 **disable**（灰掉蓋提示「請登入」），**唯一例外**是首頁 M17 AI 交易分析（不需登入即可使用）
  - Telegram Bot 也要 gating：chat_id 需先綁定已註冊的 Gmail 帳號才能使用任何指令（Bot 第一次互動時引導至登入頁）
- **登入頁**：新增 `/login` 前端路由，含 Gmail OAuth 按鈕 + Admin local auth fallback 區塊
- **DB schema**：新增 `users` 表（email / provider / provider_user_id / is_admin / created_at）與 `user_sessions` 或 JWT token 機制；Telegram 綁定另建 `user_telegram_bindings`（user_id / chat_id / verified_at）
- **API**：`POST /api/auth/google/callback`、`POST /api/auth/admin-login`、`POST /api/auth/logout`、`GET /api/auth/me`

### M19 關注買進清單
- **前提**：M18 完成（清單必須綁使用者帳號）
- **資料持久化**：一律存 **Render Postgres**，不走 localStorage（跨裝置同步需求）
- **L0 sidebar 擴展**：把現在 L1 頁面左側的 sidebar 樣式套到 L0 首頁，兩層 UI 導覽一致
- **「關注買進清單」入口**：放在 sidebar 中（具體位置設計階段再定）
- **新增持股 popup**（shadcn/ui Dialog）：
  - 股票代號（autocomplete，沿用 `/api/stocks/search`）
  - 買進日期（date picker，預設最近交易日）
  - 均價（數字輸入，必填）
  - 按「儲存」寫入 `user_watchlist` 表
- **清單展開頁**（新路由，例如 `/watchlist`）：
  - 每檔持股一張卡片：顯示股票代號/名稱、買進日期、均價、今日股價、未實現損益 %（帶顏色）
  - 卡片**右下角「交易分析」按鈕** → 呼叫 M17 AI 交易分析 endpoint，`stock_id` / `buy_date` 從卡片資料帶入（使用者無需重新輸入）
- **DB schema**：`user_watchlist`（user_id / stock_id / buy_date / avg_price / created_at，複合 unique 視需求）

### M20 交易分析擴充：加碼建議
- **前提**：M19 完成（交易分析從卡片觸發，可以帶入 avg_price 作為 context）
- **新增分析段落**：在 M17 的 PART 2 中新增「如何操作以達到 45% 預期報酬率」段落
  - 加碼點位建議：跌到 X 加碼 / 漲到 Y 加碼
  - 停損與停利點位（配合風報比 1:1.75）
- **寫死參數**（不做 UI 調整）：
  - 目標報酬率 = **45%**
  - 風報比 = **1 : 1.75**（即每承擔 1 單位下行風險，追求 1.75 單位上行報酬）
- **實作方式**：修改 `backend/app/prompts/trade_quality.md`（canonical），同步 `docs/trade_quality_prompt.md`（鏡像）。程式碼只需把 avg_price 加進 context，不改 API 契約。
- **JSON schema 調整**：`if_strong` 視需要新增 `add_position_levels: [{price, reason}, ...]` 欄位

### M21 Trade Quality Context 資料管線
- **定位**：與 M20 平行互補。M20 是 prompt 工程；M21 是把 DB raw data 預聚合成「結論層」訊號，避免 AI 自己瞎推
- **輸出**：`build_stock_analysis_input(stock_id, buy_date) -> dict`，回傳 6 區塊結構化 JSON：
  - `industry_summary`（hot_score / hot_level / price_strength / volume_trend / institution_flow / capital_type / is_false_hot）
  - `chip_summary`（foreign/trust/dealer buy_days / volume_trend / price_trend / is_accumulation / chip_strength）
  - `peer_rank`（return_5d_percentile / volume_percentile / institution_rank_percentile / leader_or_follower）
  - `fundamental`（revenue_yoy / revenue_mom / guidance）
  - `price_structure`（trend / is_breakout / is_consolidation / is_accelerating）
  - `news_input_stub`（query_stock / query_industry / date_end，給未來 M14 接入）
- **可行度**：~92%。`industry_news_heat` / `guidance` 兩欄必為 `null`（無 DB 來源，未來 M14 輿情 ETL 完成再補）
- **完整 spec**：`docs/plans/trade_quality_context_spec.md`（含 DB 欄位對照表、SQL 範例、常數門檻、null 政策）
- **實作原則**：
  - 只用 `buy_date` 當日及以前資料（no hindsight bias）
  - 規則 deterministic、可測試、不用 LLM 判斷
  - 所有門檻常數集中 `backend/app/analysis/context_thresholds.py`
  - Raw extraction 與 derived signal 分開寫（未來加欄位容易擴充）
- **技術注意**：
  - `industry_daily_flow` 只有法人淨買超，**沒有** volume → industry_volume_trend 要從 `daily_price` + `stocks_master` 跨股聚合
  - peer_rank 用 `PERCENT_RANK() OVER (PARTITION BY industry_name)` 即時算（同產業小集合速度可接受）
  - 連續買超 N 日建議 Python loop 從最新日往回數（SQL `SUM(CASE WHEN) OVER` 可讀性差）
  - Lookback 一律以**交易日**為單位（`ORDER BY trade_date DESC LIMIT N`），非 calendar days

## Phase 4：異常訊號 + 自訂策略回測（規劃中，2026-04-23 啟動，2026-04-24 修訂）

兩個獨立 milestone，共同核心：**deterministic filter / 引擎是骨幹，LLM 是輔助層**。

### 設計原則
- LLM 不預測股價、不替使用者挑股、不出操作手冊
- LLM 做兩件事：(1) 把 deterministic 訊號翻成中文白話（解釋層）、(2) 在 trigger 觸發當下依當下 context 給 yes/no 提示（現場判斷層）
- 拔掉 LLM 系統還能跑：filter 結果還在、回測結果還在
- 「有它更好、沒它也不殘」— 這是 LLM 在本專案的標準位置

### 廢棄的舊規劃（2026-04-24 review 後）
- ~~每日 AI 推薦 3 檔 60 天 +45%~~ — 60 天 +45% ≈ 年化 800% 不現實；LLM 也不產 alpha
- ~~LLM 出買進後操作手冊~~ — 攤平本身是 blow-up 風險策略；LLM 出規則容易 overfit；使用者拿到「AI 規則」反而更難違背
- 同時也廢棄前一版 M23 / M24 的「零 LLM」走極端設計，改回「核心引擎 + LLM 輔助」

---

### M23 每日異常訊號清單（2026-04-25 改版）

> **Canonical spec**：[docs/plans/m23_daily_signals_spec.md](docs/plans/m23_daily_signals_spec.md)
> LLM prompt：[backend/app/prompts/watch-list-stock.md](backend/app/prompts/watch-list-stock.md)

**你想解決的問題**：每天早上需要一份「今天值得看一下」的清單，不用自己翻產業排行 + 法人 + 融資融券一檔一檔比對；並且要找出「熱錢主線正在擴散到哪裡」（不只是已經漲的）。

**輸出三類股票**（無預測 / 無目標價 / 無 BUY-SELL，僅 WATCH / REMOVE）：
- **LEADER**：產業中最早上漲、漲幅領先、資金排名靠前、法人連買、量能放大、題材明確
- **FOLLOWER**：與 LEADER 同產業 / 同供應鏈、已同步上漲但漲幅不如 LEADER、籌碼仍支持
- **LAGGARD**：同產業 LEADER 已漲、該股漲幅落後、業務題材高度相關、法人/量能開始轉強、技術 early_turn

每檔附 **500–1000 字繁體中文 reason**（13 點強制要點，見 prompt「reason 寫作規則」）。

**Pipeline（10 步）**：data ingestion → industry rank → stock rank → candidate pool → peer/group expand → deterministic filter → LLM research → LLM explanation → persist snapshot → update job status。詳見 spec §2 / §5。

**Deterministic 部分（DB + 程式）**：
- 候選池：top_stocks_3d 40 + top_industries_3d 10 成分股 + 熱門產業龍頭 + 同供應鏈 + 集團股（spec §6，目標 60–120 檔）
- LEADER / FOLLOWER / LAGGARD candidate 預分類（spec §7）
- Hard exclusions：ETF、金融股、流動性不足、近 3 日漲超 15%（spec §9.1）
- Soft filters：retail_overheated / distribution / range_bound（spec §9.2）

**LLM 部分**（**支援 web search 的模型**，例如 `gpt-4o-search-preview`）：
- 上網查 market_state（VIX / 美股 / 台指期 / USD-TWD）→ STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK
- 上網查公司業務、產業鏈位置、題材延續性（≥ 1Q 才合格）、龍頭股 / 集團股表現
- 一檔一檔不行（cost 高），**5~10 檔 batch 一次 prompt**

**前置工作**：
- ✅ 新增 `margin_trade` 表 + `etl/finmind_margin_trade_sdk.py`（FinMind `TaiwanStockMarginPurchaseShortSale`；併入 `run_finmind_etl_sdk.py` 為 step 7，non-CRITICAL；2026-04-25 完成。2026-04-27 切換為 dataset-level fetch + 補齊 2026-03-26 ~ 2026-04-24 共 25,168 筆 backfill）
- ✅ 新增 `signal_snapshots` 表（一日一筆 UPSERT；存完整 LLM JSON + cost tracking；2026-04-25 model 完工）
- ✅ 新增 `signal_generation_jobs` 表（job_id / status / progress_pct / current_stage；給前端進度條 polling；2026-04-25 model 完工）
- 🚧 M23 延伸：40 交易日訊號追蹤清單（watchlist 命中歷史、hit count、追蹤第 N 天、報酬率與報告時間軸），spec 在 [docs/plans/m23_signal_archive_spec.md](docs/plans/m23_signal_archive_spec.md)
- 2026-04-30 新增 `signal_watch_completed_archives`：當一檔股票完成一個 40 交易日追蹤 cycle 後，封存 `first_seen_date / hit_count / return_day_10_pct / return_day_20_pct / return_day_30_pct / return_day_40_pct / completed_trade_date`；若未來同檔重新被抓到，會以新的 `first_seen_date` 再新增一列
- 2026-04-30 修正 `signal_watch_returns` 更新口徑：同一檔股票在 active 40 日追蹤 cycle 內的所有 `signal_watch_hits`，都要一起同步 `baseline_trade_date / baseline_price / latest_eval_trade_date / latest_eval_price / return_pct`；不能只更新最新一列，否則第 2 天後部分列會停在 `0%` 或舊值
- 2026-04-30 再補一層 guard：若 `trade_date == baseline_trade_date`（也就是第 2 天建立 baseline 的當天），即使人工或排程同日重跑 `run_signal_archive_returns.py`，`latest_eval_price` 也要維持 `baseline_price`，`return_pct` 必須強制是 `0.0`，不可先用同日收盤價算出正負報酬
- `run_signal_archive_returns.py` 現在必須支援手動帶日期（`python3 run_signal_archive_returns.py 2026-04-30`），而且若未帶日期，20:00 前預設要回前一天，避免凌晨手動補跑時誤打到休市日 / 當日無資料
- `run_signal_archive_returns.py` 的「未帶日期預設值」不能只看曆日，必須看 DB 內最近交易日：
  - 開盤日 20:00–23:59 → 用當天交易日
  - 每天 00:00–19:59 → 用前一個交易日
  - 若當天沒開盤（假日 / 休市）→ 自動回退到前一個交易日
- 這類 `40日追蹤` 報酬率修正 deploy 後，必須手動補跑 `backend/run_signal_archive_returns.py` 一次，才會把 DB 內既有 active rows 回補正確
- ✅ `main.py` lifespan 新增 `_ensure_m23_tables()`：自動 idempotent `CREATE TABLE IF NOT EXISTS`（仿 M18/M19 pattern）

**API**：
- `GET /api/signals/latest`（公開）
- `GET /api/signals/snapshot/{date}`（公開）
- `GET /api/signals/jobs/latest`（公開，前端 polling 用）
- `GET /api/signals/quota`（登入後可讀；前端 disable 與剩餘次數顯示用）
- `POST /api/signals/regenerate`（登入即可；每帳號每日 3 次、`failed` 不計次、同日全站 15 次上限、同日 running job 拒絕並發）

**觸發方式：使用者手動**（2026-04-27 改版，原排程已停用）
- 觸發路徑：前端 `DailySignalsPanel`「重新產生」按鈕 → POST `/api/signals/regenerate` → FastAPI `BackgroundTasks` 在 Render web service 直接執行 pipeline
- `.github/workflows/daily_signals.yml`：cron 已移除；保留 `workflow_dispatch` 作管理備援（例如 prod backfill 或 Render background task 暫不可用時用 `gh workflow run` 補跑）
- **Render web service 必須設 `OPENAI_API_KEY` env**（與 GitHub secret 是兩套，frontend 觸發走 Render 不走 Actions runner）

**前端 L0 tab bar UX**（`<DailySignalsPanel />`）：
- 版位：L0 首頁 TradeQualityAnalysis 之後、HotMoneyList 之前
- 4 個 tab：LEADER / FOLLOWER / LAGGARD / REMOVED（顯示各組 count）
- **跳跳跳通知**：localStorage 存 `last_seen_snapshot_date`，比對最新 snapshot 有更新 → header 旁顯示綠色 `animate-ping` 點 + 「新」字；點任一 tab 後寫回 localStorage 取消通知
- **多工背景產生**：點「重新產生」→ POST `/api/signals/regenerate` → 回 202 + job_id → server BackgroundTasks 跑 → 使用者可以離開頁面繼續用其他功能
- **進度條**：留在頁面時 polling `/api/signals/jobs/latest` 每 3 秒一次；顯示 `progress_pct` 與 `progress_label`（例：「正在分析第 28 / 45 檔」）
- **重產額度**：header 讀 `/api/signals/quota` 顯示今日剩餘次數；達每日 3 次時按鈕 disable；若當次 job `failed`，額度自動釋回
- **追蹤入口**：`DailySignalsPanel` header 已提供 `40日追蹤` 入口，進到 M23 訊號追蹤清單頁；頁面目前包含 active summary 與 completed archive 兩張表，completed table 初期無資料時顯示「暫無資料」
- **首頁 bootstrap**：除了 deferred mount，首頁現在還會先集中預抓 `latest trade date`、`latest signals snapshot`、`latest signal job`、`market hot money`、`industries`，再把初始 payload 灌給各 panel，避免 mount 後各元件再各自重打一次
- **首頁 client cache**：前端目前對 `latest trade date`、`/api/industries`、`/api/market/hot-money`、`/api/signals/latest` 做短 TTL client cache；但 `DailySignalsPanel` 在 regenerate 後重抓 snapshot 時必須 `bypassCache`，避免看到剛重產前的 stale 資料
- **首頁 loading UX**：首屏現在有 boot loading overlay，會顯示正在載入哪幾塊資料與總進度；不要回退成只有整頁 skeleton / spinner 而沒有載入語意
- **40日追蹤頁文案**：報酬率規則要直接寫成「第一個交易日抓到 = `--`、第二個交易日用 `(open + close) / 2` 建 baseline 並固定 `0.00%`、第三個交易日起才開始計算報酬率」，避免使用者把「第二天」誤解成「第二次命中」
- 離開頁面再回來：mount 時 polling 自動接上最新進度（不依賴 long-lived connection）

**使用流程**：
1. 早上開首頁 → tab bar 旁有跳跳跳綠點 → 知道有新報告
2. 點 tab 看 LEADER / FOLLOWER / LAGGARD 各組 → reason 一目了然
3. 對某檔有興趣 → 點卡片跳 L2 深入研究
4. 也可以隨時點「重新產生」觸發新一輪分析（背景跑、不擋使用者操作）

---

### M24 自訂進出場策略回測

**你想解決的問題**：買進一檔股票後常常面臨「該攤平 / 該停損 / 該追加碼 / 該落袋」，沒有事先想好的紀律。需要一個工具「驗證自己的操作規則有沒有 edge」，並在當下提供現場判斷。

**第一階段：使用者自訂規則（核心，使用者寫不是 LLM 寫）**

四區塊 form：
- 分層進場：基準買進價 + 下跌加碼階梯（跌 -X1% 加碼 Y1% 資金 …）
- 追價加碼：漲 +A1% 加碼 B1% 資金、漲 +A2% 加 B2% 資金 …
- 停損：絕對價 / 基準 -X% / 跌破 N 日均線（三選一或多）
- 停利：目標價 / 漲幅 +X% / 跌破 N 日均線確認

**第二階段：歷史回測（核心）**

引擎拿這組規則套在該股近 3 年資料，輸出：
- 觸發進場次數、勝率、平均達停利的實際報酬、平均停損實際虧損
- 過程中最大帳面虧損
- 累計報酬 vs Buy & Hold 同期
- equity curve、每層成交點標記、累計投入資金曲線

**回測引擎擴充**（現有 `backend/app/backtest_engine.py` 為 long-only + 單一進場點）：
- 多層分批進場 / 加碼（position sizing）
- 每層獨立成交價 + 累計持倉追蹤
- 停損停利擴充支援絕對價 / 均線條件（既有 `stop_loss_pct` / `take_profit_pct` 為基礎延伸）

**第三階段：LLM 現場判斷（輔助）**

當使用者**已買進**且**價格走到下一個 trigger 點**時，LLM 用當下的籌碼 / 產業 / 技術 / 基本面 / 題材給判斷：

- 情境 A：規則寫「跌 -5% 加碼」今天觸發 → LLM 提示：「外資 5 日連賣、產業熱度退潮、跌破 60 日均線、季 EPS 低於預期 → 建議考慮停損不攤平」
- 情境 B：規則寫「漲 +8% 加碼」今天觸發 → LLM 提示：「法人連買、突破前高 + 量能放大、月營收 YoY +30% → 可加碼，注意短線過熱」

LLM 做：在 trigger 觸發當下，把 deterministic 抓出的籌碼/技術/基本面狀態翻成「適合 / 不適合執行」判斷
LLM 不做：替使用者寫規則、告訴使用者「該買哪檔」、取代回測結果（回測說沒 edge 就不該無腦執行）

**入口**
- `/watchlist/[entry_id]/strategy`（從 watchlist 卡片點「回測操作策略」進入）
- L2 個股頁新 tab「操作策略回測」（沒持股也能玩）

**API / UI**
- API：`POST /api/backtest/custom-strategy`（繼承 M11 既有回測輸出格式 + 新欄位）+ `POST /api/strategy/check-trigger`（trigger 觸發時呼叫 LLM 給現場判斷）
- UI：新元件 `CustomStrategyPanel`（沿用 `BacktestEquityChart`）四區塊 form + equity curve + 分層成交標記 + LLM 現場提示卡片

**目標參數不寫死**
- 使用者自己輸入目標報酬 / 可容忍回撤，回測結果顯示是否達成
- 不預設 25% / 10% 等具體數字（前一版規劃寫死的數字捨棄）

**與 M17 / M19 銜接**
- 從 watchlist 卡片進入時 `buy_price` 自動填入 avg_price
- 從 /stocks/{id} 進入是空白 form

---

### 整體 LLM 定位（橫跨 M23 / M24）

| 角色 | 做什麼 | **不**做什麼 |
|------|--------|-----------|
| **解釋層**（M23） | 翻譯 deterministic 訊號 + 上網查公司業務／集團／龍頭比對；判斷 market_state；產 LEADER/FOLLOWER/LAGGARD 三類 reason | 不預測股價、不出目標價、不排推薦度、不發 BUY/SELL |
| **現場判斷層**（M24） | 在 trigger 觸發當下給 yes/no 提醒 | 不替使用者寫規則、不取代歷史回測 |

決策權永遠在使用者手上：
- M23 篩出清單給看，使用者決定要不要研究
- M24 規則使用者寫、回測算 edge、LLM 在當下提醒，使用者決定要不要按下加碼鍵

## M18 使用者註冊系統完成（2026-04-21）

### 最終範圍（與原規劃差異）
- **Auth**：Email/password 單純註冊登入（**無** Gmail OAuth、無 email 驗證、無密碼重設）。未來要加 OAuth 只需在 `users` 加 `provider` 欄位 + 新 callback
- **Session**：Server-side session（UUID token in httpOnly cookie，30 天過期，可 revoke）；非 JWT、非 localStorage
- **Telegram 綁定**：整個 drop，不做 `user_telegram_bindings`
- **Admin 帳號**：由 `ADMIN_EMAIL` / `ADMIN_PASSWORD` env 設定（必填，未設時 `get_admin_password()` 會 raise；`_seed_admin_user` 啟動時失敗會被 except 吃掉，server 仍會起來但 admin 帳號未 seed）
- **⚠️ 為何不是 `admin@local`**：Pydantic `EmailStr` 會拒收無 TLD 或 RFC 2606 保留 TLD（`.local` / `.test` / `.localhost` / `.internal` / `.invalid` / `.example`）的 email，`/api/auth/login` 會 422 而進不了 handler。預設必須是**真實 TLD**的 email。`tests/test_auth_router.py::test_admin_seeder_default_email_passes_pydantic_emailstr` 保護這個 invariant

### DB Schema
- `users`：`id / email (unique) / password_hash (bcrypt) / name / is_admin / is_active / created_at / last_login_at`
- `user_sessions`：`session_id (UUID) / user_id / created_at / expires_at / last_seen_at / user_agent / ip_address / revoked_at`
- Migration：`backend/migrate_add_users.py`（`Base.metadata.create_all()`，idempotent）

### API
- `POST /api/auth/register`（password ≥ 8 碼，自動登入）
- `POST /api/auth/login`（uniform 401 避免 email 枚舉）
- `POST /api/auth/logout`
- `GET /api/auth/me`

### Gating 範圍

#### 後端 `Depends(require_user)`
- `/api/backtest/interpret` / `run` / `advice`（L3 回測全部需登入）

#### 後端分層 rate limit（`/api/analysis/trade-quality` 維持公開）
- 未登入：**3/day** by IP
- 已登入：**30/day** by `user:{id}`
- 實作：`backend/app/rate_limit.py` 的 `trade_quality_limit_value(key: str)` 依 key prefix 決定限額（**slowapi dynamic limit signature 必須吃 `key: str`，不是 `Request`**）
- Storage: in-memory（夠用；未來多機部署再換 Redis）

#### 前端 `<RequireAuth>` 包住的路由
- `/industries/[industryName]`
- `/stocks/[stockId]`
- `/stocks/[stockId]/backtest`

#### 前端公開路由
- `/`（首頁，含 `<TradeQualityAnalysis />`）
- `/login`

### 關鍵 Gotcha
- `get_optional_user` 必須寫 `request.state.auth_user_id = user.id`，否則 rate limit key 無法辨識使用者
- FastAPI 測試用 `app.dependency_overrides[require_user] = lambda: test_user` 繞過認證
- slowapi counter 跨測試累加，fixture 需要 `limiter.reset()`
- Pydantic `EmailStr` 需安裝 `email-validator` 套件
- 前端所有 API 呼叫改用 `apiFetch`（`credentials: "include"` wrapper），否則 cookie 不會帶
- CORS middleware 必須 `allow_credentials=True`
- 密碼雜湊用 bcrypt（`backend/app/auth.py::hash_password` / `verify_password`）

## FinMind inst_flow amount_est 漏寫 bug 修復（2026-04-22）

> **狀態（2026-04-22 更新）**：Code 層已於 commit `6deefae` 修復並 push（`finmind_inst_flow_sdk.py` 修法 + `backfill_inst_flow_amount_est.py` + 單元測試），但 **prod DB backfill 尚未執行**，所以 4/10~4/15 的 `inst_stock_flow.buy_amount_est` 仍為 0.0、`industry_daily_flow` 法人金額仍為 0。要完全落地還需執行：
> 1. `python backend/scripts/backfill_inst_flow_amount_est.py`（對 prod 連線）
> 2. `python backend/rebuild_industry_flow.py --from 2026-04-10 --skip-master`
> 3. 補跑 4/20 / 4/21 因配額中斷的 `valuation / monthly_revenue / financial_statement`

### 問題現象
- L0 / L1 產業卡片在切到 FinMind 後，近期日期可能顯示「法人買賣超 +0.0 億」
- 單日 `inst_stock_flow` 有 `buy_shares / sell_shares`，但 `buy_amount_est / sell_amount_est / net_amount_est` 為 `NULL`（或預設 0）

### 根因
- `backend/etl/finmind_inst_flow_sdk.py` 只寫 shares，漏掉 `*_amount_est`
- `backend/etl/aggregate_industry_flow.py` 聚合讀的是 `*_amount_est`，所以 `industry_daily_flow` 會被聚合成 0.0

### 修法
1. `backend/etl/finmind_inst_flow_sdk.py` 在 groupby 後 join `daily_price.close_price`，寫入 `*_amount_est = shares * close_price`
2. 新增 `backend/scripts/backfill_inst_flow_amount_est.py`，回填既有 `source='finmind'` 且 `*_amount_est IS NULL` 的資料
3. 回填後重跑 `python backend/rebuild_industry_flow.py --from 2026-04-10 --skip-master`
4. 補跑中斷日期的 `run_finmind_etl_sdk.py`

### Gotcha
- 金額單位是元，前端再自行除以 `1e8` 轉億
- 若個別 `(trade_date, stock_id)` 缺 `daily_price.close_price`，amount fallback 為 `0.0`

## Industry date fallback + admin login gotcha（2026-04-22）

- `IndustryDashboard` / `StockList` 對應的 `/api/industries*` 路由現在應比照 `/market`：
  若使用者選到非交易日，後端自動 resolve 到 `<= requested_date` 的最近交易日，而不是直接 404
- 首頁點產業時，必須帶 **目前 component state 的 date**，不能帶外層舊的 query param date，否則會出現 UI 選了 `3/4`、實際跳頁卻還是舊日期的 race condition
- `/login` 前端不能對 login mode 一律套 `minLength=8` / `password.length < 8` 驗證，否則少於 8 碼的既有帳號（含 admin 若 `ADMIN_PASSWORD` 設成短密碼）永遠送不到後端
- 註冊仍維持最少 8 碼；只有登入要允許短於 8 碼的既有帳號

## Render production M18 表沒建起來修復（2026-04-22）

### 問題
- Render 後端 log 顯示 `psycopg.errors.UndefinedTable: relation "users" does not exist`
- 啟動 `_seed_admin_user` 失敗被 `except Exception` 吃掉；`POST /api/auth/login` 500 → 前端看到 `Failed to fetch`
- 根因：`backend/migrate_add_users.py` 從未在 Render 上手動跑過

### 修法（backend/app/main.py）
- `_seed_admin_user` 改成啟動時先 `Base.metadata.create_all(bind=engine, tables=[User.__table__, UserSession.__table__])` 再 seed admin
- `create_all` 是 `CREATE TABLE IF NOT EXISTS`，對既有資料 no-op；不會 DROP / ALTER
- 未來 Render 重啟或新機器部署都會自動 idempotent 建表，不需要人工進 shell 跑 migration
- schema 演進（加欄位、改型別）仍需另外寫 migration，`create_all` 管不了

## 首頁 UX 修復（2026-04-22）

### Navbar 登入按鈕 loading 卡住
- 問題：`AuthProvider` 在 mount 時打 `/api/auth/me`，Navbar 在 `status === "loading"` 只顯示 `…`；Render 冷啟動慢時按鈕長時間看不見
- 修法：[frontend/src/components/Navbar.tsx](frontend/src/components/Navbar.tsx) 把 loading 狀態也顯示「登入/註冊」按鈕，加上 `opacity-70` + `animate-pulse` 小圓點提示仍在載入
- 已登入使用者若誤點，`/login` 頁本身的 `useAuth` 會自動 `router.replace(next)` 跳回首頁，不影響流程

### 產業法人流向預設日期
- 問題：首頁預設 `todayInTaipei()`，台灣白天打開時當日 ETL 還沒跑，顯示空或 resolve 到昨天造成 UI 日期與資料不一致
- 修法：[frontend/src/app/page.tsx](frontend/src/app/page.tsx) 改成先用 `todayInTaipei()` 當 initial state，mount 後打 `fetchLatestTradeDate()`（`GET /api/market/latest-trade-date`）拿 DB 最近有資料的交易日並覆寫；有 `?date=` query param 時尊重使用者選擇，不覆寫

## M22 熱錢湧入個股排行完成（2026-04-22）

### 後端
- [backend/app/hot_money_service.py](backend/app/hot_money_service.py) — L0/L1 共用服務
  - `get_recent_trade_dates(db, end_date, days, stock_ids=None)`：以 `inst_stock_flow.trade_date DESC LIMIT N` 取 N 個交易日（非曆日）
  - `compute_hot_money(db, end_date, days, limit, stock_ids=None) -> HotMoneyResult`：聚合 `inst_stock_flow.net_amount_est`，SQL 層 `inst_type.in_(("foreign","trust","dealer"))` 過濾非三大法人
  - `price_change_pct`：窗口尾日 `close_price` / 窗口首日前一交易日 `close_price` - 1；任一端缺值回傳 `None`
- API endpoints：
  - `GET /api/market/hot-money?date=&days=3&limit=20` — L0 全市場
  - `GET /api/industries/{industry_name}/hot-money?date=&days=3&limit=10&sub_industry=` — L1 單產業
- 共用 pydantic schema：[backend/app/routers/market.py](backend/app/routers/market.py) 的 `HotMoneyResponse` + `serialize_hot_money_result`，由 industries router import 重用避免 duplication
- 測試：[backend/tests/test_hot_money_service.py](backend/tests/test_hot_money_service.py)（9 案例）+ [backend/tests/test_hot_money_router.py](backend/tests/test_hot_money_router.py)（10 案例）全部 pass

### 前端
- [frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) — 共用元件，props `{ industryName?, subIndustry?, date, days?, limit?, title? }`
  - `industryName` 有值 → 呼叫 L1 endpoint，預設 `limit=10`；無值 → L0，預設 `limit=20`
  - 點列跳 `/stocks/{id}?date={date}`
  - useEffect dependency `[industryName, subIndustry, date, days, effectiveLimit]` → **日期改變自動重新 fetch**
- L0 放 [frontend/src/app/page.tsx](frontend/src/app/page.tsx) 最下方（`IndustryDashboard` 下）
- L1 放 [frontend/src/components/StockList.tsx](frontend/src/components/StockList.tsx) 最上方（header 下、主表格上）

### Gotcha（L0 日期同步 bug 修正）
- 初版 page.tsx 的 `defaultDate` 是 `useState` initial value，只在 mount 時讀一次 `queryDate`
- 當使用者透過 `IndustryDashboard` 的 date picker 換日期 → `onDateChange` 把 URL 改成 `?date=X` → `queryDate` 更新，但 `defaultDate` 不會 re-sync，導致 `HotMoneyList` 停在舊日期
- 修法：把 `defaultDate` 改成 **derived value** `queryDate ?? latestTradeDate ?? todayInTaipei()`，`latestTradeDate` 才是 state；任何一邊變動都會讓 `defaultDate` 重算
- L1 的 StockList 內部管自己的 `date` state，靠 `setDate` 更新，HotMoneyList 直接收 prop 無此問題

## M19 關注買進清單完成（2026-04-23）

### 後端
- [backend/app/models.py](backend/app/models.py) — 新增 `UserWatchlist` model：`(user_id, stock_id, buy_date, avg_price)` + UNIQUE `(user_id, stock_id)`
- [backend/app/main.py](backend/app/main.py) — lifespan `Base.metadata.create_all` 自動建表（idempotent，避免 Render 手動 migration）
- [backend/app/routers/watchlist.py](backend/app/routers/watchlist.py) — 4 個 CRUD endpoints（全部 `Depends(require_user)`）：
  - `GET /api/watchlist` → `{ items, total, capacity }`（join `stocks_master` + 最近 `daily_price.close_price`，回傳 `unrealized_pct`）
  - `POST /api/watchlist` → 201；`404` unknown stock / `409` duplicate / `409` at cap（20）
  - `DELETE /api/watchlist/{entry_id}` → 204；僅能刪自己 entry（他人 entry 回 `404`，不洩漏存在性）
  - `DELETE /api/watchlist` → 204 bulk clear
- [backend/tests/test_watchlist_router.py](backend/tests/test_watchlist_router.py) — 14 案例 pass（空清單、auth 缺、add/dup/404/at cap、delete own/other/unknown、clear own only、null price、invalid avg_price）
- 常數 `WATCHLIST_MAX_ENTRIES = 20`

### 前端
- [frontend/src/lib/api.ts](frontend/src/lib/api.ts) — `WatchlistItem` / `WatchlistResponse` 型別 + 4 個 API function；`fetchWatchlist` 於 401 回空 response（匿名時靜默）
- [frontend/src/lib/watchlist.tsx](frontend/src/lib/watchlist.tsx) — `WatchlistProvider` context（mirror `AuthProvider`），exposes `items/total/capacity/has/entryIdOf/add/remove/clear/refresh/isReady`；auth status 變化時自動 refresh
- [frontend/src/components/AppProviders.tsx](frontend/src/components/AppProviders.tsx) — `<AuthProvider><WatchlistProvider>{children}</WatchlistProvider></AuthProvider>` 雙層包裝
- [frontend/src/components/WatchlistAddDialog.tsx](frontend/src/components/WatchlistAddDialog.tsx) — base-ui `@base-ui/react/dialog`（**不是 shadcn CLI**；專案既有 base-ui primitives）；輸入 buy_date（預設台北今天）+ avg_price
- [frontend/src/components/WatchlistAddButton.tsx](frontend/src/components/WatchlistAddButton.tsx) — 共用按鈕，5 狀態：未登入（→ /login）/ 載入中 / 已加入（綠 disabled）/ 已滿 20/20（琥珀，→ /watchlist）/ 可加入（天藍，開 dialog）；`e.stopPropagation()` 防止觸發父層 row 導航
- 入口整合：
  - [frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) — 表格新增「清單」欄（compact variant）
  - [frontend/src/components/StockList.tsx](frontend/src/components/StockList.tsx) — 每張個股卡片右下角（compact variant）
  - [frontend/src/app/stocks/[stockId]/page.tsx](frontend/src/app/stocks/[stockId]/page.tsx) — 個股頁 header 右側（default variant）
- [frontend/src/app/watchlist/page.tsx](frontend/src/app/watchlist/page.tsx) — 新路由（`<RequireAuth>`）：持股卡片顯示買進日/均價/最新收盤/未實現損益 %；右上角 ✕ 單檔移除；頂部「清空清單」→「確定清空？」兩段式確認；每張卡片「交易分析 →」按鈕 `router.push("/?stock_id=XXX&buy_date=YYYY-MM-DD#trade-quality")`
- [frontend/src/components/TradeQualityAnalysis.tsx](frontend/src/components/TradeQualityAnalysis.tsx) — URL prefill：讀 `useSearchParams()`，有 `stock_id`+`buy_date` 時 one-shot 呼叫 `searchStocks` 解析後觸發 `handleAnalyze`；外層 `id="trade-quality" scroll-mt-16` 支援 hash anchor
- [frontend/src/components/Navbar.tsx](frontend/src/components/Navbar.tsx) — 已登入且不在 `/watchlist` 時顯示「我的清單 N/20」

### Gotcha
- **`useSearchParams` 在 Next 16 必須包 `<Suspense>`**：`frontend/src/app/page.tsx` 的 `HomeContent` 已在 Suspense 內；TradeQualityAnalysis 直接在其中使用即可
- **URL prefill 用 key-based ref**：`lastPrefillKeyRef.current = "sid|bd"`，與上次比對；確保同一個 mounted component 下連點多檔不同股票會重新 prefill（不能用布林 one-shot 守門）
- **Prefill 後延後呼叫 analyze**：先 set `selected`，再用 `pendingAnalyze` flag + 第二個 effect 等 state commit 後才呼叫 `handleAnalyze`，避免用 stale `selected=null`
- **UI 不採 shadcn CLI**：專案 dep 只裝 `shadcn` 本體但**所有 UI primitive 都是 base-ui**（`select.tsx` 已採 `SelectPrimitive from "@base-ui/react/select"`）；新 dialog 也用 `@base-ui/react/dialog`，別跑 `npx shadcn@latest add dialog` 污染
- **apiFetch 必帶 `credentials: "include"`**：watchlist API 全走 session cookie；若直接 `fetch` 會變匿名呼叫 → 401
- **刪除他人 entry 回 404 不是 403**：避免枚舉攻擊（existence oracle）
- **卡片停靠按鈕 bubble**：`WatchlistAddButton` / 清單 ✕ 按鈕皆需 `e.stopPropagation()`，否則會觸發 HotMoneyList / StockList row 的 `router.push` 跳到個股頁

### Code review 後補強（2026-04-23）
- `UserWatchlist.user_id` 加上 `ForeignKey("users.id", ondelete="CASCADE")`：未來刪帳號時 watchlist 自動清除，不留孤兒列
- POST 加 `buy_date > _today_taipei()` guard（400 `買進日期不能是未來`），避免 M20 context 被未來日期汙染
- **`_today_taipei()` 以 `Asia/Taipei` 為準**（follow `analysis.py:42` 既有 `TAIPEI_TZ = ZoneInfo("Asia/Taipei")` pattern）：Render server 跑 UTC，若用 `date.today()` 會在台北 00:00~08:00 把使用者選的「今天」誤判為未來；測試用 `monkeypatch.setattr(watchlist_module, "_today_taipei", lambda: date(...))` freeze 時間驗證
- `avg_price` **暫留 Float**（與 `daily_price.close_price` 一致）；M20 加碼建議納入 avg_price 精確運算時一併換 `Numeric(12, 4)`（model column + migration）
- `router/watchlist.py` 檔頭註記：20 檔 cap 為 best-effort，雙 tab 並發可能插到第 21 筆（機率極低、無正確性影響）
- `api.ts` 移除死判斷：`204` 已屬 `res.ok=true`，`&& res.status !== 204` 不會 evaluate，改為單純 `if (!res.ok)`

### Daily ETL target date 跨日 bug 修復（2026-04-23）

**問題**：2026-04-22 的排程跑起來 DB 完全沒新資料，卡在 4/21 → 4/23 之間整天空白。

**根因**：
- `.github/workflows/daily_etl_update.yml` cron `0 15 * * 1-5`（UTC 15:00 = 台北 23:00）
- GitHub Actions cron 常延遲 10~90 分鐘，4/22 實際 UTC 16:12（台北次日 00:12）才 run
- `TARGET_DATE=$(date +%F)` 在 `TZ=Asia/Taipei` 下給**次日**（4/23，尚未開盤）
- FinMind 回空資料 → 新的 holiday short-circuit（2026-04-22 引入）判定為假日 → 7 個 step 全 skip
- 4/22 的資料根本沒被抓過

**修法**：
- `Resolve target date` 改成 `TARGET_DATE=$(date -d '4 hours ago' +%F)`
- 即使 cron 延誤到台北 00:30，往前推 4 小時仍落在當日 20:30 → `date +%F` 給正確的當日
- 手動觸發（`workflow_dispatch`）填 `target_date` input 時尊重之，不受 offset 影響

**Backfill 4/22**：`gh workflow run daily_etl_update.yml --ref main -f target_date=2026-04-22`（run 24828955408）

**Gotcha**：GitHub Actions cron 延遲是常態；任何「跑當日」的 workflow 都要內建足夠 offset buffer，避免邊界日跨天

### M19 上線後 bug 修復（2026-04-23）
M19 merge 之後使用者回報四個問題，一次修掉：

1. **登入後加入清單仍 401「未登入或登入階段已失效」（跨站 cookie）**
   - 根因：[backend/app/auth.py](backend/app/auth.py) 的 `set_session_cookie` 把 `samesite` 寫死 `"lax"`。Production 部署是 Vercel（前端）↔ Render（後端）跨站，Lax cookie 不會被瀏覽器帶到跨站 `fetch()`（即使 `credentials: "include"`），`/api/watchlist` POST 拿不到 session → 401
   - 修法：`samesite = "none" if is_cookie_secure() else "lax"`；production 設 `COOKIE_SECURE=true` 自動切 None，本地 dev（兩端都是 localhost，same-site）繼續 Lax
   - 為何不直接永遠 None：Chrome 拒絕 `SameSite=None` 但 `Secure=false` 的 cookie，本地 dev http:// 會破
   - Regression test：[backend/tests/test_auth_router.py](backend/tests/test_auth_router.py) `test_session_cookie_samesite_follows_secure_flag`

2. **L0 點「加入清單」整列跳到個股頁（click 冒泡）**
   - 根因：[frontend/src/components/HotMoneyList.tsx](frontend/src/components/HotMoneyList.tsx) `<TableRow onClick>` 包整列，`WatchlistAddButton` 內的 `e.stopPropagation()` 只擋按鈕本體；點到 `<TableCell>` 邊緣 / padding 時事件仍會冒泡到 row
   - 修法：在「清單」那格 `<TableCell>` 直接加 `onClick={(e) => e.stopPropagation()}`，整格都是安全區
   - 通則：**有 `onClick` 的 row 裡若放互動元素，包住元素的 cell 也要 stopPropagation**

3. **L0 加入清單按鈕太小看不到**
   - 根因：[frontend/src/components/WatchlistAddButton.tsx](frontend/src/components/WatchlistAddButton.tsx) compact variant 原本 `px-2 py-0.5 text-xs`，在深色表格對比太弱
   - 修法：compact 統一改 `inline-flex ... px-3 py-1 text-xs font-medium`；可加入狀態填 sky-600 + 白字強對比；表格欄寬 `w-24 → w-28`

4. **L0 版位重排**
   - [frontend/src/app/page.tsx](frontend/src/app/page.tsx) 順序改為：交易分析 → 熱錢排行 Top 20 → 產業流向

## M21 Trade Quality Context 資料管線完成（2026-04-24）

### Scope
- Phase A：純 context layer + API endpoint + 測試；**不動 M17 既有 prompt / router / 前端**
- 輸出：6 個 section 的結構化 JSON，deterministic + no-hindsight
- 後續 Phase B（獨立任務）才會修 M17 prompt 讓 AI 消費這份 context

### 檔案結構（[backend/app/analysis/](backend/app/analysis/)）
- `context_thresholds.py` — 所有 lookback / threshold 常數（module-level，**無 env override**）
- `industry_signals.py` — PART 1（hot_score / hot_level / price_strength / volume_trend / institution_flow / capital_type / is_false_hot）
- `chip_signals.py` — PART 2（foreign/trust/dealer_buy_days / volume_trend / price_trend / is_accumulation / chip_strength）
- `peer_rank.py` — PART 3（return/volume/institution 三個 top-percentile + leader_or_follower 四條件投票）
- `fundamental_signals.py` — PART 4（revenue_yoy / revenue_mom from `monthly_revenue`；`guidance` 永遠 null）
- `price_structure.py` — PART 5（slope trend / is_breakout 20d / is_consolidation 10d / is_accelerating）
- `news_stub.py` — PART 6（純字串組合，query_stock / query_industry / date_end；**不 query DB**）
- `context_builder.py` — 主入口 `build_trade_quality_context(db, stock_id, buy_date) -> dict`

### 對外 API
- `GET /api/analysis/context?stock_id=<id>&buy_date=<YYYY-MM-DD>` (`backend/app/routers/analysis.py`)
- 認證：`Depends(require_user)`（初版需登入，與 M17 前端入口一致；未來視需要放寬）
- `buy_date` 行為（決策 3b）：未指定時 fallback `get_latest_industry_trade_date(db)`，與 M17 一致
- Raises：
  - `404` unknown stock（`ValueError` from `build_trade_quality_context` → HTTPException）
  - `404` 資料庫無交易日資料（`_resolve_buy_date` 回 None）
  - `401` 未登入

### 關鍵 gotcha
- **Python 3.9 相容**：型別註記不能用 `list[float] | None`，要用 `Optional[List[float]]`（`from typing import List, Optional`）
- **institution_flow 空資料**：回 `"none"` 字串（代表無參與），**不是** `None`（保留 `None` 給 unknown 語義）；否則 `_compute_hot_score` 會把所有 weight 算成 0 而不是正確 flag 為 null
- **is_false_hot 輸入是 price_strength，不是 volume_trend**：spike 檢測（`max(recent_3d) >= baseline × 1.5`）與 volume_trend 分類（3d avg vs baseline）是兩個 orthogonal signals；單一大量的日子會把 3d avg 推進 `expanding_3d`，但不代表不該被標為 false hot
- **no-hindsight**：所有 section 都用 `trade_date <= buy_date`；lookback 皆以**交易日**計（`ORDER BY trade_date DESC LIMIT N`），**非曆日**
- **data_quality_notes 政策**：永遠 null 的欄位（`industry_news_heat` / `guidance`）**不**寫入 notes（決策 4b，避免每次 response 都有噪音）；notes 只在動態缺料（peer 不足、price history < 21 天、monthly_revenue 缺）時才加
- **peer_rank top-percentile convention**：`0.0 = 最強` / `1.0 = 最弱`（產業排名第 1 回 0.0）；leader 判定 4 條件滿足 >= 2 條
- **chip 連續買超日數**：從最新日往前走，碰到非正值 net_shares 就停；無資料時該欄位回 0，不 raise

### 測試覆蓋
- `tests/test_industry_signals.py`（17 案例）
- `tests/test_chip_signals.py`（18 案例）
- `tests/test_peer_rank.py`（8 案例）
- `tests/test_price_structure.py`（13 案例）
- `tests/test_context_builder.py`（11 案例：schema shape / unknown stock raise / notes 組合 / fundamental null / news stub / happy snapshot / deterministic）
- `tests/test_analysis_context_router.py`（5 案例：401 / 200 happy / buy_date fallback / 404 no trade dates / 404 unknown stock）

### 落地計畫與 spec
- 實作計畫：[docs/plans/m21_context_pipeline_implementation.md](docs/plans/m21_context_pipeline_implementation.md)
- 輸出 schema + 門檻說明：[docs/plans/trade_quality_context_spec.md](docs/plans/trade_quality_context_spec.md)

### Review P1 修正：peer_ids 查詢加下界（2026-04-24）
- **問題**：8 個 `stock_id IN (peer_ids) AND trade_date <= buy_date` 查詢缺下界，大產業（半導體 60+ 檔 × 2500+ 交易日 × 8 queries）會搬 10+ 萬列進 Python
- **修法**：新增 [backend/app/analysis/_helpers.py](backend/app/analysis/_helpers.py) 兩個 helper：
  - `fetch_active_peer_ids(db, industry_name)` — 取代 industry_signals / peer_rank 裡各自實作的 `_active_peer_ids`
  - `resolve_query_start_date(db, buy_date)` — 以 `SELECT DISTINCT trade_date FROM daily_price ORDER BY DESC OFFSET (N-1) LIMIT 1` 反推交易日下界（N = max lookback 21 日），自動跳過週末 / 春節長假
- **架構**：`context_builder` 預先算 `peer_ids` + `query_start_date` 各一次，往下傳給 `compute_industry_signals` / `compute_peer_rank`；兩個 entry function 都保留 optional kwargs 預設 None（未提供時自行 compute），向後相容測試
- **8 個加下界的查詢**：
  - `industry_signals.py`：`_industry_price_strength` / `_industry_volume_trend` / `_recent_flow_dates` / `_count_spike_days`
  - `peer_rank.py`：`_peer_returns` / `_peer_volume_ratios` / `_peer_institution_intensity` / `_peer_breakouts`
- **P2 順手處理**：`chip_signals._classify_price_trend` 的 `max_single_day_pct` 加註解說明是雙向絕對值（tests 所有 72 案例 pass）
- **為何用交易日反推而非 calendar offset**：春節長假 calendar offset 會切過頭；trading-day reversal 保證永遠剛好 N 筆資料，不受休市影響

## M21 Phase B：M17 prompt 吃 context pipeline（2026-04-24）

### 改法
- [backend/app/routers/analysis.py](backend/app/routers/analysis.py)：`analyze_trade_quality` 在 `_collect_context` 後新增 `_build_deterministic_context()`，呼叫 `build_trade_quality_context(db, stock_id, buy_date)` 取得 6 section JSON
- `_build_user_message(context, m21_context, warnings)` 新增 `[M21 預聚合訊號（deterministic，結論層）]` 區塊，`json.dumps(..., ensure_ascii=False, indent=2)` 直接序列化 6 section 到 user message
- raw OHLC / 法人從 10 日縮到 **5 日**，前綴「僅供對照」—— 讓 AI 以 M21 結論為主，raw 只做 sanity check；revenue 維持 3 個月
- [backend/app/prompts/trade_quality.md](backend/app/prompts/trade_quality.md) + [docs/trade_quality_prompt.md](docs/trade_quality_prompt.md) 頂部加「輸入格式（M21 預聚合訊號）」說明，明列 7 個 section 語義 + 直接對應 prompt 內「產業熱錢等級 S/A/B/C」「籌碼集中度」「Leader/Follower」等強制規則
- `rating` / `classification` / JSON contract 完全不動，前端不需改

### Fallback 設計
- `build_trade_quality_context` 丟非預期例外（`RuntimeError` 等非 `ValueError`）→ logger.exception + `warnings.append("deterministic 訊號管線暫時不可用...")`，user message 顯示「（不可用：請以下方原始資料自行推論）」
- `ValueError`（stock not found）仍依既有路徑回 404（`_collect_context` 先擋）
- 不阻斷 OpenAI 呼叫，確保 context pipeline 掛掉時 M17 仍能以 raw-only 模式工作

### 測試
- [backend/tests/test_analysis_router.py](backend/tests/test_analysis_router.py) 新增 2 案例：
  - `test_trade_quality_user_message_includes_m21_deterministic_block`：斷言 6 section 關鍵字出現在 user message
  - `test_trade_quality_falls_back_to_raw_when_m21_context_fails`：mock `build_trade_quality_context` 丟 `RuntimeError`，仍應回 200 + warnings 含提示
- 全 tests 結果：44 pass（analysis router + context router + context builder），整 backend suite 370 pass（唯一 fail 是 `test_engine_connects`，worktree 無 sqlite 檔，與本改動無關）

### Gotcha
- **不要在 `_collect_context` 內呼叫 build_trade_quality_context**：兩個 function 有不同錯誤處理契約（raw context 缺資料 → warnings；deterministic pipeline 掛掉 → warning + fallback）；分開才能讓 router 層決定如何 fallback
- **M21 JSON 用 `ensure_ascii=False`**：保留中文產業名稱（`AI 伺服器` 等）避免轉 `\u...` 浪費 token 且失去可讀性
- **`rating` / `classification` 契約不能動**：M19 卡片「交易分析」深連結與前端 Rating 色塊已硬依賴 5 階 + A/B/C，改 prompt 時也禁止變動這兩欄值域
- **raw OHLC 從 10 縮到 5 日**是 Phase B 的刻意設計：M21 已經把價格結構（trend / breakout / consolidation / accelerating）結論化了，raw 只需保留到 AI 能驗證「這 5 天真的在上漲」即可，節省 token 讓更多預算分給 M21 JSON

## M17 SSE 進度條（2026-04-24）

### 背景
- `POST /api/analysis/trade-quality` 整體耗時 5~30 秒（OpenAI 占 80%+），前端僅顯示 spinner + 「系統正在還原當天市場情境…」固定文字，使用者無法知道目前在等什麼。

### 改法
- 後端新增 `POST /api/analysis/trade-quality/stream`：與原 endpoint 同輸入，回 `application/x-ndjson`（line-delimited JSON）
  - 共用 `_collect_context` / `_build_deterministic_context` / `_build_user_message` / `_call_openai` / `_normalize_response`，邏輯零分叉
  - Pre-flight（stock 不存在 / prompt 缺檔 / 未開盤）在 stream 開始前 raise `HTTPException`，讓 4xx/5xx 走正常 HTTP 錯誤通道
  - Generator 依序 yield：`collect_raw` → `build_context` → `openai_call` → `done(payload=jsonable_encoder(TradeQualityResponse))`
  - OpenAI 不可用 → 仍以 `done` event 完成，payload `source="unavailable"`
  - Generator 內部例外 → yield `error` event；前端 throw 對應 `Error`
- 原 `POST /api/analysis/trade-quality` **保留**（M19 watchlist 深連結走的是非 stream 版，不需要進度條）
- 前端 [frontend/src/lib/api.ts](frontend/src/lib/api.ts) 新增 `streamTradeQuality(payload, onEvent, options)`：用 `fetch().body.getReader()` + `TextDecoder` 解析 NDJSON；最終 throw 或回 `TradeQualityResponse`
  - 同檔案的舊 `analyzeTradeQuality` 已無 caller → 一併刪除
- 前端 [frontend/src/components/TradeQualityAnalysis.tsx](frontend/src/components/TradeQualityAnalysis.tsx) 把 `analyzeTradeQuality` 換成 `streamTradeQuality`：
  - 新增 `progressStage` / `progressLabel` state，每收到一個 event 就更新
  - Loading UI 從 spinner + Skeleton 改為「label + 百分比 + emerald 進度條」；stage→% 對照：collect_raw 15 / build_context 35 / openai_call 60 / done 100

### Gotcha
- **NDJSON 不是 SSE**：用 `application/x-ndjson` 而非 `text/event-stream`，因為前端只需要單向收 event，不需要 EventSource 的 reconnect / event-name 機制；NDJSON 解析簡單、TestClient 也能直接 split lines 驗證
- **`jsonable_encoder` 取代 `.dict()`**：Pydantic v1/v2 序列化方法不同；`jsonable_encoder` 是 FastAPI 通用安全做法，避免 `date` / `datetime` 序列化坑
- **Pre-flight vs in-stream 例外**：stock 找不到一定要在 stream 開始前 raise，否則 HTTP 200 + done event with error payload 在前端 fetch 邏輯比較難區分
- **`_STREAM_HEADERS` 必加**（`X-Accel-Buffering: no` + `Cache-Control: no-cache`）：Vercel ↔ Render 中間 nginx 預設會 buffer 整段 response，NDJSON 進度會被攢一起送 → progress bar 跳一下就到 done，UX 等於沒做。本地 dev 不會察覺差異，prod 才看得出來。兩個 `StreamingResponse`（market_closed_stream + main generate）都要加
- **Generator 內不可 raise HTTPException**：headers 已 commit，raise 不會變 4xx，只會變成 broken stream（前端 reader 看到 EOF 而不是錯誤訊息）。所有預期 4xx 路徑必須在 pre-flight 檔下；generator 內的 `except` 統一 emit `error` event 給前端
- **進度百分比是視覺提示，不是真實進度**：OpenAI call 60% 一段會「卡」最久（5~25 秒），最後一口氣跳到 100%；這是刻意設計（avoid fake animated progress），label 同步更新即可

## M23 slice 4：signals/ 模組骨架完成（2026-04-25）

### Scope（10 切片中的第 4 片）
- 對應 spec §14：建立 `backend/app/signals/` 7 個模組的「契約面」（簽章 + docstring + stub）
- 兩個模組**完整實作**：`exclusions.py`（純規則）+ `pipeline.py`（status 流轉 / progress / UPSERT）
- 四個模組**簽章 + stub**：`candidate_pool.py` / `classification.py` / `filters.py` / `llm_caller.py`（slice 5/6 各自填）
- 一個資料檔：`group_stocks.json`（5 大集團白名單）

### 落地檔案
- [backend/app/signals/__init__.py](backend/app/signals/__init__.py) — 模組總覽 + 對應 spec 章節 + re-export `run_signal_pipeline_sync`
- [backend/app/signals/exclusions.py](backend/app/signals/exclusions.py)（**完整**）：
  - 8 個 helper：`is_etf` / `is_financial` / `is_blacklisted` / `should_exclude` / `load_group_stocks` / `find_group_for_stock` / `get_group_members` / `get_group_leader`
  - ETF 規則 `^00\d{2,}$` 或名字含 `ETF / 指數型基金 / 指數股票型`；金融規則 `industry_name` 含 `金融 / 銀行 / 保險 / 證券`
  - `EXCLUSION_BLACKLIST: Set[str] = set()`（手動黑名單，第一版空）
  - `_GROUP_STOCKS_CACHE` module-level 快取，`load_group_stocks(force_reload=True)` 可強制重讀
  - `_meta` 開頭的 key 自動過濾（不會出現在 group dict）
- [backend/app/signals/group_stocks.json](backend/app/signals/group_stocks.json) — 5 大集團（鴻海 / 台塑 / 國巨 / 聯電 / 聯發科），每組 `leader` + `members`，`leader` 必須在 `members` 內
- [backend/app/signals/candidate_pool.py](backend/app/signals/candidate_pool.py)（stub）：3 個函式 `ingest_data` / `compute_rankings` / `build_candidate_pool`，slice 5 填
- [backend/app/signals/classification.py](backend/app/signals/classification.py)（stub）：`PRELIM_TYPE_LEADER/FOLLOWER/LAGGARD_CANDIDATE` 常數 + `classify_stocks`，slice 5 填
- [backend/app/signals/filters.py](backend/app/signals/filters.py)（stub）：4 個 `HINT_*` 常數 + `apply_hard_exclusions` / `apply_soft_filters`，slice 5 填
- [backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)（stub）：`DEFAULT_BATCH_SIZE = 8` / `DEFAULT_MODEL = "gpt-4o-search-preview"` + 4 個函式 `run_research_batch` / `run_explanation_batch` / `assemble_market_context` / `assemble_final_output`，slice 6 填
- [backend/app/signals/pipeline.py](backend/app/signals/pipeline.py)（**完整**）：
  - 7 stage 常數對齊 `models.SignalGenerationJob.current_stage` enum：`STAGE_INGEST/RANK/CANDIDATE/FILTER/LLM_RESEARCH/LLM_EXPLAIN/PERSIST`
  - `run_signal_pipeline_sync(job_id, target_date, *, session_factory=None)` — cron / BackgroundTasks 共用入口
  - `_set_progress(db, job, *, status, stage, pct, label)` — 每 stage 結束 commit 一次（前端 polling 即時看到）
  - `_mark_done` / `_mark_failed`（先 `db.rollback()` 清 session error state，再 re-fetch job 寫狀態）
  - `_persist_snapshot` — `(snapshot_date)` UPSERT：existing 則 setattr 覆蓋 + 更新 `generated_at`，無則 `db.add(SignalSnapshot(...))`
  - LLM Research stage 為 batched loop（spec §5 Step 7：「一次 prompt 處理 5~10 檔」），每 batch commit 一次 progress
  - try/except 包整段：失敗時 `_mark_failed` 寫 traceback[:2000] 後 **re-raise**（讓 caller 紀錄；測試也能 `pytest.raises`）

### 測試
- [backend/tests/test_signals_exclusions.py](backend/tests/test_signals_exclusions.py)：19 案例
  - autouse fixture `_reset_group_stocks_cache` 清 module cache，避免測試殘留
  - ETF / 金融 / 黑名單 / `should_exclude` 整合 / `group_stocks.json` 載入正確性 / leader-member 一致性
- [backend/tests/test_signals_pipeline.py](backend/tests/test_signals_pipeline.py)：6 案例
  - `session_factory` fixture 用 in-memory SQLite + `Base.metadata.create_all` per test
  - `_stub_all_stages_noop(monkeypatch)` 把全部 stage function 換成 noop（happy path）
  - 失敗路徑：第一個 stage 拋 `NotImplementedError`、filter stage 拋 `RuntimeError` 中間掛掉、`job_id` 不存在 `ValueError`
  - Happy path：status=done + progress_pct=100、payload 欄位寫入 SignalSnapshot、同日重跑 UPSERT 不違反 unique
- 全 backend suite：413 pass，1 pre-existing fail（`test_engine_connects` worktree 沒 sqlite 檔）+ 5 pre-existing errors（`test_finmind_sdk_integration` 需 API token），與 slice 4 無關

### Gotcha
- **`monkeypatch.setattr(module, "name", value)` 預設 `raising=True`**：被 patch 的 attribute 必須先存在於 module，否則 `AttributeError`。所以 `llm_caller.assemble_final_output` 雖然 slice 6 才實作，slice 4 也**必須先放 stub**（簽章對齊 pipeline 的呼叫）才能讓測試 monkeypatch 成功
- **`_mark_failed` 必須先 `db.rollback()`**：上一個 stage 拋例外後 session 處於 error state，不 rollback 直接 commit 會把整個 transaction 噴掉；rollback 後再 `db.get(SignalGenerationJob, job_id)` re-fetch（不能用例外前抓的 ORM instance，已經 detached）
- **stage progress commit 在 stage 開始前**：前端 polling 看到 `current_stage=filter / pct=30` 表示「正在跑 filter」；若 filter 拋例外，DB 仍保留這個進度（test_pipeline_marks_failed_when_filter_stage_raises 驗證）讓使用者看得到失敗點
- **pipeline 不能用 request session**：spec §11.5 明確要求；本實作預設 `SessionLocal` 從 `app.database` import，測試傳 `session_factory=in_memory_factory`
- **`_persist_snapshot` 用 `(snapshot_date)` 當 key UPSERT 不是 `(snapshot_date, job_id)`**：spec 設計每天一份 snapshot，重跑會覆蓋；測試 `test_pipeline_upserts_existing_snapshot_on_rerun` 驗證重跑後 `job_id` 已更新為最後一次
- **stage function 全 raise NotImplementedError**：slice 4 跑真實 pipeline 會在 stage 1 ingest 即 failed，這是預期行為；測試靠 monkeypatch 替換為 noop 才能覆蓋 happy path

### 下一步（slice 5）
- 填 `candidate_pool.py` / `classification.py` / `filters.py` 的 deterministic 規則
- 接 `daily_price` / `inst_stock_flow` / `industry_daily_flow` / `margin_trade` / `daily_valuation` / `monthly_revenue` 算 hot_score / 法人連買日 / soft hint 等
- slice 6 才接 OpenAI（`llm_caller`）

## M23 slice 5：deterministic filter 三層完成（2026-04-26）

### Scope（10 切片中的第 5 片）
- `candidate_pool.py` / `classification.py` / `filters.py` 三模組從 stub 換成完整實作
- 對應 spec §6（候選池）/ §7（LEADER/FOLLOWER/LAGGARD 預分類）/ §9（hard exclusions + soft filters）
- 全 deterministic、純規則；slice 6 才接 OpenAI（LLM research / explanation）

### 落地檔案（覆蓋 stub）
- [backend/app/signals/candidate_pool.py](backend/app/signals/candidate_pool.py)（~600 行）：
  - 三函式 `ingest_data` / `compute_rankings` / `build_candidate_pool`，依 spec §5 step 1-4 串接
  - 候選池來源 union：top_stocks_3d 40 + top_industries_3d 10 成分股 + 熱門產業龍頭 + 同產業同 sub_industry 擴散 + 集團股（`exclusions.load_group_stocks`）
  - 每檔股票算 `industry_count` / `industry_rank_5d` / `industry_rank_net_3d` / `consecutive_buy_days_3d` / `volume_5d_to_60d_ratio` / `price_change_3d/5d/1d` / `total_institution_flow_1d/3d/5d` / `margin_change_3d` / MA5 / MA10 / OHLC / volume ratios（給 §7 §9 用）
  - 常數：`TOP_INDUSTRIES_LIMIT=10`、`TOP_STOCKS_LIMIT=40`、`TOP_STOCKS_INNER=10`、`POOL_SOFT_TRIGGER=150`、`POOL_HARD_LIMIT=120`
  - 軟上限超過 → 依「LEADER candidate（rank 高） > FOLLOWER candidate > 其他」截斷至 hard limit
- [backend/app/signals/classification.py](backend/app/signals/classification.py)（~200 行）：
  - LEADER：`industry_rank_5d` 前 30%（`ceil(count * 0.3)`）+ `industry_rank_net_3d` 前 20% + `consecutive_buy_days_3d >= 2` + `volume_5d_to_60d_ratio >= 1.5`
  - FOLLOWER：同產業已有 LEADER + `0 < price_change_5d < leader_gain × 0.7` + `total_institution_flow_3d > 0`
  - LAGGARD_CANDIDATE：guard（同產業 LEADER 漲 ≥ 5%）+ 4 條件中 hits ≥ 2（gap ≥ 5pct / net_1d>0 OR vol_1d_to_5d>1.2 / 站上 5MA OR 10MA；guard 自身已算 1 hit）
  - 三類都不符 → **剔除**（不原地保留）
- [backend/app/signals/filters.py](backend/app/signals/filters.py)（~210 行）：
  - Hard exclusions（直接剔除）：ETF / 金融 / 黑名單（`exclusions.should_exclude`）+ `total_institution_flow_5d < 0` 但**非** LAGGARD + `price_change_3d > 15%` + `avg_turnover_5d < 5e7`
  - Soft filters（標 hint，不剔除）：`HINT_WEAKENING` / `HINT_RETAIL_OVERHEATED` / `HINT_DISTRIBUTION` / `HINT_RANGE_BOUND`，多條件可同時命中
  - distribution 包兩條件（爆量不漲 / 高檔長上影），命中其一即算

### 測試
- [backend/tests/test_signals_candidate_pool.py](backend/tests/test_signals_candidate_pool.py)：13 案例（in-memory SQLite，seed 全市場 master/price/flow，驗證 ingest / rank / pool 正確；用 monkeypatch 把 `POOL_SOFT_TRIGGER=5` / `POOL_HARD_LIMIT=3` 模擬截斷）
- [backend/tests/test_signals_classification.py](backend/tests/test_signals_classification.py)：21 案例（template helper + override pattern；LEADER 4 條件各別 fail / FOLLOWER paired with LEADER / LAGGARD 2 hits 各種組合 / 整體優先序 / 多 leader 取 max gain）
- [backend/tests/test_signals_filters.py](backend/tests/test_signals_filters.py)：23 案例（hard exclusion 各條件、邊界 15% 不算、None 視為缺資料；soft filter 各 hint 個別觸發 + 不觸發 + 多重觸發；不修改原 dict）
- 全 backend suite：470 pass、1 pre-existing fail（`test_engine_connects` 是 worktree 沒 sqlite 檔，非 slice 5 影響）

### Gotcha
- **FOLLOWER vs LAGGARD 重疊**：`price_change_5d=0` 時 FOLLOWER 失敗（要求 > 0），但會落入 LAGGARD（gap = leader_gain - 0 通常 ≥ 5pct）。測試應斷言 `prelim_type != FOLLOWER`，**不可斷言 `stock_id not in result`**，否則 LAGGARD 也算 in result 會誤判。同 issue 在 `test_follower_dropped_when_3d_flow_not_positive`
- **`_is_top_pct` 邊界用 `ceil`**：`industry_count=10`、`pct=0.3` → threshold `ceil(10*0.3) = 3`，rank=4 不通過；`pct=0.2` → threshold 2，rank=3 不通過。`industry_count=0` 視為失敗（避免 div by zero）
- **distribution 高檔長上影公式**：`high - close > (close - open) × 2 AND close < high × 0.97`。紅 K（body 為負）時 inequality 自動成立，配合 close < high × 0.97 仍能正確抓到「紅 K + 拉回」的派發 pattern（不額外加紅 K guard）
- **soft filter 不修改原 dict**：用 `{**c, "soft_hints": hints}` shallow copy；`apply_soft_filters` 不可 mutate input（pipeline 可能對候選池有其他引用）
- **hard exclusions 用候選池欄位即可**：`db / target_date` 暫保留簽章但不查 DB，因為 `should_exclude` + 其他條件全部用 candidate_pool 算好的欄位

### 下一步（slice 6）
- 填 `llm_caller.py`：`run_research_batch` / `run_explanation_batch` / `assemble_market_context` / `assemble_final_output`
- 接 OpenAI `gpt-4o-search-preview`（spec §5 step 7-8）
- batch 5~10 檔一次 prompt（成本控制）

## M23 slice 6：llm_caller.py 完整實作（2026-04-26）

### Scope（10 切片中的第 6 片）
- `llm_caller.py` 從 stub 換成完整實作；接 OpenAI `gpt-4o-search-preview`（支援 web search）
- 對應 spec §3.2 / §5 step 0+7+8+9 / §10 LLM I/O contract
- 全 mock 單元測試覆蓋（不打真實網路、不依賴 API key）

### 落地檔案
- [backend/app/prompts/watch-list-stock.md](backend/app/prompts/watch-list-stock.md) — 525 行 buy-side 分析師 prompt 從 main 專案複製進 worktree（spec §10 全文 I/O contract + 13 點 reason 寫作規則）
- [backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)（~470 行）：
  - 4 個 public function 簽章與 pipeline.py 對齊（slice 4 預先放 stub 才能讓測試 monkeypatch 成功）
  - `assemble_market_context(db_market_snapshot, *, model)` — Step 0：判斷 STRONG_BULL / STRUCTURAL_BULL / RANGE / WEAK
  - `run_research_batch(stocks_batch, market_context, *, model)` — Step 7：上網查公司業務 / 產業鏈 / 題材 / 集團 / 龍頭，輸出 `type` + `business_summary` + `theme` + `group_info` + `leader_check`
  - `run_explanation_batch(research_results, market_context, *, model)` — Step 8：依 market_state gating → `signals` + `decision` (WATCH/REMOVE) + 500–1000 字 reason；caller 不需自己分 batch（內部依 `DEFAULT_BATCH_SIZE` 拆 chunk）
  - `assemble_final_output(market_context, explanation, *, candidate_pool_size, model, total_tokens)` — Step 9：拆 watchlist / removed、計算 `summary` 4 欄、組裝 spec §10.2 完整 schema
  - `_call_llm_json(system_prompt, user_msg, *, model)` 內部統一入口；`_extract_json` 容錯解析（去 ` ```json ... ``` ` markdown fence）；4 個 fallback function 對應 4 種失敗路徑

### 常數
- `DEFAULT_BATCH_SIZE = 8`（spec §5 Step 7「5~10 檔/批」中位數）
- `DEFAULT_MODEL = "gpt-4o-search-preview"`
- `_MAX_OUTPUT_TOKENS = 8000`（reason 500-1000 字 × 8 檔 batch 預留充足）
- `_PROMPT_PATH = backend/app/prompts/watch-list-stock.md`

### 測試
- [backend/tests/test_signals_llm_caller.py](backend/tests/test_signals_llm_caller.py)：26 案例
  - `_extract_json` 6 案例（plain JSON / fence with lang / fence without lang / garbage / empty / whitespace）
  - `assemble_market_context` 5 案例（happy / fence / api_key 缺 / invalid JSON / OpenAI 例外）
  - `run_research_batch` 5 案例（empty / 對齊 / 缺檔 fallback / 整體失敗 / 缺 research key）
  - `run_explanation_batch` 5 案例（empty / decision+reason / chunk 分批 / 整體失敗 / 缺檔 fallback）
  - `assemble_final_output` 5 案例（split / summary count / total_tokens / empty / unknown decision treated as remove）
- 全 backend suite：496 pass（slice 5 470 + slice 6 26）、1 pre-existing fail（`test_engine_connects` worktree 沒 sqlite 檔）

### Gotcha
- **`gpt-4o-search-preview` 不支援 `temperature` 與 `response_format=json_object`**：跟 M17 `_call_openai` 用法不一樣；本實作 `_call_llm_json` 只傳 `model / messages / max_completion_tokens`，靠 prompt instruction 「JSON only, no markdown fence」+ `_extract_json` 防禦性解析
- **fallback 預設 `decision=REMOVE`**（保守）：LLM 不可用時不該誤標 WATCH；fallback dict 標 `_unavailable: True` 給 traceability
- **stock alignment by `stock_id` / `stock` key**：LLM 回應順序可能與輸入不同，缺檔需走 fallback 補齊；用 `by_id` dict 對齊
- **`run_explanation_batch` 內部分批**：caller 可一口氣傳 60+ 檔進來，不用自己 chunk；測試 `test_run_explanation_batch_chunks_by_default_batch_size` 用 `monkeypatch.setattr(llm_caller, "DEFAULT_BATCH_SIZE", 4)` + 13 檔驗證 4 次 LLM call
- **System prompt 每次 LLM call 都重新 `_load_system_prompt()`**：方便編輯 prompt 不用重啟 server；FAQ 性能：FS read 一次成本可接受、且第一版 prompt 525 行不大
- **markdown fence 移除 logic**：`_extract_json` 先 `strip`、startswith `\`\`\`` 時找第一個 `\n` 切掉開頭、endswith `\`\`\`` 切結尾；防 LLM 偶發加 ` ```json ... ``` ` 包裝；無 fence 時直接 `json.loads`
- **slice 4 pipeline 測試需更新**：`test_pipeline_marks_failed_when_first_stage_raises_not_implemented` 名稱不再準確（slice 5 ingest_data 已實作；slice 6 llm_caller 也不再 raise NotImplementedError），改為 `test_pipeline_marks_failed_when_ingest_stage_raises` 並用 monkeypatch 注入 `_boom`，驗證一樣的失敗路徑契約

### 下一步（slice 7~10）
- slice 7：`run_signal_pipeline_async` BackgroundTasks wrapper + `/api/signals/regenerate` rate limit + concurrency guard
- slice 8：`/api/signals/latest` / `/api/signals/snapshot/{date}` / `/api/signals/jobs/latest` 三個公開 GET endpoint
- slice 9：`.github/workflows/daily_signals.yml` cron 03:00 台北 + smoke test
- slice 10：前端 `<DailySignalsPanel />` L0 tab bar UX + pulse 通知 + 進度條 polling

## M23 slice 7 API endpoints + cron entrypoint 完成（2026-04-26）

落在 branch `claude/angry-cerf-8755da`。將 spec §11 的 4 個 endpoint 與 §11.6 的 cron 入口整合進 FastAPI app；slice 8/9（前端 + workflow）獨立進行，不在本切片範圍。

### 落地檔案
- [backend/app/routers/signals.py](backend/app/routers/signals.py) — 新 router；4 個 endpoint：
  - `GET /api/signals/latest`（公開；DB 無 snapshot → 404 `No snapshot yet`）
  - `GET /api/signals/snapshot/{snapshot_date}`（公開；無 → 404）
  - `GET /api/signals/jobs/latest`（公開；無 job → **回 null（200）**，不 404，前端少寫一個分支）
  - `POST /api/signals/regenerate`（`Depends(require_user)` → 401／同日 running job → 409／user 同日 ≥10 → 429／全站同日 ≥10 → 429／成功 → 202 + `{job_id, snapshot_date}`，`BackgroundTasks` 排程 `_run_pipeline_safely`；2026-04-27 從 1/5 放寬到 10/10）
- [backend/run_daily_signals.py](backend/run_daily_signals.py) — cron 入口（spec §11.6）；4h offset 推算 target_date；建 `SignalGenerationJob(triggered_by="cron")` 後 inline 同步跑 pipeline
- [backend/app/main.py](backend/app/main.py) — `from app.routers import (..., signals, ...)` + `app.include_router(signals.router, prefix="/api")`

### Pydantic schema（spec §10.3 + §11.3）
- `SnapshotResponse`：`{ snapshot_date, generated_at, llm_model, data: { market_context / watchlist / removed / summary / candidate_pool_size / final_watchlist_size } }`
- `JobResponse`：`{ job_id, snapshot_date, status, current_stage, progress_pct, progress_label, started_at, finished_at, error_message }`
- `RegenerateAcceptedResponse`：`{ job_id, snapshot_date }`

### 限頻 / concurrency 實作
- 全部走 DB COUNT/SELECT，**沒接 slowapi**（spec §11.4 明寫 in-memory by user_id + snapshot_date，但 DB 查就夠用、且 cron job 也算進全站 10/day 額度，不需要 slowapi 的進階 key 機制）
- 常數 `USER_DAILY_REGENERATE_LIMIT=10` / `GLOBAL_DAILY_REGENERATE_LIMIT=10` 集中在 `signals.py` 頂部（2026-04-27 從 1/5 放寬到 10/10，給 prod 測試 / admin 重產彈性）
- 同日 user 額度與 concurrency guard **平行檢查不同條件**：concurrency 看 `status in ("pending","running")`，user 限頻看「不論成敗都計 1」（避免 user 連按 N 次都失敗也不 reset）

### Cron entrypoint exit code（spec §11.6）
- `0=ok / 1=no_data / 2=llm_error / 3=db_error`
- 例外分類靠訊息關鍵字：`"no candidate" / "no data" / "no trade"` → 1；`"openai" / "llm" / "prompt"` → 2；其他全部 → 3
- `_resolve_target_date_from_now()` 用 `Asia/Taipei` + `now - 4h` 推 `.date()`；保證即使 GitHub Actions cron 延遲到 04:00~06:00 仍 resolve 為昨日
- argv 第一個位置可手動覆寫 `YYYY-MM-DD`

### Pipeline 注入點
- BackgroundTasks 包 `_run_pipeline_safely(job_id, target_date)`：catch 所有 exception 不讓 worker crash；pipeline 自身會把 `job.status="failed"` + `error_message` 寫進 DB，所以這層只 log
- 餵 `session_factory=SessionLocal` 給 `run_signal_pipeline_sync`（spec §11.5：不能用 request session）

### 測試
- [backend/tests/test_signals_router.py](backend/tests/test_signals_router.py) — 15 案例（latest 404 / latest happy / snapshot 404 / snapshot happy / snapshot bad date 422 / jobs/latest null / jobs/latest happy / regenerate 401 / 202 happy + DB job + background call / 409 concurrency / 429 user / 429 global / fallback today / `_resolve_target_date` 兩個 unit）
- [backend/tests/test_run_daily_signals.py](backend/tests/test_run_daily_signals.py) — 6 案例（argv 解析 / 4h offset mock / 三類 exit code 分類 / ValueError fallback）
- 全 132 個 signal-related 測試 + 全 backend suite 517 pass（與 slice 6 baseline 一致）

### Gotcha
- **`_run_pipeline_safely` 必須 monkeypatch**：router test 用 in-memory SQLite + dependency_overrides，但 `run_signal_pipeline_sync` 內呼叫 `SessionLocal()` 會走預設連線而非測試 engine，所以測試直接攔截 `_run_pipeline_safely` 紀錄 `(job_id, target_date)` 而不真跑
- **fallback target_date 用今天 + DB 計次仍在當天**：DB 完全空時 `_resolve_target_date()` 回 `date.today()`，user 10/day 與全站 10/day 仍按「今天」計；cron 第一次部署到空 DB 時也能正常觸發
- **regenerate 第二次 429 user 限頻測試**：第一次成功後 job 是 `pending` 狀態，會卡住第二次的 concurrency guard（409）；測試需要先把它標 `done` 才能驗證 user 限頻 429
- **path param `snapshot_date` 型別解析失敗回 422**：FastAPI 對 `date` 型 path param 自動 422，不是 400；測試 `test_snapshot_invalid_date_format_returns_422` 鎖這個合約
- **jobs/latest 用 `Optional[JobResponse]` + 回 None**：Pydantic 序列化 None → `null`；前端直接 `if (!job)` 判斷，不需要 try/catch 404

### 下一步（slice 8~10）
- slice 8：前端 `<DailySignalsPanel />` L0 tab bar UX + pulse 通知 + 進度條 polling
- slice 9：`.github/workflows/daily_signals.yml` cron 03:00 台北 + smoke test
- slice 10：手動觸發驗證 prod，沒問題後等 cron 03:00 自動跑

## M23 slice 8 + 9：前端 panel + GitHub Actions workflow（2026-04-26）

落在 branch `claude/angry-cerf-8755da`，與 slice 7 同 branch（slice 7~9 一起 merge 上 main 才能讓 cron 跑得起來）。

**Why**：spec §13 前端 L0 tab bar UX + spec §12 GitHub Actions 排程；前者是 user-facing 入口、後者是每日自動產生 snapshot 的觸發器。slice 7 完成後對外有 API 但「沒有人會去打」、cron 也沒接 → 必須 8/9 同時上線才算可用閉環。

**How to apply**：
- 修進度條樣式 / pulse 動畫 → 動 `frontend/src/components/DailySignalsPanel.tsx`（單一檔案、無拆分子元件）
- 修 polling 間隔 → 動 `frontend/src/lib/useSignalJobPolling.ts` 頂部 `POLL_INTERVAL_MS = 3000`；錯誤 backoff 是 `* 2`（6 秒）
- 改 cron 時間 → 動 `.github/workflows/daily_signals.yml` `cron: '0 19 * * 1-5'`（UTC，= 台北 03:00 週二~週六）
- 改 retry 邏輯 → 不要學 `daily_etl_update.yml` 加 `sleep 5400` retry，因為 LLM 失敗用 retry 通常還是會掛（不像 quota 重試會解）；signal pipeline 失敗（exit 2/3）直接 fail workflow，靠 user 點「重新產生」處理

**前端結構**（`DailySignalsPanel.tsx`）：
- header：折疊鈕（▸/▾，預設 collapse）+「今日異常訊號清單」+ pulse badge（有新訊號時）+ snapshot_date / generated_at
- 進度條：`useSignalJobPolling()` 回傳 `job` 為 `pending`/`running` 時顯示，progress_pct + progress_label
- 4 tabs（base-ui）：LEADER / FOLLOWER / LAGGARD / REMOVED，每個 tab 顯示對應 count
- SignalCard：股票連結 + decision badge（LEADER 綠 / FOLLOWER 藍 / LAGGARD 琥珀）+ 產業/子產業 + 主題 + 訊號 chips（資金/籌碼/融資券/技術）+ reason 中文白話
- RemovedCard：紅色 REMOVED 徽章 + 排除原因
- 「重新產生」按鈕 5 狀態（spec §13.5）：未登入 disabled「重新產生（需登入）」/ running disabled「產生中…」/ 送出中「送出中…」/ 載入中 disabled / 可觸發 enabled「重新產生」
- 點任何 tab 或展開 panel → 寫入 `always-stock:signals:last_seen_snapshot_date` → 清掉 pulse
- 折疊狀態存 `always-stock:signals:collapsed`（預設 collapse）

**GitHub Actions workflow**（`daily_signals.yml`）：
- 觸發：cron `0 19 * * 1-5`（UTC = 台北 03:00 週二~週六）+ workflow_dispatch（吃 `target_date` input）
- target_date：吃 input 優先；沒帶則 `date -d '4 hours ago' +%F`（同 daily_etl_update.yml 防 cron 跨日）
- timeout 90 min（LLM 60~120 檔 × ~20s/檔 ≈ 30~60 min）
- env：`DATABASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL`（fallback `gpt-4o-search-preview`）
- exit code 對應：0/1 → workflow pass（1 = no_data 為合理結果，週末或無候選池）；2/3 → workflow fail（LLM / DB 錯誤需人工介入）
- 不做 `daily_etl_update.yml` 的 sleep+retry：FinMind quota 等 1.5h 會解、OpenAI 失敗多半是模型/prompt 問題 retry 沒用

**Gotcha**：
- **無 `node_modules` 在 worktree**：本切片 frontend 改動沒跑 `npx tsc --noEmit` / `next build`；type 錯誤靠 PR CI / vercel preview 抓。Component 用的型別都是 `frontend/src/lib/api.ts` 既有 export，型別契合度應該高
- **base-ui Tabs.Panel `value` prop**：base-ui 用 `value` 比對 `Tabs.Root` 的 `value` 決定哪個 panel 顯示；不是 shadcn `data-state="active"`。`TabsContent` (= `TabsPrimitive.Panel`) 接 `value` 自動切換
- **`fetchLatestSignalSnapshot` 404 → null**：M23 slice 7 的 endpoint 第一次 deploy 時 DB 無 snapshot，前端不能 throw、要顯示「目前尚無訊號清單」；`api.ts` 的 helper 已實作 404 → null
- **localStorage 永遠包 try/catch**：SSR + 隱私模式 + iframe 都可能噴；用 `try { window.localStorage.getItem(...) } catch { /* ignore */ }`
- **Polling cleanup**：`useSignalJobPolling` 用 `cancelledRef` + `clearTimeout(timer)`，unmount / `bumpKey` 變動時都會中斷；點「重新產生」後 `setBumpKey((k) => k + 1)` 觸發 effect 重啟（沒有 long-lived connection）
- **`job.progress_pct` 可能 > 100 / < 0**：前端用 `Math.min(100, Math.max(0, x))` clamp；後端 pipeline 寫入時雖然應該 0~100 但 UI 不能假設

**slice 10（最後一片）**：
- 部署後手動 `gh workflow run daily_signals.yml --ref main -f target_date=2026-04-25` 觸發一次
- 觀察 Render log + DB 寫入 `signal_snapshots` / `signal_generation_jobs`
- 開首頁 `https://...vercel.app/`（已登入帳號）→ 看 panel 是否能展開、訊號是否顯示、pulse 動畫是否運作
- 沒問題就等 cron 03:00 自動跑（週二~週六）

## M23 slice 11：code review patches（2026-04-26）

slice 1~9 完成後 review 出 3 個小瑕疵，集中於 slice 11 修掉，讓整條 pipeline 真正可上 prod。

**修法 1：`llm_caller.DEFAULT_MODEL` 吃 `OPENAI_MODEL` env**
- 原本 hardcode `DEFAULT_MODEL = "gpt-4o-search-preview"`，workflow 雖然 export 了 `OPENAI_MODEL` 但 `llm_caller` 從未讀取
- 修法（[backend/app/signals/llm_caller.py](backend/app/signals/llm_caller.py)）：
  ```python
  _FALLBACK_MODEL = "gpt-4o-search-preview"
  DEFAULT_MODEL = os.getenv("OPENAI_MODEL", _FALLBACK_MODEL)
  ```
- 為何不用 `app.settings.get_openai_model()`：那個 helper 預設回 `gpt-4o-mini`，不支援 web search，會讓 M23 LLM stage 全掛
- Module-level snapshot：function 預設參數 (`def f(model=DEFAULT_MODEL)`) 在 import 時 capture 一次值，後續 caller 不需要顯式傳入 model 也能吃到 env

**修法 2：`build_candidate_pool` 空 list → 短路 `ValueError`**
- 原本 pipeline 拿到空 pool 還是會繼續送空 batch 給 LLM、最後寫一筆 `watchlist=[]` 的 done snapshot；cron exit 永遠 0，無法區分「真的沒抓到」與「成功但 0 檔」
- 修法（[backend/app/signals/pipeline.py:110](backend/app/signals/pipeline.py)）：在 `build_candidate_pool` 之後加：
  ```python
  if not pool:
      raise ValueError(f"no candidate stocks for target_date={target_date}")
  ```
- 既有 pipeline exception handler 會 `_mark_failed` (status="failed") 並 re-raise；`run_daily_signals._classify_exit_code` 抓 "no candidate" 子字串映射到 exit 1（no_data，workflow 仍 pass）
- 觸發情境：週末 / 假日跑、target_date DB 無交易資料、市場太冷沒任何個股通過篩選

**修法 3：`build_candidate_pool` 截斷排序註解**
- spec 描述「LEADER candidate (rank 高) > FOLLOWER candidate > 其他」應優先保留，但截斷發生在 `classification.classify_stocks()` 之前，這時候還沒有 `prelim_type` 可用
- 修法（[backend/app/signals/candidate_pool.py:242](backend/app/signals/candidate_pool.py)）：加 8 行註解說明用 `total_institution_flow_3d`（三大法人 3 日累計淨買超）做 LEADER-aware proxy 排序：
  - LEADER 通常法人連買金額最大 → 排序前段
  - LAGGARD / 弱勢 → 法人金額 ~0 或負 → 截斷時優先丟
- 實務上 60~120 檔幾乎觸發不到 SOFT_TRIGGER=150 hard limit；這段是安全網，未來真要更精準可加 lite 預分類

**測試更新（[backend/tests/test_signals_pipeline.py](backend/tests/test_signals_pipeline.py)）**：
- `_stub_all_stages_noop` 與 `test_pipeline_marks_failed_when_filter_stage_raises` 把 `build_candidate_pool` stub 從 `[]` 改成 `[{"stock_id": "_dummy"}]`（slice 11 後空 pool 會 raise，會跑不到後續 stage）
- 新增 `test_pipeline_raises_value_error_when_candidate_pool_empty` 驗證空 pool 短路路徑：raise ValueError + status=failed + finished_at 寫入
- `test_pipeline_persists_snapshot_with_payload_fields` 的 `candidate_pool_size` 斷言從 `0` 改 `1`（dummy pool 長度）
- 既有 `test_classify_exit_code_no_data` 已驗證 `ValueError("no candidate stocks for date") → exit 1`

**測試結果**：52 M23 tests pass + 509 全 backend tests pass（`test_engine_connects` 為 worktree sqlite 路徑問題，與本切片無關）

**Gotcha**：
- **不要把 `if not pool:` 移進 `build_candidate_pool`**：keep candidate_pool 為純 deterministic transform（input → output 不丟例外）；pipeline 才是 orchestration 層、由它決定「沒 pool = 給 cron 看 exit 1」的語義
- **Module-level `os.getenv()` snapshot 要在 import 時跑**：若改用 `def get_default_model(): return os.getenv(...)` 則 function 預設參數會 evaluate 一次（仍 capture 同一份），但 import-time 寫法更直觀也不會有 monkey-patch surprise
- **`_classify_exit_code` 已 covered**：`backend/tests/test_run_daily_signals.py:47` 已斷言 `_classify_exit_code(ValueError("no candidate stocks for date")) == 1`，本切片不需新增 cron 端測試

**狀態**：9/10 + slice 11 patches，剩 slice 10（prod smoke test）。slice 7~9 + slice 11 整條同 branch (`claude/angry-cerf-8755da`)，merge 上 main 後即可手動觸發 cron 驗證。

## 最近重要修正（2026-05-03）

- **M23 LAGGARD 中文標籤從「轉弱」改為「補漲」**：`signalPresentation.ts` 的 `DECISION_LABELS.LAGGARD` + `DailySignalsPanel.tsx` tab 標籤與「本日無 X 訊號」空狀態文案三處同步；KeyFactor trend 的 `weakening: "轉弱"` 維持不動（trend 跟 signal decision 是兩種語義，不要混淆）
- **自選清單 `WatchlistTradeQualityTable` 改成 2 欄響應式卡片網格**：原本 `<Table>` 在手機版 `sm:hidden` / `md:hidden` 把「未實現」/「燈號」欄擠到神秘空間，改 `grid gap-3 lg:grid-cols-2` 後手機自動單欄堆疊；外框對齊 `<DailySignalsPanel />`（`rounded-lg border border-zinc-700 bg-zinc-700/50` + 折疊 header）
- **每張卡片精簡欄位**：股號+名稱（buy_date 縮小副字）/ 動作建議（`RatingPill` + 上次 → 本次 delta + 資料較舊 flag）/ 今日股價（`PriceLine` 收盤價 + 漲跌 %，紅漲綠跌台股慣例）/ 燈號趨勢（`KeyFactorsTimeline compact`）+ 每張卡片自帶「看細節」按鈕導向 `/stocks/{id}?buy_date=X#watchlist-trade-quality`
- **移除整體 row click 跳頁**：原本整個 `<TableRow>` 是 cursor-pointer，現在卡片只有 stock 名稱 link 與「看細節」按鈕兩個導航入口；header 上「我的清單 →」（指向 `/watchlist` 完整頁）保留
- **`<TradeQualityAnalysis />` 加 `<PricePredictionBar />`**：在「目標價/出場價」純文字之上加視覺化價位帶，自動軸範圍 `[min × 0.95, max × 1.05]`、紅色 `bg-rose-500/40` 出場區間 + 綠色 `bg-emerald-500/40` 目標區間，下方 tick label 對齊四個價位（exit_low/high + target_low/high）；上方有 legend「出場區間」/「目標區間」說明
- **第一版不畫當前價 marker**：`TradeQualityResponse` 後端契約沒回 latest_close / buy_date close；要加得改 backend，下一輪再做
- **Gotcha**：`PricePredictionBar` 在 `values.length < 2` 或 `min === max` 時直接 return null（純文字仍會顯示）；padding 用 `(max - min) * 0.1` 退化為 `max * 0.05`，避免 max==min 時 padding 為 0 導致 div by zero

### 手機版 UX + 訊號清單命名收斂（2026-05-03 第二輪）
- **手機版燈號自動換行修正**：`WatchlistTradeQualityTable` 卡片從 `grid sm:grid-cols-[auto_1fr]` 改成 `flex flex-nowrap items-stretch gap-3 overflow-x-auto`；個股資訊（名稱+建議 / 今日股價 / 燈號趨勢 / 看詳細按鈕）整列水平排，不夠寬時整列左右拉。`DailySignalsPanel` SignalCard 的 4 個 SignalMetric 也從 `grid grid-cols-2 lg:grid-cols-4` 改成 `flex flex-nowrap overflow-x-auto`，每個 metric `shrink-0 whitespace-nowrap`
- **DailySignalsPanel SignalCard 加即時報價**：用 `useRealtimeQuotes(watchlistStockIds)` 一次抓 batch；`watchlist` 用 `useMemo` 包住避免 hook dep churn；`watchlistStockIds` 在折疊狀態下回 `[]`（折疊時不打 API）
- **DailySignalsPanel SignalCard 加 `<WatchlistAddButton variant="compact" />`**：使用者可直接從 L0 異常訊號清單把個股加進自選清單，不必先點進 L1/L2；`defaultAvgPrice={quote?.price ?? null}` 把即時價當預設均價帶進 dialog
- **SignalMetric label 瘦身**：「融資券」→「融券」（其他維持「資金 / 籌碼 / 技術」），讓 4 個 chip 在窄螢幕也能單列水平拉
- **訊號清單命名收斂**：「今日異常訊號清單」→「今日捕獲的大魚尾」（`DailySignalsPanel.tsx` header）；「今日異常訊號摘要」→「今日捕獲的大魚尾摘要」（`StockSignalSummaryPanel.tsx`）；「M23 40日訊號追蹤」→「抓到的股票觀察總覽（40日）」（`signals/archive/page.tsx` h1）
- **「看細節 / 看報告」按鈕統一**：4 處（DailySignalsPanel SignalCard / WatchlistTradeQualityTable / WatchlistTradeQualityCards / signals/archive 兩處）統一改成「點我看更多分析結果」
- **40日追蹤頁加 LEADER/FOLLOWER/LAGGARD 定義說明卡**：header 下方 3 欄 `sm:grid-cols-3` 卡片，分別介紹「領漲」/「跟漲」/「補漲」三種訊號類型；綠/藍/琥珀色點對應燈號樣式，給使用者第一次進頁面就有 onboarding 說明
- **Gotcha**：`useRealtimeQuotes` 內部已用 `idsKey = stockIds.join(",")` 做 stable string dep，所以 `watchlistStockIds` 即使每次 render 是新 array 也不會 effect churn；但 ESLint `react-hooks/exhaustive-deps` 仍會警告 `watchlist` 沒被 memo（即使函式語意正確），所以仍要 `useMemo` 包 `snapshot?.data.watchlist ?? []`
- **Gotcha**：`WatchlistTradeQualityTable` 外層保留 `grid gap-3 lg:grid-cols-2`，所以桌機仍是兩欄卡片網格；單列水平拉發生在每張卡內部，不會出現「外層格子滾動條 + 內層卡片滾動條」雙重滾動

### 訊號清單表格 chip 標籤對齊（2026-05-03 第三輪）
- **每欄字數對齊**：`DailySignalsPanel` 表格的 5 個訊號欄位 chip，後端 enum → 中文標籤改用「同欄字數一致」的對照：
  - 題材（theme_fit）：`強 / 中 / 弱`
  - 資金（capital_flow）：`強 / 中 / 弱 / 無`
  - 籌碼（chip_trend）：`集中 / 中性 / 轉弱 / 出貨`
  - 融券（margin_short_signal）：`過熱 / 軋空 / 中性 / 無感`
  - 技術（technical_status）：`突破 / 上升 / 轉強 / 盤整 / 出貨 / 偏弱`
- **Chip 前綴文字移除**：`InlineMetric` 不再額外印「資金 / 籌碼 / 融券 / 技術」前綴；表格 header 已標欄位名，cell 只留色塊 chip，整列視覺乾淨且寬度可控
- **`signalPresentation.ts` 新增 `FIELD_VALUE_LABELS`**：欄位專屬字典；`signalValueLabel(rawValue, kind?)` 加 optional 第二個參數，給定 kind 時優先吃 field-specific，否則 fallback 到原本的全域 `VALUE_LABELS`
- **`StockSignalSummaryPanel` 不動**：那邊 chip 周圍還有「VIX」「期貨」「題材契合」等中文 prefix，沒傳 kind 走原本 fallback；本輪只影響 L0 訊號表格
- **Gotcha**：`InlineMetric` 的 prop 從 `label` 改名為 `kind`（語意是 enum field 而非顯示字串）；`signalValueTone(kind, value)` 內部僅在 `kind === "type"` 時走特殊分支，其他 kind 字串對 tone 計算無影響，所以從中文 `"資金"` 換成 `"capital_flow"` 行為等價

### 加入清單流程簡化（2026-05-04）
- **使用者只需點按鈕**：`WatchlistAddButton` 不再開 dialog；按下去直接 POST `/api/watchlist`，body 只帶 `stock_id`。不再要求填買進日期 / 均價
- **後端自動填值**：`POST /api/watchlist` handler 在 server-side stamp：
  - `buy_date` = 加入當天台北日曆日（既有 `_today_taipei()` helper）
  - `avg_price` = 該股最新一筆 `daily_price` 的 `(open_price + close_price) / 2`；任一缺值退回另一個；皆缺時 raise 400「尚無此股票的價格資料，無法加入清單」
- **DB schema 不動**：`UserWatchlist.buy_date` / `avg_price` 兩個 NOT NULL column **保留**。trade quality cron / on-demand refresh / `(user_id, stock_id, buy_date, snapshot_trade_date)` snapshot unique key、KeyFactor delta 比對全部走原路徑零改動
- **對外 schema 收斂**：`WatchlistItem` 與 `WatchlistTradeQualityItem` 拿掉 `buy_date` / `avg_price` / `unrealized_pct` 三欄；卡片不再顯示「未實現損益」「買進日」；個股頁深連結不再帶 `?buy_date=X`（`StockWatchlistTradeQualityPanel` 同 user×stock 唯一，直接拿第一筆）
- **`WatchlistAddDialog.tsx` 整檔刪除**；4 處 caller（`HotMoneyList`、`DailySignalsPanel`、`StockList`、個股頁 header）拿掉 `defaultDate` / `defaultAvgPrice` props；`WatchlistAddButton` 內部不再開 dialog、改顯示行內錯誤
- **provider `add()` signature 簡化**：`useWatchlist().add(stockId: string)`，不再吃 `WatchlistCreateRequest`；caller 從 `add({ stock_id, buy_date, avg_price })` 變成 `add(stockId)`
- **`TradeQualityAnalysis.tsx` 不動**：那是首頁獨立的 AI 交易質量分析「工具」，使用者自由選 stock + buy_date 跑分析，跟 watchlist 加入流程無關；watchlist 卡片深連結不再帶 `?buy_date=X` 但 URL prefill 邏輯保留（未來人為貼 URL 仍可工作）
- **Gotcha**：`POST /api/watchlist` 需要該股至少一筆 `daily_price` 才能算 `avg_price`；新上市 / ETL 漏抓的個股會撞 400。`stocks_master` 有但 `daily_price` 沒資料的邊界情境是真的會發生的（特別在新股當日加入），錯誤訊息已含「價格資料」關鍵字方便使用者理解
- **Gotcha**：DB legacy column 保留代表 trade quality 的 `(user_id, stock_id, buy_date, snapshot_trade_date)` unique key 維持「每個 entry 一個固定 buy_date、每天一筆 snapshot」的語義；如果未來真的要砍 column，必須同時改 snapshot 表 schema 與 cache lookup 條件

## M23 40日追蹤：-30% 提前結算 + +45% 達標標注（2026-05-11）

### 規則
- **提前結算路徑**：每檔股票在追蹤期間，若 `return_pct` 首次跌破 -30%（threshold = `EARLY_EXIT_THRESHOLD_PCT`），開始進入 **3 個交易日反彈寬限期**（`EARLY_EXIT_GRACE_TRADE_DAYS`）；若這 3 個交易日內任一天 return_pct ≥ -30%（漲回），警示解除、繼續正常 40 日追蹤；若 3 天結束都仍 < -30%，於第 3 個寬限日**提前結算**：寫入 `signal_watch_completed_archives`（`closure_reason='early_exit_stop_loss'`、`completed_trade_date=寬限期最後一天`）+ 刪除 `signal_watch_hits` 該股所有 row（cycle 結束）
- **+45% 達標標注**：當 `max_positive_return_pct >= 45.0`（`PEAK_MILESTONE_PCT`），前端 active / completed 表都加金色 chip「⭐ +45% 達標」；僅標注、**不結算**

### 後端
- `backend/app/models.py::SignalWatchCompletedArchive` 新增 `closure_reason` 欄位（`String(32)`, NOT NULL, default `completed_40_days`），值域 `completed_40_days | early_exit_stop_loss`
- `backend/app/signal_watch_schema.py::ensure_signal_watch_hit_return_columns` 加 `ALTER TABLE ... ADD COLUMN closure_reason VARCHAR(32) NOT NULL DEFAULT 'completed_40_days'`（Render 啟動時 idempotent 補欄位，老 DB 自動 backfill 為 `completed_40_days`）
- `backend/app/signals/archive.py`：
  - 新增 `_post_baseline_returns(db, stock_id, baseline_trade_date, baseline_price, through_trade_date)` 回升序 `(trade_date, return_pct)` list
  - 新增 `_resolve_early_exit_settle_date(returns)` 純函式：找最後一次 ≥ threshold 的索引；之後第一天 = 觸發日 D；若 D+3（含）已落在資料內，回傳 D+3 為 settle_date；否則 None
  - 新增 `_build_early_exit_archive_item(...)` + `_upsert_completed_archive(item)` 共用 helper（`refresh_completed_signal_cycles` 也改用 `_upsert_completed_archive`，避免兩處重複 setattr）
  - `update_signal_watch_returns`：每檔股票算完 baseline + return 後，呼叫 `_post_baseline_returns` + `_resolve_early_exit_settle_date`；命中時 push 進 `early_exits` list；loop 結束後統一 `_upsert_completed_archive` + `db.query(SignalWatchHit).filter(stock_id==X).delete()`；最後仍呼叫 `refresh_completed_signal_cycles` 處理 40 日滿期 case
  - `_serialize_completed_archive_item` + `list_completed_archive_summary` 都帶 `closure_reason`（`row.closure_reason or CLOSURE_REASON_COMPLETED_40_DAYS` 保護老資料）

### 前端
- `frontend/src/lib/api.ts`：`SignalArchiveCompletedItem` 新增 `closure_reason: SignalClosureReason` 欄位；新 export type `SignalClosureReason = "completed_40_days" | "early_exit_stop_loss"`
- `frontend/src/app/signals/archive/page.tsx`：
  - `StopLossWarnChip`（紅色「⚠ 跌破 -30%」）：active table 上對 `return_pct <= -30` 或 `max_negative_return_pct <= -30` 顯示
  - `PeakMilestoneChip`（金色「⭐ +45% 達標」）：active + completed table 上對 `max_positive_return_pct >= 45` 顯示
  - `ClosureReasonChip`：completed table 對 `early_exit_stop_loss` 紅色「提前結算」、`completed_40_days` 灰色「40 日結束」
  - header 說明卡多加一塊「結算與標注規則」解釋兩種 chip
- `frontend/src/components/DailySignalsPanel.tsx` L0 header 加「每日將於晚上 21:30 更新」副字

### Gotcha
- **跑 cron 後 active rows 立即消失**：提前結算後，`signal_watch_hits` 該股 row 全清掉；前端 L0 panel 與 archive active table 立刻看不到，永久紀錄 table 看得到
- **未來再被抓到 = 新 cycle**：清掉 active 後，下次 `persist_signal_watch_hits` 看不到 prior return state，新 hit 會以新的 `first_seen_date` 進入新 cycle；`signal_watch_completed_archives` 的 UNIQUE `(stock_id, first_seen_date)` 不會撞 key
- **`closure_reason` 老 DB 是空字串/NULL**：`list_completed_archive_summary` 用 `row.closure_reason or CLOSURE_REASON_COMPLETED_40_DAYS` fallback；ALTER TABLE 加欄位時 NOT NULL DEFAULT 也會自動把現有列補成 `completed_40_days`
- **`refresh_completed_signal_cycles` 顯式覆寫 closure_reason='completed_40_days'**：避免某 stock 之前曾被 early-exit 寫入但 cycle 又重新長到 40 日的邊界情境誤標
- **「未來連續 3 個交易日」精確定義**：觸發日 D 之後的 D+1, D+2, D+3 共 3 個交易日；D 本身不算「未來」；settle_date = D+3（grace 期最後一天）；單元測試覆蓋一路下殺、grace 內反彈、剛跌破還沒過 grace 三種 case
- **+45% 達標純前端衍生**：直接讀既有 `max_positive_return_pct`，無新增 DB 欄位、無新增 ETL；不結算

## Telegram bot list 指令系統（2026-05-12）

### Scope
- 個人專屬 watchlist + 每日 21:30 自動推送清單報告 + 隨時觸發 trade quality 分析
- 與站台 user_watchlist / M25 snapshot 完全獨立（chat_id 不映射 users 表），刻意設計，避免 Telegram 使用者污染 web 帳號表
- 共用既有 `run_trade_quality_for_user(db, user=None, ...)` 跑 trade quality；`user=None` 自動跳過 M25 DB cache 路徑

### DB Schema（3 張獨立表，啟動時 `_ensure_telegram_tables` 自動 idempotent 建表）
- `telegram_chats (chat_id BIGINT PK, password_verified_at, registered_at, last_seen_at, chat_label)` — 註冊白名單，須通過 `SITE_GATE_PASSWORD` 才寫入
- `telegram_watchlist (chat_id FK CASCADE, stock_id, added_at, UNIQUE(chat_id, stock_id))` — 觀察清單，上限 20 檔（service 層強制）
- `telegram_trade_quality_snapshots (chat_id FK CASCADE, stock_id, snapshot_trade_date, ...M17 payload..., key_factors JSON, source, status, UNIQUE(chat_id, stock_id, snapshot_trade_date))` — 完整 M17 結果，沒有 `buy_date` 欄位（Telegram 沒有「買進均價」概念，每次都用最新交易日當 buy_date）

### 指令清單（全部以 `list` 開頭，case-insensitive）
| 指令 | 行為 | 同步/非同步 |
|------|------|-----------|
| `list help` | 顯示完整指令說明 | 同步 |
| `list register <密碼>` | 比對 SITE_GATE_PASSWORD → 寫 telegram_chats | 同步 |
| `list show` | 顯示清單（含每檔最新股價 + sub_industry） | 同步 |
| `list add 2330` / `list add 2330, 2317` | 新增單/多檔；找不到的標紅、超過 20 標琥珀 | 同步 |
| `list delete 2330` / `list delete 2330, 2317` | 刪除單/多檔；自動顯示剩餘清單 | 同步 |
| `list watch 2330 detail` | 讀 (chat_id, stock_id) 最新 ok 快照；無資料提示用 list run | 同步 |
| `list run 2330` | 跑單檔 trade quality → 寫快照 → 推送結果 | **非同步**（背景任務） |
| `list run all` | 跑清單全部 → 寫快照 → 推送彙整 | **非同步**（背景任務） |

### 非同步背景任務模式（list run / list run all）
- handler 先 `try_acquire(chat_id)` 拿鎖；拿不到 → 直接回「⏳ 已有任務在執行中」
- 拿到鎖 → 立即回「⏳ 已開始分析，跑完會推送」訊息
- `context.application.create_task(_run_*_background(...))` 排背景任務
- 背景任務 finally 區塊 `release(chat_id)`，確保鎖一定釋放
- timeout 10 分鐘：`locks._is_expired` 在 `try_acquire` 時順手清過期鎖（worker crash 卡死保護）
- in-memory dict，server 重啟會清空（個人專案接受）

### 21:30 cron（`.github/workflows/telegram_daily_report.yml`）
- cron `30 13 * * 1-5` UTC = 台北 21:30 週一~週五
- 串在 ETL（18:00）+ M25 wtq（~21:00）後，確保拿到當日完整 trade quality 資料
- `run_telegram_daily_report.py` 邏輯：
  - 對每個 chat 撈 watchlist
  - 對每檔股票：若已有 `snapshot_trade_date == today` 的 ok 快照 → 直接讀（避免重打 OpenAI），否則 `run_trade_quality_for_user` 跑新分析寫 cron snapshot
  - `formatters.format_daily_report(chat_label, [(snap, response), ...])` 組訊息 → 切 chunk → urllib 直接打 Telegram Bot API sendMessage
- 用 urllib 不用 python-telegram-bot `Application`，避免 cron script 管理 async event loop
- exit code: 0 ok / 1 partial / 2 all_failed / 5 holiday / 3 config_error

### 註冊密碼策略
- 與站台閘門共用 `SITE_GATE_PASSWORD`，避免雙密碼管理
- `hmac.compare_digest` 防 timing attack；密碼 strip 後比對
- 環境變數沒設 → registration 回「⚠️ 系統尚未設定」（不允許未驗證註冊）

### Gotcha
- **handler 路由「list」前綴**：`stock_query_handler` 開頭加 `text.lower().startswith("list")` 檢查並 delegate 給 `list_handler`，避免「list」字首訊息被當成股票代號
- **背景任務拿不到 update.message**：`_run_*_background` 只能拿 `context.bot.send_message(chat_id=...)`，不能 `update.message.reply_text`，因為 update object 不能跨 task 安全傳遞
- **`run_trade_quality_for_user(user=None)`** 已是純計算路徑：跳過 M25 DB cache 讀寫、跳過 _persist_db_cache_if_logged_in，剛好就是 Telegram 需要的「跑分析但不污染 M25 snapshot」行為，**無需另外抽 `compute_trade_quality_payload`**
- **`list watch <id> detail` 用 latest ok 快照**：status='failed' 的 row 跳過（避免使用者讀到 partial / error payload）；沒有任何 ok 快照 → 提示「請先用 list run」
- **訊息 Markdown 跳脫**：股票代號用 `` `2330` `` 反引號包；報告長度可能 > 4096 字（Telegram 上限），靠 `chunk_for_telegram` 切，每行不切斷
- **快照表沒有 buy_date 欄位**：每次都用 `get_latest_industry_trade_date(db)` 當 buy_date 傳給 `run_trade_quality_for_user`；snapshot_trade_date 用同一個值
- **chat_id 用 BigInteger**：Telegram supergroup 是負 int64（-1001234567890 之類），Integer 在 PostgreSQL 會溢位
- **CASCADE 刪除設計**：telegram_watchlist + telegram_trade_quality_snapshots 的 chat_id 都是 ForeignKey ondelete='CASCADE'，未來刪 chat 時自動清乾淨，無孤兒 row
- **`pytest.fixture autouse=True` 重置 in-memory locks**：避免 test 之間殘留鎖；`_reset_all_for_tests()` 是測試專用 helper

### Files
- `backend/app/telegram/` — 6 個模組（`__init__.py` / `locks.py` / `registration.py` / `watchlist_service.py` / `trade_quality_service.py` / `formatters.py` / `commands.py`）
- `backend/app/telegram_bot.py` — `list_handler` + `_run_single_background` + `_run_all_background` 三個新 async 函式
- `backend/run_telegram_daily_report.py` — cron 入口
- `.github/workflows/telegram_daily_report.yml` — 21:30 cron workflow
- `backend/tests/test_telegram_{locks,registration,watchlist_service,commands,formatters}.py` — 75 個單元測試

### 部署需求（Render 環境變數）
- `TELEGRAM_BOT_TOKEN` — 既有（M5 已設定）
- `TELEGRAM_WEBHOOK_URL` — 既有（M5 已設定）
- `SITE_GATE_PASSWORD` — 既有（2026-05-06 站台閘門已設）
- `OPENAI_API_KEY` — 既有（M17 已設定）
- `ADMIN_TELEGRAM_CHAT_IDS`（**新增 / 選填**）— 開發者後台白名單 chat_id，逗號分隔；空值 → admin 指令對所有 chat 禁用
- GitHub Actions secrets 需要 `TELEGRAM_BOT_TOKEN`（21:30 cron 推送用）

### 開發者後台（admin commands）
專為「開發者監看所有使用者 list」設計，**一般使用者打不開**（偽裝成 unknown 指令拒掉，不洩漏 admin 存在）。

兩條進入路徑：

| 入口 | 用途 | 守門 |
|------|------|-----|
| `list admin chats` Telegram 指令 | 手機隨時看；列出所有註冊 chat + 清單大小，依 last_seen DESC | `ADMIN_TELEGRAM_CHAT_IDS` env 白名單 |
| `list admin show <chat_id>` Telegram 指令 | 看單一 chat 的完整觀察清單（含每檔最新股價） | 同上 |
| `python3 backend/scripts/show_telegram_chats.py` CLI | 備援；在 Render Shell 跑印 markdown / `--json` | 需要 `DATABASE_URL` |
| `--chat-id <id>` flag | 同上但只看單一 chat | 同上 |

**設定方式**：使用者跑 `list register` 後，註冊成功訊息會直接告訴他自己的 chat_id；管理員把這個 ID 加到 Render env `ADMIN_TELEGRAM_CHAT_IDS=<id>,<id2>,...` 後 redeploy 即可。

**Gotcha**：
- admin 指令**不需要 chat 自己有註冊** — 在 `list_handler` 是獨立分支處理，先於 register check
- 非白名單 chat 打 admin 指令 → 回「未知指令」（**不洩漏指令存在**）
- `get_admin_telegram_chat_ids()` 容錯：忽略無效 token、保留負值（supergroup chat_id）
- CLI 與 Telegram 指令共用 `watchlist_service.all_chats_with_summary` / `get_chat_detail`，邏輯一致

## 全面免登入 + 單一密碼閘門（2026-05-06）

把原本的「DISABLE_AUTH=true 可選 flag」直接 hardcode：`backend/app/settings.py::is_auth_disabled()` 與 `frontend/src/lib/feature_flags.ts::isAuthDisabled()` **永遠回 True**。`feature/disable-auth-gating` 分支 merge 進 main 後，去掉 flag 環境變數，邏輯一行不動就達成「全站免註冊免登入」。

### 後端
- **`require_user` / `get_optional_user`**：disable-auth merge 帶進來的邏輯——一律回傳全站共用 demo user（`demo@always-stock.dev`，lifespan `_seed_demo_user_if_disabled` 啟動時 idempotent seed）
- **新 router `backend/app/routers/gate.py`**：
  - `POST /api/gate/verify { password }` — 用 `hmac.compare_digest` 比對 `SITE_GATE_PASSWORD` env，正確 200 / 錯誤 403 / 未設 env 503（不洞開）
  - `GET /api/gate/config` — 回 `{ max_attempts, lockout_seconds }`，給前端 mount 時讀取避免閘門參數寫死兩邊
- **`settings.py` 新增 4 個 helper**：`get_site_gate_password()` / `get_site_gate_max_attempts()` / `get_site_gate_lockout_seconds()` + 重寫 `is_auth_disabled()` 回 True
- **舊測試刪 4 個**：`test_me_requires_session` / `test_me_returns_current_user` / `test_logout_revokes_session` / `test_expired_session_is_rejected`——永久免登入後系統不再讀 cookie，這 4 個測試的契約失效

### 前端
- **`<SiteGate>`**（`frontend/src/components/SiteGate.tsx`）：包在 `AppProviders` 最外層，未通過密碼前 Navbar / 主內容完全不渲染
  - 四狀態：`boot`（SSR 中性畫面，避免 hydration mismatch）/ `prompt`（密碼輸入）/ `locked`（鎖定畫面）/ `unlocked`（render children）
  - localStorage keys：`always-stock:gate:unlocked_until` / `always-stock:gate:locked_until` / `always-stock:gate:attempts`
  - 鎖定 setInterval tick 每秒重算，到時自動切回 prompt + 重置 attempts
- **`feature_flags.isAuthDisabled()` hardcode true**：保留函式名讓既有 caller (`<RequireAuth />` / `Navbar` / `/login`) 一行不動

### Env 必填 / 選填
- `SITE_GATE_PASSWORD`（必填，未設 → verify 永遠 503）
- `SITE_GATE_MAX_ATTEMPTS`（選填，default 3）
- `SITE_GATE_LOCKOUT_SECONDS`（選填，default 300 = 5 分鐘）
- 舊的 `DISABLE_AUTH=true` env 可從 Render dashboard 拿掉（`is_auth_disabled()` 已 hardcode）

### Gotcha
- **鎖定狀態存 localStorage 可被主動清掉繞過**：個人專案信任使用者，這個強度夠用；要更嚴需改成後端按 IP rate limit
- **解鎖維持 7 天**：寫死在 `SiteGate.tsx::UNLOCK_DURATION_MS`，不走 env（避免增加部署複雜度）
- **SSR boot phase 必要**：`useState("boot")` 初值讓 server / client first render 都顯示「載入中」中性畫面，避免 hydration mismatch；mount 後 useEffect 才讀 localStorage 切換到實際狀態
- **`hmac.compare_digest`**：constant-time 比對，避免 timing attack
- **`SITE_GATE_PASSWORD` 用 `.strip()`**：環境變數取值 strip trailing whitespace；前端送進來的 password **不** strip，複製貼上多空白會驗失敗（刻意 — 避免 false positive）
- **demo user `password_hash` 是無對應原文 placeholder**：`disabled-auth-demo-user` 經 bcrypt，無 plaintext 對應，無法當正常帳號登入
- **既有 `Depends(require_user)` 一行不動**：所有 endpoint 認證寫法保留，未來恢復多帳號只需把 `is_auth_disabled()` 改回 env-driven，零 endpoint 改動

## LLM 效率 / 準確率優化第一+二波（2026-05-18）

針對 M23 魚尾訊號 + M25 watchlist trade quality 兩條 LLM 路徑做的 8 項優化，分兩波完工，全部 zero new test failures（baseline 20 → 20）。

### 第一波（M25 為主）

- **B1 deterministic rating/classification mapping**（`backend/app/routers/analysis.py`）
  - 新增 `_derive_rating_from_factors(factors)` → `(rating, classification)`：純 counts(A/B/C) 投票
  - rules：`A≥5 → STRONG_BUY、A≥3 → BUY、C≥4 → RUN、C≥2 → WATCH、else NEUTRAL`；`A≥4 → 類 A、C≥3 → 類 C、else B`
  - `_enforce_deterministic_rating(response)`：覆寫 LLM 的 rating + classification，把 LLM 原判寫進 `warnings`（`"AI 原判 rating=X → 已對齊燈號改為 Y"`）供對照
  - 接在 `_apply_key_factor_fallback` 之後（非 stream + stream 兩處）
  - **Why**：prompt 規定 4+A→類 A 等規則，但 LLM 仍經常違反，導致前端燈號全綠卻顯示「再看看」，使用者體感矛盾。後端強制對齊
- **A2 deterministic key_factors 為主**（`_apply_key_factor_fallback`）
  - 行為從「LLM 不齊才補」改為「m21 可用永遠覆寫」；warning 區分「補齊」vs「覆寫」
  - 主流程 smart routing：m21 可用 → 走 `_call_openai`（單次）；m21 不可用 → 走 `_call_openai_with_factors_retry`（保險）
  - **Why**：LLM 的 key_factors 飄移大、與 deterministic 訊號常打架；M21 已 deterministic 算好就沒理由讓 LLM 拍腦袋蓋。同時拔 retry → 每檔省 1 次 OpenAI call（過去 ~30% case 觸發）
- **A1 拔 raw OHLC/法人/月營收 3 段**（`_build_user_message`）
  - m21 可用 → 不貼 raw text blocks（前面 M21 `price_structure / chip_summary / fundamental` section 已結論化）
  - m21 不可用 → 仍保留 raw 3 段，避免 LLM 完全沒資料
  - **節省**：每檔約 600–1000 tokens（依股票歷史長度）
- **A7 retry 明列缺漏 category**（`_build_factors_retry_user_msg`）
  - 新增 `existing_factors` 參數；retry 訊息列出「上一輪已提供：x, y, z」+「缺漏必補：a, b, c」
  - 主路徑 A2 後 retry 觸發率大幅降低，A7 是 m21 不可用時的防禦層

### 第二波（M23 為主）

- **B4 type 鎖死 deterministic**（`backend/app/signals/llm_caller.py`）
  - 新 `_normalize_prelim_type(raw)` helper：`LAGGARD_CANDIDATE → LAGGARD`、未知/缺值 → `LEADER`
  - `run_research_batch` alignment 強制 `aligned[].type = _normalize_prelim_type(stock["prelim_type"])`，LLM 給的 type 被覆寫
  - research / decision / watch_reason 三段 prompt 都加硬規則「type 不可修改」
  - **Why**：classification 已是 deterministic（`classify_stocks`），但 LLM research stage 可能蓋掉分類，破壞 candidate_pool 排序與 spec §7 對齊
- **B6 deterministic 證據卡傳給 LLM**（`_to_evidence_view`）
  - 把 candidate_pool dict 投影成乾淨的 evidence card：14 個關鍵欄位（產業排名 / 連買日數 / 量能比 / 漲幅 / 法人金額 / 融資融券 / soft hints）
  - research / decision / watch_reason 三段 user_msg 改吃 `_to_evidence_view(stocks)` 取代直接 dump
  - prompt 加硬規則：reason 必須引用 evidence 段 2-3 個具體數字，避免「籌碼好」「題材熱」空話
  - **Why**：原本整包 candidate dict dump（含 internal 欄位）噪音大、LLM reason 經常給空話；evidence view 更乾淨且強制 LLM 引用具體數據
- **A3 market_context 4h cache**（新增 `backend/app/signals/market_cache.py`）
  - In-process dict + 4h TTL（單 worker FastAPI 部署夠用；多 worker 要換 Redis）
  - `assemble_market_context(snapshot, use_cache=True)`：命中 cache 直接回；taiex/otc 仍從 backend snapshot 覆寫（避免 cache 鎖死當日漲跌幅）
  - fallback 路徑（OpenAI 不可用）**不寫 cache**，避免使用者連續 4h 看到 RANGE
  - cron 可用 `use_cache=False` 強制 fresh
  - **節省**：使用者連按「重新產生」第 2 次以後免一次 web_search call（5–8 秒）
- **A4 prompt 按 stage 切片**（`_load_system_prompt(stage)` + `_build_stage_prompt`）
  - 不切檔，原 `watch-list-stock.md` 保留；用 string parsing 抽取對應 STEP 區段
  - stage → 包含的 STEP：`market={0}` / `research={1,2,3,4}` / `decision={5,6,7,8,9}` / `watch_reason={7,8,9}` / `full={全部}`
  - preamble（核心原則 + INPUT 描述）與「重要限制」永遠保留；watch_reason 額外保留「WATCH 長理由寫作規則」
  - fragment 結果 module-level cache（`_PROMPT_FRAGMENT_CACHE`），不會每次 LLM call 重新切
  - **節省**：market / decision / reason stage 各省 ~50-60% input tokens（不再送無關 STEP）

### 測試與部署 Gotcha

- **跑 cron 第一次後觀察**：B6 evidence card 要求 LLM 引用數字，prod 若 reason 仍空話，可調 prompt strict 度
- **B1 + A2 上線後 watchlist row 變化**：deterministic rating 上線 → 部分 row rating 會變（與 LLM 飄移結果不同）；使用者體感是「重新分析後評級變了」。可以在使用者通知文案加說明
- **A3 cache 重啟清空**：Render 部署重啟後 cache 清空；第一個使用者按按鈕仍會跑 fresh，正常
- **A4 prompt 切片若解析錯**：fallback 回 `full`（保守），不會破 LLM 路徑；測試 `test_load_system_prompt_market_stage_drops_other_steps` 驗 STEP 切片正確
- **既有測試對齊**：M25 4 個測試（rating 5-tier mapping / retry / stream / parses_openai_json）改加 `patch("_synthesize_key_factors_from_context", return_value=None)` 模擬 m21 不可用，聚焦 LLM 原行為；M23 `test_run_research_batch_aligns_response_by_stock_id` 補 `prelim_type` 才能驗證 B4 覆寫
- **monkeypatch lambda 簽章**：`_load_system_prompt` 加 `stage` 參數後，test 內 `lambda: ...` 全部要改 `lambda stage="full": ...`

## M23 retention 30 天徹底化：砍 DB column + UPDATE enum（2026-05-21 第二輪）

### Scope
- 接續第一輪「常數 40 → 30 + UI 文案改 30」(commit `d5b6934`)；本輪把當時保留的 DB 層歷史命名也徹底清掉
- **DB destructive ops**（lifespan migration 一次性 idempotent）：
  - `DROP COLUMN signal_watch_completed_archives.return_day_40_pct`
  - `UPDATE signal_watch_completed_archives SET closure_reason='completed_30_days' WHERE closure_reason='completed_40_days'`
  - `ALTER COLUMN closure_reason SET DEFAULT 'completed_30_days'`
- 後端 / 前端 / 測試所有 `return_day_40_pct` 與 `"completed_40_days"` 字面值全清掉

### Migration helper（[backend/app/signal_watch_schema.py::migrate_completed_archive_to_30_days](backend/app/signal_watch_schema.py)）
- 三步驟各別 try/except 包：DROP / UPDATE / ALTER DEFAULT 任一失敗只 logger.warning，不阻擋 app 啟動
- `inspect()` 先看 `return_day_40_pct` column 是否存在，存在才 DROP（避免重啟重複噴 log）
- `PostgreSQL IF EXISTS` 確保 idempotent；SQLite 測試不會走這條路（直接用 `Base.metadata.create_all` + 新 schema）
- 由 `main.py::_ensure_signal_watch_schema()` lifespan 在啟動時呼叫一次

### 改動清單
- 後端：[archive.py](backend/app/signals/archive.py) 常數 / dataclass / 7 處 `return_day_40_pct` 引用 / 3 處 `CLOSURE_REASON_COMPLETED_40_DAYS` 引用全清；[models.py](backend/app/models.py) column drop + default 改 `completed_30_days`；[routers/signals.py](backend/app/routers/signals.py) pydantic schema 拔 + default 改；[signal_watch_schema.py](backend/app/signal_watch_schema.py) ALTER 預設值改 + 加 migration helper；[main.py](backend/app/main.py) lifespan 呼叫
- 前端：[lib/api.ts](frontend/src/lib/api.ts) `SignalArchiveCompletedItem.return_day_40_pct` 拔 + `SignalClosureReason` union 從 `"completed_40_days"` 改 `"completed_30_days"`；archive page 本身 `ClosureReasonChip` 只看 early-exit reason，fallback 走「追蹤期滿」chip，零改動
- 測試：[test_signal_archive_returns.py](backend/tests/test_signal_archive_returns.py) `return_day_40_pct is None` 斷言刪、`CLOSURE_REASON_COMPLETED_40_DAYS` → `_30_DAYS`、`closure_reason="completed_40_days"` seed → `"completed_30_days"`；[test_signals_router.py](backend/tests/test_signals_router.py) fixture 拔 + 斷言改 `return_day_30_pct == 6.5`

### Gotcha
- **既有歷史 row 那欄資料永久消失**：影響 = 0（前端早就不顯示 return_day_*_pct 欄位）
- **prod migration 失敗不會擋啟動**：lifespan 包 try/except + logger.warning；若 DROP / UPDATE 任一失敗，app 仍會起來但 schema 與 code 不一致。需從 Render log 看 warning 訊息
- **SQLite 測試環境**：`Base.metadata.create_all` 直接用新 schema 不存在 column，migration 函式內 inspector 也看不到 legacy column，skip 整段；測試不需特殊處理
- **常數重命名 `CLOSURE_REASON_COMPLETED_40_DAYS` → `_30_DAYS`**：所有 import 同步改；只有 archive.py 內 + 1 個測試斷言用此常數，搜尋 `CLOSURE_REASON_COMPLETED_40` 應為 0 結果（除註解／migration helper SQL 內字串）
- **`refresh_completed_signal_cycles` 寫入時用新 enum value**：避免 cycle 重新跑到滿期時又寫 `completed_40_days`
- **lifespan 失敗的容錯邊界**：UPDATE 失敗 → 新舊 enum value 並存 DB；前端 ClosureReasonChip 對未知 value fallback 走「追蹤期滿」灰色 chip，視覺不會壞但統計上會有兩種值

## Sticky horizontal scrollbar 全站套用（2026-05-21）

### Scope
- 長表（魚尾追蹤 10 欄、completed 8 欄）水平 scrollbar 原本在表格底部 → 必須先捲到表格最底才能拖、再回捲找列
- 新增 `<StickyHorizontalScroll>` wrapper：在視口底部 portal 一條 fake scrollbar，與內部 wrapper 雙向同步 `scrollLeft`
- 透過改 `frontend/src/components/ui/table.tsx::Table` 一處達成全站表格 zero-touch 受惠

### 顯示條件（避免重複 / 干擾）
- **wrapper 部分在視口內 AND wrapper 底部仍在視口外** → 顯示 fake bar
- 捲到表格底端時原生 scrollbar 露面 → fake bar 自動隱藏，不會兩條重複
- 表格在視口外時 fake bar 隱藏
- 監聽 `window.scroll` / `resize` 即時重新評估顯示條件

### 同步機制
- `useRef<"wrapper" | "bar" | null>` guard 雙向同步 scrollLeft 避免無限 loop（A 觸發 B 後立刻清旗）
- `ResizeObserver` 監看 wrapper + inner content 寬度變化（filter / inline expand row 增減即時反映）
- `MutationObserver` 看 children 變動（特別針對動態 row 增減）
- `createPortal(fakeBar, document.body)`：fake bar 跳脫表格 DOM 樹，`position: fixed; bottom: 0; z-50`

### 實作位置
- 新檔 [frontend/src/components/StickyHorizontalScroll.tsx](frontend/src/components/StickyHorizontalScroll.tsx)
- [frontend/src/components/ui/table.tsx](frontend/src/components/ui/table.tsx)：`<Table>` 內建使用 → 全站 `<Table>` 受惠
- [frontend/src/components/FinancialsPanel.tsx](frontend/src/components/FinancialsPanel.tsx)：自定義 `<table>` 改用
- [frontend/src/components/KeyFactorsTimeline.tsx](frontend/src/components/KeyFactorsTimeline.tsx)：非 compact 路徑改用（compact 是小卡片內不需要）

### Gotcha
- **SSR safe**：`useEffect setMounted(true)` 才 `createPortal`；預設 mounted=false 不 portal，避免 hydration mismatch
- **多表共存**：頁面有多個表時，各自獨立 fake bar；顯示條件天然錯開（捲到 A 表時 B 表通常已不在視口）；極端情境可能疊兩條，可接受
- **iOS Safari momentum scroll**：原生 momentum 跟同步邏輯有微秒級 lag，桌機正常；可接受
- **fake bar 高度 = 14px**：在 macOS / Windows / mobile 都接近原生 scrollbar 高度；視覺乾淨且不擋內容
- **content 寬度 + wrapper 寬度比較用 `+1` buffer**：avoid rounding edge case 誤判 hasOverflow
- **隱藏條件包含 `!hasOverflow`**：表格欄夠少不需水平捲時 fake bar 不顯示
- **KeyFactorsTimeline 的 compact 路徑沒套**：compact 是 inline 小卡片內 6 列表，本來就不會超寬，套了 overkill

## M23 訊號追蹤頁 UX 升級：搜尋框 + inline expand 報告（2026-05-21）

### Scope
- `/signals/archive` 兩個表（active 30 日追蹤 + completed 永久紀錄）各加 client-side 搜尋框（filter by stock_id 子字串 OR stock_name 子字串）
- active 表的「點我看更多分析結果」按鈕從「跳到頁面底部 detail panel」改為 **inline 在該 row 下方展開**（fragment + colSpan 整列 detail row）
- 一次只能展開一檔（沿用 `selectedStockId` single state，toggle 行為：再點同檔收合）
- 刪掉原本頁面最底下的獨立 detail panel section

### 為何 completed 表不做 inline expand
- completed 表本來就**沒有**「點我看更多」按鈕（只有 K線圖連結）
- 後端在封存時會 `db.query(SignalWatchHit).filter(stock_id==X).delete()` 清空對應 hits → `get_archive_detail(stock_id)` 對封存股票永遠回 None → 即便加 inline expand 也只會顯示「找不到報告內容」
- 想加 detail 入口需要先改 backend（archive 表額外保存 reports JSON / 或保留 hits 不清除），不在這輪範圍
- 搜尋框不依賴 detail，所以兩表都加 OK

### 實作
- `frontend/src/app/signals/archive/page.tsx`：
  - 兩個 search state：`activeSearch` / `completedSearch`
  - 兩個 `useMemo` filter：`filteredActiveItems` / `filteredCompletedItems`，純前端 `toLowerCase().includes()`
  - `toggleExpand(stockId)`：`setSelectedStockId(prev => prev === stockId ? null : stockId)`
  - active TableBody 用 `<Fragment key={stock_id}>` 包：原 row + `(active && <TableRow><TableCell colSpan={10}>...detail...</TableCell></TableRow>)`
  - 按鈕文字 toggle：「點我看更多分析結果」/「收合報告」
  - 預設 `selectedStockId=null`（先前是 fallback 到 `items[0]?.stock_id` 自動展開第一檔；inline expand UX 下應該等使用者主動點才展開）
  - 刪 `selectedSummary` useMemo（只被刪除的底部 panel 用）

### Gotcha
- **inline detail 仍走原本的 `useEffect([selectedStockId])` fetch path**：不需要改 fetch 邏輯，只是顯示位置變了
- **detail row 用 `colSpan={10}` 對齊 active 表 10 欄**；completed 表（8 欄）若未來也要 inline expand 要記得改成 `colSpan={8}`
- **`detail.stock_id !== item.stock_id` 邊界**：點 A 展開時 detail 正在 fetch，使用者瞬間點 B → `selectedStockId=B` 但 `detail` 還是 A 的；用 `detail.stock_id === item.stock_id` 守住避免 A 的 detail 顯示在 B row 下面（會有極短瞬間 race，detail loading state 視為過渡）
- **空搜尋結果用 colSpan row 顯示「找不到符合『X』的股票」**：active 用 colSpan=10、completed 用 colSpan=8，與表格欄數一致
- **搜尋框純前端**：不打 backend，refresh 不保留搜尋字串（state 在 useState，非 URL params）；如未來想保留，可比照 URL state preservation pattern 加 `?q=` query
- **completed 表搜尋與半年區間 filter 並存**：先選半年區間（後端 query）→ 再用前端搜尋框 filter 該區間 items

## M23 訊號追蹤 retention：40 → 30 個交易日（2026-05-21）

### 改動
- `backend/app/signals/archive.py::ARCHIVE_RETENTION_TRADE_DAYS = 40` → **30**
- 行為連動：`_prune_signal_watch_hits` 只保留最近 30 個 snapshot_date；`refresh_completed_signal_cycles` 第 30 天就走滿期結算（不再等 40 天）；`_resolve_nth_trade_date(day_index=30)` 是新的 cycle 終點

### 向後相容（DB schema 不動）
- `SignalWatchCompletedArchive.return_day_40_pct` column **保留**：retention 30 後新 cycle 永遠寫 NULL；既有歷史 row 不受影響
- `closure_reason = "completed_40_days"` 字面值與常數 `CLOSURE_REASON_COMPLETED_40_DAYS` **保留**：避免破壞既有資料；新註解標明這是歷史命名，語義 = 「完成追蹤 cycle」
- `_build_completed_archive_item` / `_build_early_exit_archive_item` 內 `return_day_40_pct` 顯式寫 None（不再呼叫 `_resolve_return_for_tracking_day(tracking_day=40)` 浪費 query）

### UI / 文案改動
- `frontend/src/app/signals/archive/page.tsx`：h1「抓到的股票觀察總覽（40日）」→「（30 個交易日）」；`ClosureReasonChip` 滿期 chip「40 日結束」→「追蹤期滿」；section heading「移出 40 日後紀錄」→「追蹤期滿移出紀錄」；說明文案「不必等 40 日」→「不必等追蹤期滿」
- `frontend/src/components/DailySignalsPanel.tsx`：「40日追蹤」按鈕 → 「30日追蹤」
- `README.md` 與 `docs/plans/m23_signal_archive_spec.md` 加 2026-05-21 變更註記

### 測試
- `test_refresh_completed_signal_cycles_upserts_40_day_archive_rows` 重命名為 `_upserts_full_cycle_archive_rows`
- 斷言 `completed_trade_date == first_seen + timedelta(days=29)`（第 30 個交易日，非 39）+ `return_day_40_pct is None`
- `closure_reason` 仍斷言 `CLOSURE_REASON_COMPLETED_40_DAYS` 字面值（向後相容）
- 全 14 個 `test_signal_archive_returns.py` pass

### Gotcha
- **既有 archive table 不需要 backfill**：原本 40 天 cycle 完成的歷史 row 仍是合法紀錄（按當時規則結算）；新 cycle 走 30 天規則
- **DB column 命名歷史錯位**：未來看 `return_day_40_pct` column 名仍像 40 天 retention，需配合 model docstring 與本段落理解。若未來真要重命名，DB migration + API contract + 前端三方都要同步
- **`closure_reason="completed_40_days"` 字面值已成歷史 key**：前端 `ClosureReasonChip` 對應顯示「追蹤期滿」，不再寫「40 日結束」；enum 值依然保留以避免 DB migration

## 全站 filter / tab / sort URL params 化（2026-05-21）

### Scope
- 使用者選了 tab、sort、子產業 filter、K 線天數、archive 半年區間後，點進個股看細節，再按瀏覽器上一頁要回到原本選擇的狀態
- 解決方案：把「真正的 filter / sort / 想被分享」的 state 從 `useState` 移到 URL query params，靠瀏覽器原生 back/forward stack 自動還原
- 純使用者偏好（折疊狀態、面板顯示開關）仍維持 localStorage，**不**寫進 URL 避免雜訊

### 為何不啟用 Next 16 Cache Components
- 那是 page-level state preservation，會把所有頁面包成 `<Activity>` 維持 DOM；改動範圍大，需驗證 react-echarts hidden 時不爆炸、realtime quotes polling 是否會 leak
- URL params 是更聚焦、零副作用的方案；refresh / 分享連結也能保留狀態

### 已 URL 化的欄位
| 頁面 | URL params 新增 / 既有 |
|------|----------|
| `/`（首頁） | **新增** `?signals_tab=follower\|laggard`（DailySignalsPanel，預設 leader 不寫）；既有 `?date=`、`?stock_id=&buy_date=`（TQA prefill） |
| `/industries/[name]` | **新增** `?sort=total\|foreign\|trust\|dealer\|streak`（SummaryTable 排序欄位，預設 total 不寫）+ `?sort_dir=asc`（預設 desc 不寫）；既有 `?date=`、`?sub=` |
| `/stocks/[id]` | **新增** `?chart_days=N`（K 線天數，預設 90 不寫）；既有 `?date=`、`?start=&end=`（L3 帶回的回測區間） |
| `/signals/archive` | **新增** `?sort_by=`（預設 `tracking_days_desc` 不寫）+ `?period=YYYY-MM-DD`（半年區間起始日） |

### 實作 pattern
```ts
const router = useRouter()
const pathname = usePathname()
const searchParams = useSearchParams()
const param = searchParams.get(KEY)

function update(next) {
  const params = new URLSearchParams(searchParams.toString())  // ← 保留其他 panel 的 params
  if (next === DEFAULT) params.delete(KEY)  // ← 預設值不寫進 URL
  else params.set(KEY, String(next))
  const q = params.toString()
  router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false })
}
```

### Gotcha
- **`useSearchParams` 在 Next 16 client component 內仍需 Suspense 包覆**：`signals/archive/page.tsx` 把內容抽到 `SignalArchiveContent`，default export 用 `<Suspense><SignalArchiveContent /></Suspense>` 包起來
- **`router.replace({ scroll: false })`**：tab 切換 / sort 變更不該觸發 scroll-to-top
- **StockChart 內部 `useState(initialDays)` 不會 sync prop 變化**：要再加 `useEffect(() => setDays(initialDays), [initialDays])` 才能讓 URL 控制即時反映到 chart。否則 URL 更新後 chart 看起來像沒反應
- **預設值不要寫進 URL**：`if (next === DEFAULT) params.delete(KEY)`，否則第一次點預設 tab 也會留 URL 雜訊
- **多 panel 共存**：用 `new URLSearchParams(searchParams.toString())` 為基礎再 set/delete，**不要** `new URLSearchParams()` 從空白開始（會把其他 panel 的 params 全部蓋掉）
- **L1 排序為「點同一欄切 asc/desc」**：所以 `sort_dir` 是布林（asc/desc），不是 tri-state；handleSort 直接 `syncUrl(date, sub, { sort, sortAsc: next })`
- **DailySignalsPanel `collapsed` 維持 localStorage**：per-user 偏好不該污染 URL
- **L2 個股頁 `showFinancialsPanel` 也維持 localStorage**：同上原則

### 驗證流程
1. 進首頁 → 點「跟漲」tab → URL 變 `/?signals_tab=follower`
2. 點任一檔股票 → 個股頁
3. 按瀏覽器上一頁 → 回首頁 → tab 仍在「跟漲」
4. 同樣模式驗 L1 子產業 filter / 排序欄、L2 改 K 線天數、archive 切半年區間

## M23 drawdown-from-peak 提前結算 + 半年表格（2026-05-18）

針對 40 日追蹤新增第 2 條提前結算規則 + 永久紀錄改為半年一張表。

### 新規則：drawdown from peak（停利紀律）
- **觸發條件**：`max_positive_return > 0` AND `current_return < 0` AND `(max_positive - current) >= 30%`
- **寬限期**：觸發日 D 之後 3 個交易日（D+1 / D+2 / D+3）；若任一天 drawdown 回到 < 30% 警示解除、繼續 sweep；3 天都仍超標 → D+3 結算
- **與舊規則（return ≤ -30%）並存，取較早觸發者**
- closure_reason 值：`early_exit_drawdown_from_peak`（rose-orange chip「提前結算（高點回落 30%）」）
- 實作位置：[backend/app/signals/archive.py](backend/app/signals/archive.py) `_resolve_drawdown_exit_settle_date()` + `update_signal_watch_returns` 內 chosen_settle 二選一 + `_build_early_exit_archive_item` 接 closure_reason 參數
- 常數：`DRAWDOWN_EXIT_THRESHOLD_PCT = 30.0` / `DRAWDOWN_EXIT_GRACE_TRADE_DAYS = 3`
- **Why**：規則 1 看絕對虧損（baseline 之後一路跌 -30%）；規則 2 看從高點回落（漲過再跌下來）。後者更貼近實務停利紀律 — 漲過 +15% 又跌回 -15% (drawdown 30%) 雖然 return 還沒到 -30%，但已是賺錢變賠錢的失控狀態

### 永久紀錄半年表格
- **半年區間**：以 2026-05-01 為 anchor，每 6 個月一段；用 `completed_trade_date` 當分段標準
  - 2026-05-01 ~ 2026-10-31、2026-11-01 ~ 2027-04-30、2027-05-01 ~ 2027-10-31、…
- **API**：`GET /api/signals/archive/completed?period_start=YYYY-MM-DD`（後端會 normalize 到 anchor）
- **Response**：除 items 外多回 `periods: [{period_start, period_end, count}]`（倒序）+ `selected_period_start`
- **前端 UI**（[frontend/src/app/signals/archive/page.tsx](frontend/src/app/signals/archive/page.tsx)）：表格上方加半年區間 tab；首次載入預設選最新一段
- **表格欄位精簡**（依使用者要求，從 11 欄縮成 8 欄）：股票/產業 / 首次抓到 / 抓到次數 / 類型（LEADER/FOLLOWER/LAGGARD chip） / 最大正報酬 / 最大負報酬 / 移出原因（含日期）/ 操作（K線圖）
- **拔掉**：第 10/20/30/40 天報酬欄位（仍存在 DB 與 API，前端不顯示）、獨立的「移出日」欄（合進「移出原因」cell）
- **新元件**：`SignalTypeChip`（LEADER→領漲綠 / FOLLOWER→跟漲藍 / LAGGARD→補漲琥珀）、`formatPeriodLabel` 顯示「2026/05 - 2026/10」

### Gotcha
- **`half_year_period_start(date)` 對齊邏輯**：以 anchor 起算 `months_since_anchor // 6` 找 bucket，再算回該 bucket 起始月。輸入早於 anchor 直接回 anchor。Pure function 純算數學，不依賴 DB
- **periods meta 永遠 group by 全表**：不會因為 `period_start` filter 影響 periods 列表；前端 tab 永遠看得到所有半年區間
- **selected_period_start 預設行為**：mount 時 `selectedPeriodStart=null` → API 回全表 + periods → 前端 effect 看 `data.periods[0]` 設為最新一段。避免使用者第一眼看到 200 列爆炸
- **規則 1 vs 規則 2 互斥取早者**：兩規則並存但同一 cycle 只結算一次；若兩規則同日觸發，drawdown 優先（畢竟漲過再跌的紀律更嚴）
- **drawdown 規則需 max_positive > 0**：從未漲過正報酬的股票（baseline 之後直接一路跌）只會被舊規則「return ≤ -30%」抓到，不會被新規則
- **既有 4 baseline test failure 不變**：site-passwordless 改動後未同步的 4 個 signals_router test 仍 fail，與本輪無關（驗證 baseline 20 fail = 20 fail）

## M23 融資融券資料缺漏修復（2026-05-25）

### 問題
- 魚尾 daily signals 的「融券」欄位幾乎全部顯示「無感 / 中性」，使用者抓不到融資融券訊號
- Root cause：`margin_trade` 表大量交易日 0 rows
- 對比 5/4~5/22 過去三週：`daily_price` 每天連續完整，`margin_trade` 只有 5/4、5/11、5/18、5/21、5/22 五天有資料，中間 10 個交易日全 0 rows

### Root cause
- `daily_etl_update.yml` cron `0 10 * * 1-5` UTC = 台北 18:00，加 GitHub Actions delay 通常落在 19:30–20:45
- **FinMind `TaiwanStockMarginPurchaseShortSale` dataset 需要台北 21:00 之後**（券商公告當日餘額後）才同步
- 18:00 cron 跑時 margin step 拿到 `no_data`（FinMind 回 0 筆），整天 0 rows
- candidate_pool 算 `margin_change_3d` / `short_change_3d` 需要 3 個連續交易日 row，缺一天就回 None
- evidence card [llm_caller.py:990](backend/app/signals/llm_caller.py#L990) 只送 3d 不送 1d，LLM 看到 null 就保守標 `margin_short_signal=neutral`，前端「融券」欄就顯示「無感」

### 修法（方案 A：獨立 backfill workflow）
- 新增 [backend/run_margin_backfill.py](backend/run_margin_backfill.py)：lookback / 區間 / 單日 三模式；預設掃描最近 14 個交易日，比對 `margin_rows / price_rows < 0.85` 視為缺漏自動補抓
- 新增 [.github/workflows/margin_trade_backfill.yml](.github/workflows/margin_trade_backfill.yml)：cron `30 14 * * 1-5` UTC = 台北 22:30（FinMind 21:00 後同步留 1.5h buffer）；workflow_dispatch 支援 `date / start_date / end_date / lookback / force`
- 不動 `daily_etl_update.yml` 既有時程；不動 `run_finmind_etl_sdk.py` step 7 邏輯（margin step 仍在 18:00 跑，no_data 時純當作前哨偵測，22:30 backfill 才是 source of truth）
- 一次性手動補 2026-05-05 ~ 2026-05-20 缺漏 10 個交易日，每天 ~1267~1270 筆，配額消耗 8 quota / 6000

### Gotcha
- **MIN_COVERAGE_RATIO = 0.85**：因為 ETF / 無融資資格標的會被 FinMind 過濾，實際 margin_rows / price_rows 約 91%；門檻 0.85 預留彈性，避免合理的「ETF 多了」誤判為缺漏
- **18:00 step 仍保留**：偶爾遇到 cron 延遲到 22:00 後可能直接抓到資料（如 5/22 case），這時 22:30 backfill 就 skip（coverage >= 0.85），不浪費 quota
- **script 用 BETWEEN + GROUP BY**：兩個獨立查詢然後 Python 合併，不用 `WHERE trade_date IN :tuple`（SQLAlchemy `text()` 不認，要 `bindparam(expanding=True)` 才行，太繞）
- **exit code**：`0 ok / 1 partial / 2 all_failed / 5 holiday`；workflow 視 0/1/5 為 pass，2 為 fail
- **evidence card 仍只送 3d**：本輪只解決資料缺漏；未來若要讓 LLM 更敏銳，可考慮在 [llm_caller.py:990](backend/app/signals/llm_caller.py#L990) 加 `margin_change_1d / short_change_1d`，給單日訊號 fallback

## M23 融資融券深度分析（2026-05-25 第二輪）

### 需求
- 魚尾 SignalCard 的「融券」chip 太薄，使用者要求加深層分析
- 必須包含「大盤融資融券盤勢」（瞭解整體環境）+「個股融資融券狀況」（具體解讀）
- 權重 3:7（大盤 30% / 個股 70%）
- 個股部分要用使用者範例的格式：表格 + 解讀 + 結論 + 風險提示

### 實作
- **後端新檔** [backend/app/signals/market_margin.py](backend/app/signals/market_margin.py)
  - `compute_market_margin_snapshot(db, target_date, *, short_lookback=5)`：聚合全市場融資融券；輸出 today + trend_5d + climate_label/reason
  - `climate_label` 純規則：5 日融資 +2% 以上→`expansive`、-2% 以下→`contractive`、否則 `neutral`、無資料 `unknown`
  - `climate_reason` 寫成 LLM 可直接引用的繁體中文一句話
- **candidate_pool** 加 5 個欄位給 LLM 寫表格用：`margin_balance_shares / margin_change_shares / short_balance_shares / short_change_shares / margin_short_ratio_pct`
- **llm_caller**：
  - `_to_evidence_view` 加 6 個欄位（上述 5 + `close_price`）
  - `_run_watch_reason_chunk` user_msg 加 margin_analysis schema + 嚴格 3:7 規則 + 抄 evidence 不可自編
  - `_coerce_margin_analysis(raw, *, evidence)` 從 LLM 回應抽出物件；缺漏時用 evidence 補表格部分
  - `_watch_reason_fallback` 也產 margin_analysis（至少有表格）
  - `_WATCH_REASON_HEADERS` 加「WATCH margin_analysis 寫作規則」確保 stage 切片時保留
- **pipeline** 在 explanation stage 前算一次 `market_margin.compute_market_margin_snapshot`，塞進 `market_context["margin_climate"]`，供後續 batch 共用
- **prompt** [backend/app/prompts/watch-list-stock.md](backend/app/prompts/watch-list-stock.md)
  - INPUT 個股欄位加 5 個張數欄 + `close_price`
  - INPUT market_context 加 `margin_climate` 物件
  - OUTPUT watchlist[] 加 `margin_analysis` 物件
  - 新增「WATCH margin_analysis 寫作規則」section（含 3:7 權重、欄位規則、白話口吻範例）
- **前端**：
  - [api.ts](frontend/src/lib/api.ts) 加 `SignalMarginAnalysis / SignalMarginAnalysisTable / SignalMarketMarginClimate / SignalMarketMarginToday / SignalMarketMarginTrend` 型別
  - [DailySignalsPanel.tsx](frontend/src/components/DailySignalsPanel.tsx) 加 `MarginAnalysisPanel` 元件（表格 + 個股解讀 + 結論 + 大盤摘要 + 風險提示，rose-themed）
  - 注入到 `SignalDetailDialog` 5 panel grid 之後、footer 之前
  - 台股慣例配色：融資/融券「增加」紅色（散戶活躍）、「減少」綠色（退場）

### Gotcha
- **大盤資料是 deterministic 算的，不是 LLM 上網查**：保證 pipeline 重跑結果一致；LLM 只負責把數字寫成白話 reason
- **個股 stock_table 必須抄 evidence 不可自編**：prompt 已硬寫；後端 `_coerce_margin_analysis` 用 evidence 兜底，LLM 失敗時前端仍能看到正確張數
- **`margin_change_shares` 是當日 - 昨日**：ETL 階段已用 `MarginPurchaseTodayBalance - MarginPurchaseYesterdayBalance` 算好；候選池只需從 margin_trade 一次撈
- **券資比保留 4 位小數但前端顯示 2 位**：DB 算 ratio 用 `round(x, 4)` 留精度，前端 `formatPct` toFixed(2) 給可讀性
- **margin_climate 在 explanation / watch_reason 兩段共用**：pipeline 一次算後塞 market_context，兩 stage batch 都從 market_context 拿；不重算
- **`_WATCH_REASON_HEADERS` 必須加新 section**：A4 prompt 切片時 watch_reason stage 只保留指定 section，漏加會導致新規則被裁掉、LLM 看不到
- **`market_margin` 用 `inspect()` 不會跑**：本實作純 SQL `SUM / GROUP BY`，沒用 ORM session inspector；測試環境用 in-memory SQLite + 隨機 seed 即可驗證
- **空資料 climate=unknown**：所有 metric None 時不該回 neutral（會誤導 LLM 寫「區間震盪」），明確標 unknown 讓 prompt 走 fallback 文案
- **`stock_count` 在 today 物件內**：給 LLM 判斷「資料完整度」用；若 stock_count < 1000 代表 ETL 還沒完整同步，建議在 prompt 內寫保守判讀

## M26 個股 expectation price 預測（2026-05-26）

### 範圍
- 對「已被魚尾系統抓到」的個股，輸出未來 1 個月內的「資金行情可期待價格區間」：保守價 + 夢想價 + 估值模式 + 追高風險 + 信心度
- **不**取代魚尾 WATCH/REMOVE，也**不**輸出 BUY/SELL；純粹給使用者「資金行情可期待的價位」參考
- prompt 對齊使用者沉澱的 buy-side 分析師方法論（5 種 valuation_mode：PE_VALUATION / THEME_RE_RATING / MOMENTUM_MARKUP / EXTREME_MOMENTUM_MARKUP / FAILED_FOLLOW_THROUGH）

### 資料模型
- 新 DB 表 `signal_expectation_prices`：
  - UNIQUE `(stock_id, first_detected_date)` — 一檔股票一個追蹤 cycle 一筆，重產覆寫；同檔股票若被砍掉後再次進入新 cycle，會以新的 first_detected_date 新增一列
  - 欄位：conservative_price / dream_price / valuation_mode / valuation_basis / current_price_position / chase_risk / confidence + scorecard JSON / classification JSON / valuation_detail JSON + reason_50_words / risk_note_30_words + raw_payload（LLM 原始 JSON）
  - 達標旗標：`hit_conservative_at` / `hit_dream_at`（首次觸及才寫，後續不覆寫）
  - 元資料：detected_day_high / detected_day_close / current_price / source (cron|manual) / status (ok|failed) / llm_model / llm_diagnostic
- lifespan `_ensure_m23_tables()` 自動 idempotent create_all（與 M23 其他表同批）

### 後端模組
- prompt: [backend/app/prompts/expectation_price.md](backend/app/prompts/expectation_price.md)（使用者沉澱的完整 buy-side 分析師 prompt）
- 服務模組: [backend/app/signals/expectation_price.py](backend/app/signals/expectation_price.py)
  - `build_expectation_context(db, stock_id, first_detected_date=None)` — 從 DB 組裝 prompt INPUT JSON（含 7 大區塊 + meta）
  - `generate_for_stock(db, stock_id, *, source)` — 呼叫 OpenAI + UPSERT；失敗時寫 status='failed' + error_message
  - `generate_for_new_signals(db, snapshot_date, source="cron")` — cron 入口：抓 `first_seen_date == snapshot_date` 的新進股，逐檔跑
  - `update_hit_targets(db, target_date)` — 每日對所有 active row 比對當日收盤是否觸發保守 / 夢想；首次達標才寫旗標
- cron 入口: [backend/run_signal_expectation_prices.py](backend/run_signal_expectation_prices.py)（exit 0/1/2/3 對齊 run_daily_signals.py）
- API endpoints（擴充 [backend/app/routers/signals.py](backend/app/routers/signals.py)）:
  - `GET /api/signals/expectation-prices?snapshot_date=YYYY-MM-DD`（公開）
  - `GET /api/signals/expectation-prices/quota`（需登入）
  - `GET /api/signals/expectation-prices/{stock_id}`（公開）
  - `POST /api/signals/expectation-prices/regenerate { stock_id }`（需登入 + BackgroundTasks）

### theme_score deterministic mapping
從現有 signal_watch_hits + SignalSnapshot.watchlist 推算 0-3：
- LEADER + theme_fit=HIGH → 3
- LEADER + theme_fit=MEDIUM, FOLLOWER + HIGH, 其他 HIGH → 2
- MEDIUM → 1, LOW/NONE → 0
若 watchlist payload 已有 theme_score 則優先用該值；deterministic mapping 是 fallback

### Rate limit（手動重產）
- 每帳號每日 30 次、全站每日 100 次
- 以 `SignalExpectationPrice.updated_at` 落在今天 UTC 00:00 後計次（含 ok / failed，避免使用者用 retry 繞過）
- 常數：`USER_DAILY_EXPECTATION_LIMIT = 30` / `GLOBAL_DAILY_EXPECTATION_LIMIT = 100`

### GitHub Action
- [`.github/workflows/signal_expectation_prices.yml`](.github/workflows/signal_expectation_prices.yml)：
  - `workflow_run` 接在 `daily_signals.yml` 完成之後（success 才跑），確保 signal_watch_hits 已寫入
  - workflow_dispatch 備援（手動補跑 / 指定 target_date）
  - timeout 60 min；exit 0/1/2 視為 pass（no_data / partial 是合理結果），exit 3 才 fail
  - 模型：env `OPENAI_EXPECTATION_PRICE_MODEL`（fallback `gpt-5.4-mini`）

### 前端
- [frontend/src/lib/api.ts](frontend/src/lib/api.ts) 加 8 個型別 + 4 個 fetch helper（fetchExpectationPrices / fetchExpectationPrice / fetchExpectationQuota / regenerateExpectationPrice）
- [frontend/src/components/DailySignalsPanel.tsx](frontend/src/components/DailySignalsPanel.tsx)：
  - `ExpectationPriceChips`：SignalCard 底部 2 個 chip（保 / 夢），達標時自動染綠 ✓ / 染金 🎯
  - `ExpectationPricePanel`：插入 SignalDetailDialog 5 panel grid 之後、margin_analysis 之前。顯示完整資訊（保守 / 夢想兩塊大價格區塊 + valuation_mode / current_price_position / chase_risk / confidence chip + 50字 reason + 30字 risk_note + 6 項 scorecard 明細）
  - 「重新預測」按鈕：登入即可觸發 POST → BackgroundTask → 24s 內輪詢拉新結果
  - 主 panel root 多一個 `fetchExpectationPrices(snapshot_date)` 一次抓批次，建 `expectationByStock: Map`

### Gotcha
- **`/expectation-prices/quota` 必須放在 `/{stock_id}` 之前**：FastAPI 路徑解析會被 `/{stock_id}` 吃掉，初版踩到坑後 reorder
- **detected_day_high / detected_day_close 不是「近 21 日」算出來**：是 `first_seen_date` 那天的 OHLC，要從 signal_watch_hits 取得 first_seen_date 後 join daily_price；context_builder 內顯式 inject 進 `price_data` dict（不能交給 `_compute_price_data`）
- **`generate_for_stock` 失敗 row 也算進當日 quota**：避免 OpenAI 暫時掛掉時使用者瘋狂 retry 拖垮系統；UI 對 status='failed' 顯示重試按鈕
- **hit_target 首次達標才寫**：避免後續 cron 用更晚日期覆寫；「最早觸及日」是更有用的資訊
- **prompt 內 `unknown` enum 必填**：DB 沒有 forward EPS、gross_margin_trend、earnings_momentum → 後端必須顯式填 None / "unknown"，不可省略欄位
- **rate limit 用 source='manual' 計次**：cron source='cron' 不算進手動配額（個人 + 全站額度都只看 manual）；但「全站每日 100 次」現在用 `updated_at` 過濾不分 source — 簡化為「全站總操作量」上限
- **stock 必須先進 signal_watch_hits**：未被 M23 抓過的 stock POST regenerate 會直接 404，避免使用者誤觸發任意股票
- **同 first_detected_date UPSERT，舊 hit_conservative_at / hit_dream_at 保留**：使用者重新預測新的價格區間時，已達標旗標不該被覆寫；`_upsert_expectation_row` 只在新增 row 時才寫 None，update 路徑不動旗標欄

### 測試
- [backend/tests/test_signal_expectation_price.py](backend/tests/test_signal_expectation_price.py) — 12 案例（context builder happy / unknown stock / no signal hit / generate_for_stock ok+UPSERT / generate_for_stock failed / hit_target first-touch / list endpoint / single endpoint / 404 / regenerate 404 / regenerate 202 / quota）
- 全 backend 93 個 M23 相關測試 pass，baseline 20 fail 保持（皆為 site-passwordless 改動後未同步的 disable-auth 相關 test，與本切片無關）

## M23 再偵測閘門：tracking_status + 3 條派發硬閘門（2026-05-26）

### 背景
- 觀察 6515 / 6805 績效檔：首次抓到後 max_positive 只有 +0.99%~+7.13%，但 max_negative 跌到 -14%~-16%
- Root cause：FOLLOWER 預分類只看 `0 < price_change_5d < leader_gain × 0.7` + `flow_3d > 0`，沒檢查 leader 已漲到第幾段 / 自己有沒有真的突破 / 漲幅是否還在主升段早期
- 既有 soft hints（HINT_WEAKENING / HINT_DISTRIBUTION）只是描述欄位，不是硬性閘門

### 範圍（Phase 1.1 ~ 1.3）
- **不**動 `archive.py`：這次只做「再偵測閘門」（防止重複推薦剛失敗的股票），不動 early-exit 結算邏輯
- **不**改 LLM 決策格式：仍維持 WATCH / REMOVE 二元；不加 grade / quality_tier
- 目的：把後段 FOLLOWER 在 candidate_pool 階段就攔下，不讓它進入 LLM 推薦清單

### 1.1 candidate_pool 注入 tracking_status
- 新 helper `_load_tracking_status(db, stock_ids, target_date)`：join `signal_watch_hits`
- 計算 7 個欄位（全部 flat 灌進 candidate dict）：
  - `is_tracked`（bool）
  - `first_seen_date`（date | None）= MIN(snapshot_date) per stock
  - `days_since_first_seen`（int）= `daily_price.trade_date` 介於 (first_seen, target_date] 的天數
  - `hit_count`（int）= COUNT DISTINCT snapshot_date
  - `max_positive_return_pct` / `max_negative_return_pct`：用 max / min 聚合（多筆 row 防部分 update 殘留舊值）
  - `failed_follow_through`（bool）：`days >= 3 AND max_pos < 3.0 AND max_neg < -6.0`
- 常數集中在 [candidate_pool.py](backend/app/signals/candidate_pool.py) 頂部：`TRACKING_FAILED_DAYS_THRESHOLD = 3` / `TRACKING_FAILED_MAX_POSITIVE_PCT = 3.0` / `TRACKING_FAILED_MAX_NEGATIVE_PCT = -6.0`
- 無歷史命中（首次出現）→ `_empty_tracking_status()` 灌全 None / False

### 1.2 filters.py 新增 3 條 hard exclusion
[filters.py](backend/app/signals/filters.py) `_is_hard_excluded` 在既有 4 條後追加：

5. **failed_follow_through**：直接讀 candidate `failed_follow_through == True` → 剔除
6. **price_extended_inst_selling**：`price_change_10d > 25.0` AND `total_institution_flow_1d < 0` → 派發前兆剔除
7. **inst_3d_pos_1d_neg_price_dropping**：`flow_3d > 0` AND `flow_1d < 0` AND `price_change_1d < -1.5` → 主力出貨確認剔除

### 1.3 prompt + evidence card
- [watch-list-stock.md](backend/app/prompts/watch-list-stock.md) INPUT 段加 `tracking_status` 物件描述 + 三條 backend deterministic exclusion 說明（告訴 LLM「你看到的池子已是相對乾淨的候選」）
- WATCH `capital_reason` 寫作規則加一條：「若 tracking_status.is_tracked=true 且 days_since_first_seen>=3，必須引用追蹤表現」
- [llm_caller.py](backend/app/signals/llm_caller.py) `_to_evidence_view` 加 `tracking_status` nested dict（不暴露 failed_follow_through 給 LLM，因為這類股票已被 hard filter 排除）
- 新 helper `_tracking_status_view(candidate)`：處理 `first_seen_date` 可能已是字串或 date object

### Gotcha
- **早退結算（機制 A）不在本範圍**：6515 / 6805 若已被 archive 早退（hits 表刪除），本實作 `_load_tracking_status` 拿不到資料，會回 `_empty_tracking_status`。這是刻意的：若未來要加「30 天內曾 early_exit_failed_follow_through 就不准重新進池」，需要另外 join `signal_watch_completed_archives`（機制 B 擴充）
- **failed_follow_through 不暴露給 LLM**：hard filter 已過濾，evidence card 留著反而誤導 LLM「為什麼這檔還在池子裡」；只暴露 raw 欄位（first_seen / days / hit_count / max_pos / max_neg）讓 LLM 自己理解
- **days_since_first_seen 計算用 daily_price.trade_date**：不用 calendar days（會誤吃到週末 / 春節）；用 distinct trade_date 反推交易日數
- **多筆 SignalWatchHit row 的 max_pos / max_neg 聚合用 max / min**：archive cron 每天會把同 cycle 內每筆 hit 的 max_* 都同步更新（per 2026-04-30 修正），但保守起見用 aggregate 避免某 row 因 partial update 殘留舊值
- **boundary 測試嚴格 > / <**：`price_change_10d > 25.0`（25.0 不剔除）/ `price_change_1d < -1.5`（-1.5 不剔除）；測試 `test_hard_exclusions_keeps_at_extended_boundary_25_pct` 與 `test_hard_exclusions_keeps_inst_divergence_when_price_drop_mild` 守邊界
- **既有 4 個 baseline test 仍 fail**：site-passwordless 改動後未同步的 `test_signals_router.py` 那 4 個 regenerate auth 測試與本輪無關（驗證 baseline 4 fail = 4 fail）

### 預期效果（待 prod 觀察）
- 6515 那類「max_pos +0.99% / max_neg -14.20%」5 天內就會 hit failed_follow_through，下次跑 pipeline 直接從候選池被剔除
- price_change_10d > 25% + 法人轉賣的派發前兆股提早被攔（不需要等到實際跌破才反應）
- 候選池更乾淨 → LLM 看到的 stock_pool 品質更高 → WATCH 清單命中率應提升
- Trade-off：可能會誤殺一些短期回檔但仍有題材的健康股；觀察 prod 後若太嚴，可放寬 `_HARD_PRICE_EXTENDED_10D_PCT` 從 25 → 30，或要求 `price_change_1d < -2.0`（更嚴的 1d 跌幅確認）

## M23 daily_signals workflow `date is not JSON serializable` 修復（2026-05-26 第二輪）

### 症狀
- commit `bca09f0` 部署後，2026-05-26 daily_signals cron 在 `llm_explain` stage 炸：
  ```
  TypeError: Object of type date is not JSON serializable
  File ".../app/signals/llm_caller.py", line 650, in _run_decision_chunk
      f"{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
  ```
- workflow exit code 3，pipeline 直接失敗無 snapshot 產出

### Root cause
- M23 Phase 1.1 `_load_tracking_status` 把 `first_seen_date` (date 物件) 注入 candidate dict
- `run_research_batch` line 255-265 用 `**stock` spread 把原始 candidate dict（含 date）併進 aligned 結果
- `_tracking_status_view` 雖然把 `first_seen_date` 轉成 ISO string，但只發生在 evidence_view (給 LLM 看的 user_msg)；下游 stage 拿到的 `aligned[]` 仍保留原 date 物件
- 後續 `_run_decision_chunk` / `_run_watch_reason_chunk` 對 chunk 跑 `json.dumps` 時無法序列化 date 物件

### 修法
- 在 [llm_caller.py](backend/app/signals/llm_caller.py) 新增 `_serialize_dates(value)` helper：遞迴把 dict / list 內所有 `hasattr(..., "isoformat")` 的物件轉成 ISO string
- `run_research_batch` line 255 `**stock` → `**_serialize_dates(stock)`
- `_research_fallback` line 1144 `**stock` → `**_serialize_dates(stock)`
- 兩處共用同一 helper，治本：未來 candidate_pool / 其他 deterministic 模組再注入任何 date / datetime 欄位都自動安全

### Regression test
- [test_signals_llm_caller.py](backend/tests/test_signals_llm_caller.py) 新增 2 案例：
  - `test_run_research_batch_serializes_date_fields_for_downstream_json_dumps`：candidate 含 `first_seen_date: date` → aligned 結果可被 `json.dumps` 序列化
  - `test_run_research_batch_fallback_serializes_date_fields`：LLM 失敗走 fallback path 也須 stringify
- 49 個 llm_caller 測試全 pass

### Gotcha
- **不能只在 `_tracking_status_view` 處理 date**：那個 helper 只組「給 LLM 看的 view」；下游 stage 拿到的 raw aligned dict 是另一條 path，要在 alignment 階段就 stringify
- **不要選「7 處 json.dumps 全加 default=str」方案**：使用者選擇治本路徑 — 在 aligned 組裝階段就 stringify，下游 stage 看到的就是乾淨 dict
- **`hasattr(value, "isoformat")` 同時 cover date / datetime / time**：不需要 isinstance 多個型別
- **遞迴 walk dict / list**：candidate 含 nested 結構（例如 `soft_hints` list、未來可能 nested config），單層淺 copy 不夠


## M23 候選池產業規則改版：金融順延 + 當日賣超剔除（2026-06-08）

### 改動範圍
- 只動 [backend/app/signals/candidate_pool.py](backend/app/signals/candidate_pool.py) 的 `ingest_data` + `compute_rankings`（產業排行 + 個股排序窗）；`build_candidate_pool`、集團股擴散、classification、enrich flag、metrics **不動**（評強度仍 3d/5d）

### 候選池三來源（聯集）
- **A 個股**：`compute_hot_money(days=RANKING_WINDOW_DAYS, limit=30)` → **2 日**法人買超前 30 大個股
- **B+C 產業（合併新規則）**：
  1. 全市場各產業以 **2 日（RANKING_WINDOW_DAYS）** 法人淨買超排序
  2. 由高往低取，**遇金融類產業跳過順延**（用 `exclusions.is_financial(industry_name)`），湊滿 `TOP_INDUSTRIES_LIMIT=10` 個非金融產業
  3. 另算**當日(1 日)** 各產業淨額，取最賣超的前 `TODAY_SELL_BLACKLIST_LIMIT=10` 個產業為黑名單
  4. 步驟 2 的 10 個產業中，落在當日賣超黑名單者**剔除（不回補，剩幾個算幾個）**
  5. 存活產業成分股全進池
- **集團股擴散（保留）**：top_stocks 前 `TOP_STOCKS_INNER=6` 的同集團成員照舊進池

### 排序窗 3→2 日（2026-06-08 同日第二次調整）
- **動機**：3 日反應慢，主線啟動第 1 天可能排不進前段；魚尾目標是抓主線早期/補漲，快一點更貼合
- **決策**：個股 + 產業排序窗從 3 日縮為 **2 日（`RANKING_WINDOW_DAYS=2`）**，搶反應速度；**只動「誰進池」**
- **不動**：classification（吃 `price_change_5d` / `industry_rank_5d` / `net_3d` / `consecutive_buy_days_3d` / `volume_5d_to_60d`）與 metrics 維持 3d/5d 評強度；**當日賣超煞車維持 1 日**
- **分工**：進池用 2 日放寬搶速度、評強度/分類用 3~5 日把關（嚴）；2 日易受單日假性買超干擾，靠下游分類 + 當日煞車補
- **實作**：`ingest_data` 新增 `trade_dates_2d = trade_dates_60d[-RANKING_WINDOW_DAYS:]`；`compute_rankings` 產業聚合改吃 `trade_dates_2d`、`today = trade_dates_2d[-1]`、個股 `compute_hot_money(days=RANKING_WINDOW_DAYS)`

### 常數變更
- `TOP_INDUSTRIES_LIMIT`：6 → **10**
- 新增 `TODAY_SELL_BLACKLIST_LIMIT = 10`
- 新增 `RANKING_WINDOW_DAYS = 2`（進池排序窗）

### Gotcha
- **「當日賣超」只算 net < 0 的產業**：黑名單 comprehension 加 `if net < 0` guard；若一個產業當日是買超（正值），永遠不該被當賣超剔除。否則市場當日淨賣超產業不足 10 個時，會誤把買超產業也塞進黑名單（現有測試只 seed 2 個正值產業就會全被殺光）
- **金融順延 vs 當日賣超剔除順序**：先三日排名 + 金融順延湊滿 10 個非金融，**再**用當日賣超黑名單剔除；金融會回補（順延），當日賣超不回補
- **產業 canonical name 落差仍在**：industry flow 經 `normalize_industry_name` canonicalize（「半導體業」→「半導體」），但 `stocks_master.industry_name` 是原始名；`build_candidate_pool` 的 industry membership 比對可能對不上 → 實務上靠個股來源（A）與集團擴散補進池，這是既有行為非本次改動引入
- **命名債（刻意接受）**：rankings dict key `top_stocks_3d` / `top_industries_3d` 與 candidate flag `in_top_*_3d` 沿用 `_3d` 歷史命名，但實際窗已是 `RANKING_WINDOW_DAYS=2`；語義以常數為準，未改名是為了不連動 classification / llm_caller evidence / 測試。改窗只需動 `RANKING_WINDOW_DAYS`
- 測試：[test_signals_candidate_pool.py](backend/tests/test_signals_candidate_pool.py) 新增 `test_compute_rankings_skips_financial_industries_and_backfills` + `test_compute_rankings_drops_industry_in_today_sell_blacklist` + `test_compute_rankings_industry_ranking_uses_two_day_window`；22 candidate_pool + 62 classification/filters/pipeline 全 pass

## L0 產業流向改成可展開樹 + 子產業量能 bar（2026-06-13）

### 範圍
- 純前端 + jest 設定；**後端零改動**（子產業沿用既有 `GET /api/industries/{name}/summary`）

### 改動
- **L0 [IndustryDashboard.tsx](frontend/src/components/IndustryDashboard.tsx) 樹狀化**：
  - 產業列點一下 = 展開/收合子產業（取代舊的「點列即跳頁」）；產業名稱旁加「進入 →」按鈕（`e.stopPropagation()`）才跳 L1
  - 展開時 **lazy-fetch** `fetchSubIndustrySummary`，以 `${date}::${industry}` 為 key 存 `subCache` Map；重複展開即時顯示。`subLoading` / `subError` 兩個獨立 state 控制載入中 / 失敗列（colSpan=7）
  - 換日期 → `useEffect([date])` 收合全部（cache 依日期區隔，重展開抓新日資料）
  - 子產業列點擊 → `onSelectSubIndustry(name, sub, date)` → 跳 `/industries/{name}?date=&sub=`（既有 filter 深連結，L1 `page.tsx` 讀 `sub` + `StockList` 吃 `defaultSubFilter`，零成本）
  - 子產業列複用既有 `AmountCell` / `BarAmountCell` / `StreakCell`，合計欄帶量能 bar（maxAbs 取該產業內子產業最大絕對值）
- **page.tsx** 接 `onSelectSubIndustry` callback
- **L1 [StockList.tsx](frontend/src/components/StockList.tsx) `SummaryTable`**：合計欄從純文字 `AmountCell` 換成 `BarAmountCell`（對齊 L0 樣式），maxAbs 取該表 rows 最大絕對值

### jest 設定修復（順手）
- [src/__tests__/setup.ts](frontend/src/__tests__/setup.ts) 補 `window.matchMedia` + `ResizeObserver` polyfill
- 根因：共用 `<Table>` 內含 `StickyHorizontalScroll`，effect 內呼叫 `matchMedia` / `new ResizeObserver`，jsdom 沒有 → **所有用 `<Table>` 的測試 mount 即 crash**（pre-existing，非本輪引入）
- 修完後 IndustryDashboard suite 16/16 全綠；全前端 suite 從 baseline 35 fail → 18 fail

### Gotcha
- **`toggleExpand` 用 render 當下的 `expanded.has(industry)` 快照判斷展開/收合**：`wasExpanded=true` → 收合不抓資料；`false` → 展開才 fetch。不可在 setExpanded 的 functional updater 後讀 state（stale）
- **`onSelectIndustry` 測試改點「進入」按鈕**：`IndustryDashboard.test.tsx` 兩個 onSelect 測試從點產業名稱改成 `within(row).getByRole("button", { name: /進入/ })`，因為點名稱現在是展開不是導航
- **StockList / StockChart / BacktestPanel suite 仍 fail 與本輪無關**：StockList 是 `useAuth must be used within <AuthProvider>`（M19 `WatchlistAddButton` 測試沒包 provider），StockChart 是 ECharts，BacktestPanel 是 jest type 設定；都是 pre-existing

## M23 提前結算 StaleDataError 修復（2026-06-15）

### 症狀
- GitHub Actions `Signal Archive Returns Update`（手動補跑 6/15）fail，exit code 2
- log：`sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'signal_watch_hits' expected to update 10 row(s); 9 were matched.`，發生在 `update_signal_watch_returns` 的 `db.commit()`（[archive.py](backend/app/signals/archive.py)）
- 6/12 排程能成功、6/15 失敗，因為**剛好有股票在 6/15 觸發提前結算**（-30% 或高點回落 30%）才會走到 delete 路徑

### 根因
- `update_signal_watch_returns` 對提前結算股先在 1170-1179 改寫 `SignalWatchHit` rows（變 session dirty，待 UPDATE），稍後又 `db.query(SignalWatchHit).filter(stock_id==X).delete(synchronize_session=False)`
- production session 是 `autoflush=False`（[database.py:84](backend/app/database.py)）→ delete 前那批 dirty UPDATE **不會先 flush**；`synchronize_session=False` 又**不會把被刪 row 移出 session**
- 最後 `db.commit()` flush 時對「已刪除的 row」發 UPDATE → StaleDataError，**整個 transaction rollback**：不只提前結算那檔，當天**所有追蹤股的報酬率更新全部沒寫進去**

### 修法（[archive.py](backend/app/signals/archive.py) early-exit delete）
- `.delete(synchronize_session=False)` → `.delete(synchronize_session="evaluate")`
- `stock_id == X` 是單純等值條件，`evaluate` 會在 Python 端比對並把對應 dirty 物件移出 session，丟棄那筆注定被刪 row 的無效 UPDATE
- 提前結算股的最終結果不變：永久紀錄（`signal_watch_completed_archives`）在 delete **之前**就由 `_build_early_exit_archive_item` 用 row 靜態欄位 + 即時查 DB 組好，**不依賴**被丟棄的 UPDATE 欄位；active hits 照樣刪除

### Gotcha
- **只改 early-exit 那一處 delete**：`persist_signal_watch_hits` 內另一處 `delete(synchronize_session=False)`（依 snapshot_date 刪）無 dirty-row 衝突，維持 False
- **既有測試抓不到**：`test_signal_archive_returns.py` 的 session 是 `sessionmaker(bind=engine)`（預設 `autoflush=True`），delete 前會自動 flush 掉 UPDATE，撞不到 bug。新增 regression test `test_update_signal_watch_returns_early_exit_commits_with_autoflush_false` 鏡像 production 的 `autoflush=False`，舊行為會重現同一個 StaleDataError
- **這類修正 deploy 後要手動補跑**：`gh workflow run "Signal Archive Returns Update" --ref main -f target_date=<YYYY-MM-DD>`，把當天卡住的 active rows 回補正確
- **觸發背景**：本次是順著「GitHub schedule 6/15 整批被丟掉 → 手動補跑」連帶發現的潛伏 bug；GitHub `schedule` 事件 best-effort，高負載時整點 cron（`0 10`/`0 11`/`0 12`）容易被延遲甚至整批丟棄，必要時用 workflow_dispatch 補
- **同日已把整點 cron 錯開（降低再被丟機率）**：`daily_etl_update` `0 10`→`17 10`（台北 18:17）、`daily_signals` `0 11`→`23 11`（台北 19:23）、`signal_archive_returns` `0 12`→`41 12`（台北 20:41）；維持 ETL→Signals→Archive 相依順序。`telegram_daily_report`（`30 13`）/ `margin_trade_backfill`（`30 14`）本就非整點不動

## M23 候選池當日成交量死線（2026-06-26）

針對魚尾候選池新增一條 hard exclusion：當日成交量未達股價級距對應最低張數即剔除（不進候選池），與既有「流動性 < 5000 萬 TWD」同層。

### 級距規則（股價以 `close_1d` 判斷；1 張 = 1000 股）
- 股價 `< 1000` 元 → 日量需 **> 1500 張**
- 股價 `1000 ~ 5000` 元（含 1000、不含 5000）→ 日量需 **> 800 張**
- 股價 `>= 5000` 元 → 日量需 **> 500 張**

### 實作
- [candidate_pool.py](backend/app/signals/candidate_pool.py)：`_compute_price_data` 新增 `out["volume_1d"] = last_row.volume`（當日成交量股數）；空模板補 `"volume_1d": None`
- [filters.py](backend/app/signals/filters.py)：`_is_hard_excluded` 第 4b 條呼叫新 helper `_below_volume_deadline`；常數 `_SHARES_PER_LOT=1000` + 三個 `_HARD_MIN_LOTS_*`
- 比對用 `lots = volume_1d / 1000`，`lots <= min_lots` → 剔除

### Gotcha
- **「突破」= 嚴格大於**：剛好等於門檻（例 1500 張）不算突破，仍剔除（`<=` 判斷）
- **price 或 volume 為 None → 不剔除**：沿用 turnover filter 慣例，避免資料缺漏日把整池清空
- **級距邊界**：股價剛好 1000 元落入中間級距（門檻 800）、剛好 5000 元落入高價級距（門檻 500）
- 測試：[test_signals_filters.py](backend/tests/test_signals_filters.py) 新增 9 案例（三級距 above/below + 1500 exact + 兩邊界 + price/volume None）；全 62 個 filters+candidate_pool 測試 pass

## 魚尾 / 30 日追蹤新增 prompt 版本欄（2026-06-26）

為了讓未來改 prompt 後能做績效歸因，魚尾清單與 30 日追蹤都加一欄「版本」，標記「這檔是哪一版 prompt 產生的」。目前全部是 `v1`。

### 版本來源（single source of truth）
- [llm_caller.py](backend/app/signals/llm_caller.py) 常數 `PROMPT_VERSION = "v1"`
- **改 prompt 方法論時**：同步 bump 此常數 + [watch-list-stock.md](backend/app/prompts/watch-list-stock.md) 檔頭 `<!-- PROMPT_VERSION -->` 註記（v1 → v2 …）
- `assemble_final_output` 把版本蓋進 payload 頂層 + 每筆 watchlist item

### 資料流
- `signal_snapshots.prompt_version`（_persist_snapshot 從 payload 帶入）
- `signal_watch_hits.prompt_version`（persist_signal_watch_hits 從 watchlist item 帶入；缺值 fallback v1）
- `signal_watch_completed_archives.prompt_version`（取該 cycle 最新一次命中 `latest_row.prompt_version`）
- 三張表都 `ADD COLUMN ... NOT NULL DEFAULT 'v1'`（[signal_watch_schema.py](backend/app/signal_watch_schema.py) idempotent migration，既有列自動 backfill v1）

### API / 前端
- `SnapshotResponse.prompt_version` + `SignalArchiveSummaryItemResponse.prompt_version` + `SignalArchiveCompletedItemResponse.prompt_version`（皆 default `"v1"`）
- 魚尾 SignalCard 右上多一顆灰色版本 chip（讀 `item.prompt_version`）
- 30 日追蹤 active + completed 兩張表各加「版本」欄（`VersionChip`，在「最新類型/類型」之後）；colSpan 同步 +1（active 11→12、completed 9→10）

### Gotcha
- **舊資料一律視為 v1**：DB server_default + 後端序列化 `or "v1"` + 前端 `version || "v1"` 三層保底；舊快照 watchlist JSON 沒這欄也會顯示 v1
- **completed archive 取「最新一次命中」的版本**：若一個 cycle 橫跨 v1→v2 改版日，會以最後命中那天的版本為準（與 `latest_signal_type` 同口徑）
- **bump 後不回溯**：改 v2 只影響之後新產生的 snapshot；已存的 v1 列不動（這正是版本欄的用途——區分新舊方法論的成效）
- 本輪一併提交既有未提交 WIP：`_run_decision_chunk` 取消「只保留 top-3」硬性 cap，改成逐檔獨立判斷（+ 對應測試 `test_run_explanation_batch_prompt_does_not_force_top_3_cap`）

## M27 魚尾 Market Regime Gate（2026-06-26）

### 背景
- 使用者分析 6/26 匯出的兩份魚尾 CSV：完成追蹤 cohort（4/28~5/14）30 日平均 +18.9% / 勝率 67%；6/5 後 active cohort 目前平均 +1.3% / 勝率 39% / 虧損率 52%。
- 根因：prompt 在**震盪盤**仍用「多頭追強」邏輯，把 Follower / Laggard / 單次命中 / 急拉突破股評太高。CSV 證據：6/5 後 hit_count>=3 勝率 77%、hit_count=1 僅 24%；LEADER 平均 -2.8%（追高被反殺）。
- 決策（使用者選）：做進**魚尾 M23**（不是 trade_quality M17）、regime 用 **deterministic 指數算**、降級規則以 **deterministic 為主**。

### 關鍵洞見：regime 不是看指數漲跌，是看「盤中波動 / 創高急殺」
- 6 月指數其實一路創高（46k→47k），純 MA-trend 分類器會把整段判 BULL_TREND → 對使用者的問題完全無效。
- 使用者的「震盪盤」= 盤中震盪大（6/8 振幅 4.9%）、創高後急殺（6/23 收距高 -2.3% 收黑）、強勢股輪動快。
- 解法：在趨勢判斷上**疊加波動度 overlay**——指數即使創高，只要近 5 日盤中振幅大 / 出現創高急殺反轉日 / 近 3 日有大跌，就視為 VOLATILE_RANGE。

### 實作（deterministic 為骨幹）
- 新模組 [market_regime.py](backend/app/signals/market_regime.py)：用 `daily_price` 的 `TAIEX` OHLC 歷史（297 天可用）算 MA10/20/60 + 報酬 + 盤中振幅 + 創高急殺反轉日；`classify_regime(metrics)` 純函式回 BULL_TREND / VOLATILE_RANGE / RISK_OFF。優先序：RISK_OFF（跌破 MA20 + 短均壓制/5 日大跌）> 高波動 VOLATILE（overlay）> BULL（多頭排列 + MA20 上揚 + 波動可控）> VOLATILE（fallback）。資料不足→保守 VOLATILE。
- 門檻常數：`_VOL_RANGE_HIGH_PCT=2.8`（近 5 日均振幅）、`_BIG_DOWN_1D_PCT=-2.5`（近 3 日單日跌幅）、創高急殺 = 收盤距高點 ≤ -2% 且收黑、近 5 日 ≥1 根即算高波動。2026-06 實測校準：5/14~6/5 BULL、6/8~6/12 + 6/23 + 6/25 VOLATILE。
- [filters.py](backend/app/signals/filters.py) `apply_regime_gate(candidates, regime)`：deterministic 降級/剔除 + 標 `regime_conviction`(high/medium/low)：
  - BULL_TREND：不額外剔除
  - VOLATILE_RANGE：剔除 distribution / 單次命中（hit_count<=1）的非 LEADER / 急拉突破（量>5日均量×2 且當日漲>5%）
  - RISK_OFF：只留 LEADER + hit_count>=3 + 近 5 日法人>0 + 非 distribution，其餘剔除
- [pipeline.py](backend/app/signals/pipeline.py)：candidate→classify→hard→soft→**regime_gate**→LLM；regime 掛進 `market_context["market_regime"]`；assemble 後用 `conviction_by_stock` deterministic 蓋回每筆 watchlist item 的 `conviction`/`regime`（不依賴 LLM）。

### Prompt（輔助層）+ 版本
- [watch-list-stock.md](backend/app/prompts/watch-list-stock.md)：STEP 8 改為 regime-aware（regime 為最高優先、覆寫 market_state）；input 標 `market_regime`/`regime_conviction` 為 backend authoritative 不可改寫/上調；output schema 加 `market_context.market_regime` + 每檔 `conviction`。
- `_to_evidence_view` 加 market_regime + regime_conviction 給 LLM。
- **PROMPT_VERSION v1 → v2**（[llm_caller.py](backend/app/signals/llm_caller.py)）：方法論改版，讓 30 日追蹤可比較 v1（無 gate）vs v2（有 gate）cohort。

### 前端
- [api.ts](frontend/src/lib/api.ts)：`SignalMarketRegime` / `SignalConviction` 型別；`SignalMarketContext.market_regime`、`SignalWatchlistItem.conviction`/`regime`。
- [DailySignalsPanel.tsx](frontend/src/components/DailySignalsPanel.tsx)：header `RegimeBadge`（大盤：大多頭/震盪盤/風險退潮，色碼綠/琥珀/紅）+ 卡片 `ConvictionChip`（信心高/中/低）。

### Gotcha
- **regime 是波動度 overlay，不是純指數漲跌**：純 MA 分類器在創高震盪盤會誤判 BULL；必須用盤中振幅 + 創高急殺反轉日才抓得到使用者要的「震盪」。
- **conviction 是 deterministic 蓋回，不信任 LLM**：pipeline 在 assemble_final_output 後用 candidate 的 `regime_conviction` 覆寫，prompt 只要求 LLM 語氣對齊。
- **RISK_OFF / VOLATILE 可能讓 watchlist 變很少甚至空**：這是刻意的（退潮盤不新買）；空 watchlist 不視為 no_data（候選池空才 raise），snapshot 正常 done。
- **OTC 指數沒進 daily_price**：regime 只用 TAIEX；OTC 僅 market_snapshot 漲跌幅。
- **門檻是用 2026-06 一段資料校準**：未來可再調 `_VOL_RANGE_HIGH_PCT` 等常數；集中在 market_regime.py 頂部。

### M27 v2 refinement（2026-06-26 第二輪 review 修正）

使用者 review v2 後挑出 6 個會影響結果的問題 + 2 個增強，本輪修掉（仍在 `feat/m27-regime-gate`，PROMPT_VERSION 維持 v2，因 M27 尚未產生 prod 資料）：

1. **market_regime 欄位定位**：大盤狀態是**全市場一個**，移到 `market_context.market_regime`（攤平成 string + `market_regime_label` + `market_regime_reason`，pipeline 設定）；`regime_conviction` 才是每檔一個。`_to_evidence_view` 移除 per-stock market_regime（避免 LLM 以為每檔不同）。前端 `SignalMarketContext.market_regime` 改 flat string、`RegimeBadge` 讀攤平欄位。
2. **時空隔離**：prompt 開頭 + STEP 0 加 date-bounded 規則（歷史日期不可用 date 之後新聞/股價/財報；無法確認發布時間視為不可用；外部查詢只能用於業務/產業鏈定位）。否則 v1/v2 回測會被後見之明污染。
3. **震盪盤 low 硬剔除**：`apply_regime_gate` 在 VOLATILE_RANGE 把 `conviction=low`（單次命中非 LEADER）直接剔除，**例外**：已追蹤且 max_pos>=3% 且 max_neg>-6% 留校；conviction 改資料導向（hit>=3→high、LEADER 或 hit==2→medium、其餘→low；RISK_OFF 存活者→high）。STEP 8 同步寫 A/B 組硬條件 + 強制 REMOVE 清單。
4. **LAGGARD regime 限制**：核心原則 #8「必須納入 LAGGARD」加註只在 BULL_TREND 成立；VOLATILE 不可因 Leader 已漲就強塞 LAGGARD（需另符合震盪盤硬條件）、RISK_OFF LAGGARD 原則 REMOVE。
5. **theme_score deterministic**：STEP 2 把「原則上降級或排除」改硬規則（score=0 一律 REMOVE；score=1 只有 BULL_TREND + LEADER + conviction!=low 可 WATCH，VOLATILE/RISK_OFF 一律 REMOVE）。
6. **deterministic chip/technical（部分，前向相容）**：STEP 6/7 加註「若 backend 提供 deterministic chip_trend/technical_status 必須直接採用」，但**實際 backend 計算延後**（需逐股技術型態偵測，較大；可接 M11 `backtest_patterns.py`）。目前仍由 LLM 從 price_change/volume fallback 判。
- **watch_intensity（增強）**：新 deterministic 欄位（aggressive/normal/cautious），`filters.regime_watch_intensity(regime, conviction)` 算、pipeline 蓋回 watchlist item；前端卡片 chip 顯示「積極/正常/保留」（舊快照無則 fallback 顯示信心度）。
- **removed 結構化（增強）**：STEP 9 removed 加 `remove_category`（theme_mismatch / weak_chip / bad_technical / low_conviction / regime_filter / overextended / data_insufficient），方便日後統計 v1/v2 排除原因差異。

#### Gotcha（本輪）
- **conviction 是資料導向不是 type 導向**：震盪盤 hit_count>=3 一律 high（CSV 證據 77% 勝率），不分 LEADER/FOLLOWER；單次命中 LEADER 給 medium（留校）、單次命中非 LEADER 給 low（剔除，除非追蹤中表現好）。
- **market_regime 攤平後 snapshot.market_context 結構改變**：`market_regime` 從 object 變 string + 另兩個欄位；前端與任何讀 market_context 的地方都要用 flat 欄位。
- **watch_intensity / conviction deterministic 蓋回**：LLM 輸出的同名欄位會被 pipeline 覆寫，prompt 只要求 LLM「原樣回填」對齊語氣。
- **#6 尚未真正 deterministic**：prompt 已前向相容，但 chip_trend/technical_status 目前仍是 LLM 判；要完全落地需另開一個「backend 技術/籌碼訊號計算」工作。

### 30 日追蹤 prompt 版本改成「cycle 版本集合」（2026-06-26）

- 需求：一檔在追蹤週期內跨版本被抓到（之前 v1、今天 v2）→ 追蹤摘要要顯示 `v1, v2`，不是只看最新一次。
- 改法：`archive.py::_distinct_versions(rows)` 把 cycle 內所有 hit 的 `prompt_version` 去重 + 排序 + 逗號相連（如 `"v1,v2"`）；`_build_archive_summary_item` / `_build_completed_archive_item` / `_build_early_exit_archive_item` 從 `latest_row.prompt_version` 改用 `_distinct_versions(rows)`。
- per-hit `signal_watch_hits.prompt_version` 與 per-snapshot `signal_snapshots.prompt_version` **不變**（仍各存單一版本）；只有追蹤聚合改集合語意。完成封存的 `signal_watch_completed_archives.prompt_version` 在封存當下就寫入聚合字串（hits 之後會被刪，無法回算）。
- 前端 archive `VersionChip` 把 `"v1,v2"` 拆成多顆小 chip 顯示；魚尾首頁卡片版本 chip 維持單一（today snapshot）。
- Gotcha：欄位仍 VARCHAR(16)，`"v1,v2,v3"` 等短字串夠用（30 交易日內不可能 bump 到溢位）；單一版本時 `_distinct_versions` 回 `"v1"`，既有測試不破。

## 魚尾依大盤 regime 選 prompt 版本 + UI 移除燈號說明（2026-07-02）

三個改動，直接進 main（含先前未提交的 v4 WIP 一起）。

### 1. 依 regime 路由 prompt 版本（[llm_caller.py](backend/app/signals/llm_caller.py)）
- `_resolve_prompt_version(market_regime)`：**BULL_TREND → v1（追強）、VOLATILE_RANGE / RISK_OFF / 未知 → v4（收斂）**
- 常數：`PROMPT_VERSION_BULL="v1"` / `PROMPT_VERSION_VOLATILE="v4"`；`PROMPT_VERSION` 保留＝預設收斂版 v4（向後相容、market stage 用）
- `_PROMPT_PATHS` 一版一檔：v1→`watch-list-stock-v1.md`、v4→`watch-list-stock.md`
- `_load_system_prompt(stage, version)`：cache key 改 `version:stage`；找不到 version fallback 到 v4
- research / decision / watch_reason 三 stage 都從 `market_context["market_regime"]` resolve 版本；`assemble_final_output` 的 `prompt_version` label 也跟著 regime 走 → 30 日追蹤能區分 v1/v4

### 2. v1 prompt 另存檔
- 從 commit `73b1588` 抽出 → [watch-list-stock-v1.md](backend/app/prompts/watch-list-stock-v1.md)（675 行；已含 5 段 bullet reason + margin_analysis + tracking_status，與 A4 stage 切片相容）
- 同時是「保存檔」＋「多頭盤實跑 prompt」

### 3. UI 移除大盤燈號說明
- [MarketContextStrip.tsx](frontend/src/components/MarketContextStrip.tsx) + [StockSignalSummaryPanel.tsx](frontend/src/components/StockSignalSummaryPanel.tsx)：刪「燈號說明：」圖例列 + 各自 local `MARKET_STATE_LEGEND` const；`market_state_reason` 與狀態 chip 保留

### 強制指定 prompt 版本（2026-07-13 新增）
- env `SIGNALS_FORCE_PROMPT_VERSION`（值 = v1 / v4）可覆寫 regime routing（[llm_caller.py](backend/app/signals/llm_caller.py) `_resolve_prompt_version`）；未知值忽略走原邏輯。同時影響 prompt 選擇與寫進 DB 的 `prompt_version` label（30 日追蹤歸因一致）
- workflow_dispatch 對應 input：`gh workflow run daily_signals.yml --ref main -f target_date=YYYY-MM-DD -f force_prompt_version=v1`
- 用途：人工重跑做 v1 / v4 版本對照實驗

### Gotcha
- v4＝先前 working tree 未提交 WIP（`entry_quality` / `sector_rotation_status` / `institution_flow_momentum` / `theme_maturity` 等 deterministic_signals），本次一起 commit，成為震盪/退潮盤收斂版
- `market_regime` 由 pipeline 用 TAIEX deterministic 算好塞 `market_context`，LLM stage 只讀不改；market stage（STEP 0）regime 未知 → 用預設 v4（regime-agnostic 無妨）
- decision stage user_msg 的 JSON 模板**共用**（含 v4 新欄位）；跑 v1 時 LLM 仍被要求輸出那幾欄，缺了走 `_default_signals()`——不影響。要 v1 完全乾淨需另把 user_msg 模板做成版本感知
- 重跑：`gh workflow run daily_signals.yml --ref main -f target_date=YYYY-MM-DD`（Actions runner checkout main 直接跑，不必等 Render deploy）
- 測試：新增 3 個 routing case（llm_caller 54 pass）；`test_signals_router.py` 4 個 site-passwordless 登入測試仍 fail 為既有 baseline，與本改動無關

## /signals 正式版／工程版 toggle + 追蹤紀錄與每日觀察合併成單一入口（2026-08-08 ~ 2026-08-10）

跨多輪完成，起因是 `/signals/*` 系列頁面同時服務「一般使用者看推薦」與「工程稽核」兩種需求，
擠在同一份 UI 上造成英文 enum、UUID、JSON dump、Funnel 等工程細節干擾一般使用者；後段發現
魚尾 30 日追蹤（archive，M23-era）跟每日觀察（P4，`SignalObservation`）是兩套獨立計算的追蹤
系統，並存讓使用者搞不清楚兩者關係，且 P4 有一批「假警戒」股票（`2618`/`6533` 等）因為
`baseline_quality=LEGACY_INCOMPLETE` 卡在無限警戒，永遠不會 STOP。

### 正式版／工程版 toggle
- [signalsViewMode.tsx](frontend/src/lib/signalsViewMode.tsx)：`useSyncExternalStore` + module-level
  pub-sub `Set<() => void>`（同 tab 內 reactivity；`localStorage` 原生 `storage` event 只有跨 tab
  才會觸發，不能只靠它）。`SignalsViewModeProvider`／`useSignalsViewMode()` 比照既有
  `WatchlistProvider` pattern
- [(product)/layout.tsx](frontend/src/app/signals/(product)/layout.tsx)：route group（資料夾名不影響
  URL）統一包 `<SignalsViewModeProvider><SignalProductNav />{children}</SignalsViewModeProvider>`；
  `phase2/page.tsx` 刻意排除在外（既有決策，不受影響）
- [SignalProductNav.tsx](frontend/src/components/SignalProductNav.tsx) 的 `ENGINEERING_ONLY_HREFS`：
  正式版 nav 不顯示 Debug／結果分析／觀察生命週期連結，但直接輸入網址仍可進入（只是不曝光入口，
  同 Debug 頁既有待遇）
- prompt 修正：[recommendation-reason-v7.md](backend/app/prompts/recommendation-reason-v7.md) 加語言規則，
  禁止 reason 文字出現英文欄位名／enum code（修「威強電推薦理由出現『後端為 ACTIVE_TREND』」類問題）

### 結果分析頁（outcomes）全中文化 + 逐項可點擊下鑽
- [outcomes/page.tsx](frontend/src/app/signals/(product)/outcomes/page.tsx)：每個區塊都加
  `SectionExplainer` 白話說明＋具體數字範例；日期區間明確顯示；長條圖／Backend Rank 表格改
  `onClick`／`onEvents` 可點擊下鑽到「逐筆明細」並自動 `scrollIntoView`
- [OutcomeCharts.tsx](frontend/src/components/OutcomeCharts.tsx)：`OutcomeDistributionChart` 加
  `onSelect` callback（echarts `onEvents={{ click }}`），X 軸標籤改中文
- [signalP6Presentation.ts](frontend/src/lib/signalP6Presentation.ts)：`OUTCOME_LABELS`／
  `REVIEW_CATEGORY_LABELS` 去英文化（如 `WINNER: "大漲達標"`）
- 使用者確認這頁對他來說是「工程稽核用」沒有日常價值 → 最終收進 `engineeringOnly`，內容維持單一
  版本不做 mode 判斷分支（比照 Debug 頁「內容不分版本，只是 nav 不連過去」）

### 正式推薦卡片改魚尾風格
- [recommendations/page.tsx](frontend/src/app/signals/(product)/recommendations/page.tsx)：拿掉
  「追蹤中：自 X 起持續觀察」這行（不直覺），改用 `fetchSignalArchive()` 建
  `Map<stock_id, archive item>`，卡片顯示「首次抓到 {date}（第 N 個交易日）」＋收盤價／當日漲跌幅
  ＋報酬率＋保守價／夢想價（archive／expectation_price 資料本來就對 P3 RECOMMEND 股票存在，
  同一次 pipeline 寫入，純前端接資料即可，後端零改動）
  - **關鍵事實**：P4（`SignalObservation`）完全沒有 `return_pct`／price 欄位，P5/P6 的
    `day10_return` 是滿 10 交易日後的一次性快照非即時報酬——這是為什麼要接 archive 資料而不是
    在 P4 裡重造一套
- 加排序選項（`rank`／`date_desc`／`return_desc`，仿魚尾）＋一般股 asset badge 只在
  `asset_type !== "COMMON_STOCK"` 才顯示（call site 條件渲染，`SignalAssetBadge.tsx` 本身沒改）
- 工程版卡片也補齊收盤價／報酬率／追蹤天數欄位

### 追蹤紀錄與每日觀察合併成單一入口（2026-08-10）
- **根因**：`bootstrap_legacy_observations()`（`observation_lifecycle.py`，P4 上線前從舊
  `signal_watch_hits` 回填）寫入的觀察 `baseline_quality="LEGACY_INCOMPLETE"` 且
  `selection_version` 永遠是 null（舊資料 `signal_metrics` 沒有這欄位）；
  `decide_observation_action()` 的「持續警戒→STOP」判斷有 `not baseline_incomplete` 前置條件，
  導致這批觀察連續警戒再多次也永遠不會自然 STOP（2618／6533 實測卡在連續 13 次警戒）
  - **關鍵區分**：`selection_version IS NULL` 精準區分「真正舊的回填觀察」與「今天才被 v7
    pipeline 推薦、只是敘事文字剛好缺一項」的正常觀察（後者的 `selection_version` 一定有值，
    來自 `_initial_snapshot_from_recommendation()` 讀今天的即時 payload）——用這個條件避免誤停
    活躍的 v7 推薦
- 拿掉 `run_daily_observation_reviews` 開頭呼叫 `bootstrap_legacy_observations(db)`（函式保留不刪，
  註解說明如何還原），停止繼續產生新的 LEGACY_INCOMPLETE 觀察
- 新增一次性腳本 [stop_legacy_incomplete_observations.py](backend/stop_legacy_incomplete_observations.py)：
  篩選 `status IN (OBSERVING, CAUTION) AND baseline_quality="LEGACY_INCOMPLETE" AND
  selection_version IS NULL`，轉 `STOPPED` + 補建 `SignalObservationArchive`
  （沿用既有 `_finalize_observation_archive`）；`--dry-run` 預設只印清單、`--execute` 才寫入。
  Dry-run 對 production DB 執行結果：**命中 68 檔**（含 2618／6533），已交給使用者確認，
  **`--execute` 尚未執行**
- **前端合併採「archive 當唯一入口」而非後端資料模型合併**：archive（30 交易日 retention，短暫
  重現可容忍）與 P4（5 交易日空窗即重置 episode）起始日／重置規則完全不同，後端真合併需要先
  決定誰的規則當標準，工程量大；改用 archive 頁當「追蹤紀錄」正式入口，P4 狀態以徽章形式嵌入
  archive 詳情 popup，兩套後端邏輯繼續各自獨立運作
- [`(product)/archive/page.tsx`](<frontend/src/app/signals/(product)/archive/page.tsx>)（原
  `archive/page.tsx` 搬進 route group，URL 不變，自動套上 toggle）：`StockDetailDialog` 額外
  `fetchSignalObservations({ limit: 2000 })` 建 `Map<stock_id, observation>`，在既有 chip 列旁加
  `<ObservationStatusBadge status={...} />`（重用既有元件）+「查看完整追蹤紀錄（推薦論點／每日
  檢查）→」連到 `/signals/observations`；**列表卡片（極簡卡片）刻意不動**，沿用 2026-07-13
  「只留代號+名稱/收盤/漲跌幅」的既有設計，P4 狀態只在詳情 popup 顯示
- Nav／總覽：`SignalProductNav.tsx` `LINKS` 新增「追蹤紀錄」（正式版可見）；
  `ENGINEERING_ONLY_HREFS` 新增 `/signals/observations`；`(product)/page.tsx` `NAV_CARDS`
  同步——「追蹤紀錄」卡片取代原本「觀察生命週期」在正式版的曝光位置，後者改標
  `engineeringOnly: true` 保留（不刪除）
- GitHub Actions **不動**：`daily_signals.yml` 排程本來就不會跑 `legacy_split`（只有手動
  `workflow_dispatch` 能選），使用者明確要求保留當 rollback 手段

### Gotcha
- **P3（`signal_snapshots`）沒有「離開推薦榜」規則**：每天從零重新評估全部候選，不是「連續
  N 天才移除」；2618/6533 停留在推薦榜單純是每天重算後仍合格，跟 P4 的觀察/警戒狀態無關
  （`sync_recommendations()` 只讀 P4 狀態決定要不要建立/延續 episode，從不反向 gate P3 的
  RECOMMEND 決策）
- **`STOP_CONFIRM_THRESHOLD=3` 不是「連續 3 次警戒觸發 STOP」**：那是相反方向——STOP 觸發*之後*
  再連續觀察 3 天確認不會恢復，才由 `_finalize_observation_archive()` 永久封存。真正的
  「持續警戒→STOP」判斷是 `prior_decision==CAUTION` 且非 recovery 且非 baseline_incomplete，且
  MOMENTUM_STRUCTURE／PARTICIPATION 兩個核心維度在前一次與這一次都失敗（`decide_observation_action()`
  893-913 行），本輪修正前這條路徑對 LEGACY_INCOMPLETE 觀察被結構性跳過
  - **本輪過程中我曾對使用者說錯這個機制**（誤以為是「連續 3 次警戒觸發 STOP」），是基於更早一輪
    Explore agent 過度簡化的摘要；重新讀 code 後已更正
- `outcome_metrics.py::classify_day10_return()` 的 WINNER 門檛是「第 10 個交易日收盤價那個時間點」
  的單一快照（`return_pct >= 10.0`），不是最高點也不是即時報酬——這是「為什麼我的股票已經漲超過
  20% 卻沒被算進 Winner」的答案，即時報酬率要看正式推薦卡片／archive 的 `return_pct`，兩者是不同
  指標，設計上刻意分開
  - **與本次合併行動安全性相關**：確認過 stopping 一筆 P4 觀察不會動到 P3 或 archive 的任何
    資料，三套系統的追蹤／持久化互相獨立，暫停行動的 blast radius 僅限於 P4 自己的
    `SignalObservation`／`SignalObservationArchive`
- **`git add path1 path2 badpath` 整批靜默失敗**：任一路徑不存在會讓整條指令連好的路徑都不 stage；
  本輪起改成逐路徑個別 `git add` + `git diff --cached --stat` 驗證再 commit
- **搬檔案進 route group 後必須跑 `npx jest`**：光 `tsc --noEmit`＋`eslint` 抓不到測試檔案的
  import path 沒跟著更新（本輪較早一輪就踩過，3 個測試檔案的 import 壞了兩輪才被發現）；固定
  流程改成 tsc → eslint（僅本輪改動檔案）→ 全套 `npx jest` 三段都跑，且跟已知 baseline（StockChart／
  BacktestPanel／StockList 三個 pre-existing 失敗）比對，確認沒有新增失敗才算過

## P3 global_selector 輸出 token 上限依候選數量動態放寬（2026-08-11）

### 症狀
`Daily Signals Generation` GitHub Action 連續兩次（2026-08-05／2026-08-10）以
`exit_code=4 partial_failure` 失敗；查 `signal_snapshots` 發現這兩天的快照確實存在
但 `watchlist=0`、`selection_complete=False`——**整天沒有任何推薦**，不是次要欄位缺漏。

### 根因
`global_selector.run_global_selection()` 把所有 eligible 候選塞進**一次性** LLM call
做全體比較，`_call_llm_json` 的 `max_output_tokens` 吃 `estimate_selection_capacity()`
算出的 `output_token_reserve`，這個值原本是**寫死的 16,000**，不隨候選數量調整。
Structured Outputs 的嚴格 schema 要求每一檔候選都要有非 nullable 的 `selection_reason`
＋約 13 個其他欄位（nullable 但仍佔 key），token 需求不會因為大多數候選最終判
NOT_SELECTED 而縮小。兩次失敗當天的候選數（135／116 檔 eligible，對照
`signal_snapshots.candidate_pool_size` 也同步暴增到 703／779，平常日子是 338～491）
遠超平常，16,000 token 在這個規模下持續被 `max_output_tokens` 截斷，3 次契約重試
（`GLOBAL_SELECTION_LLM_FAILED`）全部用盡後直接 raise，整份快照的 watchlist 變空。

### 修法（[global_selector.py](backend/app/signals/global_selector.py)）
`_OUTPUT_TOKEN_RESERVE`（固定 16,000）→ `_default_output_token_reserve(candidate_count)`
= `3,000 + candidate_count * 220`；`estimate_selection_capacity()` 把這個動態值當
`_positive_env_int()` 的 default 傳入，`SIGNALS_GLOBAL_SELECTOR_OUTPUT_TOKEN_RESERVE`
env var override 語意不變（設了就優先用 env 值）。既有的 `within_limit` 檢查
（`estimated_input_tokens + reserve <= limit`）維持不動——真的超大候選池會在呼叫 LLM
**之前**就丟出明確的 `GLOBAL_SELECTION_CONTEXT_EXCEEDED`，不會再靜默送出去被
`max_output_tokens` 截斷、燒 3 次重試才失敗。

### Gotcha
- 220 tokens/candidate 是保守估計：135/116 檔在 16,000/135≈118、16,000/116≈138
  tokens/candidate 這個水準已經不夠，抓 220 留足夠安全邊界（不是精算出來的，是「明顯
  高於已知的不夠用門檻」）
- `_positive_env_int(name, default)` 對 unset env var 的行為是回傳傳入的 `default`
  參數（`os.getenv(name, str(default))` 沒抓到才會用 default），所以把 fixed 值換成
  函式呼叫結果完全相容既有 env override 邏輯，不需要改 `_positive_env_int` 本身
- 新增 4 個 regression test（`test_global_selector.py`）：兩個直接鎖住 135／116 這兩個
  真實觸發過事故的候選數，斷言新算出的 reserve 大於舊的固定 16,000；一個驗證隨候選數
  線性成長；一個驗證 env override 仍然優先。全 backend suite 20 fail/5 error 維持既有
  baseline（site-passwordless auth + FinMind SDK 需要 live token 兩類），零新增失敗
- 沒有動 `_DEFAULT_CONTEXT_LIMIT_TOKENS=114_688`（該常數用途是「輸入+輸出 token 總和
  上限」，跟本次改的 output reserve 是不同層次的東西，超出時 `within_limit` 檢查依然
  是唯一的把關者）

## P4 觀察拿掉 5 天空窗判斷（2026-08-11）

### 症狀
2026-08-10 手動補跑當天推薦清單後（見上一輪 token 上限修復），使用者發現 2615 萬海
明明才被 P3 重新推薦（今天第一天），`/signals/recommendations` 卡片卻顯示「已停止
觀察」；同時 2049 上銀顯示「警戒中」讓使用者懷疑是不是把「每日推薦清單」跟「持續觀察/
警戒清單」搞混了。

### 查證結論
- **2049 不是 bug**：查 DB 發現它是 2026-08-07 就開始被 P4 追蹤（非今天第一天），已
  連續 3 天警戒——P3（每天重新選）跟 P4（持續追蹤同一檔表現）本來就是兩套獨立系統，
  疊在同一張卡片顯示是設計上刻意的，這張卡片正確無誤
- **2615 才是真的 bug**：它是上一輪 `stop_legacy_incomplete_observations.py --execute`
  停止的 68 檔之一（`stop_reason_code=LEGACY_BASELINE_RETIRED`）。查
  `sync_recommendations()`（`observation_lifecycle.py`）發現：對「最新一筆觀察是
  STOPPED」的股票，要不要重開觀察是看**魚尾（`signal_watch_hits`）有沒有連續 5 個
  未命中交易日**，不是看 P4 自己的時間軸。萬海因為一直被 P3 持續選中，魚尾追蹤從沒
  斷過 → 系統永遠判定「沒有真正空窗」→ 拒絕重開 → 卡片卡在顯示舊的 STOPPED 狀態，
  即使今天已經是全新的 P3 推薦

### 修法（[observation_lifecycle.py](backend/app/signals/observation_lifecycle.py)
`sync_recommendations()`）
拿掉整段「5 個未命中交易日空窗」判斷（連同只給這段用的 `latest_hit_rows`／
`trade_index`／`EPISODE_GAP_TRADE_DAYS` 一起刪除，皆為死碼）：只要該股票目前**沒有
進行中（OBSERVING/CAUTION）的觀察**，P3 今天推薦就立即開新觀察，不再檢查空窗天數。
**P3 的每日推薦判斷是唯一權威，P4 不該用自己的冷卻期二次否決**——這正是使用者提出的
設計方向（AskUserQuestion 確認：P4 已有紀錄就直接設回觀察中）。回傳 dict 拿掉不再
使用的 `restart_deferred` key（`grep` 確認 pipeline.py／測試都沒有消費這個 return
value 的任何 key，可以安全整段移除，不用留 always-empty 相容殼）

### Production 資料回補
對 production 直接重跑一次 `sync_recommendations(db, signal_date=2026-08-10,
watchlist=snap.watchlist)`（此函式本身冪等，`continued`/`created` 分流不會動到已在
觀察中的股票）——當天 12 檔推薦中有 3 檔（3231 緯創／2059 川湖／2615 萬海）撞上這個
問題，重跑後 3 檔都立刻拿到新的 `OBSERVING` 觀察（`started_signal_date=2026-08-10`），
舊的 STOPPED 紀錄保留不動（歷史紀錄，不覆寫）

### Gotcha
- **`EPISODE_GAP_TRADE_DAYS` 只是 `candidate_pool.EPISODE_NEW_GAP_TRADE_DAYS` 的
  local alias**：底層常數本身在 `candidate_pool.py` 還有另一個獨立用途（v2.2 spec
  §7.4 的 episode hit_count 計算，跟 P4 的重開觀察判斷完全無關），只刪 observation_
  lifecycle.py 這邊的 alias，**不要動** `candidate_pool.py` 的原始常數
- `test_stopped_stock_can_restart_after_existing_five_day_gap`（既有測試，驗證「有
  空窗時會重開」）在拿掉空窗判斷後**仍然通過**，因為新行為是舊行為的超集（有空窗一定
  重開，沒空窗現在也會重開）；新增
  `test_stopped_stock_restarts_immediately_without_waiting_for_gap` 專門鎖住「0 天
  空窗也立刻重開」這個新行為（既有測試沒有反向案例會被破壞）
- 全 backend suite：45 個 observation_lifecycle 測試 pass（新增 1 個），全 suite
  20 fail/5 error 維持既有 baseline，零新增失敗

## 首頁交易質量分析入口拔除（2026-08-11）

使用者反映首頁左側「交易質量分析」sidebar toggle 沒人用、一直佔畫面邊緣。查
[page.tsx](frontend/src/app/page.tsx) 發現 `showTradeQuality` 預設值其實已經是
`false`（`readStoredToggle(..., false)`），問題是**toggle 按鈕本身**（`HomeSidebar`）
一直固定顯示在畫面左側，不管有沒有展開都佔位。

**修法**：`<HomeSidebar />` 從 render 拔掉（連同 `<main>` 的 `ml-12` 讓位間距一併
移除），`showTradeQuality` state 改成唯讀（`const [showTradeQuality] = useState(...)`，
拿掉 `setShowTradeQuality` 與同步寫 localStorage 的 `useEffect`，兩者都已無呼叫點）；
`HomeSidebar` 函式本身**保留不刪**（沿用 StockChart／BrokerPanel 慣例：程式碼保留、
只拔入口），加 `eslint-disable-next-line @typescript-eslint/no-unused-vars` 壓掉
unused warning，要復活只要把 `<HomeSidebar />` 加回 render 即可

**M19 深連結不受影響**：`/watchlist` 卡片「交易分析 →」按鈕靠
`forceShowTradeQuality`（URL 帶 `?stock_id=&buy_date=`）強制顯示分析區塊，這條路徑
完全獨立於 sidebar toggle，拔掉 toggle 後這個入口照常運作

## 正式推薦卡片加領漲/跟漲/補漲 chip + P4 狀態改整卡上色（2026-08-11）

### 查證：領漲/跟漲/補漲概念還在嗎
使用者問「現在新的選股，還有分領漲/跟漲/補漲的概念嗎？」查證發現：**還在，只是
`/signals/recommendations` 這頁沒有顯示**。
- `global_selector.py`（P3 全體比較）本身完全不處理 LEADER/FOLLOWER/LAGGARD——這是
  一開始 grep 零命中造成的第一個誤判線索
- 真正的來源是更早一段：`pipeline.py` 的 production 分支對 Phase 2 存活者呼叫
  `pipeline_v2.role_to_prelim_type(c)`，把 `role`（SECTOR_LEADER/CO_LEADER/
  INDEPENDENT_LEADER/SECTOR_FOLLOWER/ROTATION_LAGGARD/…）或已追蹤股的
  `tracking_state`（ACTIVE_TREND/HEALTHY_PULLBACK/…）映射成簡化的 `prelim_type`
  （LEADER/FOLLOWER/LAGGARD），這個值全程原封不動流過 `global_selector.
  merge_selection_items()`（該函式的 LLM 輸出 schema 完全沒有 `type` 欄位，純粹
  `{**source, **selected}` 帶過），最後變成 watchlist item 的 `type` 欄位
- 首頁 `DailySignalsPanel.tsx` 的 `SignalEmotionCard`（`tone={decisionToTone(item.type)}`）
  本來就在用這個欄位做整卡上色（rose=領漲／amber=跟漲／sky=補漲，刻意不用綠因為台股
  綠色語意是跌），但 `/signals/recommendations` 頁面完全沒有讀取或顯示 `item.type`——
  這才是使用者「看不到領漲跟漲補漲」的真正原因，不是概念消失，是這頁的 UI 缺漏

### 改動（[recommendations/page.tsx](<frontend/src/app/signals/(product)/recommendations/page.tsx>)）
- 新增 `TypeChip`：小型 pill，色階與 label（領漲/跟漲/補漲）對齊首頁
  `SignalEmotionCard` 的三色（未匯出共用 module，本頁另建一份本地 3 行對照表，避免
  跨檔耦合換一個 3 色 map 的成本）
- 觀察中/警戒中/已停止觀察小徽章不夠明顯 → 新增 `observationCardTone()`：卡片
  `<article>` 的 `className` 依 `observation?.status` 動態決定（CAUTION=琥珀底、
  STOPPED=灰底、其餘維持原本中性色）；小徽章文字（`ObservationStatusBadge`）保留不
  拿掉，整卡上色給一眼掃過的訊號、文字徽章給精確狀態名稱，兩者互補不衝突
- **STOPPED 在這頁理論上會越來越少見**：上一輪已把 `sync_recommendations()` 改成
  「P3 今天推薦、且目前沒有進行中觀察，就立即開新的 OBSERVING」，所以正常情況下一檔
  出現在正式推薦頁的股票不該同時是 STOPPED；保留這個色階只是防禦性處理歷史快照或
  極端 race window

### Gotcha
- `TYPE_CHIP_CLASSES`／`TYPE_LABELS` 三色三字對照表刻意不從 `SignalEmotionCard.tsx`
  匯出共用（該檔目前只匯出 `emotionLabel` function 跟型別，color map 是模組內部
  常數）——3 行對照表複製一份比新增一個匯出介面成本低，這輪判斷不值得為此重構共用檔
- 已確認 `recommendations/page.tsx` 的 `snapshot.data.watchlist` 跟首頁吃的是**同一個
  `SignalWatchlistItem[]` 型別**（`SignalRecommendationResponse extends
  SignalSnapshotResponse`），`item.type` 欄位本來就在，純前端補顯示即可，後端零改動

## 正式推薦頁標題改名 + rank 編號改條件顯示 + 觀察/警戒定義（2026-08-11）

同一輪後續：使用者在 Vercel 正式站上看這頁時反映「標題叫『今日推薦』但裡面有幾天前
就在觀察的股票，這名字不對」；另外問「#1 #2 代號是什麼？感覺不是很需要」；並要求
補充觀察中／警戒的定義。

### 標題改名
h2「今日正式推薦（N）」→「**目前正式推薦（N）**」；正式版 subtitle 從「以下是今天
系統推薦的股票與推薦理由」改成「系統每天重新比較全部候選股票；同一檔可能連續多天
勝出、持續留在名單上，不是每天都會整批換新」——直接把 P3「每天從零重新評估，同一檔
可以連續多天勝出」的設計講清楚，而不是換個名字迴避問題

### #N 排名編號改條件顯示
查 `_normalize_recommendation_ranks()`（`global_selector.py`）確認 `recommendation_rank`
不是隨機編號——是 LLM 自報排序（若合法）搭配 `backend_priority_rank` 打散重複值算出來
的 1..N 連續序列，**是有意義的資訊，只是跟畫面顯示順序脫鉤時會造成誤導**：這頁本身有
排序選項（推薦排序／抓到日期／報酬率），切到後兩者時卡片視覺順序已經不是 rank 順序，
`#7` 出現在畫面第 3 張的位置會讓人以為編號亂掉。**修法不是刪除，是加條件**：
`{sortBy === "rank" && <span>#{...}</span>}`，只有選「推薦排序」這個 sort mode 時才顯示
編號，切到其他排序自動隱藏，避免顯示跟位置對不上的數字

### 觀察/警戒定義說明文字
在正式推薦清單標題下方加一段固定顯示的白話說明（[recommendations/page.tsx](<frontend/src/app/signals/(product)/recommendations/page.tsx>)）：
「觀察中＝動能結構、資金參與等關鍵條件目前仍然成立；警戒＝部分關鍵條件今天檢查後開始
不成立，但還沒到判定『論點失效』的程度，值得留意但不是賣出訊號」——文字直接對應
`decide_observation_action()`（`observation_lifecycle.py`）的實際判斷邏輯（caution_
dimensions 命中即 CAUTION，尚未觸發 STOP 條件），不是憑感覺寫的行銷文案

### Gotcha
- 測試 `SignalRecommendationsPage.test.tsx` 兩處字面比對「今日正式推薦（N）」同步改成
  「目前正式推薦（N）」；`#1`/`#2` rank badge 斷言不需要改，因為預設 `sortBy` 仍是
  `"rank"`，條件顯示邏輯下預設狀態行為不變
- 本輪再次踩到 Bash tool cwd 不會跨呼叫持續生效的環境問題（`cd backend && ... ; sleep
  6` 後同一個工具呼叫內的下一行仍在 backend/，但下一個獨立 tool call 又回到舊目錄）；
  每次要跑 frontend 指令前一律重新 `cd frontend && pwd` 確認，不要假設前一輪設的 cwd
  還在——這是這個環境的已知常態，不是一次性意外

## 追蹤中／正式推薦卡片改即時股價（2026-08-11）

### 需求
使用者要求 `/signals/archive`「追蹤中」與 `/signals/recommendations` 正式推薦兩頁的
股價在開盤期間即時更新，可接受延遲 3 分鐘；建議去 goodinfo／yahoo 用 XPath 爬。

### 查證：不需要新爬蟲，現成基礎設施已經在用
專案早就有 `GET /api/realtime/quotes`（[realtime.py](backend/app/routers/realtime.py)）
直接打 TWSE 官方盤中 API（`mis.twse.com.tw/stock/api/getStockInfo.jsp`），回傳乾淨
JSON（`price`/`change_pct`/`open`/`high`/`low`/`volume`/`trade_time`），**不是爬蟲**，
不需要 XPath；前端 `fetchRealtimeQuotes()`（[api.ts](frontend/src/lib/api.ts)）已內建
50 檔一批自動分批（`Promise.all` 平行）、`useRealtimeQuotes(stockIds, intervalMs)`
（[useRealtimeQuotes.ts](frontend/src/lib/useRealtimeQuotes.ts)）已內建「只在
09:00–13:30 週一~五才啟動 polling、市場休市時 silently ignore」的邏輯——這條路徑早就
在 L2 個股頁／首頁 `DailySignalsPanel` SignalCard 用著，只是這兩頁沒接上。**遇到「能不能
即時更新」這類需求，先查有沒有現成的資料源在用，不要預設要另外接外部網站爬蟲**。

### 改動
- 兩頁都呼叫 `useRealtimeQuotes(stockIds, 180_000)`（3 分鐘，對齊使用者可接受延遲；
  預設值 15 秒但這裡刻意調慢，避免對一次可能上百檔的「追蹤中」清單頻繁打 TWSE）
- 新增 3 個 page-local helper（`resolveLivePrice`/`resolveLiveChangePct`/
  `resolveLiveReturnPct`，兩頁各自複製一份，比照 3 色 type chip 那次的判斷：3 行邏輯
  複製一份比新增共用匯出成本低）：有即時報價優先用，`null`／收盤後 fallback 回 archive
  的 EOD `latest_close_price`/`daily_change_pct`
- **報酬率也做到即時**：`(quote.price - baseline_price) / baseline_price * 100`，
  `baseline_price` 是 `SignalArchiveSummaryItem` 本來就有的欄位（第二個交易日起才有值，
  沿用既有「第二天固定 0%」規則）；沒有 baseline 或沒有即時報價時 fallback 回後端算好
  的 `return_pct`
- **範圍嚴格限定**：archive 頁只套用在「追蹤中」卡片（極簡卡片 + 詳情 popup），移出
  紀錄區（`completed`）不動；recommendations 頁只套用在正式 RECOMMEND 卡片，未列入
  今日推薦／明確移除／技術失敗（工程版限定區塊）不動——完全對齊使用者「工程版都先不必
  更新」的要求
- 兩頁都加了固定顯示的說明文字（沿用既有「說明框」樣式），講清楚 3 分鐘更新頻率與資料
  來源

### Gotcha
- `RealtimeQuote.price` 收盤後／非交易時段是 `null`（TWSE 該欄位語意是「今日最新成交
  價」，非交易時沒有新成交），`resolveLivePrice` 的 `quote?.price ?? fallback` 會自動
  接住這個情況，不需要額外判斷「現在是不是開盤中」
- 本機 dev server 實測（非交易時段）：`/api/realtime/quotes?stock_ids=2330,2317` 正常
  回應，`price: null` 但 `prev_close`/`open`/`high`/`low`/`volume`/`trade_time` 都有
  值——證實 fallback 邏輯覆蓋這個情境沒有問題
- `useRealtimeQuotes` 的 hook call 位置：兩頁都確認過呼叫點在 component function 最
  上層、任何條件式 early return **之前**，符合 React hooks 規則（不能放在 if 區塊或
  return 之後）
- `SignalRecommendationsPage.test.tsx` 沒有額外 mock `fetchRealtimeQuotes` 也能過：
  jsdom 測試環境下 fetch 會失敗，`useRealtimeQuotes` 內部已用 try/catch 靜默吞掉（原本
  就是為了「市場休市或連線失敗不炸頁面」設計的），不影響既有測試斷言

### 追蹤中／正式推薦即時報價修正（2026-08-11 第二輪，同日）

上線後使用者回報「漲幅看起來像昨天的」，另外問可不可以改成每 1 分鐘更新一次。

- **漲跌幅卡在舊值的根因**：`fetchRealtimeQuotes()`（[api.ts](frontend/src/lib/api.ts)）
  對 `/api/realtime/quotes` 的輪詢請求原本沒有明確關閉 fetch 快取，同一組
  `stock_ids` 組成完全相同的 GET URL、每次輪詢都是同一條 request，瀏覽器或中間層有
  機會把它當成可快取回應重複使用，導致畫面停在某一次抓到的舊漲跌幅（但股價本身因為
  fallback 邏輯多半還是新的，只有 `change_pct` 卡住，所以症狀具體是「漲幅」不對，
  不是股價完全沒動）。修法：`apiFetch(url, { cache: "no-store" })` 明確關閉
  快取，確保每次輪詢真的打到最新資料
- **更新頻率**：兩頁 `REALTIME_INTERVAL_MS` 從 `180_000`（3 分鐘）改成 `60_000`
  （1 分鐘），使用者確認可接受；`fetchRealtimeQuotes` 本身已有 50 檔一批＋
  `Promise.all` 平行請求，改頻率不需要額外調整批次邏輯
