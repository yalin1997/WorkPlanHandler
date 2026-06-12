"""M1 DoD:engine 純函式狀態機測試(規格 02 §7 測試矩陣)。

全部使用 mock 元件(不呼叫任何 LLM 或真實工具,D3)。
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from workplan import engine
from workplan.engine import Action
from workplan.errors import PlanIntegrityError
from workplan.executors import MockExecutor
from workplan.models import (
    AcceptanceCriterion,
    Plan,
    PlanState,
    Step,
    StepStatus,
)
from workplan.planners import MockPlanner
from workplan.protocols import StepOutput, VerificationResult
from workplan.verifiers import MockVerifier
from workplan.verifiers.mock import failed


def make_step(step_id: str, description: str = "") -> Step:
    return Step(
        id=step_id,
        description=description or f"步驟 {step_id}",
        acceptance=AcceptanceCriterion(description=f"{step_id} 完成"),
    )


def make_plan(*step_ids: str, goal: str = "測試目標") -> Plan:
    return Plan(goal=goal, steps=[make_step(s) for s in step_ids])


def drive(
    dec: engine.Decision,
    executor: MockExecutor,
    verifier: MockVerifier,
    planner: MockPlanner | None = None,
    max_replans: int = engine.MAX_REPLANS,
) -> engine.Decision:
    """模擬 adapter 迴圈:依 Decision.action 呼叫對應 engine 函式,
    直到 DONE / FAILED / ESCALATE 為止。"""
    while dec.action in (Action.EXECUTE, Action.RETRY, Action.VERIFY, Action.REPLAN):
        state = dec.state
        if dec.action in (Action.EXECUTE, Action.RETRY):
            out = executor.execute(state.current_step, state)
            dec = engine.on_executed(state, out, max_replans=max_replans)
        elif dec.action == Action.VERIFY:
            res = verifier.verify(
                state.current_step,
                StepOutput(content=state.current_step.output),
                state,
            )
            dec = engine.on_verified(state, res, max_replans=max_replans)
        else:  # REPLAN
            assert planner is not None, "REPLAN 需要 planner"
            failure = VerificationResult(passed=False, feedback=dec.feedback)
            dec = engine.on_replanned(state, planner.replan(state, failure))
    return dec


def event_types(state: PlanState) -> list[str]:
    return [e["type"] for e in state.history]


# ---------------------------------------------------------------- T1
def test_t1_happy_path_three_steps_all_pass():
    dec = engine.initialize(make_plan("s1", "s2", "s3"))
    assert dec.action == Action.EXECUTE

    dec = drive(dec, MockExecutor(), MockVerifier())

    state = dec.state
    assert dec.action == Action.DONE
    assert state.cursor == 3
    assert state.status == "done"
    assert all(s.status == StepStatus.DONE for s in state.plan.steps)
    per_step = ["step_started", "step_output", "verify_passed", "step_done"]
    assert event_types(state) == (["plan_created"] + per_step * 3 + ["run_completed"])


# ---------------------------------------------------------------- T2
def test_t2_retry_second_step_fails_once_then_passes():
    verifier = MockVerifier(script={"s2": [failed("第一次差一點")]})
    dec = engine.initialize(make_plan("s1", "s2", "s3"))

    dec = drive(dec, MockExecutor(), verifier)

    state = dec.state
    assert dec.action == Action.DONE
    assert state.status == "done"
    s2 = state.plan.steps[1]
    assert s2.attempts == 1
    assert "第一次差一點" in s2.notes
    retried = [e for e in state.history if e["type"] == "step_retried"]
    assert len(retried) == 1
    assert retried[0]["step_id"] == "s2"
    assert retried[0]["payload"]["feedback"] == "第一次差一點"


# ---------------------------------------------------------------- T3
def test_t3_replan_after_k_failures_keeps_done_prefix():
    verifier = MockVerifier(script={"s2": [failed("不行"), failed("還是不行")]})
    tail = Plan(
        goal="測試目標",
        steps=[make_step("s2b"), make_step("s3b")],
        revision_note="s2 驗收不可達,改走 s2b/s3b",
    )
    planner = MockPlanner(tails=[tail])
    dec = engine.initialize(make_plan("s1", "s2", "s3"))

    dec = drive(dec, MockExecutor(), verifier, planner=planner)

    state = dec.state
    assert dec.action == Action.DONE
    assert state.status == "done"
    assert state.replans == 1
    assert state.plan.version == 2
    # DONE 前綴保留:s1 仍在原位且 DONE
    assert state.plan.steps[0].id == "s1"
    assert state.plan.steps[0].status == StepStatus.DONE
    assert [s.id for s in state.plan.steps] == ["s1", "s2b", "s3b"]
    assert all(s.origin == "replan" for s in state.plan.steps[1:])
    revised = [e for e in state.history if e["type"] == "plan_revised"]
    assert len(revised) == 1
    assert revised[0]["payload"] == {
        "from_version": 1,
        "to_version": 2,
        "reason": tail.revision_note,
        "kept_step_ids": ["s1"],
    }


# ---------------------------------------------------------------- T4
def escalate_after_exhaustion() -> engine.Decision:
    """fail s2 兩次 → replan(上限 1)→ s2b 再 fail 兩次 → escalate。"""
    verifier = MockVerifier(
        script={
            "s2": [failed("fail 1"), failed("fail 2")],
            "s2b": [failed("fail 3"), failed("fail 4")],
        }
    )
    tail = Plan(
        goal="測試目標",
        steps=[make_step("s2b"), make_step("s3b")],
        revision_note="重排尾巴",
    )
    dec = engine.initialize(make_plan("s1", "s2", "s3"))
    return drive(
        dec,
        MockExecutor(),
        verifier,
        planner=MockPlanner(tails=[tail]),
        max_replans=1,
    )


def test_t4_escalate_when_retries_and_replans_exhausted():
    dec = escalate_after_exhaustion()

    state = dec.state
    assert dec.action == Action.ESCALATE
    assert state.status == "blocked"
    assert state.replans == 1
    assert state.current_step.id == "s2b"
    assert state.current_step.status == StepStatus.BLOCKED
    assert state.pending_human is not None
    assert state.pending_human.step_id == "s2b"
    assert state.pending_human.resolution == "pending"
    escalated = [e for e in state.history if e["type"] == "escalated"]
    assert len(escalated) == 1
    assert escalated[0]["payload"]["attempts_exhausted"] is True


# ---------------------------------------------------------------- T5
def test_t5_human_resume_approved_continues_to_done():
    dec = escalate_after_exhaustion()
    state = dec.state

    gate = replace(state.pending_human, resolution="approved", human_note="人工放行")
    dec = engine.on_human_resolved(state, gate)
    assert dec.action == Action.EXECUTE  # 還有 s3b
    assert state.status == "running"
    assert state.pending_human is None
    assert state.plan.steps[1].status == StepStatus.DONE  # s2b 視為通過

    dec = drive(dec, MockExecutor(), MockVerifier())
    assert dec.action == Action.DONE
    assert dec.state.status == "done"
    resolved = [e for e in dec.state.history if e["type"] == "human_resolved"]
    assert len(resolved) == 1
    assert resolved[0]["payload"] == {
        "resolution": "approved",
        "human_note": "人工放行",
    }


# ---------------------------------------------------------------- T6
def test_t6_insert_steps_mid_run_keeps_cursor_and_version():
    executor, verifier = MockExecutor(), MockVerifier()
    dec = engine.initialize(make_plan("s1", "s2", "s3"))
    # 推進到 s2 進行中(cursor=1)
    state = dec.state
    dec = engine.on_executed(state, executor.execute(state.current_step, state))
    dec = engine.on_verified(state, verifier.verify(state.current_step, None, state))
    assert state.cursor == 1

    dec = engine.insert_steps(state, "s2", [make_step("s2x"), make_step("s2y")])

    assert dec.action == Action.EXECUTE
    assert [s.id for s in state.plan.steps] == ["s1", "s2", "s2x", "s2y", "s3"]
    assert state.cursor == 1  # cursor 不變
    assert state.plan.version == 1  # version 不變(D5 插步 ≠ replan)
    s2x = state.plan.steps[2]
    assert s2x.origin == "insert"
    assert s2x.parent_id == "s2"
    inserted = [e for e in state.history if e["type"] == "steps_inserted"]
    assert inserted[0]["payload"] == {
        "after_step_id": "s2",
        "new_step_ids": ["s2x", "s2y"],
    }

    dec = drive(dec, executor, verifier)
    assert dec.state.status == "done"
    assert dec.state.cursor == 5


# ---------------------------------------------------------------- T7
def test_t7_replan_overwriting_done_step_raises():
    executor, verifier = MockExecutor(), MockVerifier()
    dec = engine.initialize(make_plan("s1", "s2", "s3"))
    # s1 通過,進入 s2
    state = dec.state
    dec = engine.on_executed(state, executor.execute(state.current_step, state))
    dec = engine.on_verified(state, verifier.verify(state.current_step, None, state))
    assert state.plan.steps[0].status == StepStatus.DONE

    bad_tail = Plan(goal="測試目標", steps=[make_step("s1")])  # 重用 DONE 步的 id
    with pytest.raises(PlanIntegrityError):
        engine.on_replanned(state, bad_tail)


# ---------------------------------------------------------------- T8
def test_t8_serialization_roundtrip_of_intermediate_state():
    # 取 T2 的中間狀態:s2 剛被判 fail、決策為 RETRY
    verifier = MockVerifier(script={"s2": [failed("第一次差一點")]})
    executor = MockExecutor()
    dec = engine.initialize(make_plan("s1", "s2", "s3"))
    while True:  # 跑到第一個 RETRY 為止
        state = dec.state
        if dec.action in (Action.EXECUTE, Action.RETRY):
            dec = engine.on_executed(state, executor.execute(state.current_step, state))
        elif dec.action == Action.VERIFY:
            res = verifier.verify(state.current_step, None, state)
            dec = engine.on_verified(state, res)
            if dec.action == Action.RETRY:
                break
        else:
            pytest.fail(f"未預期的 action:{dec.action}")

    mid = dec.state
    restored = PlanState.from_dict(json.loads(json.dumps(mid.to_dict())))

    assert restored.to_dict() == mid.to_dict()  # 欄位一致(I2)
    assert restored.cursor == 1
    assert restored.plan.steps[1].attempts == 1
    assert restored.plan.steps[1].status == StepStatus.IN_PROGRESS
    assert restored.history == mid.history

    # 還原後可續跑至 done(中斷續跑的前提)
    dec = drive(engine.Decision(Action.RETRY, restored), executor, MockVerifier())
    assert dec.action == Action.DONE
    assert dec.state.status == "done"
    assert dec.state.cursor == 3
