"""M4:LLMJudgeVerifier 單測(stub chat model,離線確定性,不燒 key)。

涵蓋:固定 verdict → 判定正確、feedback 可行動、threshold 邊界(== pass)、
fail-closed(stub 拋例外 → passed=False)、非結構化回應 → fail、
最終 passed 以 threshold 重算(不直接信任模型的 passed)。

依賴 langchain(llm extra);未裝時整檔 skip,核心測試不受影響。
"""

from __future__ import annotations

import pytest
from stub_chat_model import StubChatModel

from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.protocols import StepOutput

pytest.importorskip("langchain", reason="需要 llm extra:pip install 'workplan[llm]'")

from workplan.verifiers.llm_judge import (  # noqa: E402
    DEFAULT_JUDGE_MODEL,
    JudgeVerdict,
    LLMJudgeVerifier,
)


def soft_step(threshold: float = 0.7, rubric: str = "輸出需含三個競品與來源") -> Step:
    return Step(
        id="s1",
        description="蒐集競品資料",
        acceptance=AcceptanceCriterion(
            description="完成競品蒐集",
            kind="llm_judge",
            spec={"rubric": rubric},
            threshold=threshold,
            layer="soft",
        ),
    )


def _state(step: Step) -> PlanState:
    return PlanState(plan=Plan(goal="g", steps=[step]))


def _verify(verifier: LLMJudgeVerifier, step: Step, content: str = "某輸出"):
    return verifier.verify(step, StepOutput(content=content), _state(step))


def test_pass_above_threshold():
    step = soft_step(threshold=0.7)
    verdict = JudgeVerdict(score=0.9, passed=True, feedback="很好")
    res = _verify(LLMJudgeVerifier(model=StubChatModel(verdict)), step)
    assert res.passed is True
    assert res.score == 0.9
    assert res.layer == "soft"


def test_fail_below_threshold_has_actionable_feedback():
    step = soft_step(threshold=0.7)
    verdict = JudgeVerdict(score=0.4, passed=False, feedback="缺第三家競品與來源")
    res = _verify(LLMJudgeVerifier(model=StubChatModel(verdict)), step)
    assert res.passed is False
    assert "競品" in res.feedback  # feedback 可行動


def test_threshold_boundary_equal_is_pass():
    step = soft_step(threshold=0.8)
    verdict = JudgeVerdict(score=0.8, passed=False, feedback="剛好達標")
    res = _verify(LLMJudgeVerifier(model=StubChatModel(verdict)), step)
    assert res.passed is True  # == 視為 pass(邊界)


def test_passed_recomputed_from_threshold_not_model_flag():
    """模型自報 passed=True 但分數低於門檻 → 仍判 fail(threshold 重算)。"""
    step = soft_step(threshold=0.7)
    verdict = JudgeVerdict(score=0.5, passed=True, feedback="模型過於樂觀")
    res = _verify(LLMJudgeVerifier(model=StubChatModel(verdict)), step)
    assert res.passed is False


def test_fail_closed_on_exception():
    step = soft_step()
    verifier = LLMJudgeVerifier(model=StubChatModel(raises=RuntimeError("timeout")))
    res = _verify(verifier, step)
    assert res.passed is False
    assert res.score == 0.0
    assert "judge 不可用" in res.feedback


def test_non_structured_response_is_fail():
    step = soft_step()
    verifier = LLMJudgeVerifier(model=StubChatModel(result=None))  # 非結構化
    res = _verify(verifier, step)
    assert res.passed is False
    assert "judge 不可用" in res.feedback


def test_rubric_falls_back_to_description_when_missing():
    """spec 無 rubric 時用 criterion.description(不應炸)。"""
    step = Step(
        id="s1",
        description="做某事",
        acceptance=AcceptanceCriterion(
            description="完成某事", kind="llm_judge", spec={}, threshold=0.5
        ),
    )
    verdict = JudgeVerdict(score=0.9, passed=True, feedback="ok")
    res = _verify(LLMJudgeVerifier(model=StubChatModel(verdict)), step)
    assert res.passed is True


def test_default_judge_model_is_lightweight():
    """預設 judge 模型與 planner 不同(spec 03 §3.2 減 self-preference)。"""
    assert "haiku" in DEFAULT_JUDGE_MODEL
