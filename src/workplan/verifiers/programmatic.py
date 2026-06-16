"""ProgrammaticVerifier(hard 層,規格 03 §3.1)。

跑使用者提供的判定函式:``criterion.spec["check"]`` 是

  - Callable:直接呼叫(僅限不經 adapter 持久化的場景——callable 進了
    spec 就無法 JSON 序列化,違反 I2);或
  - str:從建構時注入的 ``checks`` 註冊表查名(name/ref)。**經 LangGraph
    adapter 持久化時必須用這種形式**,spec 才保持純 JSON。

check 簽名:``check(output: StepOutput, state: PlanState)``,回傳
``bool`` 或 ``(passed, score, feedback)``。raise / 查無 check / spec 缺
"check" 一律視為 fail(fail-closed,規格 03 §5:放錯比擋錯成本高)。
"""

from __future__ import annotations

from typing import Callable, Union

from ..models import PlanState, Step
from ..protocols import StepOutput, VerificationResult

CheckResult = Union[bool, tuple[bool, float, str]]
Check = Callable[[StepOutput, PlanState], CheckResult]


def _fail(feedback: str) -> VerificationResult:
    return VerificationResult(passed=False, score=0.0, feedback=feedback, layer="hard")


class ProgrammaticVerifier:
    """hard 驗收:最便宜、最可信,LayeredVerifier 中永遠排第一(D10)。

    Args:
        checks: 名稱 → check 函式的註冊表,供 spec["check"] 以 str 引用。
    """

    def __init__(self, checks: dict[str, Check] | None = None) -> None:
        self.checks = dict(checks or {})

    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult:
        ref = step.acceptance.spec.get("check")
        if ref is None:
            return _fail(
                f"步驟 {step.id} 的 criterion.spec 缺 'check',hard 層無從判定"
                "(fail-closed);請在規劃期提供判定函式或註冊名(I5)"
            )
        check: Check | None = self.checks.get(ref) if isinstance(ref, str) else ref
        if check is None:
            return _fail(
                f"spec['check']={ref!r} 不在註冊表中(已註冊:"
                f"{sorted(self.checks)});fail-closed 不放行"
            )
        try:
            result = check(output, state)
        except Exception as exc:  # 規格 03 §5:check raise → fail + 例外入 feedback
            return _fail(f"check 執行時拋出例外:{exc!r}")

        if isinstance(result, bool):
            passed, score, feedback = result, (1.0 if result else 0.0), ""
        else:
            passed, score, feedback = result
        if not passed and not feedback:  # 契約:fail 時 feedback 必填且可行動
            feedback = f"hard check 未通過(條件:{step.acceptance.description})"
        return VerificationResult(
            passed=passed, score=score, feedback=feedback, layer="hard"
        )
