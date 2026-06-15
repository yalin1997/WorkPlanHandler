"""MCP gatekeeper 邏輯層(Gatekeeper)離線單測。

不經網路、不需 fastmcp:直接驅動 tool 邏輯,跑完設計圖 §5 sequence
(pass / retry→pass / escalate→resolve / replan),全用宣告式 check。
真 HTTP 端到端煙霧測另見 test_mcp_server_http.py(@slow)。
"""

from __future__ import annotations

from workplan import engine
from workplan.adapters.mcp_server import Gatekeeper
from workplan.stores.json_store import JsonFilePlanStore


def _gk(tmp_path, **kw):
    return Gatekeeper(store=JsonFilePlanStore(root=tmp_path), **kw)


def _plan_spec():
    return [
        {
            "id": "s1",
            "description": "蒐集競品資料",
            "acceptance": {
                "description": "至少 3 字",
                "layer": "hard",
                "kind": "programmatic",
                "spec": {"check": "min_words", "args": {"n": 3}},
            },
        },
        {
            "id": "s2",
            "description": "提出策略建議",
            "acceptance": {
                "description": "需含『建議』",
                "layer": "hard",
                "kind": "programmatic",
                "spec": {"check": "contains", "args": {"all": ["建議"]}},
            },
        },
    ]


def test_start_returns_first_step(tmp_path):
    gk = _gk(tmp_path)
    res = gk.start("市場分析", _plan_spec())
    assert res["thread_id"]
    assert res["current_step"]["step_id"] == "s1"
    assert "GOAL" in res["recitation"]


def test_submit_pass_advances_to_next_step(tmp_path):
    gk = _gk(tmp_path)
    tid = gk.start("市場分析", _plan_spec())["thread_id"]
    v = gk.submit(tid, "競品 A B C 三家")
    assert v["result"] == "advanced"
    assert v["passed"] is True and v["may_advance"] is True
    assert v["next_step"]["step_id"] == "s2"


def test_submit_fail_blocks_with_feedback(tmp_path):
    """弱輸出 → retry,may_advance=False,且拿不到下一步(資訊槓桿)。"""
    gk = _gk(tmp_path)
    tid = gk.start("市場分析", _plan_spec())["thread_id"]
    v = gk.submit(tid, "太短")  # < 3 字
    assert v["result"] == "retry"
    assert v["may_advance"] is False
    assert v["feedback"]
    # 仍卡在 s1
    assert gk.current(tid)["step"]["step_id"] == "s1"


def test_full_run_to_done(tmp_path):
    gk = _gk(tmp_path)
    tid = gk.start("市場分析", _plan_spec())["thread_id"]
    gk.submit(tid, "競品 A B C 三家")  # s1 pass → s2
    v = gk.submit(tid, "我的策略建議如下")  # s2 含「建議」
    assert v["result"] == "done"
    assert v["may_advance"] is True
    assert gk.current(tid)["status"] == "done"


def test_retry_then_pass(tmp_path):
    gk = _gk(tmp_path)
    tid = gk.start("市場分析", _plan_spec())["thread_id"]
    assert gk.submit(tid, "短")["result"] == "retry"
    v = gk.submit(tid, "競品 A B C 三家")
    assert v["result"] == "advanced" and v["next_step"]["step_id"] == "s2"


def test_escalate_then_resolve_approved(tmp_path):
    """attempts/replans 用盡 → escalated(blocked);人工 approve → 續跑完成。"""
    gk = _gk(tmp_path, max_replans=0)
    spec = [
        {
            "id": "s1",
            "description": "難關",
            "max_attempts": 1,
            "acceptance": {
                "layer": "hard",
                "kind": "programmatic",
                "spec": {"check": "min_words", "args": {"n": 100}},
            },
        }
    ]
    tid = gk.start("g", spec)["thread_id"]
    v = gk.submit(tid, "達不到一百字")
    assert v["result"] == "escalated" and v["may_advance"] is False
    assert gk.current(tid)["status"] == "blocked"

    done = gk.resolve(tid, "approved", note="人工放行")
    assert done["result"] == "done"


def test_replan_needed_then_replan(tmp_path):
    """attempts 用盡但仍可 replan → replan_needed;agent 補新尾巴 → 續跑。"""
    gk = _gk(tmp_path, max_replans=1)
    spec = [
        {
            "id": "s1",
            "description": "原步驟",
            "max_attempts": 1,
            "acceptance": {
                "layer": "hard",
                "kind": "programmatic",
                "spec": {"check": "min_words", "args": {"n": 100}},
            },
        }
    ]
    tid = gk.start("g", spec)["thread_id"]
    v = gk.submit(tid, "短輸出")
    assert v["result"] == "replan_needed"

    new_tail = [
        {
            "id": "r2-1",
            "description": "改用較寬鬆的新步驟",
            "acceptance": {
                "layer": "hard",
                "kind": "programmatic",
                "spec": {"check": "non_empty"},
            },
        }
    ]
    rv = gk.replan(tid, {"steps": new_tail, "revision_note": "放寬"})
    assert rv["result"] == "advanced"
    assert rv["next_step"]["step_id"] == "r2-1"
    # 補完新步驟 → done
    assert gk.submit(tid, "有內容")["result"] == "done"


def test_unknown_thread_raises(tmp_path):
    import pytest

    gk = _gk(tmp_path)
    with pytest.raises(ValueError):
        gk.submit("nope", "x")


def test_plan_view_and_audit_tail(tmp_path):
    gk = _gk(tmp_path)
    tid = gk.start("市場分析", _plan_spec())["thread_id"]
    v = gk.submit(tid, "競品 A B C 三家")
    assert isinstance(v["audit_tail"], list) and v["audit_tail"]
    view = gk.plan(tid)
    assert view["goal"] == "市場分析"
    assert view["cursor"] == 1
    assert [s["id"] for s in view["steps"]] == ["s1", "s2"]


def test_start_warns_on_ungated_step(tmp_path):
    """沒有可強制驗收條件的步驟應在 start 回 warnings(誠實標示無閘門)。"""
    gk = _gk(tmp_path)
    spec = [{"id": "s1", "description": "無驗收", "acceptance": {"spec": {}}}]
    res = gk.start("g", spec)
    assert res["warnings"]


def test_max_replans_default_matches_engine(tmp_path):
    gk = _gk(tmp_path)
    assert gk.max_replans == engine.MAX_REPLANS
