"""HumanGateVerifier(human 層,D8,規格 03 §3.3)。

直接回 ``needs_human=True``,把判定交給人:engine 收到後 `_escalate`
(標 blocked + pending_human),adapter 翻成 ``interrupt()`` 暫停等人,
人工裁決(approved/rejected/edited)經 ``on_human_resolved`` 回流。

用於高風險/不可逆步驟(付款、發佈、刪除)。注意:本 verifier **單獨使用
時對每一步都攔**;要「只攔高風險步驟」請掛進 LayeredVerifier——它只在
``step.acceptance.kind == "human"`` 的步驟啟用 human 層(見 base.py)。
"""

from __future__ import annotations

from ..models import PlanState, Step
from ..protocols import StepOutput, VerificationResult


class HumanGateVerifier:
    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult:
        return VerificationResult(
            passed=False,
            score=0.0,
            feedback=f"待人工確認:{step.acceptance.description}",
            needs_human=True,
            layer="human",
        )
