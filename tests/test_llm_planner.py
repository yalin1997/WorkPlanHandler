"""M4:LLMPlanner 單測(stub chat model,離線確定性,不燒 key)。

涵蓋:make_plan 解析出帶 acceptance 的 steps(kind=llm_judge/soft、rubric 入
spec)、缺 acceptance 補預設 + warning(I5)、replan 只回尾巴且新步用全新 id +
附 revision_note、回傳能被 engine.on_replanned 接受(不覆寫 DONE,I1)。

依賴 langchain(llm extra);未裝時整檔 skip。
"""

from __future__ import annotations

import pytest
from stub_chat_model import StubChatModel

from workplan import engine
from workplan.models import Plan, PlanState, Step, StepStatus
from workplan.protocols import VerificationResult

pytest.importorskip("langchain", reason="需要 llm extra:pip install 'workplan[llm]'")

from workplan.planners.llm_planner import (  # noqa: E402
    DEFAULT_PLANNER_MODEL,
    AcceptanceDraft,
    LLMPlanner,
    PlanDraft,
    ReplanDraft,
    StepDraft,
)


def make_plan_draft() -> PlanDraft:
    return PlanDraft(
        steps=[
            StepDraft(
                description="蒐集競品資料",
                acceptance=AcceptanceDraft(
                    rubric="至少三家競品含來源", threshold=0.8, required=True
                ),
            ),
            StepDraft(
                description="撰寫摘要",
                acceptance=AcceptanceDraft(rubric="一頁摘要含結論"),
            ),
        ]
    )


def test_make_plan_parses_steps_with_acceptance():
    planner = LLMPlanner(model=StubChatModel(make_plan_draft()))
    plan = planner.make_plan("產出市場分析", {"deadline": "明天"})
    assert isinstance(plan, Plan)
    assert [s.id for s in plan.steps] == ["s1", "s2"]
    s1 = plan.steps[0]
    assert s1.acceptance.kind == "llm_judge"
    assert s1.acceptance.layer == "soft"
    assert s1.acceptance.spec["rubric"] == "至少三家競品含來源"
    assert s1.acceptance.threshold == 0.8
    assert planner.last_warnings == []  # 都自帶驗收,無警告


def test_missing_acceptance_gets_default_and_warning():
    draft = PlanDraft(steps=[StepDraft(description="無驗收的步驟", acceptance=None)])
    planner = LLMPlanner(model=StubChatModel(draft))
    plan = planner.make_plan("g", {})
    crit = plan.steps[0].acceptance
    assert crit.kind == "llm_judge" and crit.layer == "soft"
    assert crit.spec["rubric"]  # 補了預設 rubric(回退用 description)
    assert planner.last_warnings  # I5:記了 warning
    assert any("planner-warning" in n for n in plan.steps[0].notes)  # 留審計痕跡


def test_every_step_has_acceptance_for_i5():
    """I5:make_plan 回傳的每個 step 都必須自帶 AcceptanceCriterion。"""
    planner = LLMPlanner(model=StubChatModel(make_plan_draft()))
    plan = planner.make_plan("g", {})
    assert all(s.acceptance is not None for s in plan.steps)


def _state_with_one_done() -> PlanState:
    """s1 已 DONE、s2 卡住的執行狀態(供 replan)。"""
    s1 = Step(id="s1", description="已完成步", acceptance=_dummy_crit())
    s1.status = StepStatus.DONE
    s2 = Step(id="s2", description="卡住步", acceptance=_dummy_crit())
    s2.status = StepStatus.FAILED
    plan = Plan(goal="總目標", steps=[s1, s2], version=1)
    return PlanState(plan=plan, cursor=1, status="running")


def _dummy_crit():
    from workplan.models import AcceptanceCriterion

    return AcceptanceCriterion(description="d", kind="llm_judge", spec={"rubric": "r"})


def test_replan_returns_fresh_tail_with_note():
    state = _state_with_one_done()
    draft = ReplanDraft(
        revision_note="改用替代資料源",
        steps=[
            StepDraft(description="新尾巴步A", acceptance=AcceptanceDraft(rubric="A")),
            StepDraft(description="新尾巴步B", acceptance=AcceptanceDraft(rubric="B")),
        ],
    )
    planner = LLMPlanner(model=StubChatModel({"PlanDraft": None, "ReplanDraft": draft}))
    failure = VerificationResult(passed=False, feedback="資料源失效")
    tail = planner.replan(state, failure)

    assert tail.revision_note == "改用替代資料源"
    existing = {"s1", "s2"}
    assert all(s.id not in existing for s in tail.steps)  # D6:全新 id
    assert len({s.id for s in tail.steps}) == 2  # 彼此不撞


def test_replan_result_accepted_by_engine_preserving_done():
    """replan 產物餵 engine.on_replanned:保留 DONE 前綴、version++,不 raise(I1)。"""
    state = _state_with_one_done()
    draft = ReplanDraft(
        revision_note="r",
        steps=[StepDraft(description="新步", acceptance=AcceptanceDraft(rubric="x"))],
    )
    planner = LLMPlanner(model=StubChatModel({"PlanDraft": None, "ReplanDraft": draft}))
    tail = planner.replan(state, VerificationResult(passed=False, feedback="f"))

    dec = engine.on_replanned(state, tail)
    assert dec.action == engine.Action.EXECUTE
    assert state.plan.version == 2
    assert state.plan.steps[0].id == "s1"  # DONE 前綴保留
    assert state.plan.steps[0].status == StepStatus.DONE
    assert state.cursor == 1  # 指向第一個未完成(新)步


def test_default_planner_model_differs_from_judge():
    assert "sonnet" in DEFAULT_PLANNER_MODEL  # 較強模型(judge 用 haiku)
