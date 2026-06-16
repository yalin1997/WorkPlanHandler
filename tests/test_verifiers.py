"""M3 DoD:分層驗收測試(規格 03 §6 測試案例)。

重點:hard 失敗短路(soft 層呼叫次數 == 0,驗省 token)、needs_human
短路、advisory 不擋推進、fail-closed。全 mock、不需 langgraph / LLM。
"""

from __future__ import annotations

import pytest
from conftest import make_step

from workplan.models import AcceptanceCriterion, Step
from workplan.protocols import StepOutput, VerificationResult
from workplan.verifiers import (
    HumanGateVerifier,
    LayeredVerifier,
    MockVerifier,
    ProgrammaticVerifier,
)
from workplan.verifiers.mock import failed, passed


def hard_step(step_id: str = "s1", check: object = None) -> Step:
    """帶 hard 驗收(spec 含 check)的步驟。"""
    return Step(
        id=step_id,
        description=f"步驟 {step_id}",
        acceptance=AcceptanceCriterion(
            description=f"{step_id} 完成",
            kind="programmatic",
            spec={"check": check if check is not None else "nonempty"},
            layer="hard",
        ),
    )


def human_step(step_id: str = "s9") -> Step:
    return Step(
        id=step_id,
        description=f"步驟 {step_id}",
        acceptance=AcceptanceCriterion(
            description=f"{step_id} 需人工放行", kind="human", layer="human"
        ),
    )


class CountingVerifier:
    """包一層 verifier 並計呼叫次數(驗短路省成本用)。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    def verify(self, step, output, state):
        self.calls += 1
        return self.inner.verify(step, output, state)


# ---------------------------------------------------------------- V1
def test_programmatic_callable_pass_and_fail():
    v = ProgrammaticVerifier()
    step = hard_step(check=lambda out, st: (len(str(out.content)) >= 3, 1.0, ""))

    ok = v.verify(step, StepOutput(content="夠長的輸出"), None)
    assert ok.passed is True
    assert ok.layer == "hard"

    ng = v.verify(step, StepOutput(content="短"), None)
    assert ng.passed is False
    assert ng.feedback  # 契約:fail 時 feedback 必填


# ---------------------------------------------------------------- V2
def test_programmatic_check_by_registry_name():
    v = ProgrammaticVerifier(
        checks={"nonempty": lambda out, st: bool(str(out.content or "").strip())}
    )
    step = hard_step(check="nonempty")

    assert v.verify(step, StepOutput(content="有料"), None).passed is True
    ng = v.verify(step, StepOutput(content="  "), None)
    assert ng.passed is False


# ---------------------------------------------------------------- V3
def test_programmatic_fail_closed_on_raise_missing_check_unknown_name():
    """check raise / spec 缺 check / 查無註冊名 → 一律 fail-closed。"""
    v = ProgrammaticVerifier()

    boom = hard_step(check=lambda out, st: 1 / 0)
    r = v.verify(boom, StepOutput(content="x"), None)
    assert r.passed is False
    assert "例外" in r.feedback

    no_check = make_step("s1")  # conftest 預設 criterion 無 spec["check"]
    r = v.verify(no_check, StepOutput(content="x"), None)
    assert r.passed is False
    assert "check" in r.feedback

    unknown = hard_step(check="不存在的名字")
    r = v.verify(unknown, StepOutput(content="x"), None)
    assert r.passed is False
    assert "不存在的名字" in r.feedback


# ---------------------------------------------------------------- V4
def test_human_gate_always_needs_human():
    r = HumanGateVerifier().verify(human_step(), StepOutput(content="x"), None)
    assert r.passed is False
    assert r.needs_human is True
    assert r.layer == "human"
    assert "待人工確認" in r.feedback


# ---------------------------------------------------------------- V5(短路省成本)
def test_layered_hard_fail_short_circuits_soft_not_called():
    soft = CountingVerifier(MockVerifier())
    layered = LayeredVerifier(
        layers=[
            ("hard", ProgrammaticVerifier(), True),
            ("soft", soft, True),
        ]
    )
    step = hard_step(check=lambda out, st: (False, 0.0, "缺必填欄位 price"))

    r = layered.verify(step, StepOutput(content="x"), None)

    assert r.passed is False
    assert r.layer == "hard"
    assert r.feedback == "缺必填欄位 price"
    assert soft.calls == 0  # 規格 03 §6:hard fail 時 judge 不被呼叫


# ---------------------------------------------------------------- V6
def test_layered_needs_human_only_after_earlier_layers_pass():
    hard = CountingVerifier(ProgrammaticVerifier())
    human = CountingVerifier(HumanGateVerifier())
    layered = LayeredVerifier(
        layers=[("human", human, False), ("hard", hard, True)]  # 故意亂序
    )
    step = human_step()
    step.acceptance.spec = {"check": lambda out, st: True}  # hard 也適用且會過

    r = layered.verify(step, StepOutput(content="x"), None)

    assert r.needs_human is True
    assert r.layer == "human"
    assert hard.calls == 1  # 排序生效:hard 先跑、通過後才輪到 human


# ---------------------------------------------------------------- V7
def test_layered_advisory_fail_does_not_block_but_keeps_feedback():
    layered = LayeredVerifier(
        layers=[
            ("hard", ProgrammaticVerifier(), True),
            ("soft", MockVerifier(default=failed("語氣可再正式些")), False),
        ]
    )
    step = hard_step(check=lambda out, st: (True, 1.0, ""))

    r = layered.verify(step, StepOutput(content="x"), None)

    assert r.passed is True  # advisory 不擋推進
    assert "advisory:soft" in r.feedback
    assert "語氣可再正式些" in r.feedback


# ---------------------------------------------------------------- V8
class ExplodingVerifier:
    def verify(self, step, output, state):
        raise RuntimeError("verifier 內部爆炸")


def test_layered_fail_closed_when_layer_raises():
    layered = LayeredVerifier(layers=[("soft", ExplodingVerifier(), True)])
    r = layered.verify(make_step("s1"), StepOutput(content="x"), None)
    assert r.passed is False
    assert "fail-closed" in r.feedback


# ---------------------------------------------------------------- V9
def test_layered_score_is_min_and_layers_skipped_by_criterion():
    """整體分數取 min;hard 層在無 check 的步驟自動跳過、human 層只對
    kind=='human' 的步驟生效(視步驟風險才掛)。"""
    hard = CountingVerifier(ProgrammaticVerifier())
    human = CountingVerifier(HumanGateVerifier())
    layered = LayeredVerifier(
        layers=[
            ("hard", hard, True),
            ("soft", MockVerifier(default=passed(score=0.7)), True),
            ("human", human, False),
        ]
    )
    plain = make_step("s1")  # 無 check、kind 預設 llm_judge

    r = layered.verify(plain, StepOutput(content="x"), None)

    assert r.passed is True
    assert r.score == 0.7  # 只有 soft 層計分
    assert hard.calls == 0  # 無 check → hard 層跳過(不 fail-closed 擋下)
    assert human.calls == 0  # kind != human → human 層不掛


# ---------------------------------------------------------------- V10
def test_layered_rejects_unknown_layer_name():
    with pytest.raises(ValueError):
        LayeredVerifier(layers=[("medium", MockVerifier(), True)])


# ---------------------------------------------------------------- V11
def test_layered_result_is_verification_result_contract():
    """整體回傳仍是 VerificationResult(可直接餵 engine.on_verified)。"""
    layered = LayeredVerifier(layers=[("soft", MockVerifier(), True)])
    r = layered.verify(make_step("s1"), StepOutput(content="x"), None)
    assert isinstance(r, VerificationResult)
    assert r.passed is True and r.score == 1.0
