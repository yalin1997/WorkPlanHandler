"""MCP gatekeeper bug 修復測試(B1 fail-open / B2 race)。

對映 docs/phase3/02-mcp-bugfix-plan.md:
  - B1:宣告了 server 擋不住的驗收層 → start 必須 fail-closed(raise);
        advisory 全局開關可正當關閉閘門;配置 soft 層後 llm_judge 步可真驗收。
  - B2:同 thread_id 並發 submit 不得 lost update;不同 thread_id 不互相阻塞。

全離線(soft 層用 stub judge),不需網路 / 真 key。
"""

from __future__ import annotations

import importlib.util
import threading
import time

import pytest

# 共用 stub judge(離線):pytest 會把 conftest.py 所在的 tests/ 加入 sys.path。
from stub_chat_model import StubChatModel

from workplan.adapters.mcp_server import Gatekeeper, _is_gated
from workplan.models import AcceptanceCriterion
from workplan.protocols import VerificationResult
from workplan.stores.json_store import JsonFilePlanStore
from workplan.verifiers import LayeredVerifier
from workplan.verifiers.builtin_checks import BUILTIN_CHECKS
from workplan.verifiers.programmatic import ProgrammaticVerifier

# soft 層測試需 LLMJudgeVerifier(llm extra → langchain);未裝時只 skip 那兩個
# 測試,B1 能力校驗 / advisory / B2 並發等純邏輯測試在純核心(core)環境照跑。
_HAS_LLM = importlib.util.find_spec("langchain") is not None
_requires_llm = pytest.mark.skipif(
    not _HAS_LLM, reason="需要 llm extra:pip install 'workplan[llm]'"
)


def _hard_only_gk(tmp_path, **kw):
    return Gatekeeper(store=JsonFilePlanStore(root=tmp_path), **kw)


def _llm_judge_step():
    return [
        {
            "id": "s1",
            "description": "寫一段分析",
            "acceptance": {
                "description": "品質達標",
                "kind": "llm_judge",
                "layer": "soft",
                "spec": {"rubric": "內容須完整"},
                "threshold": 0.7,
            },
        }
    ]


def _human_step():
    return [
        {
            "id": "s1",
            "description": "發佈到正式環境",
            "acceptance": {
                "description": "需人工核可",
                "kind": "human",
                "layer": "human",
                "spec": {},
            },
        }
    ]


# ── B1:fail-closed 能力校驗 ────────────────────────────────────────────────


def test_start_raises_when_llm_judge_declared_but_no_soft_layer(tmp_path):
    """宣告 llm_judge 但 server 無 soft 層 → start 必須 raise(不得假閘門開跑)。"""
    gk = _hard_only_gk(tmp_path)  # 預設僅 hard 層
    with pytest.raises(ValueError, match="soft"):
        gk.start("g", _llm_judge_step())


def test_start_raises_when_human_declared_but_no_human_layer(tmp_path):
    """宣告 human 但 server 無 human 層 → start 必須 raise。"""
    gk = _hard_only_gk(tmp_path)
    with pytest.raises(ValueError, match="human"):
        gk.start("g", _human_step())


def test_hard_programmatic_without_check_does_not_raise_only_warns(tmp_path):
    """programmatic 但無具名 check:非『擋不住的層』,只 warning 不 raise(守 I5)。"""
    gk = _hard_only_gk(tmp_path)
    res = gk.start(
        "g", [{"id": "s1", "description": "無驗收", "acceptance": {"spec": {}}}]
    )
    assert res["warnings"]  # 仍誠實標示無閘門


# ── B1:advisory 全局開關 ──────────────────────────────────────────────────


def test_advisory_mode_always_advances_without_verification(tmp_path):
    """advisory:不跑 check、may_advance 恆 true、warnings 標明 advisory。"""
    gk = _hard_only_gk(tmp_path, mode="advisory")
    spec = [
        {
            "id": "s1",
            "description": "step1",
            "acceptance": {"spec": {"check": "min_words", "args": {"n": 100}}},
        },
        {"id": "s2", "description": "step2", "acceptance": {"spec": {}}},
    ]
    res = gk.start("g", spec)
    assert any("advisory" in w for w in res["warnings"])
    # 即使遠不到 100 字,advisory 仍放行
    v = gk.submit(res["thread_id"], "短")
    assert v["may_advance"] is True
    assert v["result"] == "advanced"
    assert any("advisory" in w for w in v.get("warnings", []))


def test_advisory_skips_capability_validation(tmp_path):
    """advisory 是 operator 有意識關閘門 → 即使宣告 llm_judge 也不 raise。"""
    gk = _hard_only_gk(tmp_path, mode="advisory")
    res = gk.start("g", _llm_judge_step())  # 不應 raise
    assert res["thread_id"]


# ── B1:配置 soft 層後 llm_judge 真驗收 ────────────────────────────────────


def _soft_gk(tmp_path, *, verdict_passed=True):
    from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier

    judge = LLMJudgeVerifier(
        model=StubChatModel(
            JudgeVerdict(
                score=0.9 if verdict_passed else 0.1,
                passed=verdict_passed,
                feedback="" if verdict_passed else "未達標",
            )
        )
    )
    verifier = LayeredVerifier(
        [
            ("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True),
            ("soft", judge, True),
        ]
    )
    return Gatekeeper(store=JsonFilePlanStore(root=tmp_path), verifier=verifier)


@_requires_llm
def test_soft_layer_enables_llm_judge_step(tmp_path):
    """配置 soft 層(stub judge)後,llm_judge 步可正常 start 並過驗收。"""
    gk = _soft_gk(tmp_path, verdict_passed=True)
    res = gk.start("g", _llm_judge_step())  # 不再 raise
    v = gk.submit(res["thread_id"], "一段足夠完整的分析內容")
    assert v["result"] == "done"
    assert v["may_advance"] is True


@_requires_llm
def test_soft_layer_fail_blocks(tmp_path):
    """soft 層判不過 → 不放行(真的有跑驗收,非 fail-open)。"""
    gk = _soft_gk(tmp_path, verdict_passed=False)
    res = gk.start("g", _llm_judge_step())
    v = gk.submit(res["thread_id"], "內容")
    assert v["may_advance"] is False
    assert v["result"] in ("retry", "replan_needed", "escalated")


# ── B1:_is_gated 能力感知(單元) ─────────────────────────────────────────


def _crit(**kw):
    kw.setdefault("description", "x")
    return AcceptanceCriterion(**kw)


def test_is_gated_false_for_llm_judge_without_capability():
    crit = _crit(kind="llm_judge", layer="soft", spec={"rubric": "x"})
    assert _is_gated(crit, {"hard"}) is False


def test_is_gated_true_for_llm_judge_with_soft_capability():
    crit = _crit(kind="llm_judge", layer="soft", spec={"rubric": "x"})
    assert _is_gated(crit, {"hard", "soft"}) is True


def test_is_gated_programmatic_needs_named_check():
    has = _crit(kind="programmatic", layer="hard", spec={"check": "non_empty"})
    none = _crit(kind="programmatic", layer="hard", spec={})
    assert _is_gated(has, {"hard"}) is True
    assert _is_gated(none, {"hard"}) is False


# ── B2:read-modify-write race ─────────────────────────────────────────────


class _SlowFailVerifier:
    """always-fail 且 verify 內 sleep,放大 load→save 空窗以暴露 race。"""

    def verify(self, step, output, state):  # noqa: ARG002
        time.sleep(0.05)
        return VerificationResult(
            passed=False, score=0.0, feedback="never", layer="hard"
        )


def test_concurrent_submit_same_thread_no_lost_update(tmp_path):
    """同 thread 並發兩個失敗 submit:attempts 應精確 +2(全程持鎖無 lost update)。"""
    gk = Gatekeeper(
        store=JsonFilePlanStore(root=tmp_path),
        verifier=_SlowFailVerifier(),
        max_replans=0,
    )
    spec = [
        {
            "id": "s1",
            "description": "難關",
            "max_attempts": 5,
            "acceptance": {"spec": {"check": "non_empty"}},
        }
    ]
    tid = gk.start("g", spec)["thread_id"]

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def worker():
        try:
            barrier.wait()
            gk.submit(tid, "x")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    attempts = gk.plan(tid)["steps"][0]["attempts"]
    assert attempts == 2, f"lost update:attempts={attempts}(期望 2)"


def test_concurrent_different_threads_not_blocked(tmp_path):
    """不同 thread_id 並發互不干擾,皆正確完成。"""
    gk = Gatekeeper(store=JsonFilePlanStore(root=tmp_path))
    spec = [
        {
            "id": "s1",
            "description": "step",
            "acceptance": {"spec": {"check": "non_empty"}},
        }
    ]
    tids = [gk.start(f"g{i}", spec)["thread_id"] for i in range(4)]
    results: dict[str, str] = {}

    def worker(tid):
        results[tid] = gk.submit(tid, "有內容")["result"]

    threads = [threading.Thread(target=worker, args=(t,)) for t in tids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(r == "done" for r in results.values())
    assert len(results) == 4
