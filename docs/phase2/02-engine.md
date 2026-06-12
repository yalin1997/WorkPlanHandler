# 規格 02:核心 Engine(純函式狀態機)

**元件**:`workplan/engine.py`
**相關決策**:D2(薄殼/零框架依賴)、D5(動態插步)、D6(replan)、D8(escalate)、P2(同步 async-ready)、I1–I6

---

## 1. 職責與邊界

Engine 是**整個模組的大腦**,但**不碰 I/O、不依賴任何框架、不直接呼叫 LLM**。它只做一件事:

> 給定「目前 `PlanState` + 一次外部結果(執行輸出 / 驗收結果 / 人工裁決)」,計算「下一個狀態 + 要採取的動作 + 產生的事件」。

LLM 呼叫、工具執行、持久化、`interrupt()` 全部由**呼叫方(adapter)**負責。這保證 engine 可被純單元測試(I6),且換框架時不動 engine(D2)。

**禁止**:`import langgraph`、`import anthropic`、檔案/網路 I/O、`time.sleep`、全域可變狀態。

---

## 2. 核心型別

```python
class Action(str, Enum):
    EXECUTE   = "execute"    # 請 adapter 執行 current_step
    VERIFY    = "verify"     # 請 adapter 對 current_step 跑驗收
    ADVANCE   = "advance"    # cursor 前進(已寫入,通知 adapter 繼續)
    RETRY     = "retry"      # 同一步重試(帶 feedback)
    REPLAN    = "replan"     # 請 adapter 呼叫 planner.replan
    ESCALATE  = "escalate"   # D8:請 adapter 觸發 interrupt() 等人
    DONE      = "done"       # 全部完成
    FAILED    = "failed"     # 終止失敗

@dataclass
class Decision:
    action: Action
    state: PlanState         # 已套用本次轉移的新狀態(I3:adapter 拿去 save)
    events: list[Event]      # 本次轉移產生的審計事件(I4)
    # 給 adapter 的提示(視 action 而定):
    feedback: str = ""       # RETRY/REPLAN 時要餵回 executor/planner 的反思
```

Engine 是一組**純函式**,核心是「reducer」:

```python
def initialize(plan: Plan, *, thread_id: str | None = None) -> Decision:
    """建立初始 state(status=running, cursor=0),產 PLAN_CREATED 事件,回傳 action=EXECUTE。"""

def on_executed(state: PlanState, output: StepOutput) -> Decision:
    """執行完一步後呼叫。寫入 output、產 STEP_OUTPUT,回傳 action=VERIFY。
       若 output.error 非空(執行期硬錯)→ 視為一次失敗,走 _handle_failure。"""

def on_verified(state: PlanState, result: VerificationResult) -> Decision:
    """驗收完成後呼叫。核心路由邏輯在此(見 §3)。"""

def on_human_resolved(state: PlanState, gate: HumanGate) -> Decision:
    """D8:人工裁決後呼叫。approved→繼續(ADVANCE/REPLAN);rejected→FAILED;
       edited→把人改過的 plan/criteria 併入後 RETRY 或 REPLAN。"""

def on_replanned(state: PlanState, new_plan: Plan) -> Decision:
    """planner.replan 產出新 plan 後呼叫。套用 D6 語意(保留 DONE、version++、
       產 PLAN_REVISED),回傳 action=EXECUTE。"""
```

> 為何拆成多個 `on_*` 而非一個大 `step()`?因為每個外部結果(執行/驗收/人工/重規劃)是不同的非同步邊界,adapter 在不同 LangGraph 節點呼叫對應函式。這讓 engine 與圖節點一一對映、好測。

---

## 3. 路由核心:`on_verified`(狀態機心臟)

```
on_verified(state, result):
    step = state.current_step
    if result.needs_human:                      # D8 顯式 human gate
        return _escalate(state, reason="human gate", exhausted=False)

    if result.passed:
        mark step DONE; emit VERIFY_PASSED, STEP_DONE
        if 還有下一步:
            cursor += 1; emit (next step) → Decision(EXECUTE)
        else:
            state.status = "done"; emit RUN_COMPLETED → Decision(DONE)
        return

    # ---- 失敗分支 ----
    emit VERIFY_FAILED(score, layer, feedback)
    step.notes.append(result.feedback)          # Reflexion 式 verbal feedback
    step.attempts += 1
    return _handle_failure(state, result)
```

`_handle_failure`(統一處理執行錯與驗收失敗):
```
_handle_failure(state, result):
    step = state.current_step
    if step.attempts < step.max_attempts:                 # K:同步重試
        emit STEP_RETRIED(attempt, feedback)
        return Decision(RETRY, feedback=result.feedback)  # adapter 帶 feedback 重執行
    if state.replans < MAX_REPLANS:                       # R:重規劃
        return Decision(REPLAN, feedback=_replan_brief(state, result))
    # 兩者皆用盡 → D8 escalate(非直接 FAILED)
    return _escalate(state, reason="retries+replans exhausted", exhausted=True)
```

`_escalate`(D8):
```
_escalate(state, reason, exhausted):
    step.status = BLOCKED; state.status = "blocked"
    state.pending_human = HumanGate(step_id=step.id, reason=reason, asked_at=now())
    emit ESCALATED(reason, attempts_exhausted=exhausted)
    return Decision(ESCALATE)      # adapter 翻成 interrupt()
```

可調參數(建構 engine 時注入,P2 無全域狀態):
- `MAX_REPLANS R`(預設 2);`max_attempts` 預設 2,可被 `Step.max_attempts` 覆寫。

---

## 4. replan 套用:`on_replanned`(D6)

```
on_replanned(state, new_plan):
    assert new_plan 未覆寫任何 DONE 步(I1)         # 否則 raise PlanIntegrityError
    kept = state.plan.completed_steps()
    new_plan.version = state.plan.version + 1
    state.plan = _merge(kept, new_plan)             # 保留 DONE 前綴 + 新尾巴
    state.replans += 1
    state.cursor = len(kept)                        # 指到第一個未完成步
    emit PLAN_REVISED(from_version, to_version, reason, kept_step_ids)
    return Decision(EXECUTE)
```

動態插步(D5)由 planner/executor 在執行中決定要拆解時,呼叫:
```
def insert_steps(state, after_step_id, new_steps) -> Decision:
    state.plan.insert_after(after_step_id, new_steps)   # origin="insert"
    emit STEPS_INSERTED(after_step_id, new_step_ids)
    return Decision(EXECUTE)   # 不改 version(僅插入,非重規劃);cursor 不變
```

> D5 vs D6 區別:**插步**=不否定既有計劃、只是補充(version 不變);**replan**=改動尾巴結構/驗收(version++)。審計上分別用 `STEPS_INSERTED` 與 `PLAN_REVISED`。

---

## 5. 狀態機全圖(文字版)

```
initialize ──▶ EXECUTE
   │
   ▼ (adapter 執行 step)
on_executed ──▶ VERIFY  ──(error)──┐
   │                                │
   ▼ (adapter 驗收)                 ▼
on_verified ───────────────▶ _handle_failure
   │ passed                         │
   ├─ 有下一步 ─▶ EXECUTE           ├─ attempts<K ─▶ RETRY ─▶(adapter 重執行)─▶ on_executed
   └─ 無 ─▶ DONE                    ├─ replans<R  ─▶ REPLAN ─▶ on_replanned ─▶ EXECUTE
                                    └─ 皆盡 ─▶ ESCALATE ─▶(adapter interrupt)─▶ on_human_resolved
                                                                               ├ approved ▶ ADVANCE/REPLAN
                                                                               ├ edited   ▶ RETRY/REPLAN
                                                                               └ rejected ▶ FAILED
```

---

## 6. 錯誤處理

| 情況 | engine 行為 |
|------|-------------|
| `StepOutput.error`(執行期例外) | 視為一次失敗,走 `_handle_failure`(不直接崩) |
| planner.replan 回傳覆寫 DONE | raise `PlanIntegrityError`(I1 守恆,絕不容忍) |
| 在 `blocked` 狀態收到非 `on_human_resolved` 呼叫 | raise `IllegalTransitionError` |
| `current_step is None` 但 status==running | raise `IllegalTransitionError`(狀態不一致) |

例外型別集中於 `workplan/errors.py`。Engine **拋例外而非吞掉**——讓 adapter 決定如何記錄/中止(I6 保持純粹)。

---

## 7. 測試矩陣(M1 DoD,全用 mock 元件)

| 測試 | 路徑 | 斷言 |
|------|------|------|
| T1 快樂路徑 | 3 步全 pass | cursor 0→3、status=done、事件序正確 |
| T2 retry | 第2步 fail 一次後 pass | attempts==1、有 STEP_RETRIED、最終 done |
| T3 replan | 第2步 fail K 次 → replan → pass | replans==1、version==2、DONE 前綴保留 |
| T4 escalate | retry+replan 全用盡 | status=blocked、有 ESCALATED、pending_human 正確 |
| T5 human resume | T4 後 approved | on_human_resolved → 繼續至 done |
| T6 insert | 執行中 insert_steps | 新步插入正確位置、cursor 不變、version 不變 |
| T7 I1 守恆 | replan 試圖改 DONE | raise PlanIntegrityError |
| T8 序列化續跑 | 任一中間 state 序列化還原後續跑 | 結果與不中斷一致(配合規格 05) |

全部以 **mock planner/executor/verifier**(可程式控制其 pass/fail 序列)驅動,**不需真 LLM**(D3)。
