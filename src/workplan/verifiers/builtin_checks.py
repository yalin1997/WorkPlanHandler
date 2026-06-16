"""宣告式 check 註冊表(MCP gatekeeper 的 hard 層,規格 03 §3.1 的具名版)。

agent 經 MCP/HTTP 送來的 plan 是 JSON,``AcceptanceCriterion.spec["check"]``
只能是**字串名**(不能傳 Python callable,否則不可序列化,違反 I2)。本檔提供
一組 server 端內建、以名字引用的宣告式 check,參數由 ``spec["args"]`` 攜帶,例如::

    {"check": "min_words", "args": {"n": 50}}
    {"check": "contains",  "args": {"all": ["結論", "風險"]}}

check 簽名沿用 ProgrammaticVerifier 的契約 ``check(output, state) -> (passed,
score, feedback)``;**參數缺失/型別錯一律 fail-closed**(回 False,不放行;規格
03 §5:放錯比擋錯成本高)。純函式、零框架依賴(core 檔)。
"""

from __future__ import annotations

import json
import re

from ..models import PlanState
from ..protocols import StepOutput
from .programmatic import Check, CheckResult


def _content(output: StepOutput) -> str:
    return "" if output.content is None else str(output.content)


def _args(state: PlanState) -> dict:
    step = state.current_step
    if step is None:
        return {}
    args = step.acceptance.spec.get("args", {})
    return dict(args) if isinstance(args, dict) else {}


def _pos_int(args: dict, key: str = "n") -> int | None:
    """取非負整數參數;非整數(含 bool)或負數回 None(交由呼叫端 fail-closed)。"""
    val = args.get(key)
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        return None
    return val


def non_empty(output: StepOutput, state: PlanState) -> CheckResult:
    if _content(output).strip():
        return True, 1.0, ""
    return False, 0.0, "輸出為空;此步要求非空內容。"


def min_chars(output: StepOutput, state: PlanState) -> CheckResult:
    n = _pos_int(_args(state))
    if n is None:
        return False, 0.0, "min_chars 需在 spec.args.n 提供非負整數(fail-closed)。"
    length = len(_content(output).strip())
    if length >= n:
        return True, 1.0, ""
    return False, 0.0, f"內容 {length} 字元,未達最低 {n} 字元。"


def max_chars(output: StepOutput, state: PlanState) -> CheckResult:
    n = _pos_int(_args(state))
    if n is None:
        return False, 0.0, "max_chars 需在 spec.args.n 提供非負整數(fail-closed)。"
    length = len(_content(output).strip())
    if length <= n:
        return True, 1.0, ""
    return False, 0.0, f"內容 {length} 字元,超過上限 {n} 字元。"


def min_words(output: StepOutput, state: PlanState) -> CheckResult:
    n = _pos_int(_args(state))
    if n is None:
        return False, 0.0, "min_words 需在 spec.args.n 提供非負整數(fail-closed)。"
    count = len(_content(output).split())
    if count >= n:
        return True, 1.0, ""
    return False, 0.0, f"字數 {count},未達最低 {n} 字。"


def contains(output: StepOutput, state: PlanState) -> CheckResult:
    args = _args(state)
    text = _content(output)
    all_terms = args.get("all")
    any_terms = args.get("any")
    if not all_terms and not any_terms:
        return False, 0.0, "contains 需在 spec.args 提供 'all' 或 'any' 字串清單。"
    if all_terms is not None:
        if not isinstance(all_terms, list):
            return False, 0.0, "contains.all 必須是字串清單(fail-closed)。"
        missing = [t for t in all_terms if str(t) not in text]
        if missing:
            return False, 0.0, f"缺少必含關鍵字:{missing}。"
    if any_terms is not None:
        if not isinstance(any_terms, list):
            return False, 0.0, "contains.any 必須是字串清單(fail-closed)。"
        if not any(str(t) in text for t in any_terms):
            return False, 0.0, f"需至少包含其一:{any_terms}。"
    return True, 1.0, ""


def regex_match(output: StepOutput, state: PlanState) -> CheckResult:
    pattern = _args(state).get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return (
            False,
            0.0,
            "regex_match 需在 spec.args.pattern 提供非空 regex(fail-closed)。",
        )
    try:
        rx = re.compile(pattern, re.DOTALL)
    except re.error as exc:
        return False, 0.0, f"regex 無法編譯(fail-closed):{exc}"
    if rx.search(_content(output)):
        return True, 1.0, ""
    return False, 0.0, f"內容未匹配 regex:{pattern!r}。"


def json_valid(output: StepOutput, state: PlanState) -> CheckResult:
    try:
        parsed = json.loads(_content(output))
    except (ValueError, TypeError) as exc:
        return False, 0.0, f"輸出非合法 JSON(fail-closed):{exc}"
    require_keys = _args(state).get("require_keys")
    if require_keys:
        if not isinstance(parsed, dict):
            return False, 0.0, "指定 require_keys 時,JSON 頂層必須是物件。"
        missing = [k for k in require_keys if k not in parsed]
        if missing:
            return False, 0.0, f"JSON 缺少必含鍵:{missing}。"
    return True, 1.0, ""


BUILTIN_CHECKS: dict[str, Check] = {
    "non_empty": non_empty,
    "min_chars": min_chars,
    "max_chars": max_chars,
    "min_words": min_words,
    "contains": contains,
    "regex_match": regex_match,
    "json_valid": json_valid,
}
