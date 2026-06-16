# WorkPlanHandler 使用手冊

> 版本 0.1.0 | MVP = LangGraph 外掛 | 文件語言:繁體中文

---

## 目錄

1. [專案目的與定位](#1-專案目的與定位)
2. [快速上手(新手教學)](#2-快速上手新手教學)
3. [技術說明](#3-技術說明)

---

## 1. 專案目的與定位

### 1.1 解決什麼問題?

當你讓一個 LLM agent 執行「超過五步」的長任務時,會遇到三個典型問題:

**問題一:目標漂移(Goal Drift)**
Agent 在第八步時忘了第一步定的限制,開始做「差不多但不對」的事情。Context 越長,漂移越嚴重。

**問題二:無法驗收(No Acceptance Gate)**
Agent 跑完就算完成——但「跑完」不等於「做對了」。你不知道每一步的輸出是否真的達到你想要的標準。失敗要到最後才被發現,此時修正成本極高。

**問題三:中斷就重來(No Resumption)**
長任務跑到一半,process 被 kill 或 API 逾時,所有進度歸零,只能從頭再來。

### 1.2 WorkPlanHandler 的解法

WorkPlanHandler 是一個**長任務執行管理模組(Execution Management Module for Long-Horizon Agent Plans)**,以四個機制應對上述問題:

| 機制 | 對應問題 | 做法 |
|------|----------|------|
| **結構化計劃(Structured Plan)** | 目標漂移 | 每個 Step 明確描述「要做什麼」+「怎樣算做完」(AcceptanceCriterion);計劃摘要在每次執行時注入 prompt(recitation 模式) |
| **驗收閘門(Acceptance Gate)** | 無法驗收 | 每步驗收通過才推進游標;失敗就 retry(帶 feedback)→ replan → 等人工介入 |
| **持久化狀態(Durable State)** | 中斷就重來 | 每次狀態轉移落盤 SQLite;同 `thread_id` 重啟後從斷點續跑 |
| **重規劃迴圈(Replan Loop)** | 計劃失效 | 重試次數耗盡後,由 Planner 重生未完成的步驟(保留已完成進度),自動適應新情況 |

### 1.3 定位與邊界(誠實說明)

**MVP = LangGraph 外掛。**持久化、中斷續跑、human gate 等功能透過 `workplan[langgraph]` extra 供應。Framework-agnostic 的純核心(engine)雖然零依賴可獨立使用,但非 LangGraph 的整合路徑需要自己實作持久化迴圈,不在 MVP 範圍內。

**散布方式:git install。** 尚未發佈到 PyPI,以 `pip install "git+https://github.com/yalin1997/WorkPlanHandler.git#egg=workplan[...]"` 安裝。

**LLM 相容性:以 LangChain 介面為橋。** `LLMPlanner` 與 `LLMJudgeVerifier` 依賴 LangChain 的 `with_structured_output` 標準介面,Anthropic/OpenAI/Google 的 ChatModel 皆可注入。實測結果:Gemini-3.5-flash 驗證通過(judge spread=0.0、planner 穩定);OpenAI 尚未實測但介面相同。

---

## 2. 快速上手(新手教學)

### 2.1 安裝

**本地開發(建議使用 uv)**:

```bash
git clone https://github.com/yalin1997/WorkPlanHandler.git
cd WorkPlanHandler

uv venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1

# 選擇安裝深度:
uv pip install -e ".[dev]"                  # 純核心(mock demo 即可跑,不需 LLM key)
uv pip install -e ".[langgraph,dev]"        # + 持久化/續跑(LangGraph adapter)
uv pip install -e ".[langgraph,llm,dev]"    # + 真 LLM planner/judge(需 API key)
```

**外部整合者(git install)**:

```bash
pip install "git+https://github.com/yalin1997/WorkPlanHandler.git#egg=workplan[langgraph,llm]"
```

### 2.2 第一個 Demo(不需任何 key)

安裝好之後,先跑最基礎的 mock demo,確認一切正常:

```bash
python examples/demo_mock.py
```

預期輸出:

```
  ↻ RETRY s2,feedback:缺少客群欄位

結束 action=done, status=done, cursor=3
s2 attempts=1, notes=['缺少客群欄位']

— 事件審計軌跡(state.history,I4)—
  plan_created    step=-
  step_started    step=s1
  step_executed   step=s1
  verify_passed   step=s1
  step_done       step=s1
  step_started    step=s2
  step_executed   step=s2
  verify_failed   step=s2
  step_retried    step=s2
  step_executed   step=s2
  verify_passed   step=s2
  step_done       step=s2
  ...
  run_completed   step=-

JSON round-trip OK(I2)
```

這個 demo 展示:s2 第一次驗收失敗 → engine 回傳 `RETRY`(帶 feedback)→ 第二次通過。

### 2.3 把你自己的 Agent 接進來(整合 quickstart)

這是 MVP 的核心整合情境。**你需要寫的只有兩塊膠水**:

#### 膠水一:單步執行函式

把「你自己的 LLM 呼叫/工具執行」包進一個函式,交給 `CallableExecutor`:

```python
from workplan.executors.callable import CallableExecutor, ExecContext
from workplan import StepOutput, Step, PlanState

def my_agent_step(step: Step, state: PlanState, ctx: ExecContext) -> StepOutput:
    # ctx.feedback:上一次驗收失敗的可行動回饋(首次為空字串)
    prompt = f"請完成這個步驟:{step.description}"
    if ctx.feedback:
        prompt += f"\n\n上次驗收未過,請修正:{ctx.feedback}"

    # ctx.with_recitation:把計劃摘要注入 prompt 尾端(對抗目標漂移)
    prompt = ctx.with_recitation(prompt)

    # 這一行換成你自己的 LLM/工具:
    content = your_llm_call(prompt)
    return StepOutput(content=content)

executor = CallableExecutor(my_agent_step)
```

`CallableExecutor` 會自動幫你做兩件事:
- **Recitation 注入**:把整體計劃摘要附在每次 prompt 的尾端,讓 agent 不忘目標
- **Retry feedback 傳遞**:把上一次驗收失敗的原因傳給你的函式,讓 agent 能修正

#### 膠水二:驗收條件

為每個 Step 設定「完成定義(Definition of Done)」:

```python
from workplan import Plan, Step, AcceptanceCriterion
from workplan.verifiers import LayeredVerifier
from workplan.verifiers.programmatic import ProgrammaticVerifier

# 定義 hard check 函式(判定函式,不燒 LLM token)
def has_target_audience(output, state) -> tuple[bool, float, str]:
    if "客群" in str(output.content):
        return True, 1.0, ""
    return False, 0.0, "報告缺少『目標客群』欄位,請補上。"

# 建立分層驗收器
verifier = LayeredVerifier(layers=[
    ("hard", ProgrammaticVerifier(checks={"has_target_audience": has_target_audience}), True),
    # 想要 LLM 語意評分再加:
    # ("soft", LLMJudgeVerifier(model=...), True),
    # 想要人工審核再加:
    # ("human", HumanGateVerifier(), False),
])

# 計劃:每個 Step 的 acceptance 欄位指定驗收方式
plan = Plan(
    goal="產出市場分析報告",
    steps=[
        Step(
            id="s1",
            description="蒐集競品基礎資料",
            acceptance=AcceptanceCriterion(
                description="內容非空",
                kind="programmatic",
                layer="hard",
                spec={"check": "min_length"},
            ),
        ),
        Step(
            id="s2",
            description="撰寫市場分析(需含目標客群)",
            acceptance=AcceptanceCriterion(
                description="含『目標客群』欄位",
                kind="programmatic",
                layer="hard",
                spec={"check": "has_target_audience"},
            ),
        ),
    ],
)
```

#### 組裝並執行

```python
from workplan.adapters.langgraph import WorkPlanRunner
import uuid

with WorkPlanRunner(executor=executor, verifier=verifier, db_path="/tmp/demo.sqlite") as runner:
    res = runner.run(plan=plan, thread_id=uuid.uuid4().hex)

print(f"status={res.status}, cursor={res.state.cursor}")
```

完整範例見 [`examples/quickstart_integration.py`](../examples/quickstart_integration.py)。

```bash
python examples/quickstart_integration.py   # 離線可跑,不需 key
```

### 2.4 接真 LLM

換成你自己的真實 LLM,只需把 `LLMPlanner` 和 `LLMJudgeVerifier` 注入模型實例:

```bash
pip install "workplan[langgraph,llm]"
# Anthropic: pip install langchain-anthropic
# OpenAI:    pip install langchain-openai
# Google:    pip install langchain-google-genai
```

```python
from workplan.planners.llm_planner import LLMPlanner
from workplan.verifiers.llm_judge import LLMJudgeVerifier

# Anthropic(主路徑)
from langchain_anthropic import ChatAnthropic
planner = LLMPlanner(model=ChatAnthropic(model="claude-sonnet-4-6"))
judge   = LLMJudgeVerifier(model=ChatAnthropic(model="claude-haiku-4-5"))

# Google Gemini(M6-3 實測通過)
from langchain_google_genai import ChatGoogleGenerativeAI
planner = LLMPlanner(model=ChatGoogleGenerativeAI(model="gemini-2.0-flash-exp"))

# OpenAI(介面相同,尚未實測)
from langchain_openai import ChatOpenAI
planner = LLMPlanner(model=ChatOpenAI(model="gpt-4.1"))

# 或直接用 model_name 字串:
planner = LLMPlanner(model_name="anthropic:claude-sonnet-4-6")
judge   = LLMJudgeVerifier(model_name="google_genai:gemini-2.0-flash-exp")
```

`LLMPlanner.make_plan(goal, context)` 會自動生成包含驗收條件的計劃;`LLMJudgeVerifier` 則用 LLM 對輸出進行語意評分。失敗時一律 fail-closed(不確定就不放行)。

### 2.5 中斷後續跑

`WorkPlanRunner` 使用 SQLite 持久化,process 被 kill 後可從斷點恢復:

```python
thread_id = "my-job-001"

# 第一次執行(跑到一半被 kill)
with WorkPlanRunner(..., db_path="/tmp/run.sqlite") as runner:
    runner.run(plan=plan, thread_id=thread_id)

# 重啟後,以同一個 thread_id + db_path 續跑
with WorkPlanRunner(..., db_path="/tmp/run.sqlite") as runner:
    res = runner.resume(thread_id)  # 已 DONE 的步驟不會重跑
```

完整 demo(含真正 kill -9 測試):

```bash
python examples/demo_resume.py
```

> **WSL2 注意**:db_path 請指定原生 Linux 路徑(如 `/tmp/` 或 `/home/...`),避免跨 `/mnt/c/` 的 SQLite 鎖定問題。

### 2.6 Human Gate(人工介入)

當某步驟需要人工審核時,`HumanGateVerifier` 會讓 runner 暫停並等待人工決策:

```python
from workplan.verifiers.human_gate import HumanGateVerifier

verifier = LayeredVerifier(layers=[
    ("hard",  ProgrammaticVerifier(...), True),
    ("human", HumanGateVerifier(), False),  # advisory:不擋自動流程,但可設為 required
])
```

Runner 暫停時 `res.interrupted == True`,人工審核後呼叫 `resume()` 恢復:

```python
res = runner.run(plan=plan, thread_id="job-01")
if res.interrupted:
    # 此時可讓真人查看輸出,再決定
    res = runner.resume("job-01", resolution="approved", note="人工確認符合要求")
    # 或拒絕:resolution="rejected", note="請重做第三節"
    # 或修改後批准:resolution="edited", edited_content="修改後的內容..."
```

完整 demo:

```bash
python examples/demo_layered.py
```

### 2.7 其他 Demo 總覽

| Demo | 說明 | 指令 |
|------|------|------|
| `demo_mock.py` | 最基礎:驗收閘門 + retry + 事件審計 | `python examples/demo_mock.py` |
| `demo_resume.py` | kill 後同 thread_id 續跑 | `python examples/demo_resume.py` |
| `demo_layered.py` | hard 短路 + retry + human gate | `python examples/demo_layered.py` |
| `demo_llm_injection.py` | provider-agnostic 模型注入展示(離線 stub) | `python examples/demo_llm_injection.py` |
| `demo_e2e.py` | 整合 E2E:五步含故意失敗 + kill 續跑 + human gate + 審計輸出 | `python examples/demo_e2e.py` |
| `demo_research_llm.py` | 真 LLM 研究報告:分層驗收攔截失敗並修正(離線 stub) | `python examples/demo_research_llm.py` |
| `quickstart_integration.py` | ★ 整合入口:把你自己的 agent+LLM 接進來 | `python examples/quickstart_integration.py` |

---

## 3. 技術說明

### 3.1 核心架構

WorkPlanHandler 的設計核心是**薄殼策略(Thin Shell, D2)**:

```
你的 Planner ──Plan──▶ Engine(純函式狀態機) ◀──── 你的 Executor
   (可插拔)                │ 路由 Action                (副作用都在這)
                           ▼
                        Verifier(分層 hard→soft→human)
                           │
                   Events / Audit(JSON 事件流 + Markdown 摘要)
                           │  via adapter
               adapters/langgraph(StateGraph + SqliteSaver + interrupt)
```

**`engine.py` 是純函式狀態機**:不做 I/O、不呼叫 LLM、不依賴任何框架。它只接收 `(state, 外部結果)` 並計算出 `(新 state, Action, events)`。所有 LLM 呼叫、工具執行、持久化、`interrupt()` 全由 adapter 負責。

這個設計帶來兩個優點:
1. **可純單元測試**:engine 的所有路徑都可以用 mock 元件確定性驗證,不需要 LLM 或資料庫
2. **可換框架**:非 LangGraph 整合者只需實作自己的 adapter,不需改動任何核心邏輯

### 3.2 模組清單

```
src/workplan/
├── protocols.py           ← ★ 穩定公開契約(0.1.0):四個可插拔點
├── models.py              ← 資料模型:Plan / Step / AcceptanceCriterion / PlanState
├── events.py              ← 13 種 EventType + 事件記錄
├── engine.py              ← ★ 純函式狀態機(六個 reducer)
├── errors.py              ← 自訂例外
├── executors/
│   ├── mock.py            ← 測試用假執行器
│   └── callable.py        ← ★ CallableExecutor(整合者主要入口)
├── verifiers/
│   ├── mock.py            ← 測試用假驗收器
│   ├── base.py            ← ★ LayeredVerifier(hard→soft→human 串接)
│   ├── programmatic.py    ← ProgrammaticVerifier(hard 層,跑判定函式)
│   ├── llm_judge.py       ← LLMJudgeVerifier(soft 層,真 LLM 評分)
│   └── human_gate.py      ← HumanGateVerifier(human 層,等人裁決)
├── planners/
│   ├── mock.py            ← 測試用假規劃器
│   ├── llm_planner.py     ← LLMPlanner(真 LLM 生成含驗收條件的計劃)
│   └── external.py        ← ExternalPlanner(把外部計劃 ingest 進迴圈)
├── audit/
│   └── render.py          ← 審計輸出:JSON 事件流 + Markdown 摘要
└── adapters/
    └── langgraph.py       ← ★ WorkPlanRunner(唯一依賴 LangGraph 的檔案)
```

### 3.3 資料模型

#### Plan 與 Step

```python
@dataclass
class Plan:
    goal: str                  # 整體目標(人類可讀)
    steps: list[Step]          # 有序子任務列表
    version: int = 0           # replan 時遞增,留審計紀錄

@dataclass
class Step:
    id: str                    # 全 Plan 唯一(s1/s2/... 或 UUID)
    description: str           # 要做什麼
    acceptance: AcceptanceCriterion   # 怎樣算做完(I5:規劃期就要設定)
    status: StepStatus         # PENDING→IN_PROGRESS→VERIFYING→DONE/FAILED/BLOCKED
    attempts: int = 0          # 已嘗試次數(retry 計數)
    output: Any = None         # 最近一次執行輸出
    notes: list[str] = []      # 歷次 feedback 紀錄(Reflexion 式)
    max_attempts: int = 2      # retry 上限(耗盡則觸發 replan)
```

#### AcceptanceCriterion(驗收條件)

```python
@dataclass
class AcceptanceCriterion:
    description: str           # 人類可讀的完成定義
    kind: Literal["programmatic", "llm_judge", "human"] = "llm_judge"
    layer: Literal["hard", "soft", "human"] = "soft"
    spec: dict = {}            # programmatic: {"check": "函式名稱"}
                               # llm_judge: {"rubric": "評分說明"}
    threshold: float = 1.0    # llm_judge 通過分數(0~1)
    required: bool = True      # False = advisory,不阻擋推進
```

**最佳實務**:在計劃規劃期就設定好驗收條件(I5),不要執行後才補——否則驗收標準容易被既有輸出「合理化」而失去意義。

### 3.4 Engine 狀態機

engine 提供六個 reducer,每個對應 LangGraph StateGraph 的一個節點:

```python
# 初始化計劃,回傳第一個 Decision
decision = engine.initialize(plan, thread_id="job-01")

# 執行完成後告知 engine
decision = engine.on_executed(state, step_output)

# 驗收完成後告知 engine
decision = engine.on_verified(state, verification_result)

# 人工介入完成後告知 engine
decision = engine.on_human_resolved(state, gate_result)

# 重規劃完成後告知 engine
decision = engine.on_replanned(state, new_plan)

# 動態插入新步驟(不觸發 replan,version 不變)
new_state = engine.insert_steps(state, new_steps, after_step_id="s2")
```

`Decision` 包含:
- `action: Action` — EXECUTE / RETRY / VERIFY / REPLAN / ESCALATE / DONE
- `state: PlanState` — 新狀態
- `feedback: str` — RETRY 時的可行動回饋
- `events: list[Event]` — 本次轉移產生的事件

**失敗路由**:驗收失敗 → RETRY(帶 feedback)→(重試次數耗盡)→ REPLAN → ESCALATE(等人工)。

### 3.5 分層驗收(LayeredVerifier)

```
hard(ProgrammaticVerifier)  → 便宜先跑(不燒 LLM token)
  ↓ 通過
soft(LLMJudgeVerifier)      → 語意評分(較貴,hard 通過才跑)
  ↓ 通過
human(HumanGateVerifier)    → 人工審核(最貴,前兩層通過才觸發)
```

任一 `required=True` 的層失敗即短路(fail-closed):不確定就不放行。`advisory` 層(`required=False`)失敗只記錄,不阻擋推進。

```python
verifier = LayeredVerifier(layers=[
    ("hard",  ProgrammaticVerifier(checks={...}), True),   # required
    ("soft",  LLMJudgeVerifier(model=...),        True),   # required
    ("human", HumanGateVerifier(),                False),  # advisory
])
```

### 3.6 可插拔協定(Public API 0.1.0)

四個可插拔點定義在 `workplan.protocols`:

```python
class Planner(Protocol):
    def make_plan(self, goal: str, context: dict) -> Plan: ...
    def replan(self, state: PlanState, failure: VerificationResult) -> Plan: ...

class Executor(Protocol):
    def execute(self, step: Step, state: PlanState) -> StepOutput: ...

class Verifier(Protocol):
    def verify(self, step: Step, output: StepOutput, state: PlanState) -> VerificationResult: ...

class PlanStore(Protocol):
    def save(self, thread_id: str, state: PlanState) -> None: ...
    def load(self, thread_id: str) -> PlanState | None: ...
```

**穩定契約**:這四個介面的簽章變動視為破壞性變更,會隨版號管理。整合者自帶的元件只要滿足 Protocol 即可插入,無需繼承任何基底類別。

### 3.7 WorkPlanRunner(LangGraph Adapter)

`WorkPlanRunner` 是 MVP 的主要整合門面,封裝了 LangGraph StateGraph + SqliteSaver + interrupt:

```python
from workplan.adapters.langgraph import WorkPlanRunner

# context manager 確保 checkpointer 正確關閉
with WorkPlanRunner(
    executor=my_executor,
    verifier=my_verifier,
    planner=my_planner,   # 選填;不給則只跑 executor+verifier
    db_path="/tmp/run.sqlite",
) as runner:
    # 全新執行
    result = runner.run(plan=plan, thread_id="job-01")

    # 續跑(已 DONE 的步不重跑)
    result = runner.resume("job-01")

    # 人工決策後續跑
    result = runner.resume(
        "job-01",
        resolution="approved",   # approved / rejected / edited
        note="人工確認",
        edited_content="...",    # resolution="edited" 時提供
    )
```

`RunResult` 包含:
- `status: str` — "done" / "failed" / "interrupted"
- `state: PlanState` — 最終狀態
- `interrupted: bool` — True 表示觸發了 human gate,等待 resume

### 3.8 審計輸出

執行結束後可從 `state.history` 產生審計報告:

```python
from workplan.audit.render import to_event_log, to_markdown, write_audit

# JSON 事件流(可序列化存檔)
event_log = to_event_log(state)

# Markdown 驗收摘要(適合 human review)
md_report = to_markdown(state)

# 同時寫出兩個檔案
write_audit(state, output_dir="/tmp/audit/", prefix="job-01")
# → /tmp/audit/job-01_events.json
# → /tmp/audit/job-01_summary.md
```

### 3.9 依賴邊界(鐵則)

```
workplan.engine
workplan.models      → 這些核心模組禁止 import langgraph / anthropic / adapters
workplan.verifiers
workplan.planners
workplan.executors

workplan.planners.llm_planner   → 允許 import langchain(白名單)
workplan.verifiers.llm_judge    → 允許 import langchain(白名單)

workplan.adapters.langgraph     → 唯一允許 import langgraph 的檔案
```

此邊界由 `tests/test_import_boundaries.py` 自動守門。`pyproject.toml` 對應:

```toml
[project]
dependencies = []                       # 核心零依賴(只需標準庫)

[project.optional-dependencies]
langgraph = ["langgraph>=1.0", "langgraph-checkpoint-sqlite"]
llm       = ["langchain>=0.3", "langchain-anthropic"]
dev       = ["pytest", "ruff", "pre-commit"]
```

**LLM 元件不被 `__init__` eager import**:請以顯式路徑取用:

```python
# 正確
from workplan.planners.llm_planner import LLMPlanner
from workplan.verifiers.llm_judge import LLMJudgeVerifier

# 錯誤(會把 langchain 拖進核心)
from workplan import LLMPlanner   ← 不存在
```

### 3.10 跑測試

```bash
pytest -q                  # 全套(77 個測試)
pytest -q -m "not slow"    # 日常(75 個,跳過 subprocess 級的真 kill 測試;CI 跑這個)
pytest tests/test_engine.py -q            # 只跑 engine 單測
pytest tests/test_import_boundaries.py   # 驗證依賴邊界
```

CI(`.github/workflows/ci.yml`)在每次 push/PR 自動執行:
- `ruff check` + `ruff format --check`(lint/格式)
- 純核心 job + 完整 extras job
- Python 3.11 / 3.12 矩陣

---

## 附錄

### A. 設計決策速查

| 決策 | 結論 |
|------|------|
| D2 薄殼 | `engine.py` 純函式,不做 I/O;副作用由 adapter 承擔 |
| D4 LLM 綁定 | 透過 LangChain `init_chat_model`,預設 Claude |
| D6 replan 語意 | 保留已 DONE 步、只重生未完成尾巴、`version++` |
| D8 escalate 終點 | 標記 `blocked` + `interrupt()` 暫停等人 |
| D9 打包 | 核心零依賴;adapter/llm 為 optional extra |
| D10 驗收分層 | hard→soft→human,任一 required 層失敗即短路 |

### B. 跨元件不變量

| 不變量 | 說明 |
|--------|------|
| I1 單調進度 | `cursor` 只在某步進入 DONE 後才前進;已 DONE 步不被覆寫 |
| I2 可序列化 | `PlanState` 任一時刻可完整 JSON 序列化/還原 |
| I3 每次落盤 | engine 每次狀態轉移,adapter 必須在推進前 `save()` |
| I4 審計完整 | 每個轉移都對應至少一個 Event |
| I5 驗收前置 | `AcceptanceCriterion` 必須在 step 進入 IN_PROGRESS 前就存在 |
| I6 純函式 | engine 不做 I/O,給定 `(state, input)` 輸出確定 |

### C. 文件地圖

| 文件 | 說明 |
|------|------|
| 本文件(`docs/guide.md`) | 使用手冊(專案目的 + 新手教學 + 技術說明) |
| [`docs/01-survey.md`](01-survey.md) | 技術棧與論文理論 Survey(設計背景) |
| [`docs/02-mvp-proposal.md`](02-mvp-proposal.md) | MVP 架構與里程碑提案 |
| [`docs/phase2/00-overview-and-decisions.md`](phase2/00-overview-and-decisions.md) | ★ 決策表 D1–D12(唯一設計權威來源) |
| [`docs/phase2/`](phase2/) | 逐元件實作規格 |
| [`examples/quickstart_integration.py`](../examples/quickstart_integration.py) | ★ 整合入口範例 |
