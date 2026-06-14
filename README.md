# WorkPlanHandler

> 為 agent planner 設計的「長任務執行管理模組」(Execution Management Module for Long-Horizon Agent Plans)

[![CI](https://github.com/yalin1997/WorkPlanHandler/actions/workflows/ci.yml/badge.svg)](https://github.com/yalin1997/WorkPlanHandler/actions/workflows/ci.yml)
&nbsp;`v0.1.0` &nbsp;·&nbsp; M1–M6 完成 &nbsp;·&nbsp; 77 測試全綠

---

業界主流(LangGraph / Manus / Devin)與學術前緣(Plan-and-Act、LLM-Modulo、Reflexion/ADaPT)的共識正在收斂成同一個骨架:
**結構化計劃 + 持久化狀態 + 驗收閘門 + 重規劃迴圈**。
目前缺的是一個「**framework-agnostic、以驗收為核心、可插拔**」的整合模組——這正是 WorkPlanHandler 的定位。

## 核心特性

- **計劃遵循(plan adherence)** — 不在長 context 中漂移、遺忘目標;以 recitation 模式把計劃注入每步 prompt 尾端
- **階段性驗收(staged completion)** — 把大任務拆成可獨立驗收的子任務,逐步推進
- **驗收即閘門(acceptance-as-gate)** — 每個階段有明確「完成定義」並自動檢核;**驗收通過才能推進,不是事後評測**
- **Framework-agnostic 可插拔** — 純函式核心零依賴,透過 adapter 掛載到 LangGraph(首發)

## 快速開始

### 安裝

```bash
# 對外整合者(git install):
pip install "git+https://github.com/yalin1997/WorkPlanHandler.git#egg=workplan[langgraph,llm]"

# 本地開發(建議 uv):
uv venv .venv
uv pip install -p .venv -e ".[dev]"                 # 純核心(mock demo 即可跑)
uv pip install -p .venv -e ".[langgraph,dev]"       # + LangGraph adapter(持久化/續跑)
uv pip install -p .venv -e ".[langgraph,llm,dev]"   # + 真 LLM planner/judge
```

> **MVP 定位**:首版 MVP = LangGraph 外掛。持久化/HITL/續跑透過 `workplan[langgraph]` adapter 供應;
> 散布走 git install(未上 PyPI)。

### 最快跑起來

```bash
.venv/bin/python examples/quickstart_integration.py  # 整合 quickstart(離線可跑,換真模型只需注入)
.venv/bin/python examples/demo_mock.py               # engine 迴圈 + 驗收閘門 + retry
.venv/bin/python examples/demo_resume.py             # crash → 重啟 → 同 thread_id 續跑
.venv/bin/python examples/demo_layered.py            # hard 層短路 + human gate
.venv/bin/python examples/demo_e2e.py                # 完整 E2E(retry + crash 續跑 + human gate + 審計)
```

## 核心用法

每個 `Step` 規劃期就要附驗收條件,engine 是純函式——只算決策,不做 I/O:

```python
from workplan import Action, StepOutput, engine
from workplan.models import AcceptanceCriterion, Plan, Step

plan = Plan(
    goal="產出市場分析報告",
    steps=[
        Step(id="s1", description="蒐集競品資料",
             acceptance=AcceptanceCriterion(description="至少三家、含來源")),
        # ...
    ],
)

dec = engine.initialize(plan, thread_id="demo-1")
while dec.action in (Action.EXECUTE, Action.RETRY, Action.VERIFY):
    state = dec.state
    step = state.current_step
    if dec.action in (Action.EXECUTE, Action.RETRY):
        out = executor.execute(step, state)          # 你的工具 / agent runtime
        dec = engine.on_executed(state, out)
    else:  # VERIFY
        res = verifier.verify(step, StepOutput(content=step.output), state)
        dec = engine.on_verified(state, res)         # 驗收 = 推進閘門
# 失敗時 engine 依序回 RETRY → REPLAN → ESCALATE
```

M2 之後,整段迴圈可交給 LangGraph adapter,並獲得 SQLite 持久化與中斷續跑:

```python
from workplan.adapters.langgraph import WorkPlanRunner

runner = WorkPlanRunner(executor=..., verifier=..., planner=...,
                        db_path="/tmp/run.sqlite")
res = runner.run(plan=plan, thread_id="job-42")

# process 被 kill 後,同 db + 同 thread_id 接續(已 DONE 的步不重跑):
res = runner.resume("job-42")

# 驗收觸發 human gate 時 res.interrupted == True,裁決後恢復:
res = runner.resume("job-42", resolution="approved", note="人工放行")
```

## 接真 LLM

`LLMPlanner` / `LLMJudgeVerifier` 只依賴 LangChain 標準介面,Anthropic / OpenAI / Google 皆可注入:

```python
from workplan.planners.llm_planner import LLMPlanner      # 顯式路徑(不經 __init__)
from workplan.verifiers.llm_judge import LLMJudgeVerifier

from langchain_anthropic import ChatAnthropic
planner = LLMPlanner(model=ChatAnthropic(model="claude-sonnet-4-6"))
judge   = LLMJudgeVerifier(model=ChatAnthropic(model="claude-haiku-4-5"))

# 或給 model_name 字串,按 prefix 路由 provider:
planner = LLMPlanner(model_name="anthropic:claude-sonnet-4-6")
judge   = LLMJudgeVerifier(model_name="openai:gpt-4.1-mini")
```

> **驗證程度**:M6-3 以 Gemini-3.5-flash 實測——judge spread=0.0、planner 結構化輸出穩定、fail-closed 行為符合預期。OpenAI 尚未實測。詳見 `m6_probe_out/m6_probe_record.json`。

## 模組結構

```
src/workplan/
├── protocols.py     # ★ public API 0.1.0:Planner / Executor / Verifier / PlanStore
├── engine.py        # ★ 純函式狀態機(initialize / on_executed / on_verified / on_replanned …)
├── models.py        # Plan / Step / AcceptanceCriterion / PlanState / HumanGate
├── events.py        # Event + 13 種 EventType
├── executors/
│   ├── mock.py
│   └── callable.py  # CallableExecutor(recitation 注入 + retry feedback,零依賴)
├── verifiers/
│   ├── base.py      # LayeredVerifier(hard→soft→human,fail-closed)
│   ├── programmatic.py
│   ├── human_gate.py
│   └── llm_judge.py # LLMJudgeVerifier(soft 層,白名單 langchain)
├── planners/
│   ├── llm_planner.py  # LLMPlanner(白名單 langchain)
│   └── external.py     # ExternalPlanner(外部計劃 ingest)
├── audit/render.py  # to_event_log / to_markdown / write_audit(零依賴)
└── adapters/langgraph.py  # ★ 唯一依賴 langgraph 的檔案(D9)
```

依賴鐵則:core 零依賴;`langchain` 只准 `llm_planner.py` + `llm_judge.py`;`langgraph` 只准 `adapters/langgraph.py`。由 `tests/test_import_boundaries.py` 自動守門。

## 文件

| 文件 | 說明 |
|------|------|
| **[`docs/guide.md`](docs/guide.md)** | **★ 使用手冊(新手必讀):專案目的 + 新手教學 + 技術說明** |
| [`docs/01-survey.md`](docs/01-survey.md) | 技術棧與論文 survey |
| [`docs/02-mvp-proposal.md`](docs/02-mvp-proposal.md) | MVP 架構提案 |
| [`docs/phase2/`](docs/phase2/) | **逐元件實作規格(Source of Truth)**,`00` 是決策表 |
| [`CHANGELOG.md`](CHANGELOG.md) | 里程碑進度與開發流程 log |

## 開發

```bash
.venv/bin/pytest -q               # 全套(77 測試)
.venv/bin/pytest -q -m "not slow" # 日常(75;跳過 subprocess 級 kill 測試;CI 跑這個)
.venv/bin/ruff check . && .venv/bin/ruff format --check .

bash scripts/verify_clean_install.sh           # 乾淨環境 git install 驗證
GOOGLE_API_KEY=... python scripts/m6_real_llm_probe.py  # 真 LLM 穩定度實測
```

實作任何元件前,先讀 [`docs/phase2/00-overview-and-decisions.md`](docs/phase2/00-overview-and-decisions.md)(決策表 D1–D12 + 不變量 I1–I6)。
