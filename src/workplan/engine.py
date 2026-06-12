"""WorkPlanHandler — 核心 Engine(純函式狀態機,規格 02)。

Engine 是整個模組的大腦,但不碰 I/O、不依賴任何框架、不直接呼叫 LLM(D2/I6)。
它只做一件事:

  給定「目前 PlanState + 一次外部結果(執行輸出 / 驗收結果 / 人工裁決)」,
  計算「下一個狀態 + 要採取的動作 + 產生的事件」。

LLM 呼叫、工具執行、持久化、interrupt() 全部由呼叫方(adapter)負責。

禁止:import langgraph、import anthropic、檔案/網路 I/O、time.sleep、
全域可變狀態。`max_replans` 以參數注入(P2),預設 MAX_REPLANS。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .errors import IllegalTransitionError, PlanIntegrityError
from .events import Event, EventType
from .models import HumanGate, Plan, PlanState, Step, StepStatus
from .protocols import StepOutput, VerificationResult

MAX_REPLANS = 2  # R:重規劃上限(可由呼叫端以 max_replans 參數覆寫)

_OUTPUT_SUMMARY_LIMIT = 500  # history 內 output 摘要長度上限(bounded growth)


class Action(str, Enum):
    EXECUTE = "execute"    # 請 adapter 執行 current_step
    VERIFY = "verify"      # 請 adapter 對 current_step 跑驗收
    RETRY = "retry"        # 同一步重試(帶 feedback)
    REPLAN = "replan"      # 請 adapter 呼叫 planner.replan
    ESCALATE = "escalate"  # D8:請 adapter 觸發 interrupt() 等人
    DONE = "done"          # 全部完成
    FAILED = "failed"      # 終止失敗


@dataclass
class Decision:
    action: Action
    state: PlanState          # 已套用本次轉移的新狀態(I3:adapter 拿去 save)
    events: list[Event] = field(default_factory=list)  # 本次轉移的審計事件(I4)
    feedback: str = ""        # RETRY/REPLAN 時要餵回 executor/planner 的反思


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(
    state: PlanState,
    type: EventType,
    step_id: str | None = None,
    **payload: object,
) -> Event:
    """產出事件並 append 到 state.history(I4 審計完整)。"""
    event = Event(
        type=type,
        ts=_now(),
        step_id=step_id,
        plan_version=state.plan.version,
        payload=dict(payload),
    )
    state.history.append(event.to_dict())
    return event


def _summarize(content: object) -> str | None:
    if content is None:
        return None
    return str(content)[:_OUTPUT_SUMMARY_LIMIT]


def _require_running(state: PlanState) -> Step:
    """共同前置檢查:必須在 running 且有 current_step。"""
    if state.status == "blocked":
        raise IllegalTransitionError(
            "state 為 blocked,僅允許 on_human_resolved"
        )
    if state.status != "running":
        raise IllegalTransitionError(
            f"state.status={state.status!r},不接受新的轉移"
        )
    step = state.current_step
    if step is None:
        raise IllegalTransitionError(
            f"status==running 但 cursor={state.cursor} 無對應步驟(狀態不一致)"
        )
    return step


def _start_current_step(state: PlanState) -> Event:
    """把 current_step 標為 IN_PROGRESS 並產 STEP_STARTED(含 I5 檢查)。"""
    step = state.current_step
    if step is None:
        raise IllegalTransitionError("無可啟動的步驟")
    if step.acceptance is None:  # I5:驗收條件必須先於執行存在
        raise PlanIntegrityError(
            f"步驟 {step.id} 缺少 AcceptanceCriterion,不得進入 IN_PROGRESS(I5)"
        )
    step.status = StepStatus.IN_PROGRESS
    return _emit(
        state, EventType.STEP_STARTED, step_id=step.id,
        description=step.description, attempt=step.attempts,
    )


def initialize(plan: Plan, *, thread_id: str | None = None) -> Decision:
    """建立初始 state(status=running, cursor=0),產 PLAN_CREATED,回 EXECUTE。"""
    state = PlanState(plan=plan, thread_id=thread_id)
    events = [
        _emit(
            state, EventType.PLAN_CREATED,
            goal=plan.goal, step_ids=[s.id for s in plan.steps],
        ),
        _start_current_step(state),
    ]
    return Decision(Action.EXECUTE, state, events)


def on_executed(
    state: PlanState, output: StepOutput, *, max_replans: int = MAX_REPLANS
) -> Decision:
    """執行完一步後呼叫。寫入 output、產 STEP_OUTPUT,回傳 action=VERIFY。

    若 output.error 非空(執行期硬錯)→ 視為一次失敗,走 _handle_failure。
    """
    step = _require_running(state)
    events = [
        _emit(
            state, EventType.STEP_OUTPUT, step_id=step.id,
            content=_summarize(output.content), error=output.error,
        )
    ]
    step.output = output.content
    if output.error:
        step.notes.append(output.error)
        step.attempts += 1
        return _handle_failure(state, output.error, events, max_replans)
    step.status = StepStatus.VERIFYING
    return Decision(Action.VERIFY, state, events)


def on_verified(
    state: PlanState, result: VerificationResult, *, max_replans: int = MAX_REPLANS
) -> Decision:
    """驗收完成後呼叫。核心路由邏輯(規格 02 §3)。"""
    step = _require_running(state)
    events: list[Event] = []

    if result.needs_human:  # D8 顯式 human gate
        return _escalate(state, "human gate", exhausted=False, events=events)

    if result.passed:
        events.append(
            _emit(
                state, EventType.VERIFY_PASSED, step_id=step.id,
                score=result.score, layer=result.layer, feedback=result.feedback,
            )
        )
        step.status = StepStatus.DONE
        events.append(_emit(state, EventType.STEP_DONE, step_id=step.id))
        state.cursor += 1
        if state.current_step is not None:
            events.append(_start_current_step(state))
            return Decision(Action.EXECUTE, state, events)
        state.status = "done"
        events.append(_emit(state, EventType.RUN_COMPLETED))
        return Decision(Action.DONE, state, events)

    # ---- 失敗分支 ----
    events.append(
        _emit(
            state, EventType.VERIFY_FAILED, step_id=step.id,
            score=result.score, layer=result.layer, feedback=result.feedback,
        )
    )
    step.notes.append(result.feedback)  # Reflexion 式 verbal feedback
    step.attempts += 1
    return _handle_failure(state, result.feedback, events, max_replans)


def _handle_failure(
    state: PlanState, feedback: str, events: list[Event], max_replans: int
) -> Decision:
    """統一處理執行錯與驗收失敗:RETRY → REPLAN → ESCALATE。"""
    step = state.current_step
    assert step is not None
    if step.attempts < step.max_attempts:  # K:同步重試
        step.status = StepStatus.IN_PROGRESS
        events.append(
            _emit(
                state, EventType.STEP_RETRIED, step_id=step.id,
                attempt=step.attempts, feedback=feedback,
            )
        )
        return Decision(Action.RETRY, state, events, feedback=feedback)
    if state.replans < max_replans:  # R:重規劃
        step.status = StepStatus.FAILED
        brief = (
            f"步驟 {step.id} 已嘗試 {step.attempts} 次仍未通過驗收。"
            f"最後回饋:{feedback}"
        )
        return Decision(Action.REPLAN, state, events, feedback=brief)
    # 兩者皆用盡 → D8 escalate(非直接 FAILED)
    return _escalate(
        state, "retries+replans exhausted", exhausted=True, events=events
    )


def _escalate(
    state: PlanState, reason: str, *, exhausted: bool, events: list[Event]
) -> Decision:
    """D8:標記 blocked、填 pending_human,等 adapter 翻成 interrupt()。"""
    step = state.current_step
    assert step is not None
    step.status = StepStatus.BLOCKED
    state.status = "blocked"
    state.pending_human = HumanGate(
        step_id=step.id, reason=reason, asked_at=_now()
    )
    events.append(
        _emit(
            state, EventType.ESCALATED, step_id=step.id,
            reason=reason, attempts_exhausted=exhausted,
        )
    )
    return Decision(Action.ESCALATE, state, events)


def on_human_resolved(state: PlanState, gate: HumanGate) -> Decision:
    """D8:人工裁決後呼叫。approved→步驟視為通過續跑;rejected→FAILED;
    edited→把人工指示併入 notes 後 RETRY(attempts 歸零重來)。
    """
    if state.status != "blocked" or state.pending_human is None:
        raise IllegalTransitionError(
            "on_human_resolved 僅允許在 blocked 且有 pending_human 時呼叫"
        )
    step = state.current_step
    if step is None or step.id != gate.step_id:
        raise IllegalTransitionError(
            f"gate.step_id={gate.step_id!r} 與 current_step 不一致"
        )
    events = [
        _emit(
            state, EventType.HUMAN_RESOLVED, step_id=step.id,
            resolution=gate.resolution, human_note=gate.human_note,
        )
    ]
    state.pending_human = None

    if gate.resolution == "approved":
        step.status = StepStatus.DONE
        events.append(_emit(state, EventType.STEP_DONE, step_id=step.id))
        state.status = "running"
        state.cursor += 1
        if state.current_step is not None:
            events.append(_start_current_step(state))
            return Decision(Action.EXECUTE, state, events)
        state.status = "done"
        events.append(_emit(state, EventType.RUN_COMPLETED))
        return Decision(Action.DONE, state, events)

    if gate.resolution == "rejected":
        step.status = StepStatus.FAILED
        state.status = "failed"
        events.append(
            _emit(
                state, EventType.RUN_FAILED, step_id=step.id,
                reason=gate.human_note or gate.reason,
            )
        )
        return Decision(Action.FAILED, state, events)

    if gate.resolution == "edited":
        state.status = "running"
        step.status = StepStatus.IN_PROGRESS
        step.attempts = 0  # 人工修訂後重新計數
        if gate.human_note:
            step.notes.append(gate.human_note)
        return Decision(Action.RETRY, state, events, feedback=gate.human_note)

    raise IllegalTransitionError(
        f"無法處理的 resolution:{gate.resolution!r}(仍為 pending?)"
    )


def on_replanned(state: PlanState, new_plan: Plan) -> Decision:
    """planner.replan 產出新 plan(只含未完成尾巴)後呼叫。套用 D6 語意:
    保留 DONE 前綴、version++、產 PLAN_REVISED,回傳 action=EXECUTE。

    若 new_plan 覆寫任何 DONE 步 → raise PlanIntegrityError(I1)。
    """
    if state.status != "running":
        raise IllegalTransitionError(
            f"on_replanned 僅允許在 running 狀態(目前 {state.status!r})"
        )
    kept = state.plan.completed_steps()
    from_version = state.plan.version
    state.plan.replace_tail_from(len(kept), new_plan.steps)  # I1 檢查在此
    state.plan.version = from_version + 1
    state.plan.revision_note = new_plan.revision_note
    state.replans += 1
    state.cursor = len(kept)  # 指到第一個未完成步
    events = [
        _emit(
            state, EventType.PLAN_REVISED,
            from_version=from_version, to_version=state.plan.version,
            reason=new_plan.revision_note, kept_step_ids=[s.id for s in kept],
        ),
        _start_current_step(state),
    ]
    return Decision(Action.EXECUTE, state, events)


def insert_steps(
    state: PlanState, after_step_id: str, new_steps: list[Step]
) -> Decision:
    """D5 動態插步:不否定既有計劃、只是補充。version 不變、cursor 不變。"""
    _require_running(state)
    state.plan.insert_after(after_step_id, new_steps)
    events = [
        _emit(
            state, EventType.STEPS_INSERTED,
            after_step_id=after_step_id,
            new_step_ids=[s.id for s in new_steps],
        )
    ]
    return Decision(Action.EXECUTE, state, events)
