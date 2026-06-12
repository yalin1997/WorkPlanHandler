# Phase 2 總覽與決策記錄(Overview & Decision Log)

**狀態**:規格定稿中(尚未動工寫 code)
**前置**:[`../01-survey.md`](../01-survey.md)、[`../02-mvp-proposal.md`](../02-mvp-proposal.md)
**本批文件**:各元件實作規格與計劃(`docs/phase2/`)

---

## 1. 決策記錄(Decision Log)

以下為與需求方三階段問答後鎖定的決策。**規格的一切以此為準**;標注「已定預設」者為我方代為決定、可被推翻。

| # | 決策項 | 結論 | 影響的元件 |
|---|--------|------|-----------|
| D1 | **模組邊界** | Planner **可插拔**:預設內建 LLM planner,亦可由上游 agent 傳入現成 `Plan` | Planner、Engine API |
| D2 | **核心耦合** | **薄殼**:framework-agnostic 純函式 engine,LangGraph 僅在 adapter | Engine、Adapter |
| D3 | **首發場景** | **純 mock 任務**(不接真 LLM),先把狀態機 + 續跑打穩 | Mock 元件、里程碑排序 |
| D4 | **LLM 綁定** | **模型無關**,透過 LangChain `init_chat_model`,預設 Claude | Planner、LLMJudgeVerifier |
| D5 | **執行拓撲** | **線性 + 動態插步**(replan 可插入/修改後續步驟);完整 DAG 留 Phase 3 | Engine、Plan model |
| D6 | **replan 語意** | **保留 DONE + 重生尾巴 + 可改驗收條件**,`version++` 留審計 | Engine、Planner、Audit |
| D7 | **持久化後端** | **SqliteSaver**(檔案級,真能 kill 後續跑) | Persistence、Adapter |
| D8 | **escalate 終點** | **標記 `blocked` 並用 `interrupt()` 暫停等人**(最小 HITL) | Engine、Adapter、HumanGateVerifier |
| D9 | **打包** | **核心零框架依賴**;`pip install workplan[langgraph]` 才裝 adapter | 套件結構 |
| D10 | **Verifier 組合** | **分層閘門 hard→soft→human**,任一 required 層失敗即短路 | CompositeVerifier |
| D11 | **審計輸出** | **JSON 事件流 + Markdown 摘要** | Audit、Events |
| D12 | **工程基線** | **本階段只寫規格**,test/lint/CI 等動工再建 | — |
| P1 | *(已定預設)* `PlanState↔LangGraph` | 單一 channel + last-write-wins reducer(MVP 求簡;Phase 3 視需要拆 channel) | Adapter、Persistence |
| P2 | *(已定預設)* 並行模型 | engine 核心**同步**,介面設計成 async-ready(方法不含 blocking I/O 假設) | Engine |
| P3 | *(已定預設)* `thread_id` 來源 | 由呼叫端提供;未提供時模組產生 UUID 並回傳 | Adapter API |

---

## 2. 元件地圖與規格索引

```
                         ┌──────────────────────────────────────┐
                         │  framework-agnostic 核心(零框架依賴)  │
                         │                                       │
   外部 Plan ─┐          │   Planner ── Plan ──▶ Engine ◀── Executor
              ▼          │     │(可插拔)        │(純狀態機)   │(mock)
   [02-04 Planner] ──────┼─────┘                 │              │
                         │                        ▼              │
                         │                    Verifier ──[03]    │
                         │                  (分層: hard/soft/human)│
                         │                        │              │
                         │              Events/Audit ──[01]      │
                         └───────────────────────┬───────────────┘
                                                 │ via adapter
                                  ┌──────────────▼───────────────┐
                                  │  adapters/langgraph (extra)   │
                                  │  StateGraph + SqliteSaver +   │
                                  │  interrupt()  ──[05]          │
                                  └──────────────────────────────┘
```

| 規格文件 | 元件 | 核心介面 |
|----------|------|----------|
| [`01-data-model-and-events.md`](01-data-model-and-events.md) | 資料模型 + 事件/審計 schema | `Plan/Step/AcceptanceCriterion/PlanState/Event` |
| [`02-engine.md`](02-engine.md) | 純函式執行 engine(狀態機) | `step_once / route / apply` |
| [`03-verifiers.md`](03-verifiers.md) | Verifier 協定 + 4 實作 + 分層 | `Verifier.verify`、`LayeredVerifier` |
| [`04-planner-and-executor.md`](04-planner-and-executor.md) | Planner(可插拔/外部 ingest)+ Executor | `Planner.make_plan/replan`、`Executor.execute` |
| [`05-langgraph-adapter-and-persistence.md`](05-langgraph-adapter-and-persistence.md) | LangGraph adapter + 持久化 | `build_graph`、`SqlitePlanStore`、HITL |

---

## 3. 套件結構(D9:核心零框架依賴)

```
src/workplan/
├── __init__.py            # 只 re-export 核心,不 import 任何 adapter
├── models.py              # D1,D5,D6 資料模型(已有草圖,Phase 2 補 events)
├── events.py              # D11 事件型別 + 審計
├── protocols.py           # 四個 Protocol(已有草圖)
├── engine.py              # D2 純函式狀態機(零框架依賴)
├── errors.py              # 自訂例外
├── verifiers/             # D10
│   ├── base.py            # LayeredVerifier / CompositeVerifier
│   ├── programmatic.py
│   ├── llm_judge.py       # D4 用 init_chat_model
│   └── human_gate.py      # D8
├── planners/
│   ├── llm_planner.py     # D1,D4 預設內建
│   └── external.py        # D1 把外部 Plan 包成 Planner(make_plan 直接回傳)
├── executors/
│   └── mock.py            # D3 首發
├── audit/
│   └── render.py          # D11 JSON event log → Markdown
└── adapters/
    └── langgraph.py       # D2,D7,D8 唯一依賴 langgraph 的檔案
```

**`pyproject.toml` 依賴分層(D9)**:
```toml
[project]
dependencies = []                      # 核心零依賴(僅標準庫 + pydantic 可選)

[project.optional-dependencies]
langgraph = ["langgraph>=1.0", "langgraph-checkpoint-sqlite"]
llm       = ["langchain>=0.3", "langchain-anthropic"]   # init_chat_model + 預設 Claude
dev       = ["pytest", "ruff", "mypy"]
```
> 安裝範例:`pip install workplan`(純核心,可跑 mock)/ `pip install "workplan[langgraph,llm]"`(完整)。

**依賴方向鐵則**:`engine.py` / `models.py` / `verifiers/` / `planners/` **禁止** import `langgraph` 或 `adapters`。以一條 import-lint 規則(Phase 2 動工時加)強制。

---

## 4. 里程碑(承 `02-mvp-proposal.md §8`,依 D3 重排)

純 mock 優先 → M1–M3 完全不需真 LLM 即可驗收。

| 里程碑 | 內容 | 依賴決策 | 驗收(DoD) |
|--------|------|----------|-----------|
| **M1** 核心骨架 | models+events+protocols+engine | D2,D5,D6,P2 | engine 純函式單測:pass/fail/retry/replan/insert/escalate 六路徑全綠(用 mock 元件) |
| **M2** 持久化 + adapter | SqlitePlanStore + langgraph adapter | D7,P1,P3 | mock 任務跑通;**kill process 後同 thread_id 續跑**;狀態一致 |
| **M3** 分層驗收 | LayeredVerifier + programmatic + human_gate | D10,D8 | hard 失敗短路、human gate 觸發 `interrupt()` 並可 resume |
| **M4** 真 Planner/Judge | llm_planner + llm_judge(預設 Claude) | D1,D4 | 給 goal 能產含驗收條件之 Plan;judge 評分可重現;外部 Plan ingest 路徑通 |
| **M5** 審計 + E2E demo | audit render + 端到端 demo | D11 | JSON event log + Markdown 摘要產出;§5 demo 全數通過 |

> M1–M3 是「**證明骨架**」(mock,去風險);M4–M5 是「**證明價值**」(真 LLM + 驗收賣點)。

---

## 5. E2E Demo 驗收(MVP 整體 DoD)

沿用提案,但因 D3 先以 mock 跑 M1–M3:

**Mock 階段(M1–M3)**:用一個 5 步 mock 任務,其中第 3 步前兩次故意 fail。要展示:
1. 全程照計劃推進(cursor 線性前進)。
2. 第 3 步 retry 帶 feedback → 第三次 pass(驗 retry 迴圈)。
3. 在第 4 步前 `kill -9` → 同 thread_id 重啟 → 從第 4 步續跑(驗 SqliteSaver 續跑)。
4. 第 5 步設 human gate → 觸發 `interrupt()` → resume 後完成(驗 HITL)。
5. 輸出 JSON event log + Markdown 驗收摘要(audit trail)。

**真 LLM 階段(M4–M5)**:把 mock planner/executor 換成 llm_planner + 真 executor,跑「研究報告」任務,展示分層驗收(programmatic 字數/URL + llm_judge 切題)實際攔截並修正一次失敗。

**整體 DoD**:`≥5 步、含至少一次故意失敗的長任務,全程照計劃推進、逐階段自動驗收、失敗自我修正、中斷後續跑,並輸出可審計紀錄`。

---

## 6. 跨元件不變量(Invariants,所有元件都要守)

- **I1 單調進度**:`cursor` 只在某 step 進入 `DONE` 後才前進;已 `DONE` 步驟不被覆寫(replan 也保留)。
- **I2 可序列化**:`PlanState` 任一時刻皆可完整 JSON 序列化/還原(持久化前提)。
- **I3 每次轉移落盤**:engine 每產生一次狀態轉移,adapter 必須在推進前 `save()`(D7 續跑前提)。
- **I4 審計完整**:每個轉移都對應至少一個 `Event`;`Plan.version` 變更必留事件。
- **I5 驗收前置**:`AcceptanceCriterion` 必須在 step 進入 `IN_PROGRESS` 前就存在(survey §3.1 rubric 最佳實務)。
- **I6 純函式 engine**:engine 不做 I/O、不依賴框架、給定 `(state, input)` 輸出確定(P2;隨機性由注入的 planner/executor 承擔)。
