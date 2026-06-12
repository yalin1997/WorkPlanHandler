"""MockExecutor(D3 首發,規格 04 §B.3)。

依 step.id 從預設腳本回傳輸出;可程式設定「前 N 次嘗試回執行錯誤」
以驗 retry/replan 路徑(規格 02 T2/T3)。無副作用,天然冪等。
"""

from __future__ import annotations

from typing import Any

from ..models import PlanState, Step
from ..protocols import StepOutput


class MockExecutor:
    """可腳本化的 mock executor。

    Args:
        script: step_id → 成功時回傳的 content;未列入者回預設字串。
        fail_first: step_id → 前 N 次呼叫回傳執行期錯誤(StepOutput.error)。
    """

    def __init__(
        self,
        script: dict[str, Any] | None = None,
        fail_first: dict[str, int] | None = None,
    ) -> None:
        self.script = dict(script or {})
        self.fail_first = dict(fail_first or {})
        self.calls: dict[str, int] = {}  # step_id → 已被呼叫次數

    def execute(self, step: Step, state: PlanState) -> StepOutput:
        n = self.calls.get(step.id, 0) + 1
        self.calls[step.id] = n
        if n <= self.fail_first.get(step.id, 0):
            return StepOutput(
                content=None,
                error=f"mock 執行錯誤:step {step.id} 第 {n} 次嘗試",
            )
        content = self.script.get(step.id, f"mock output for {step.id}")
        return StepOutput(content=content)
