# MVP 實作提案:WorkPlanHandler

**承接**:[`01-survey.md`](01-survey.md) 的結論。
**目標**:用最小可行範圍,證明「**結構化計劃 + 驗收閘門 + 重規劃迴圈 + 持久化**」這套骨架能讓 agent 在長任務上**照計劃走、逐階段驗收完成**,且以**可插拔**方式掛載到 LangGraph。

---

## 1. MVP 範圍(Scope)

### 1.1 要做(In Scope)

- 一組 **framework-agnostic 核心抽象**(Plan / Step / AcceptanceCriterion / Verifier / Planner / Executor / PlanStore)。
- 一個 **執行管理迴圈**:`plan → execute_step → verify → advance | retry | replan | escalate`。
- **一個 LangGraph adapter**:把上述迴圈編譯成 LangGraph 圖,沿用其 checkpointer 做持久化與續跑。
- **兩種內建 Verifier**:`ProgrammaticVerifier`(callable / 測試)與 `LLMJudgeVerifier`(rubric 評分)。
- **計劃即記憶(recitation)**:每步把精簡計劃摘要注入 prompt 尾端。
- **一個端到端 demo**:用一個多步驟任務(例:「研究主題 X 並產出含 N 個段落、每段附引用的報告」)跑通整個迴圈,展示逐階段驗收。

### 1.2 先不做(Out of Scope,留待 Phase 3)

- Temporal / 分散式 durable execution 後端(MVP 用 LangGraph checkpointer 即可)。
- 形式化 PDDL/VAL verifier(以介面預留,先不實作)。
- 多 agent / 並行子任務 DAG(MVP 先做線性 + 動態插入步驟)。
- 完整記憶體 consolidation(MVP 只做 working + 簡單 episodic log)。
- 量化 benchmark 評測(Phase 3)。

---

## 2. 架構總覽

```
┌─────────────────────────────────────────────────────────────┐
│                     使用者 / 上層 Agent                        │
│                    (給定 goal / 高階任務)                      │
└───────────────────────────┬─────────────────────────────────┘
                            │ goal
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  WorkPlanHandler 核心 (framework-agnostic)     │
│                                                               │
│   ┌──────────┐   plan    ┌───────────────────────────────┐  │
│   │ Planner  │──────────▶│           Plan                │  │
│   │ (LLM)    │  replan   │  [Step{desc, acceptance, ...}]│  │
│   └──────────┘◀──────────└───────────────────────────────┘  │
│        ▲                              │ current step          │
│        │ feedback                     ▼                       │
│   ┌────┴─────┐  result   ┌──────────────────┐                │
│   │ Executor │◀─────────▶│   執行迴圈引擎     │                │
│   │ (tools)  │           │  (state machine) │                │
│   └──────────┘           └────────┬─────────┘                │
│                                    │ output                   │
│                                    ▼                          │
│                          ┌──────────────────┐  pass/fail      │
│                          │    Verifier      │──────┐          │
│                          │ (hard/soft/human)│      │          │
│                          └──────────────────┘      │          │
│                                                     ▼          │
│                          advance | retry | replan | escalate  │
└──────────────────────────────┬──────────────────────────────┘
                               │ persist
                               ▼
                       ┌───────────────┐
                       │   PlanStore   │  ← MVP: LangGraph Checkpointer
                       │ (durable)     │     Phase3: Temporal/Postgres
                       └───────────────┘
```

**設計原則**:
- **核心不 import LangGraph**。LangGraph 只出現在 `adapters/langgraph.py`。這保證可插拔。
- **驗收是一等公民**:`AcceptanceCriterion` 內建於每個 `Step`,在**規劃期**就生成(遵循 survey §3.1 的 rubric 最佳實務)。
- **狀態外部化**:整個執行狀態(計劃 + 進度 + 軌跡)是可序列化的 `PlanState`,交給 `PlanStore` 持久化。

---

## 3. 核心抽象(Core Abstractions)

> 完整型別草圖見 [`../src/workplan/`](../src/workplan/)。以下為設計意圖摘要。

### 3.1 資料模型

```python
class StepStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    DONE = "done"          # 已通過驗收
    FAILED = "failed"      # 重試/重規劃用盡
    BLOCKED = "blocked"    # 等待人工

@dataclass
class AcceptanceCriterion:
    """階段性驗收目標 —— 本模組的核心。在規劃期生成。"""
    description: str                  # 人類可讀的「完成定義 / DoD」
    kind: Literal["programmatic", "llm_judge", "human"]
    spec: dict                        # programmatic: {"callable": ...} / llm_judge: {"rubric": ...}
    threshold: float = 1.0            # llm_judge 的通過分數(0~1)

@dataclass
class Step:
    id: str
    description: str                  # 要做什麼
    acceptance: AcceptanceCriterion   # 怎樣算做完(驗收)
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    output: Any = None
    notes: list[str] = field(default_factory=list)  # reflection / 失敗教訓(episodic)

@dataclass
class Plan:
    goal: str
    steps: list[Step]
    version: int = 1                  # replan 時遞增,可審計

@dataclass
class PlanState:
    """整個可序列化的執行狀態 —— 交給 PlanStore 持久化。"""
    plan: Plan
    cursor: int = 0                   # 目前在第幾步
    history: list[dict] = field(default_factory=list)  # episodic 軌跡
    status: Literal["running","done","failed","blocked"] = "running"
```

### 3.2 可插拔策略介面(Protocols)

```python
class Planner(Protocol):
    def make_plan(self, goal: str, context: dict) -> Plan: ...
    def replan(self, state: PlanState, failure: "VerificationResult") -> Plan: ...

class Executor(Protocol):
    def execute(self, step: Step, state: PlanState) -> StepOutput: ...

class Verifier(Protocol):
    def verify(self, step: Step, output: StepOutput, state: PlanState) -> VerificationResult: ...

class PlanStore(Protocol):
    def save(self, thread_id: str, state: PlanState) -> None: ...
    def load(self, thread_id: str) -> PlanState | None: ...
```

`VerificationResult` 含 `passed: bool`、`score: float`、`feedback: str`(失敗時回饋給 Planner/Executor 做 reflection 重試)。

### 3.3 內建 Verifier 實作

| 類別 | 機制 | 用途 |
|------|------|------|
| `ProgrammaticVerifier` | 跑使用者提供的 `callable(output, state) -> bool`(可包單元測試、assertion、schema 檢查) | **硬驗收**(最可信,優先) |
| `LLMJudgeVerifier` | 用 rubric 對 output/trajectory 評分;分離「rubric 生成」與「評分」 | **軟驗收**(開放式輸出) |
| `HumanGateVerifier` | 暫停並等待人工確認(對接 LangGraph human-in-the-loop) | **高風險步驟** |
| `CompositeVerifier` | 串接多個 verifier(全過才過 / 加權) | 混合驗收 |

---

## 4. 執行迴圈(State Machine)

```
        ┌─────────┐
        │  PLAN   │  Planner.make_plan(goal)  → 含每步 acceptance
        └────┬────┘
             ▼
        ┌─────────┐   recitation: 注入精簡計劃到 context 尾端
        │ EXECUTE │   Executor.execute(current_step)
        └────┬────┘
             ▼
        ┌─────────┐
        │ VERIFY  │   Verifier.verify(step, output)
        └────┬────┘
     pass     │     fail
     ┌────────┴────────┐
     ▼                 ▼
┌─────────┐      attempts < K ?
│ ADVANCE │       ┌────┴────┐
│ cursor++│      yes        no
└────┬────┘       ▼          ▼
     │         ┌───────┐  replans < R ?
  more steps?  │ RETRY │   ┌───┴───┐
   ┌───┴───┐   │(+feed │  yes      no
  yes      no  │ back) │   ▼        ▼
   │        ▼  └───┬───┘ ┌──────┐ ┌──────────┐
   └──▶EXECUTE ▼   │     │REPLAN│ │ ESCALATE │
            ┌─────┐│     │      │ │ (human/  │
            │ DONE││     └──┬───┘ │  blocked)│
            └─────┘└─EXECUTE◀┘    └──────────┘
```

- **每個轉移後都 `PlanStore.save()`** → 崩潰可從任一步續跑。
- **retry 帶 feedback**:把 `VerificationResult.feedback` 寫入 `step.notes`(Reflexion 式 verbal feedback),再餵回 Executor。
- **replan**:把目前 `PlanState` + 失敗原因給 Planner,產生新版 `Plan`(version+1),保留已完成步驟。
- **escalate**:標記 `blocked`,透過 human gate 等待。

可調參數:`max_retries_per_step K`(預設 2)、`max_replans R`(預設 2)。

---

## 5. LangGraph 整合(可插拔示範)

核心迴圈直接對映到 LangGraph 的節點/條件邊;LangGraph 提供 checkpointer = 我們的 `PlanStore`。

```python
# adapters/langgraph.py  (核心程式碼不依賴 LangGraph,只有這裡依賴)
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver   # MVP;prod 換 PostgresSaver

def build_graph(planner, executor, verifier, *, K=2, R=2):
    g = StateGraph(PlanState)            # PlanState 即 LangGraph state schema

    g.add_node("plan",    lambda s: _plan_node(s, planner))
    g.add_node("execute", lambda s: _execute_node(s, executor))
    g.add_node("verify",  lambda s: _verify_node(s, verifier))
    g.add_node("replan",  lambda s: _replan_node(s, planner))

    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", _route_after_verify, {
        "advance":  "execute",   # 還有步驟 → 下一步
        "retry":    "execute",   # 同一步重試(帶 feedback)
        "replan":   "replan",
        "done":     END,
        "escalate": END,         # 透過 interrupt 等人(human-in-the-loop)
    })
    g.add_edge("replan", "execute")

    return g.compile(checkpointer=MemorySaver())   # ← 持久化 = PlanStore
```

- **續跑**:用同一個 `thread_id` 呼叫 `graph.invoke(None, config={"configurable":{"thread_id": tid}})`,LangGraph 自動從最後 checkpoint 接續。
- **human gate**:用 LangGraph 的 `interrupt()` 暫停在 escalate,人工確認後 resume。
- **可換框架**:CrewAI/AutoGen/Temporal 各寫一個對應 adapter 即可,核心抽象不動。

---

## 6. 端到端 Demo(驗收這個 MVP 本身的方式)

**任務**:「研究『agent 長任務執行管理』並產出一份報告:需含 ≥3 個段落,每段 ≥1 條可追溯引用。」

**展示重點**:
1. Planner 產出含驗收條件的計劃(每段一個 Step,acceptance = 「該段 ≥80 字且含 ≥1 個 URL」)。
2. Executor 逐段產生內容。
3. `ProgrammaticVerifier` 檢查字數/URL(硬驗收);`LLMJudgeVerifier` 檢查段落是否切題(軟驗收)——用 `CompositeVerifier` 串接。
4. 故意讓某段第一次不含引用 → 觀察 **retry 帶 feedback** 後修正通過。
5. 中途 kill process → 用同一 `thread_id` 重啟 → 觀察**從中斷處續跑**。
6. 全部步驟 DONE → 輸出最終報告 + 一份「驗收紀錄(audit trail)」。

**這個 demo 同時就是 MVP 的驗收標準**(見 §8)。

---

## 7. 專案結構(Phase 2 落地時)

```
WorkPlanHandler/
├── docs/                      # Phase 1 交付(本批文件)
├── src/workplan/
│   ├── models.py              # Plan / Step / AcceptanceCriterion / PlanState
│   ├── protocols.py           # Planner / Executor / Verifier / PlanStore 介面
│   ├── engine.py              # framework-agnostic 執行迴圈(state machine)
│   ├── verifiers/
│   │   ├── programmatic.py
│   │   ├── llm_judge.py
│   │   ├── human_gate.py
│   │   └── composite.py
│   ├── planners/
│   │   └── llm_planner.py     # 預設 LLM planner(prompt 模板 + 結構化輸出)
│   └── adapters/
│       └── langgraph.py       # ← 唯一依賴 LangGraph 的檔案
├── examples/
│   └── research_report_demo.py
└── tests/
```

> 本次 Phase 1 已附上 `src/workplan/models.py`、`protocols.py` 的**介面草圖**(非可執行成品),用以具體化上述設計。

---

## 8. 里程碑與驗收標準(Phase 2)

| 里程碑 | 內容 | 驗收標準(Definition of Done) |
|--------|------|------------------------------|
| **M1**(週1) | 核心資料模型 + protocols + engine 骨架 | 單元測試:狀態機在 mock planner/executor/verifier 下走完 pass/fail/retry/replan 各路徑 |
| **M2**(週2) | 兩種 Verifier(programmatic + llm_judge)+ composite | 對固定輸入,驗收判定正確;rubric 評分可重現 |
| **M3**(週3) | LangGraph adapter + checkpointer 持久化 | demo 任務跑通;**kill 後同 thread_id 可續跑** |
| **M4**(週4) | recitation + retry-with-feedback + human gate | demo 中故意失敗的步驟能自我修正通過;高風險步驟可暫停等人 |
| **M5**(週4) | 端到端 demo + audit trail 輸出 | §6 六項展示全數通過,產出驗收紀錄 |

**MVP 整體驗收(對應專案 Phase 1 的「MVP 實作提案」要求)**:
> 能在一個 ≥5 步、含至少一次故意失敗的長任務上,**全程照計劃推進、逐階段自動驗收、失敗自我修正、中斷後續跑**,並輸出可審計的驗收紀錄。

---

## 9. 後續(Phase 3 展望)

- 接 **Temporal** 後端取得 exactly-once 與真正 durable execution。
- 加入 **PDDL/VAL 形式化 verifier**(LLM-Modulo 路線)。
- **並行子任務 DAG**(LLM-Compiler 路線)。
- **記憶體 consolidation**(semantic skill memory、跨任務重用 rubric)。
- **量化評測**:在 long-horizon benchmark 上對比「有/無本模組」的成功率與步數效率。

---

*理論依據與資料來源見 [`01-survey.md`](01-survey.md) 與 [`references.md`](references.md)。*
