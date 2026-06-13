"""ExternalPlanner(D1:外部計劃 ingest,規格 04)。

某些使用情境下,計劃由**外部 planner agent**(本模組之外)產生,
WorkPlanHandler 只負責「照計劃執行 + 逐階段驗收」。此類別把一份已備妥的
``Plan`` 原樣接入執行迴圈:

  - ``make_plan``:忽略 goal/context,原樣回傳注入的 plan(I5 仍要求每步自帶
    ``AcceptanceCriterion``——這裡做一次防呆檢查,缺則 raise,以免下游 engine
    在 ``_start_current_step`` 才炸)。
  - ``replan``:外部 planner 不一定支援重規劃。若建構時注入了 ``replanner``
    (任何實作 ``replan`` 的 Planner)則委派;否則 raise ``ReplanNotSupported``。
    此例外即「交人(escalate)」的訊號——語意等同「retry 用盡且無重規劃能力」;
    由 adapter 攔截並轉為 escalate(完整圖串接見後續里程碑,M4 僅定義訊號)。

本檔**零框架依賴**(不 import langchain),故可正常從 planners/__init__ export。
"""

from __future__ import annotations

from typing import Any

from ..errors import ReplanNotSupported
from ..models import Plan, PlanState
from ..protocols import Planner, VerificationResult


class ExternalPlanner:
    """把外部產生的 Plan 接入執行迴圈(D1)。

    Args:
        plan: 外部 planner 已產好的計劃(make_plan 原樣回傳)。
        replanner: 可選;支援重規劃時委派給它,否則 replan 觸發 ReplanNotSupported。
    """

    def __init__(self, plan: Plan, *, replanner: Planner | None = None) -> None:
        self._plan = plan
        self._replanner = replanner

    def make_plan(self, goal: str, context: dict[str, Any]) -> Plan:
        missing = [s.id for s in self._plan.steps if s.acceptance is None]
        if missing:  # I5:驗收條件必須先於執行存在
            raise ValueError(
                f"注入的 Plan 有步驟缺 AcceptanceCriterion:{missing}"
                "(I5:驗收條件須於規劃期備妥)"
            )
        return self._plan

    def replan(self, state: PlanState, failure: VerificationResult) -> Plan:
        if self._replanner is None:
            raise ReplanNotSupported(
                "ExternalPlanner 未注入 replanner,無法重規劃;此步將交人工處理(escalate)"
            )
        return self._replanner.replan(state, failure)
