# 規格 04:Planner 與 Executor

**元件**:`workplan/planners/`、`workplan/executors/`
**相關決策**:D1(planner 可插拔/外部 ingest)、D3(mock 首發)、D4(模型無關預設 Claude)、D5(動態插步)、D6(replan)、I5(驗收前置)

---

## A. Planner

### A.1 職責

把 `goal` 拆成**含驗收條件的結構化 `Plan`**(`make_plan`),並在驗收失敗時產出修訂計劃(`replan`)。Planner 是**可插拔**的(D1):預設提供 LLM 版,也支援把上游 agent 的現成 `Plan` 包成 planner。

### A.2 協定(已有草圖)

```python
class Planner(Protocol):
    def make_plan(self, goal: str, context: dict) -> Plan: ...
    def replan(self, state: PlanState, failure: VerificationResult) -> Plan: ...
```

**`make_plan` 契約(關鍵,I5)**:回傳的**每個 `Step` 必須自帶 `AcceptanceCriterion`**,且 criterion 在規劃期就定義(survey §3.1)。違反則模組補預設 soft rubric 並記 warning。

**`replan` 契約(D6)**:
- 輸入 `state`(含已完成步、失敗的 `result`)→ 輸出**只含「未完成尾巴」的新步序**(engine 的 `on_replanned` 負責 merge 保留 DONE 前綴、`version++`)。
- 可修訂尾巴步驟的 `acceptance`(D6 允許改驗收)。
- **不得**輸出覆寫 DONE 的步驟(engine 會 assert,違反 raise `PlanIntegrityError`)。

### A.3 內建實作

#### `LLMPlanner`(預設,D4)
```python
class LLMPlanner(Planner):
    def __init__(self, model=None):   # init_chat_model,預設 "anthropic:claude-..."
```
- **結構化輸出**:用 tool-use / JSON schema 強制 LLM 回傳 `{steps:[{description, acceptance:{kind,layer,spec,threshold,required}}]}`。不靠自由文字解析。
- **prompt 要點**:要求模型(a)拆成可獨立驗收的階段;(b)為每步寫明「完成定義」與對應驗收方式(能程式判定者標 `kind=programmatic` 並給 check 規格,開放式者給 `rubric`);(c)步數適中(避免過細/過粗)。
- **replan**:把 `state`(計劃現況 + 失敗步 + feedback)餵回,要求只重寫未完成尾巴,可調整驗收條件並附 `revision_note`。
- **動態插步協助(D5)**:提供 `decompose(step, reason) -> list[Step]`,當 executor 回報「此步太大需拆解」時呼叫,結果交 engine 的 `insert_steps`。

#### `ExternalPlanner`(D1 外部 ingest)
```python
class ExternalPlanner(Planner):
    """把上游 agent 已產生的 Plan 包成 Planner。"""
    def __init__(self, plan: Plan, replanner: Planner | None = None): ...
    def make_plan(self, goal, context): return self._plan          # 直接回傳外部 plan
    def replan(self, state, failure):
        if self._replanner: return self._replanner.replan(state, failure)
        raise ReplanNotSupported   # 無 replanner 時,engine 收到後直接走 escalate(D8)
```
- 用途:WorkPlanHandler 作為「**純執行管理器**」掛在別人的 planner 後面。
- 若外部未提供 replanner,failure 用盡 retry 後直接 escalate 給人(不自動 replan)。

#### `MockPlanner`(D3,測試/M1)
- 依建構參數產出固定 step 序列與可控 acceptance,供 engine 單測(規格 02 §7)。不呼叫任何 LLM。

---

## B. Executor

### B.1 職責

執行**單一 step**:呼叫工具 / 子 agent / LLM,回傳 `StepOutput(content, artifacts, error)`。Executor 是與「真實世界」互動的唯一出口(engine 純函式,executor 才有副作用)。

### B.2 協定(已有草圖)

```python
class Executor(Protocol):
    def execute(self, step: Step, state: PlanState) -> StepOutput: ...
```

**契約**:
- **recitation 注入點(survey §4.1)**:executor 在組 prompt 時,應把 `state.plan.render_for_recitation()` 注入 context 尾端,對抗長任務目標漂移。這是 executor 的責任(engine 不碰 prompt)。
- **retry 語意**:engine 觸發 `RETRY` 時會把 `feedback` 經 adapter 傳入(寫在 `step.notes[-1]`);executor 應讀取最近 feedback 並據以修正(Reflexion 式)。
- **錯誤回報**:可預期失敗 → `StepOutput(error=...)`(engine 當一次失敗處理);不可預期例外可直接拋(adapter 捕捉記 `RUN_FAILED`)。
- **冪等性提醒(D7 續跑)**:同一 step 可能因 crash-resume 被重跑;有副作用的 executor 應盡量冪等或自帶 dedup key(MVP mock 無副作用;真實副作用的 exactly-once 留 Phase 3 Temporal)。

### B.3 內建實作

#### `MockExecutor`(D3 首發)
- 依 step.id 從預設腳本回傳輸出;可程式設定「第 N 次嘗試才成功」以驗 retry/replan(規格 02 T2/T3)。
- 可設定某 step 回報「需拆解」以驗 D5 insert 路徑。

#### `CallableExecutor`(通用橋接)
- `criterion`-無關的薄包裝:`execute = user_fn(step, state) -> StepOutput`。讓使用者用任意工具/agent 實作執行,而不必懂模組內部。

#### (Phase 3)`LangChainToolExecutor` / `SubAgentExecutor`
- 接 LangChain tools 或子 agent;非 MVP 範圍,介面預留。

---

## C. 兩者與 engine 的協作時序(線性 + 動態插步,D5)

```
adapter:
  plan = planner.make_plan(goal, ctx)        # 或 ExternalPlanner 直接給
  dec  = engine.initialize(plan)             # → EXECUTE
  loop:
    save(dec.state)                          # I3:推進前落盤
    match dec.action:
      EXECUTE:
        out = executor.execute(cur_step, dec.state)   # recitation 在此注入
        if out.requests_decompose:                    # D5
            subs = planner.decompose(cur_step, out.reason)
            dec = engine.insert_steps(dec.state, cur_step.id, subs)
        else:
            dec = engine.on_executed(dec.state, out)
      VERIFY:   dec = engine.on_verified(dec.state, verifier.verify(...))
      RETRY:    cur_step 帶 feedback,回 EXECUTE       # engine 已寫 feedback 到 notes
      REPLAN:   new = planner.replan(dec.state, failure); dec = engine.on_replanned(dec.state, new)
      ESCALATE: interrupt(...)  # D8,等 on_human_resolved
      DONE/FAILED: break
```
> 注意:這段「loop」在 LangGraph adapter 中被拆成各節點與 conditional edges(規格 05),不是一個 while 迴圈。此處僅示意 engine↔planner↔executor 的呼叫關係。

---

## D. 測試案例

- `LLMPlanner` 結構化輸出:mock model 回傳固定 JSON → 解析出帶 acceptance 的 steps;缺 acceptance 時補預設並記 warning。
- `ExternalPlanner`:`make_plan` 原樣回傳;無 replanner 時 `replan` 觸發 escalate 路徑。
- `MockExecutor`:第 N 次成功的腳本能驅動 engine 的 retry/replan 測試。
- recitation:executor 組出的 prompt 尾端含計劃摘要(以 spy 斷言)。
- D5:executor 回報 decompose → engine.insert_steps 被呼叫、新步入列。
