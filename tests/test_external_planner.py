"""M4:ExternalPlanner 單測(D1 ingest,零依賴——不需 langchain)。

涵蓋:make_plan 原樣回傳注入的 plan、缺 acceptance 防呆 raise(I5)、
無 replanner 時 replan raise ReplanNotSupported(escalate 訊號)、
有 replanner 時委派。
"""

from __future__ import annotations

import pytest

from workplan.errors import ReplanNotSupported
from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.planners import ExternalPlanner
from workplan.planners.mock import MockPlanner
from workplan.protocols import VerificationResult


def _crit():
    return AcceptanceCriterion(description="d", kind="llm_judge", spec={"rubric": "r"})


def _plan(*ids: str) -> Plan:
    return Plan(
        goal="g", steps=[Step(id=i, description=i, acceptance=_crit()) for i in ids]
    )


def test_make_plan_returns_injected_plan_verbatim():
    plan = _plan("s1", "s2")
    ext = ExternalPlanner(plan)
    out = ext.make_plan("ignored goal", {"ignored": "ctx"})
    assert out is plan  # 原樣(同一物件)回傳


def test_make_plan_rejects_step_without_acceptance():
    bad = Plan(goal="g", steps=[Step(id="s1", description="x", acceptance=None)])
    ext = ExternalPlanner(bad)
    with pytest.raises(ValueError, match="AcceptanceCriterion"):
        ext.make_plan("g", {})


def test_replan_without_replanner_raises_escalate_signal():
    ext = ExternalPlanner(_plan("s1"))
    state = PlanState(plan=_plan("s1"))
    with pytest.raises(ReplanNotSupported):
        ext.replan(state, VerificationResult(passed=False, feedback="f"))


def test_replan_delegates_when_replanner_injected():
    tail = _plan("s2new")
    inner = MockPlanner(tails=[tail])
    ext = ExternalPlanner(_plan("s1"), replanner=inner)
    state = PlanState(plan=_plan("s1"))
    failure = VerificationResult(passed=False, feedback="f")
    out = ext.replan(state, failure)
    assert out is tail
    assert inner.replan_calls == [failure]  # 確實委派
