# 規格 01:資料模型與事件(Data Model & Events)

**元件**:`workplan/models.py`(擴充 Phase 1 草圖)、`workplan/events.py`(新增)
**相關決策**:D5(動態插步)、D6(replan 語意)、D11(JSON+MD 審計)、I2/I4/I5

---

## 1. 職責

定義整個模組共用的、**可序列化、零框架依賴**的核心型別:計劃結構(`Plan/Step/AcceptanceCriterion`)、執行狀態(`PlanState`)、與審計事件(`Event`)。所有其他元件透過這些型別溝通。

---

## 2. 計劃模型(已有草圖,Phase 2 增補)

Phase 1 草圖(`Step/AcceptanceCriterion/Plan/PlanState`)維持,新增以下欄位與方法以支援 D5/D6。

### 2.1 `Step` 增補

```python
@dataclass
class Step:
    id: str
    description: str
    acceptance: AcceptanceCriterion
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    output: Any = None
    notes: list[str] = field(default_factory=list)
    # --- Phase 2 新增 ---
    origin: Literal["initial", "replan", "insert"] = "initial"  # D5/D6 來源追溯
    parent_id: str | None = None        # 動態插步時記錄被誰拆出(ADaPT 式)
    max_attempts: int = 2               # 此步的 retry 上限(覆寫全域 K)
```

- `id` 規範:`s{n}` 或 UUID;**全 Plan 唯一且永不重用**(replan 插入新步用新 id,利於審計對齊)。
- `origin`/`parent_id`:支援 D5「動態插步」的譜系追蹤。

### 2.2 `Plan` 增補(支援 D6 replan)

```python
@dataclass
class Plan:
    goal: str
    steps: list[Step]
    version: int = 1
    revision_note: str = ""             # D6 本版為何被改(replan 理由)

    def insert_after(self, step_id: str, new_steps: list[Step]) -> None: ...
        # D5:在指定步驟後插入;new_steps.origin="insert"
    def replace_tail_from(self, cursor: int, new_tail: list[Step]) -> None: ...
        # D6:保留 [0:cursor] 已完成,替換尾巴;version 由 engine 負責 ++
    def completed_steps(self) -> list[Step]: ...
    def render_for_recitation(self) -> str: ...   # Phase 1 已有
```

**D6 不變量**:`replace_tail_from` 不得動到任何 `status==DONE` 的步驟(以 assert 守 I1)。

### 2.3 `AcceptanceCriterion`(對應 D10 分層)

Phase 1 草圖維持。`kind` 已涵蓋 `programmatic | llm_judge | human`。Phase 2 增補供 `LayeredVerifier`(D10)使用的欄位:

```python
@dataclass
class AcceptanceCriterion:
    description: str
    kind: AcceptanceKind = "llm_judge"
    spec: dict[str, Any] = field(default_factory=dict)
    threshold: float = 1.0
    # --- Phase 2 新增 ---
    required: bool = True               # D10:required 層失敗即短路;False=advisory
    layer: Literal["hard", "soft", "human"] = "soft"  # D10 分層歸屬
```

> 一個 `Step` 可帶**多個** criterion 嗎?MVP 決定:`Step.acceptance` 保持單一,但 `LayeredVerifier` 可在 `spec["sub"]` 內掛多條子準則(見規格 03)。保持模型簡單。

---

## 3. 執行狀態 `PlanState`(增補)

```python
@dataclass
class PlanState:
    plan: Plan
    cursor: int = 0
    history: list[dict] = field(default_factory=list)   # 改為存 Event.to_dict()
    replans: int = 0
    status: PlanRunStatus = "running"     # running|done|failed|blocked
    # --- Phase 2 新增 ---
    thread_id: str | None = None          # P3:對應持久化 thread
    pending_human: HumanGate | None = None # D8:escalate 時填,記錄等待中的關卡

    @property
    def current_step(self) -> Step | None: ...
```

`HumanGate`(D8):
```python
@dataclass
class HumanGate:
    step_id: str
    reason: str                  # 為何卡(連續失敗 / 高風險步驟)
    asked_at: str                # ISO 時間
    resolution: Literal["pending", "approved", "rejected", "edited"] = "pending"
    human_note: str = ""
```

---

## 4. 事件模型(`events.py`,對應 D11/I4)

**設計原則**:engine 每次轉移產出一或多個 `Event`,append 到 `PlanState.history`。事件是**append-only、不可變**的審計來源(survey §4.3 event history 概念的輕量版)。

```python
class EventType(str, Enum):
    PLAN_CREATED      = "plan_created"
    PLAN_REVISED      = "plan_revised"      # D6 replan / criteria 修改
    STEP_STARTED      = "step_started"
    STEP_OUTPUT       = "step_output"
    VERIFY_PASSED     = "verify_passed"
    VERIFY_FAILED     = "verify_failed"
    STEP_RETRIED      = "step_retried"
    STEPS_INSERTED    = "steps_inserted"    # D5
    STEP_DONE         = "step_done"
    ESCALATED         = "escalated"         # D8
    HUMAN_RESOLVED    = "human_resolved"    # D8
    RUN_COMPLETED     = "run_completed"
    RUN_FAILED        = "run_failed"

@dataclass(frozen=True)
class Event:
    type: EventType
    ts: str                       # ISO-8601 UTC
    step_id: str | None = None
    plan_version: int = 1
    payload: dict[str, Any] = field(default_factory=dict)
    # payload 內容依 type 而定,見 §4.1

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> "Event": ...
```

### 4.1 各事件的 `payload` 約定(節選)

| EventType | payload 必含欄位 |
|-----------|-----------------|
| `VERIFY_PASSED` / `VERIFY_FAILED` | `score: float`, `layer: str`, `feedback: str` |
| `STEP_RETRIED` | `attempt: int`, `feedback: str` |
| `PLAN_REVISED` | `from_version: int`, `to_version: int`, `reason: str`, `kept_step_ids: list[str]` |
| `STEPS_INSERTED` | `after_step_id: str`, `new_step_ids: list[str]` |
| `ESCALATED` | `reason: str`, `attempts_exhausted: bool` |
| `HUMAN_RESOLVED` | `resolution: str`, `human_note: str` |

> 規範:`payload` 必須可 JSON 序列化(不得放物件參考);大型 output 存摘要 + 指標(避免 history 爆量,呼應 survey §4.2 bounded growth)。

---

## 5. 審計輸出(D11)

`audit/render.py` 提供兩個純函式(零依賴):

```python
def to_event_log(state: PlanState) -> list[dict]:
    """回傳完整 JSON 事件流(state.history 即是,提供穩定 schema 版本標頭)。"""

def to_markdown(state: PlanState) -> str:
    """渲染人讀驗收摘要:目標、各步驟狀態與驗收結果、replan 次數、最終結論。"""
```

**Markdown 摘要範本(示意)**:
```markdown
# 驗收報告:<goal>   (plan v3, status: done)
| # | 步驟 | 狀態 | 驗收 | 嘗試 | 分數 |
|---|------|------|------|------|------|
| 1 | ...  | ✅ done | hard+soft | 1 | 1.0 |
| 3 | ...  | ✅ done | hard      | 3 | 1.0 |  ← 前2次失敗,第3次通過
...
- replan 次數:1(理由:第3步驟原驗收條件不可達,放寬 threshold)
- 人工關卡:第5步 approved(note: ...)
```

---

## 6. 測試案例(M1 驗收用)

- **序列化往返**:任意 `PlanState` → `json.dumps(to_event_log)` → 還原,欄位一致(I2)。
- **I1 守恆**:對含 `DONE` 步的 plan 呼叫 `replace_tail_from`,若嘗試覆寫 `DONE` 應 raise。
- **插步譜系**:`insert_after` 後新步 `origin=="insert"`、`parent_id` 正確。
- **事件 schema**:每種 `EventType` 的 `payload` 必含欄位齊全(以 schema 驗證)。
- **Markdown 渲染**:對固定 `PlanState` 產出穩定快照(snapshot test)。
