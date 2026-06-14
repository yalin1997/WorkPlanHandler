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

**M1–M5 已完成(MVP 達標)**:核心純函式狀態機 + LangGraph adapter 持久化(kill 後同 thread_id 續跑)+ 分層驗收閘門(hard→soft→human)與完整 HITL(approved/rejected/edited)+ 真 LLM planner / judge(provider-agnostic 模型注入,介面上 Anthropic/OpenAI/Google 皆可;以 Anthropic 為主路徑,跨 provider 真實穩定度待 M6-3 實測)+ 外部計劃 ingest + 審計輸出(JSON 事件流 + Markdown 摘要)+ CallableExecutor(通用橋接,recitation 注入)+ 端到端 demo。M1–M3 全 mock 不需 key;M4/M5 的 LLM 元件離線測試用 stub,真連線才需 key。

| 里程碑 | 內容 | 狀態 |
|--------|------|------|
| **M1** | models + events + engine + mock 元件 + T1–T8 測試 | ✅ 完成 |
| **M2** | SqliteSaver + LangGraph adapter(kill process 後同 thread_id 續跑) | ✅ 完成 |
| **M3** | 分層 verifier(hard→soft→human,任一 required 層失敗即短路)+ human gate(`interrupt()`)+ 完整 HITL 矩陣 | ✅ 完成 |
| **M4** | 真 LLM planner / judge(provider-agnostic 模型注入)+ ExternalPlanner ingest;LLM 元件以 stub 離線測試 | ✅ 完成 |
| **M5** | 審計輸出(`audit/`:JSON 事件流 + Markdown 摘要 + 寫檔)+ `CallableExecutor`(通用橋接,recitation 注入 + retry feedback)+ 整合 E2E demo + 真 LLM 階段研究報告 demo(分層驗收實際攔截並修正一次失敗) | ✅ 完成 |
| **M6** | 最小可整合 MVP(中期評核重定義,目標導向):CI(GitHub Actions)+ 乾淨環境 git install 測試 + 跨 provider 真 LLM 穩定度測試(燒 key 驗證 planner/judge 真效果)+ 釘 public API 與版號 `0.1.0` + executor 整合 quickstart(繁中)+ 誠實定位。MVP 收尾與 Phase 3 閘門,規格見 `docs/phase2/00 §4.1` | ✅ 完成 |

> **里程碑現況**:M1–M6 全部完成,**77 測試全綠**(日常 `-m "not slow"` 為 75)。M6-3 真 LLM 實測已以 Gemini-3.5-flash 跑完:judge 重現性 spread=0.0(5 次完全一致)、planner 產出 3 步驟且全有驗收條件、fail-closed 行為符合預期(`m6_probe_out/m6_probe_record.json`)。**M6 重定義為「最小可整合 MVP」**:目標是讓外部使用者把模組整合進自己的 agent、串接自己的真實 LLM。範圍決策——MVP **只做 LangGraph 外掛**(framework-agnostic 核心已就緒,非 LangGraph 電池待需求驅動)、以 **git install** 交付(不上 PyPI)、文件**維持繁中**。Phase 3(DAG 並行 / Temporal exactly-once)改為**需求驅動**,僅 `LangChainToolExecutor`(接真工具)優先保留,其餘等真實使用情境出現再排(見 `docs/phase2/00 §4.2`)。

已實作的模組(`src/workplan/`):

```
workplan/
├── models.py        # Plan / Step / AcceptanceCriterion / PlanState / HumanGate(I2 可序列化)
├── events.py        # Event + 13 種 EventType(I4 審計)
├── errors.py        # PlanIntegrityError / IllegalTransitionError
├── protocols.py     # Planner / Executor / Verifier / PlanStore 可插拔協定
├── engine.py        # ★ 純函式狀態機(D2):initialize / on_executed / on_verified /
│                    #   on_human_resolved / on_replanned / insert_steps
├── executors/
│   ├── mock.py
│   └── callable.py    # M5:CallableExecutor(通用橋接,recitation 注入 + retry feedback,零依賴)
├── audit/render.py    # M5 ★ 審計輸出(D11):to_event_log / to_markdown / write_audit(純函式零依賴)
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

> **M5 範圍說明**:`docs/phase2/00` 里程碑表將 M5 定為「審計 + E2E demo(D11)」,§5 另含「真 LLM 階段」(llm_planner + 真 executor 跑研究報告,分層驗收實際攔截)。本里程碑兩者皆交付:審計以零依賴純函式 `audit/` 實作;真 executor 以零依賴 `CallableExecutor`(規格 04 §B.3 的「通用橋接」)實作,LLM 接線留在 demo 的使用者函式(維持 D9 核心零依賴)。研究報告 demo 預設用離線 stub(不燒 key),換真連線只需注入 `ChatAnthropic` 實例。`LLMToolExecutor` / 子 agent executor 仍屬 Phase 3。

> **D9/D4 依賴邊界**:`langgraph` 只准 `adapters/langgraph.py`;`langchain`/`anthropic` 只准 `planners/llm_planner.py` + `verifiers/llm_judge.py`。兩支 LLM 元件**刻意不被 `__init__` eager import**(否則會把 langchain 拖進零依賴核心),請以顯式路徑 `from workplan.planners.llm_planner import LLMPlanner` 取用。此邊界由 `tests/test_import_boundaries.py` 自動守門。

## 快速開始

**整合者最短路徑**:`examples/quickstart_integration.py` 示範把你自己的 agent 單步執行
+ 你的 LLM 接進 `CallableExecutor` + `WorkPlanRunner`,獲得照計劃推進、逐階段驗收、
retry/續跑/HITL。離線可跑(用假 LLM),換真模型只需注入 `ChatAnthropic` 等實例。

> **MVP 定位(誠實)**:首版 MVP = **LangGraph 外掛**——持久化/HITL/續跑等電池透過
> `workplan[langgraph]` 的 adapter 供應;framework-agnostic 純核心(engine)雖零依賴可
> 獨立使用,但非 LangGraph 的一級整合路徑留待需求驅動。散布走 git install(未上 PyPI)。

### 安裝

```bash
# 對外整合者(git install):
pip install "git+https://github.com/yalin1997/WorkPlanHandler.git#egg=workplan[langgraph,llm]"

# 本地開發(建議 uv):
uv venv .venv
uv pip install -p .venv -e ".[dev]"                 # 純核心(mock demo 即可跑)
uv pip install -p .venv -e ".[langgraph,dev]"       # + LangGraph adapter(持久化/續跑)
uv pip install -p .venv -e ".[langgraph,llm,dev]"   # + 真 LLM planner/judge(預設接 Claude)
```

### 跑 demo

```bash
.venv/bin/python examples/quickstart_integration.py  # ★ 整合 quickstart:把你的 agent+LLM 接進來(離線可跑)
.venv/bin/python examples/demo_mock.py     # M1:engine 迴圈 + 驗收閘門 + retry
.venv/bin/python examples/demo_resume.py   # M2:s4 crash → 重啟 → 同 thread_id 續跑
.venv/bin/python examples/demo_layered.py  # M3:hard 層短路 + retry + 高風險步驟 human gate → resume
.venv/bin/python examples/demo_llm_injection.py  # M4:provider-agnostic 模型注入(離線 stub,不燒 key)
.venv/bin/python examples/demo_e2e.py       # M5:整合 E2E(線性推進→s3 retry→crash 續跑→s5 human gate→審計輸出)
.venv/bin/python examples/demo_research_llm.py  # M5:真 LLM 階段研究報告(分層驗收攔截並修正,離線 stub)
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
因此 Anthropic / OpenAI / Google 的 ChatModel 在介面上都能直接傳進來。

> **驗證程度(誠實標註)**:目前以 **Anthropic 為主路徑設計**(預設模型即 Claude)。
> 跨 provider 因走相同 LangChain 介面而**理論相容**,但 `with_structured_output` 在
> 各家行為不完全一致。**M6-3 實測結果(Gemini-3.5-flash)**:judge 重現性 spread=0.0、
> planner 結構化輸出穩定可用;OpenAI 尚未實測。詳見 `m6_probe_out/m6_probe_record.json`。

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
.venv/bin/pytest -q               # 全套(77):engine + adapter + verifier + planner/judge + D9 邊界 + public API + quickstart + probe
.venv/bin/pytest -q -m "not slow" # 日常(75;跳過 subprocess 級的真 kill 測試;CI 跑這個)
# M4 LLM 元件以 stub chat model 離線測試(不燒 key);未裝 llm extra 時自動 skip。

bash scripts/verify_clean_install.sh   # M6-2:空 venv git install + extras 邊界驗證
```

> CI(`.github/workflows/ci.yml`)在 push/PR 自動跑:`ruff check/format --check` + 純核心 job(extras 測試自動 skip)+ 完整 extras job,Python 3.11/3.12 矩陣。

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
| 2026-06-13 | _(本次)_ | **M5** | 審計輸出 + 真 LLM 階段(TDD 先寫測試再實作):`audit/render.py`(`to_event_log` 事件流深拷貝 I2、`to_markdown` 驗收摘要快照、`write_audit` 落檔 JSON envelope + MD,零依賴純函式)、`executors/callable.py`(`CallableExecutor` 通用橋接 + `ExecContext`:recitation 注入 + retry feedback,零依賴,LLM 接線留在使用者函式)。測試:`test_audit.py`(序列化往返 / 快照 / 落檔 / §4.1 payload schema)、`test_executors.py`(recitation spy / feedback / 型別守門)、`test_research_e2e.py`(離線 stub 研究報告經 adapter 跑通,hard 層攔截一次→修正通過);`audit/` 納入 D9 import 邊界守門。demo:`examples/demo_e2e.py`(§5 五項整合)、`examples/demo_research_llm.py`(真 LLM 階段,離線 stub) |

> MVP(M1–M5)已達標:`≥5 步、含故意失敗的長任務,全程照計劃推進、逐階段自動驗收、失敗自我修正、中斷後續跑,並輸出可審計紀錄`(doc 00 §5 整體 DoD)。

## 文件導覽

| 文件 | 說明 |
|------|------|
| **[`docs/guide.md`](docs/guide.md)** | **★ 使用手冊(新手必讀):專案目的 + 新手教學 + 技術說明** |
| [`docs/01-survey.md`](docs/01-survey.md) | 技術棧與論文理論 Survey 報告 |
| [`docs/02-mvp-proposal.md`](docs/02-mvp-proposal.md) | MVP 實作提案(架構、核心抽象、LangGraph 整合、里程碑) |
| [`docs/references.md`](docs/references.md) | 參考文獻與資料來源 |
| [`docs/phase2/`](docs/phase2/) | **逐元件實作規格(Source of Truth)**,`00` 是決策表、`README.md` 是索引 |

## 一句話結論

> 業界主流(LangGraph / Manus / Devin)與學術前緣(Plan-and-Act、LLM-Modulo、Reflexion/ADaPT)的共識正在收斂成同一個骨架:
> **結構化計劃 (structured plan) + 持久化狀態 (durable state) + 驗收閘門 (verifier-as-gate) + 重規劃迴圈 (replan loop)**。
> 目前缺的是一個「**framework-agnostic、以驗收為核心、可插拔**」的整合模組——這正是 WorkPlanHandler 的定位。
