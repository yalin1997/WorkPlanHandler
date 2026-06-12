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

**M1、M2 已完成**:核心純函式狀態機 + LangGraph adapter 持久化,kill process 後可同 thread_id 續跑(全 mock,不需 LLM key)。

| 里程碑 | 內容 | 狀態 |
|--------|------|------|
| **M1** | models + events + engine + mock 元件 + T1–T8 測試 | ✅ 完成 |
| **M2** | SqliteSaver + LangGraph adapter(kill process 後同 thread_id 續跑) | ✅ 完成 |
| M3 | 分層 verifier(hard→soft→human)+ human gate(`interrupt()`) | 待動工 |
| M4 | 真 LLM planner / LLM judge(Claude via LangChain) | 待動工 |
| M5 | 審計輸出(JSON 事件流 + Markdown 摘要)+ E2E demo | 待動工 |

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
├── verifiers/mock.py
├── planners/mock.py
└── adapters/langgraph.py  # M2:StateGraph + SqliteSaver + interrupt()(唯一依賴 langgraph 的檔案)
```

真 LLM planner/judge(M4)、分層 verifier(M3)、審計輸出(M5)尚未實作,規格見 [`docs/phase2/`](docs/phase2/)。

## 快速開始(現階段:mock demo)

M1 階段所有元件皆為 mock,**不需任何 API key** 即可體驗完整的
plan → execute → verify(gate)→ retry → done 迴圈。

### 安裝

```bash
# 建議用 uv(或自行 python -m venv)
uv venv .venv
uv pip install -p .venv -e ".[dev]"            # 純核心(M1 demo 即可跑)
uv pip install -p .venv -e ".[langgraph,dev]"  # + LangGraph adapter(M2 持久化/續跑)
```

### 跑 demo

```bash
.venv/bin/python examples/demo_mock.py     # M1:engine 迴圈 + 驗收閘門 + retry
.venv/bin/python examples/demo_resume.py   # M2:s4 crash → 重啟 → 同 thread_id 續跑
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
.venv/bin/pytest -q               # 全套:engine T1–T8 + adapter A1–A8 + D9 import 邊界
.venv/bin/pytest -q -m "not slow" # 日常(跳過 subprocess 級的真 kill 測試)
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
| 2026-06-12 | *(本次)* | **M2** | LangGraph adapter + SQLite 持久化:`adapters/langgraph.py`(StateGraph 五節點 + SqliteSaver + interrupt)、`WorkPlanRunner` 門面、A1–A8 測試(含 in-process 與 subprocess 真 kill 續跑)、D9 import 邊界守門測試、`examples/demo_resume.py`。三個 sub-agent 平行開發(adapter 本體 / E2E 測試 / 邊界測試)後整合 |

> 後續里程碑進度(M3–M5)依此表持續追記。

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
