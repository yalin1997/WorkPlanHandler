"""宣告式 check 註冊表(BUILTIN_CHECKS)單測。

這些 check 是 MCP gatekeeper 的 hard 層:agent 的 plan 走 JSON,無法傳
Python callable,故以「具名 + spec.args」宣告;check 從
``state.current_step.acceptance.spec["args"]`` 讀參數。

驗收重點:pass/fail 行為正確;**參數缺失或型別錯一律 fail-closed**(不放行)。
"""

from __future__ import annotations

from workplan import StepOutput
from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.verifiers.builtin_checks import BUILTIN_CHECKS


def run_check(name: str, content, args: dict | None = None):
    spec: dict = {"check": name}
    if args is not None:
        spec["args"] = args
    crit = AcceptanceCriterion(
        description="d", kind="programmatic", layer="hard", spec=spec
    )
    step = Step(id="s1", description="d", acceptance=crit)
    state = PlanState(plan=Plan(goal="g", steps=[step]))
    return BUILTIN_CHECKS[name](StepOutput(content=content), state)


def test_registry_has_expected_names():
    assert {
        "non_empty",
        "min_chars",
        "max_chars",
        "min_words",
        "contains",
        "regex_match",
        "json_valid",
    } <= set(BUILTIN_CHECKS)


def test_non_empty():
    assert run_check("non_empty", "hello")[0] is True
    assert run_check("non_empty", "   ")[0] is False
    assert run_check("non_empty", None)[0] is False


def test_min_chars():
    assert run_check("min_chars", "abcde", {"n": 5})[0] is True
    passed, _, fb = run_check("min_chars", "abc", {"n": 5})
    assert passed is False and fb  # feedback 必填且可行動
    # fail-closed:缺 args / 型別錯
    assert run_check("min_chars", "abc", None)[0] is False
    assert run_check("min_chars", "abc", {"n": "five"})[0] is False


def test_max_chars():
    assert run_check("max_chars", "abc", {"n": 5})[0] is True
    assert run_check("max_chars", "abcdef", {"n": 3})[0] is False
    assert run_check("max_chars", "abc", None)[0] is False  # fail-closed


def test_min_words():
    assert run_check("min_words", "one two three", {"n": 3})[0] is True
    assert run_check("min_words", "one two", {"n": 3})[0] is False
    assert run_check("min_words", "x", {"n": -1})[0] is False  # fail-closed (負數)


def test_contains_all_and_any():
    assert run_check("contains", "alpha beta", {"all": ["alpha", "beta"]})[0] is True
    assert run_check("contains", "alpha beta", {"all": ["alpha", "gamma"]})[0] is False
    assert run_check("contains", "alpha beta", {"any": ["x", "beta"]})[0] is True
    assert run_check("contains", "alpha beta", {"any": ["x", "y"]})[0] is False
    # fail-closed:沒給 all 也沒給 any
    assert run_check("contains", "alpha", {})[0] is False
    assert run_check("contains", "alpha", None)[0] is False


def test_regex_match():
    assert run_check("regex_match", "abc123", {"pattern": r"\d{3}"})[0] is True
    assert run_check("regex_match", "abc", {"pattern": r"\d{3}"})[0] is False
    # fail-closed:壞 regex / 缺 pattern
    assert run_check("regex_match", "abc", {"pattern": "("})[0] is False
    assert run_check("regex_match", "abc", None)[0] is False


def test_json_valid():
    assert run_check("json_valid", '{"a": 1}')[0] is True
    assert run_check("json_valid", "not json")[0] is False  # fail-closed
    assert run_check("json_valid", '{"a": 1}', {"require_keys": ["a"]})[0] is True
    assert run_check("json_valid", '{"a": 1}', {"require_keys": ["a", "b"]})[0] is False


def test_integrates_with_layered_programmatic_verifier():
    """確認可直接餵進 server 端 LayeredVerifier(hard 層)。"""
    from workplan.verifiers import LayeredVerifier
    from workplan.verifiers.programmatic import ProgrammaticVerifier

    spec = {"check": "min_words", "args": {"n": 2}}
    crit = AcceptanceCriterion(
        description="至少兩字", kind="programmatic", layer="hard", spec=spec
    )
    step = Step(id="s1", description="d", acceptance=crit)
    state = PlanState(plan=Plan(goal="g", steps=[step]))
    verifier = LayeredVerifier([("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True)])

    ok = verifier.verify(step, StepOutput(content="one two three"), state)
    assert ok.passed
    bad = verifier.verify(step, StepOutput(content="one"), state)
    assert not bad.passed and bad.feedback
