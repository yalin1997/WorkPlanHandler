"""MockPlanner(供 M1 engine 測試用)。

make_plan 回傳預先給定的 Plan;replan 依序回傳預錄的「未完成尾巴」
(只含新尾巴步驟的 Plan,merge 由 engine.on_replanned 負責,D6)。
"""

from __future__ import annotations

from typing import Any

from ..models import Plan, PlanState
from ..protocols import VerificationResult


class MockPlanner:
    """可腳本化的 mock planner。

    Args:
        plan: make_plan 固定回傳的計劃。
        tails: 每次 replan 依序回傳的尾巴計劃(耗盡則 raise)。
    """

    def __init__(
        self, plan: Plan | None = None, tails: list[Plan] | None = None
    ) -> None:
        self.plan = plan
        self.tails = list(tails or [])
        self.replan_calls: list[VerificationResult | str] = []

    def make_plan(self, goal: str, context: dict[str, Any]) -> Plan:
        if self.plan is None:
            raise RuntimeError("MockPlanner 未設定 plan")
        return self.plan

    def replan(self, state: PlanState, failure: VerificationResult) -> Plan:
        self.replan_calls.append(failure)
        if not self.tails:
            raise RuntimeError("MockPlanner 的 replan 尾巴已耗盡")
        return self.tails.pop(0)
