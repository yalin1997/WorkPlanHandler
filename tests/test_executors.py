"""M5b Executor 測試(規格 04 §B.3/§D)。

CallableExecutor 是「通用橋接」——把任意使用者函式包成 Executor,並由
executor 層負責 recitation 注入(survey §4.1)與 retry feedback 帶入
(Reflexion 式)。LLM 接線留在使用者函式(或 demo),核心保持零依賴。
"""

from __future__ import annotations

import pytest

from workplan.executors import CallableExecutor, ExecContext
from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.protocols import StepOutput


def _state() -> PlanState:
    plan = Plan(
        goal="產出研究報告",
        steps=[
            Step(
                id="s1",
                description="蒐集資料",
                acceptance=AcceptanceCriterion(description="≥3 來源"),
            ),
            Step(
                id="s2",
                description="撰寫摘要",
                acceptance=AcceptanceCriterion(description="切題"),
            ),
        ],
    )
    return PlanState(plan=plan, cursor=0)


def test_callable_executor_returns_step_output():
    state = _state()
    ex = CallableExecutor(lambda step, st, ctx: StepOutput(content=f"done {step.id}"))
    out = ex.execute(state.plan.steps[0], state)
    assert isinstance(out, StepOutput)
    assert out.content == "done s1"


def test_callable_executor_injects_recitation():
    """spy 斷言:executor 把計劃摘要(recitation)交給使用者函式(規格 04 §D)。"""
    state = _state()
    seen: dict[str, ExecContext] = {}

    def fn(step, st, ctx):
        seen["ctx"] = ctx
        return StepOutput(content="ok")

    CallableExecutor(fn).execute(state.plan.steps[0], state)
    ctx = seen["ctx"]
    assert ctx.recitation == state.plan.render_for_recitation()
    assert "GOAL:" in ctx.recitation  # 摘要含目標,對抗長 context 漂移


def test_with_recitation_appends_at_prompt_tail():
    """recitation 接在 prompt 尾端(survey §4.1:recitation 模式)。"""
    state = _state()
    ctx = ExecContext(
        recitation=state.plan.render_for_recitation(), feedback="", attempt=0
    )
    prompt = ctx.with_recitation("請執行此步驟")
    assert prompt.startswith("請執行此步驟")
    assert prompt.rstrip().endswith(ctx.recitation)


def test_callable_executor_passes_feedback_and_attempt():
    """retry 時最近 feedback(step.notes[-1])與嘗試次數要帶進 ctx。"""
    state = _state()
    step = state.plan.steps[0]
    step.notes.append("缺少 URL 來源")
    step.attempts = 1
    seen: dict[str, ExecContext] = {}

    def fn(s, st, ctx):
        seen["ctx"] = ctx
        return StepOutput(content="ok")

    CallableExecutor(fn).execute(step, state)
    assert seen["ctx"].feedback == "缺少 URL 來源"
    assert seen["ctx"].attempt == 1


def test_recitation_disabled():
    state = _state()
    seen: dict[str, ExecContext] = {}

    def fn(s, st, ctx):
        seen["ctx"] = ctx
        return StepOutput(content="ok")

    CallableExecutor(fn, inject_recitation=False).execute(state.plan.steps[0], state)
    assert seen["ctx"].recitation == ""
    # 停用時 with_recitation 不改動 prompt
    assert seen["ctx"].with_recitation("P") == "P"


def test_callable_executor_rejects_non_stepoutput():
    state = _state()
    ex = CallableExecutor(lambda step, st, ctx: "不是 StepOutput")
    with pytest.raises(TypeError):
        ex.execute(state.plan.steps[0], state)
