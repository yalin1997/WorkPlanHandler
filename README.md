# WorkPlanHandler

> 為 agent planner 設計的「長任務執行管理模組」(Execution Management Module for Long-Horizon Agent Plans)

## 專案目標

研發一個可支援 agent planner 制定並執行**長任務**的執行管理模組,確保 agent 能:

1. **照著計劃進行**(plan adherence)——不在長 context 中漂移、遺忘目標
2. **完成各階段性任務**(staged task completion)——把大任務拆成可驗收的子任務並逐步推進
3. **達成驗收目標**(acceptance verification)——每個階段都有明確的「完成定義 / Definition of Done」並自動檢核

## 設計需求

- **可插拔(pluggable)**:以 framework-agnostic 的核心抽象為主,可掛載到 LangGraph 等 Python 常用 agent 開發套件
- **狀態持久化與可恢復**:長任務可能跨越數小時/數天,需可 checkpoint、可中斷續跑
- **驗收驅動(acceptance-driven)**:以「驗收條件」作為流程推進的閘門(gate),而非單純跑完步驟

## 目前進度

**M1–M4 已完成**:核心純函式狀態機 + LangGraph adapter 持久化(kill 後同 thread_id 續跑)+ 分層驗收閘門(hard→soft→human)與完整 HITL(approved/rejected/edited)+ 真 LLM planner / judge(provider-agnostic 模型注入,Anthropic/OpenAI/Google 皆可)+ 外部計劃 ingest。M1–M3 全 mock 不需 key;M4 的 LLM 元件離線測試用 stub,真連線才需 key。

| 里程碑 | 內容 | 狀態 |
|--------|------|------|
| **M1** | models + events + engine + mock 元件 + T1–T8 測試 | ✅ 完成 |
| **M2** | SqliteSaver + LangGraph adapter(kill process 後同 thread_id 續跑) | ✅ 完成 |
| **M3** | 分層 verifier(hard→soft→human,任一 required 層失敗即短路)+ human gate(`interrupt()`)+ 完整 HITL 矩陣 | ✅ 完成 |
| **M4** | 真 LLM planner / judge(provider-agnostic 模型注入)+ ExternalPlanner ingest;LLM 元件以 stub 離線測試 | ✅ 完成 |
| M5 | 真 LLM executor + recitation 注入 + 審計輸出(JSON 事件流 + Markdown 摘要)+ E2E demo | 待動工 |

已實作的模組(`src/workplan/`):

```
workplan/
├── models.py        # Plan / Step / AcceptanceCriterion / PlanState / HumanGate(I2 可序列化)
├── events.py        # Event + 13 種 EventType(I4 審計)
├── errors.py        # PlanIntegrityError / IllegalTransitionError
├── protocols.py     # Planner / Executor / Verifier / PlanStore 可插拔協定
├── engine.py        # ★ 純函式狀態機(D2):initialize / on_executed / on_verified /
│                    #   on_human_resolved / on_replanned / insert_steps
├── executors/mock.py
├── verifiers/
│   ├── mock.py
│   ├── base.py         # M3 ★ LayeredVerifier(D10 分層閘門 hard→soft→human,短路省 token)
│   ├── programmatic.py # M3:ProgrammaticVerifier(hard,跑使用者 check 函式,fail-closed)
│   ├── human_gate.py   # M3:HumanGateVerifier(human,D8,交人裁決)
│   └── llm_judge.py    # M4 ★ LLMJudgeVerifier(soft,真 LLM 評分,fail-closed;白名單 import langchain)
├── planners/
│   ├── mock.py
│   ├── llm_planner.py  # M4 ★ LLMPlanner(make_plan/replan/decompose;白名單 import langchain)
│   └── external.py     # M4:ExternalPlanner(D1 外部計劃 ingest,零依賴)
└── adapters/langgraph.py  # M2:StateGraph + SqliteSaver + interrupt()(唯一依賴 langgraph 的檔案)
```

真 LLM executor + recitation 注入、審計輸出(M5)尚未實作,規格見 [`docs/phase2/`](docs/phase2/)。

> **D9/D4 依賴邊界**:`langgraph` 只准 `adapters/langgraph.py`;`langchain`/`anthropic` 只准 `planners/llm_planner.py` + `verifiers/llm_judge.py`。兩支 LLM 元件**刻意不被 `__init__` eager import**(否則會把 langchain 拖進零依賴核心),請以顯式路徑 `from workplan.planners.llm_planner import LLMPlanner` 取用。此邊界由 `tests/test_import_boundaries.py` 自動守門。

## 快速開始(現階段:mock demo)

M1 階段所有元件皆為 mock,**不需任何 API key** 即可體驗完整的
plan → execute → verify(gate)→ retry → done 迴圈。

### 安裝

```bash
# 建議用 uv(或自行 python -m venv)
uv venv .venv
uv pip install -p .venv -e ".[dev]"                 # 純核心(M1 demo 即可跑)
uv pip install -p .venv -e ".[langgraph,dev]"       # + LangGraph adapter(M2 持久化/續跑)
uv pip install -p .venv -e ".[langgraph,llm,dev]"   # + 真 LLM planner/judge(M4;預設接 Claude)
```

### 跑 demo

```bash
.venv/bin/python examples/demo_mock.py     # M1:engine 迴圈 + 驗收閘門 + retry
.venv/bin/python examples/demo_resume.py   # M2:s4 crash → 重啟 → 同 thread_id 續跑
.venv/bin/python examples/demo_layered.py  # M3:hard 層短路 + retry + 高風險步驟 human gate → resume
.venv/bin/python examples/demo_llm_injection.py  # M4:provider-agnostic 模型注入(離線 stub,不燒 key)
```

[`examples/demo_mock.py`](examples/demo_mock.py) 的情境:三步計劃,第 2 步首次驗收失敗,
engine 回傳 `RETRY`(帶 feedback),重試後通過、全程留下事件審計。核心用法:

```python
from workplan import Action, StepOutput, engine
from workplan.models import AcceptanceCriterion, Plan, Step

# 1) 每個 Step 內建驗收條件(完成定義),規劃期就要給(I5)
plan = Plan(
    goal="產出市場分析報告",
    steps=[
        Step(id="s1", description="蒐集競品資料",
             acceptance=AcceptanceCriterion(description="至少三家、含來源")),
        # ...
    ],
)

# 2) adapter 迴圈:engine 只算決策(純函式),副作用都在呼叫方(D2 薄殼)
dec = engine.initialize(plan, thread_id="demo-1")
while dec.action in (Action.EXECUTE, Action.RETRY, Action.VERIFY):
    state = dec.state
    step = state.current_step
    if dec.action in (Action.EXECUTE, Action.RETRY):
        out = executor.execute(step, state)          # ← 你的工具 / agent runtime
        dec = engine.on_executed(state, out)
    else:  # VERIFY
        res = verifier.verify(step, StepOutput(content=step.output), state)
        dec = engine.on_verified(state, res)         # ← 驗收 = 推進閘門

# 失敗時 engine 會依序回 RETRY → REPLAN → ESCALATE(等人工),呼叫方
# 對應呼叫 on_replanned(planner.replan(...)) / on_human_resolved(gate)。
```

M2 之後,上面整段迴圈可交給 LangGraph adapter,並獲得 SQLite 持久化與中斷續跑:

```python
from workplan.adapters.langgraph import WorkPlanRunner

runner = WorkPlanRunner(executor=..., verifier=..., planner=...,
                        db_path="/tmp/run.sqlite")     # WSL2 注意:db 放原生路徑
res = runner.run(plan=plan, thread_id="job-42")        # 未給 thread_id 會自動生成

# process 被 kill 後,新 process 以同 db + 同 thread_id 接續(已 DONE 的步不重跑):
res = runner.resume("job-42")

# 驗收觸發 human gate 時 res.interrupted == True,人工裁決後恢復:
res = runner.resume("job-42", resolution="approved", note="人工放行")
```

### 接真 LLM(M4):provider-agnostic 模型注入

主要使用情境是**你自己注入模型實例接通 LLM**。`LLMPlanner` / `LLMJudgeVerifier`
只依賴 LangChain 標準介面 `model.with_structured_output(Schema).invoke(msgs)`,
因此 Anthropic / OpenAI / Google 的 ChatModel 都能直接傳進來——跨 provider 相容是免費的。

```python
from workplan.planners.llm_planner import LLMPlanner      # 顯式路徑(D9:不經 __init__)
from workplan.verifiers.llm_judge import LLMJudgeVerifier

# 路徑一(主):注入任意 LangChain BaseChatModel 實例
from langchain_anthropic import ChatAnthropic
planner = LLMPlanner(model=ChatAnthropic(model="claude-sonnet-4-6"))   # planner 用較強模型
judge   = LLMJudgeVerifier(model=ChatAnthropic(model="claude-haiku-4-5"))  # judge 用較輕模型

# 接 OpenAI:pip install langchain-openai 後同樣注入實例
# from langchain_openai import ChatOpenAI
# planner = LLMPlanner(model=ChatOpenAI(model="gpt-4.1"))

# 路徑二:給 model_name 字串走 init_chat_model(按 prefix 路由 provider;需設好 API key)
planner = LLMPlanner(model_name="anthropic:claude-sonnet-4-6")
judge   = LLMJudgeVerifier(model_name="openai:gpt-4.1-mini")  # 需先裝 langchain-openai
```

- **planner**:`make_plan(goal, context)` 產出每步自帶驗收條件(`kind="llm_judge"` soft rubric,I5)的 `Plan`;`replan` 只回未完成尾巴、新步用全新 id、附 `revision_note`(D6)。缺驗收條件會補預設並記 `planner.last_warnings`。
- **judge**:soft 層真 LLM 評分,`passed = score >= criterion.threshold`(`==` 視為 pass);呼叫失敗/逾時/非結構化一律 **fail-closed**(不放行)。直接替換 `LayeredVerifier` 的 soft 槽位即可,介面零變動。
- **ExternalPlanner**(D1):計劃由外部 planner 產生時,把現成 `Plan` 原樣接入執行迴圈(`make_plan` 原樣回傳);未注入 `replanner` 時 `replan` 觸發 `ReplanNotSupported`(交人 escalate 訊號)。
- **離線 demo**(不燒 key,展示注入介面):`.venv/bin/python examples/demo_llm_injection.py`。

demo 輸出(節錄):

```
  ↻ RETRY s2,feedback:缺少客群欄位

結束 action=done, status=done, cursor=3
s2 attempts=1, notes=['缺少客群欄位']

— 事件審計軌跡(state.history,I4)—
  plan_created    step=-
  step_started    step=s1
  ...
  verify_failed   step=s2
  step_retried    step=s2
  ...
  run_completed   step=-

JSON round-trip OK(I2)
```

### 跑測試

```bash
.venv/bin/pytest -q               # 全套:engine + adapter + verifier + planner/judge + D9 import 邊界
.venv/bin/pytest -q -m "not slow" # 日常(跳過 subprocess 級的真 kill 測試)
# M4 LLM 元件以 stub chat model 離線測試(不燒 key);未裝 llm extra 時自動 skip。
```

## 開發流程

1. **讀規格**:實作任何元件前,先讀 [`docs/phase2/00-overview-and-decisions.md`](docs/phase2/00-overview-and-decisions.md)(決策表 D1–D12 + 不變量 I1–I6,衝突以此為準),再讀對應元件規格(01–05)。
2. **鐵則**:core(engine/models/events/verifiers/planners/executors)**禁止** import langgraph / anthropic / adapters;唯一例外是 `adapters/langgraph.py`(D9)。核心 `dependencies = []` 零依賴。
3. **寫測試**:engine 是純函式,新路徑一律先在 `tests/test_engine.py` 用 mock 元件覆蓋。
4. **commit 前自動掃描**:已設定 pre-commit hooks(ruff lint/format + 基礎檢查),`git commit` 時自動觸發;新環境 clone 後執行一次:

   ```bash
   .venv/bin/pre-commit install        # 註冊 git hook
   .venv/bin/pre-commit run --all-files  # 手動全量掃描
   ```

5. **Git**:開發分支 `claude/agent-task-execution-survey-ass5p3`,PR 對 `main`。文件與 commit message 用繁體中文。

## 開發流程 Log

| 日期 | Commit | 里程碑 | 內容 |
|------|--------|--------|------|
| 2026-06-12 | `5d2e559` | Phase 1 | 技術棧/論文 survey 報告(`docs/01-survey.md`)+ MVP 實作提案(`docs/02-mvp-proposal.md`)+ 核心抽象介面草圖 |
| 2026-06-12 | `3ccf997` | — | 加入 `.gitignore`,移除誤 commit 的 `__pycache__` |
| 2026-06-12 | `5fdd4d9` | Phase 2 規劃 | 逐元件實作規格書(`docs/phase2/00`–`05`):決策表 D1–D12、不變量 I1–I6、資料模型/engine/verifier/planner/adapter 規格 |
| 2026-06-12 | `7bb888d` | — | 加入 `CLAUDE.md`(專案導覽與架構鐵則) |
| 2026-06-12 | `8990e50` | **M1** | 核心實作:純函式狀態機 `engine.py`(6 個 reducer + RETRY→REPLAN→ESCALATE 路由)、完整資料模型、13 種事件、mock 三件套、`pyproject.toml`(核心零依賴)、T1–T8 測試全綠 |
| 2026-06-12 | `a7679cf` | — | 加入 pre-commit hooks(pre-commit-hooks v6 + ruff check/format),統一全 repo 程式碼格式 |
| 2026-06-12 | `76e3853` | — | README 補使用方式與開發 log;新增 `examples/demo_mock.py` |
| 2026-06-12 | `d55e1bc` | **M2** | LangGraph adapter + SQLite 持久化:`adapters/langgraph.py`(StateGraph 五節點 + SqliteSaver + interrupt)、`WorkPlanRunner` 門面、A1–A8 測試(含 in-process 與 subprocess 真 kill 續跑)、D9 import 邊界守門測試、`examples/demo_resume.py`。三個 sub-agent 平行開發(adapter 本體 / E2E 測試 / 邊界測試)後整合 |
| 2026-06-13 | `0f36827` | **M3** | 分層驗收閘門:`verifiers/base.py`(`LayeredVerifier`,hard→soft→human 排序、required 層失敗即短路、needs_human 立即交人、advisory 不擋推進、fail-closed)、`programmatic.py`(hard,check 以註冊名引用保持 JSON 可序列化)、`human_gate.py`(human,D8)。測試:V1–V11 verifier 單測 + A9–A12 adapter 整合(LayeredVerifier 經圖跑通、完整 HITL 矩陣 rejected/edited、高風險步驟才掛人);`examples/demo_layered.py` |
| 2026-06-13 | `dffe0ad` | **M4** | 真 LLM planner / judge(provider-agnostic 模型注入):`verifiers/llm_judge.py`(`LLMJudgeVerifier`,soft 層真 LLM 評分、threshold 重算、呼叫失敗/非結構化 fail-closed)、`planners/llm_planner.py`(`LLMPlanner`,make_plan 每步自帶 llm_judge soft 驗收 + I5 補預設、replan 只回尾巴用全新 id、可選 decompose)、`planners/external.py`(`ExternalPlanner`,D1 ingest、無 replanner 觸發 `ReplanNotSupported`)。兩支 LLM 元件只依賴 LangChain `with_structured_output` 標準介面,Anthropic/OpenAI/Google 皆可注入;預設 planner=sonnet、judge=haiku(不同模型減 self-preference)。D9 邊界測試精緻化為 langchain/anthropic 白名單(僅 llm 兩檔)。測試:judge/planner/external 三檔以 stub chat model 離線確定性驗證(不燒 key);`examples/demo_llm_injection.py` |

> 後續里程碑進度(M5)依此表持續追記。

## 文件導覽

| 文件 | 說明 |
|------|------|
| [`docs/01-survey.md`](docs/01-survey.md) | 技術棧與論文理論 Survey 報告 |
| [`docs/02-mvp-proposal.md`](docs/02-mvp-proposal.md) | MVP 實作提案(架構、核心抽象、LangGraph 整合、里程碑) |
| [`docs/references.md`](docs/references.md) | 參考文獻與資料來源 |
| [`docs/phase2/`](docs/phase2/) | **逐元件實作規格(Source of Truth)**,`00` 是決策表、`README.md` 是索引 |

## 一句話結論

> 業界主流(LangGraph / Manus / Devin)與學術前緣(Plan-and-Act、LLM-Modulo、Reflexion/ADaPT)的共識正在收斂成同一個骨架:
> **結構化計劃 (structured plan) + 持久化狀態 (durable state) + 驗收閘門 (verifier-as-gate) + 重規劃迴圈 (replan loop)**。
> 目前缺的是一個「**framework-agnostic、以驗收為核心、可插拔**」的整合模組——這正是 WorkPlanHandler 的定位。
