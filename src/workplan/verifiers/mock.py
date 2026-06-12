"""MockVerifier(供 M1 engine 測試用)。

可程式控制每個 step 的 pass/fail 序列:script[step_id] 是
VerificationResult 佇列,依呼叫順序消耗;耗盡或未列入者回預設結果。
"""
from __future__ import annotations

from ..models import PlanState, Step
from ..protocols import StepOutput, VerificationResult


def passed(score: float = 1.0, feedback: str = "") -> VerificationResult:
    return VerificationResult(passed=True, score=score, feedback=feedback)


def failed(feedback: str = "未達驗收標準", score: float = 0.0) -> VerificationResult:
    return VerificationResult(passed=False, score=score, feedback=feedback)


def needs_human(reason: str = "human gate") -> VerificationResult:
    return VerificationResult(
        passed=False, feedback=reason, needs_human=True, layer="human"
    )


class MockVerifier:
    """可腳本化的 mock verifier。

    Args:
        script: step_id → 依序回傳的 VerificationResult 佇列。
        default: 佇列耗盡 / 未列入 step 時回傳的結果(預設 pass)。
    """

    def __init__(
        self,
        script: dict[str, list[VerificationResult]] | None = None,
        default: VerificationResult | None = None,
    ) -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = default or passed()

    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult:
        queue = self.script.get(step.id)
        if queue:
            return queue.pop(0)
        return self.default
