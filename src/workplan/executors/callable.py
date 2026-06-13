"""CallableExecutor(通用橋接,規格 04 §B.3)。

把任意使用者函式包成 ``Executor``,讓使用者用自己的工具 / 子 agent / LLM
落地執行,而不必懂模組內部。**LLM 接線留在使用者函式**——核心因此維持
零依賴(D9):真 LLM executor 的 demo 在 ``examples/`` 內注入會呼叫
``ChatAnthropic`` 等的函式,而本檔不 import 任何框架。

executor 層的兩個責任(規格 04 §B.3)由本類別代為履行,使用者函式只需專注
產出內容:

  - **recitation 注入(survey §4.1)**:把 ``plan.render_for_recitation()``
    經 ``ExecContext`` 交給使用者函式,並提供 ``ctx.with_recitation(prompt)``
    把計劃摘要接到 prompt 尾端,對抗長 context 目標漂移。
  - **retry feedback(Reflexion 式)**:engine 觸發 RETRY 時 feedback 已寫在
    ``step.notes[-1]``;本類別把它取出放進 ``ctx.feedback`` 供修正。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import PlanState, Step
from ..protocols import StepOutput

_RECITATION_HEADER = "--- 計劃摘要(請持續遵循)---"


@dataclass
class ExecContext:
    """交給使用者函式的執行脈絡(executor 已備妥的注入資訊)。"""

    recitation: str  # plan.render_for_recitation();停用注入時為空字串
    feedback: str  # 最近一次 retry feedback(step.notes[-1]),無則空字串
    attempt: int  # 目前嘗試次數(step.attempts;首次為 0)

    def with_recitation(self, prompt: str) -> str:
        """把計劃摘要接到 prompt 尾端(survey §4.1 recitation 模式)。

        無 recitation(停用或空)時原樣回傳。
        """
        if not self.recitation:
            return prompt
        return f"{prompt}\n\n{_RECITATION_HEADER}\n{self.recitation}"


# 使用者函式簽名:fn(step, state, ctx) -> StepOutput
ExecFn = Callable[[Step, PlanState, ExecContext], StepOutput]


class CallableExecutor:
    """把 ``fn(step, state, ctx) -> StepOutput`` 包成 Executor。

    Args:
        fn: 使用者執行函式;收 step、唯讀 state 與備妥的 ``ExecContext``。
        inject_recitation: 是否計算並注入 recitation(預設 True)。
    """

    def __init__(self, fn: ExecFn, *, inject_recitation: bool = True) -> None:
        self._fn = fn
        self._inject = inject_recitation

    def execute(self, step: Step, state: PlanState) -> StepOutput:
        recitation = state.plan.render_for_recitation() if self._inject else ""
        feedback = step.notes[-1] if step.notes else ""
        ctx = ExecContext(
            recitation=recitation, feedback=feedback, attempt=step.attempts
        )
        result = self._fn(step, state, ctx)
        if not isinstance(result, StepOutput):
            raise TypeError(
                "CallableExecutor 的 fn 必須回傳 StepOutput,"
                f"實得 {type(result).__name__}"
            )
        return result
