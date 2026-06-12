# 規格 05:LangGraph Adapter 與持久化

**元件**:`workplan/adapters/langgraph.py`(唯一依賴 langgraph 的檔案)、持久化整合
**相關決策**:D2(薄殼)、D7(SqliteSaver)、D8(interrupt HITL)、D9(optional extra)、P1(單一 channel)、P3(thread_id)、I3(轉移前落盤)

---

## 1. 職責

把規格 02 的純函式 engine 「編譯」成一張 LangGraph 圖,並借用 LangGraph 的:
- **SqliteSaver checkpointer** 作為持久化(D7)→ 對應提案的 `PlanStore`;
- **conditional edges** 作為 engine `Action` 的路由;
- **`interrupt()`** 實作 D8 的 human gate / escalate。

**本檔是唯一可 `import langgraph` 的地方**(D9 依賴方向鐵則)。engine/models/verifiers/planners 不得 import 它。

---

## 2. State schema 對映(P1)

MVP 採**單一 channel**承載整個 `PlanState`,reducer 為 last-write-wins:

```python
from typing import Annotated
from langgraph.graph import StateGraph

def _take_last(_old, new): return new        # last-write-wins reducer

class GraphState(TypedDict):
    ps: Annotated[PlanState, _take_last]      # 整包 PlanState
    # 暫態傳遞(不持久化關鍵語意,僅節點間傳值):
    last_output: StepOutput | None
    last_result: VerificationResult | None
```

- **為何單一 channel**:engine 已是 reducer,LangGraph 只需保存/傳遞 `PlanState`,不必把欄位拆成多 channel。求簡、減少 reducer bug。Phase 3 若要欄位級並行再拆。
- **序列化**:`PlanState` 須能被 checkpointer 序列化。MVP 用 dataclass→dict(`to_event_log`/`asdict`);若 SqliteSaver 用 pickle 亦可,但**優先 JSON-able**(I2,跨語言/可檢視)。

---

## 3. 圖拓撲(對映 engine Action)

```python
def build_graph(planner, executor, verifier, *, db_path="workplan.sqlite",
                max_replans=2):
    g = StateGraph(GraphState)
    g.add_node("plan",    n_plan(planner))
    g.add_node("execute", n_execute(executor))
    g.add_node("verify",  n_verify(verifier))
    g.add_node("replan",  n_replan(planner))
    g.add_node("human",   n_human())          # D8:interrupt 點

    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges("execute", route_after_execute, {
        "verify": "verify", "insert": "execute",        # D5 insert 後續執行
    })
    g.add_conditional_edges("verify", route_after_verify, {
        "execute": "execute",   # ADVANCE 或 RETRY 都回 execute
        "replan":  "replan",
        "human":   "human",     # ESCALATE
        "done":    END,
        "failed":  END,
    })
    g.add_edge("replan", "execute")
    g.add_conditional_edges("human", route_after_human, {
        "execute": "execute", "replan": "replan", "failed": END,
    })

    saver = SqliteSaver.from_conn_string(db_path)        # D7
    return g.compile(checkpointer=saver)
```

**節點 = engine 函式的薄包裝(D2)**,例如:
```python
def n_verify(verifier):
    def _node(gs: GraphState) -> GraphState:
        ps, out = gs["ps"], gs["last_output"]
        result = verifier.verify(ps.current_step, out, ps)     # 副作用在 verifier
        dec = engine.on_verified(ps, result)                   # 純函式路由
        return {"ps": dec.state, "last_result": result, "_action": dec.action}
    return _node
```
`route_after_verify(gs)` 只是讀 `gs["_action"]` 映射到邊名。**所有決策邏輯在 engine,圖只負責接線**——這就是薄殼(D2)的具體落地。

---

## 4. 持久化與續跑(D7,I3)

- **thread_id(P3)**:呼叫端透過 `config={"configurable":{"thread_id": tid}}` 提供;未給則模組生成 UUID 並在回傳中告知。
- **轉移前落盤(I3)**:LangGraph 在每個 super-step(節點)後自動 checkpoint;因每個節點對應一次 engine 轉移,等同「每轉移落盤」。durability 模式 MVP 用 `sync`(求正確;Phase 3 可調 `async` 換吞吐,survey §4.3)。
- **kill 後續跑**:
  ```python
  app = build_graph(...)                       # 新 process
  app.invoke(None, config={"configurable":{"thread_id": tid}})  # None=從最後 checkpoint 接續
  ```
  SqliteSaver 從 DB 讀回 `PlanState` → 從中斷的節點繼續(規格 02 T8 / demo 步驟 3)。

> **釐清(survey §4.3)**:這是 checkpoint 級續跑,**非 Temporal 級 exactly-once**。有副作用的步驟在 resume 時可能重跑;MVP mock 無副作用故安全,真實副作用的 idempotency/exactly-once 留 Phase 3(可換 Temporal 後端,engine 不動)。

### 4.1 `PlanStore` 抽象與後端可換性

雖然 MVP 直接用 LangGraph checkpointer,仍保留 `PlanStore` Protocol(規格 protocols)作為抽象縫:
```python
class LangGraphCheckpointStore(PlanStore):
    """以 LangGraph SqliteSaver 為後端的 PlanStore 實作(讀寫 PlanState)。"""
```
讓「核心概念上的持久化」與「LangGraph 細節」解耦——Phase 3 換 Temporal/Postgres 時,engine 與上層 API 不動。

---

## 5. Human-in-the-loop(D8)

`n_human` 節點用 LangGraph `interrupt()`:
```python
def n_human():
    def _node(gs: GraphState) -> GraphState:
        ps = gs["ps"]; gate = ps.pending_human
        decision = interrupt({                  # 圖在此暫停,狀態已 checkpoint
            "type": "human_gate",
            "step_id": gate.step_id, "reason": gate.reason,
            "question": ps.current_step.acceptance.description,
        })
        # resume 時 decision 由外部送入:{"resolution": "...", "human_note": "..."}
        gate.resolution = decision["resolution"]
        gate.human_note = decision.get("human_note", "")
        dec = engine.on_human_resolved(ps, gate)
        return {"ps": dec.state, "_action": dec.action}
    return _node
```
- **暫停**:`interrupt()` 拋出後 LangGraph 保存狀態並回傳給呼叫端「正在等人」。
- **恢復**:呼叫端用 `Command(resume={"resolution":"approved", ...})` + 同 thread_id 續跑。
- **裁決種類**(對映 engine `on_human_resolved`):`approved`→繼續、`rejected`→FAILED、`edited`→把人改過的 plan/criteria 併入後 RETRY/REPLAN(D6 改驗收的人工版)。

---

## 6. 對外 API(門面)

給使用者的最小介面(藏掉 LangGraph 細節):
```python
from workplan.adapters.langgraph import WorkPlanRunner

runner = WorkPlanRunner(planner=..., executor=..., verifier=..., db_path="run.sqlite")
result = runner.run(goal="...", thread_id="job-42")   # 內含 ExternalPlanner 時可改傳 plan=
# 若中途 interrupt:
result = runner.resume(thread_id="job-42", resolution="approved", note="ok")
# 取審計:
events_json = runner.audit_log("job-42")     # → list[dict]  (規格 01 §5)
report_md   = runner.audit_markdown("job-42") # → str
```

---

## 7. 測試案例(M2/M3 DoD)

| 測試 | 驗證 |
|------|------|
| 圖編譯 | `build_graph` 成功、節點/邊齊全 |
| 快樂路徑 E2E | mock 元件跑完,status=done,事件序正確 |
| **kill 續跑** | 跑到第 4 步前另起 process(或刪記憶體物件)以同 thread_id resume → 從第 4 步繼續,結果與不中斷一致(demo 步驟 3) |
| 分層短路 | hard fail 時 judge 不被呼叫(配合規格 03) |
| HITL | escalate→interrupt→resume(approved)→完成;rejected→FAILED;edited→改驗收後 RETRY(demo 步驟 4) |
| 依賴方向 | import-lint:engine/models/verifiers/planners 不含 `langgraph`(D9 鐵則) |

---

## 8. 未來框架 adapter(Phase 3,佐證 D2 可插拔)

同一套 engine + 元件,只要再寫:
- `adapters/crewai.py`:把 step 映成 crew task,verify 作為 task 的 guardrail。
- `adapters/temporal.py`:engine 轉移包成 workflow,executor 為 activity(取得 exactly-once)。
- `adapters/plain.py`:純 while 迴圈 + 自帶 SQLite store(無任何 agent 框架,給最輕量場景)。

engine、models、verifiers、planners **一行都不用改**——這是整個架構的驗收標準(D2)。
