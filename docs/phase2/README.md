# Phase 2 規格書與實作計劃

本目錄是 Phase 2(MVP 實作)的**事前計劃**:依與需求方三階段問答鎖定的決策,逐元件輸出規格與實作計劃。**尚未動工寫 code**(D12)。

## 閱讀順序

1. [`00-overview-and-decisions.md`](00-overview-and-decisions.md) — **先讀**。決策記錄(D1–D12 + 預設 P1–P3)、元件地圖、套件結構、里程碑、E2E demo 驗收、跨元件不變量。
2. [`01-data-model-and-events.md`](01-data-model-and-events.md) — 資料模型(Plan/Step/AcceptanceCriterion/PlanState)+ 事件/審計 schema。
3. [`02-engine.md`](02-engine.md) — 純函式狀態機 engine(路由心臟、replan、insert、escalate)。
4. [`03-verifiers.md`](03-verifiers.md) — 分層驗收(hard→soft→human)、LLM-as-Judge、fail-closed。
5. [`04-planner-and-executor.md`](04-planner-and-executor.md) — 可插拔 planner(內建/外部 ingest)、executor、recitation。
6. [`05-langgraph-adapter-and-persistence.md`](05-langgraph-adapter-and-persistence.md) — LangGraph 圖、SqliteSaver 續跑、interrupt HITL、對外 API。

## 一頁速覽:鎖定的決策

| 主題 | 決策 |
|------|------|
| 模組邊界 | Planner 可插拔(內建 LLM planner / 外部傳入 Plan 皆可) |
| 架構 | 薄殼:framework-agnostic 純函式 engine + LangGraph adapter |
| 首發 | 純 mock 任務(先打穩狀態機與續跑) |
| LLM | 模型無關,`init_chat_model` 預設 Claude |
| 拓撲 | 線性 + 動態插步(完整 DAG 留 Phase 3) |
| replan | 保留 DONE + 重生尾巴 + 可改驗收,version++ |
| 持久化 | SqliteSaver(檔案級,真能 kill 後續跑) |
| escalate | 標記 blocked + `interrupt()` 暫停等人 |
| 打包 | 核心零框架依賴;`pip install workplan[langgraph]` |
| 驗收組合 | 分層閘門 hard→soft→human,required 失敗短路 |
| 審計 | JSON 事件流 + Markdown 摘要 |

## 里程碑

| | 內容 | 用 mock? | DoD 重點 |
|---|------|---------|----------|
| M1 | models+events+engine | ✅ | 六路徑單測全綠 |
| M2 | SqliteSaver + langgraph adapter | ✅ | **kill 後同 thread_id 續跑** |
| M3 | 分層驗收 + human gate | ✅ | hard 短路、interrupt+resume |
| M4 | 真 LLM planner/judge | ❌ | 產含驗收條件之 plan;judge 可重現 |
| M5 | 審計 + E2E demo | ❌ | JSON+MD 產出;demo 全通過 |
