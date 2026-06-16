"""JsonFilePlanStore(框架無關持久化)單測。

驗證:save→load 完整還原(I2)、覆寫取最新、缺 thread 回 None、檔案為合法
UTF-8 JSON、blocked+pending_human 也完整還原、非法 thread_id 拒絕(防穿越)。
"""

from __future__ import annotations

import json

import pytest
from conftest import make_plan

from workplan import StepOutput, engine
from workplan.protocols import VerificationResult
from workplan.stores.json_store import JsonFilePlanStore


def _advanced_state(thread_id: str = "T1"):
    """跑兩個 transition 產出含 history 的真實 state(s1 通過,游標到 s2)。"""
    plan = make_plan("s1", "s2", goal="json store 測試")
    st = engine.initialize(plan, thread_id=thread_id).state
    st = engine.on_executed(st, StepOutput(content="第一步輸出")).state
    st = engine.on_verified(st, VerificationResult(passed=True, score=1.0)).state
    return st


def test_save_then_load_round_trip(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    state = _advanced_state("T1")
    store.save("T1", state)
    loaded = store.load("T1")
    assert loaded is not None
    assert loaded.to_dict() == state.to_dict()


def test_load_missing_returns_none(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    assert store.load("does-not-exist") is None


def test_overwrite_keeps_latest_no_tmp_residue(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    plan = make_plan("s1", goal="g")
    s1 = engine.initialize(plan, thread_id="T2").state
    store.save("T2", s1)
    s2 = engine.on_executed(s1, StepOutput(content="x")).state
    store.save("T2", s2)
    assert store.load("T2").to_dict() == s2.to_dict()
    assert list(tmp_path.glob("*.tmp")) == []  # atomic write 不留殘檔


def test_persisted_file_is_valid_utf8_json(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    store.save("T3", _advanced_state("T3"))
    data = json.loads((tmp_path / "T3.json").read_text(encoding="utf-8"))
    assert data["thread_id"] == "T3"
    assert data["cursor"] == 1  # s1 通過後游標前進


def test_blocked_pending_human_round_trip(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    plan = make_plan("s1", goal="g")
    st = engine.initialize(plan, thread_id="TH").state
    st = engine.on_executed(st, StepOutput(content="x")).state
    dec = engine.on_verified(st, VerificationResult(passed=False, needs_human=True))
    assert dec.state.status == "blocked" and dec.state.pending_human is not None
    store.save("TH", dec.state)
    loaded = store.load("TH")
    assert loaded.status == "blocked"
    assert loaded.pending_human is not None
    assert loaded.to_dict() == dec.state.to_dict()


def test_rejects_unsafe_thread_id(tmp_path):
    store = JsonFilePlanStore(root=tmp_path)
    for bad in ("../evil", "a/b", "", ".", ".."):
        with pytest.raises(ValueError):
            store.save(bad, _advanced_state())
