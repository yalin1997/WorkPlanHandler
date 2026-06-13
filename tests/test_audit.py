"""M5a 審計輸出測試(規格 01 §5/§6,對應 D11/I2/I4)。

TDD 先行:本檔定義 audit/render.py 的契約——
  - to_event_log:回完整 JSON 事件流,可序列化往返(I2)。
  - to_markdown:對固定 PlanState 產出穩定快照(snapshot)。
  - write_audit:落檔 .json(envelope)+ .md,JSON 可重新載入。
  - 每種 EventType 的 payload 必含欄位齊全(§4.1 schema,I4)。
"""

from __future__ import annotations

import json

from workplan import engine
from workplan.audit import (
    EVENT_LOG_SCHEMA_VERSION,
    to_event_log,
    to_markdown,
    write_audit,
)
from workplan.events import Event, EventType
from workplan.models import (
    AcceptanceCriterion,
    HumanGate,
    Plan,
    PlanState,
    Step,
    StepStatus,
)
from workplan.protocols import StepOutput, VerificationResult

# ---------------------------------------------------------------------------
# fixtures:手工建構確定性 PlanState(不依賴 ts,快照才穩定)
# ---------------------------------------------------------------------------


def _event(type_, step_id=None, plan_version=1, **payload) -> dict:
    return Event(
        type=type_,
        ts="2026-06-13T00:00:00+00:00",  # 固定 ts:不影響 markdown,但讓 json 可比對
        step_id=step_id,
        plan_version=plan_version,
        payload=payload,
    ).to_dict()


def _done_step(sid: str, desc: str, attempts: int = 1) -> Step:
    s = Step(id=sid, description=desc, acceptance=AcceptanceCriterion(description=desc))
    s.status = StepStatus.DONE
    s.attempts = attempts
    return s


def sample_state() -> PlanState:
    """涵蓋 retry(s2 兩次失敗)、replan(v1→v2)、human gate(s3 approved)。"""
    plan = Plan(
        goal="產出市場分析報告",
        steps=[
            _done_step("s1", "蒐集競品資料", attempts=1),
            _done_step("s2", "彙整比較表", attempts=3),
            _done_step("s3", "發佈報告", attempts=1),
        ],
        version=2,
    )
    history = [
        _event(EventType.PLAN_CREATED, step_id=None, payload_goal="產出市場分析報告"),
        _event(EventType.VERIFY_PASSED, "s1", score=1.0, layer="soft", feedback=""),
        _event(
            EventType.VERIFY_FAILED, "s2", score=0.0, layer="hard", feedback="缺客群"
        ),
        _event(
            EventType.VERIFY_FAILED, "s2", score=0.0, layer="hard", feedback="缺客群"
        ),
        _event(
            EventType.PLAN_REVISED,
            plan_version=2,
            from_version=1,
            to_version=2,
            reason="放寬 s2 驗收門檻",
            kept_step_ids=["s1"],
        ),
        _event(EventType.VERIFY_PASSED, "s2", score=0.9, layer="soft", feedback=""),
        _event(
            EventType.ESCALATED, "s3", reason="human gate", attempts_exhausted=False
        ),
        _event(
            EventType.HUMAN_RESOLVED,
            "s3",
            resolution="approved",
            human_note="主管核准",
        ),
        _event(EventType.RUN_COMPLETED, step_id=None),
    ]
    return PlanState(
        plan=plan,
        cursor=3,
        history=history,
        replans=1,
        status="done",
        thread_id="job-42",
    )


# ---------------------------------------------------------------------------
# to_event_log:I2 序列化往返
# ---------------------------------------------------------------------------


def test_to_event_log_returns_history_copy():
    state = sample_state()
    log = to_event_log(state)
    assert log == state.history
    # 必須是拷貝:改動回傳值不污染原 state(I2 審計來源不可變)
    log.append({"x": 1})
    assert len(state.history) == 9


def test_to_event_log_json_roundtrip():
    state = sample_state()
    log = to_event_log(state)
    restored = json.loads(json.dumps(log, ensure_ascii=False))
    assert restored == log
    # 每筆事件可還原成 Event(schema 穩定)
    for d in restored:
        assert Event.from_dict(d).to_dict() == d


# ---------------------------------------------------------------------------
# to_markdown:固定 PlanState → 穩定快照
# ---------------------------------------------------------------------------

_EXPECTED_MD = """\
# 驗收報告:產出市場分析報告

- plan 版本:v2
- 執行狀態:done
- 步驟數:3
- replan 次數:1

## 步驟驗收明細

| # | 步驟 | 狀態 | 驗收層 | 嘗試 | 分數 |
| --- | --- | --- | --- | --- | --- |
| 1 | 蒐集競品資料 | ✅ done | soft | 1 | 1.00 |
| 2 | 彙整比較表 | ✅ done | soft | 3 | 0.90 |
| 3 | 發佈報告 | ✅ done | human | 1 | - |

## 重規劃紀錄

- v1 → v2:放寬 s2 驗收門檻

## 人工關卡

- s3:approved(主管核准)

## 結論

全部 3 個步驟通過驗收,任務完成(done)。
"""


def test_to_markdown_snapshot():
    assert to_markdown(sample_state()) == _EXPECTED_MD


def test_to_markdown_blocked_pending_human():
    """blocked 狀態(等待中的 human gate)要列在人工關卡且結論反映暫停。"""
    plan = Plan(goal="G", steps=[_done_step("s1", "做事")])
    plan.steps[0].status = StepStatus.BLOCKED
    state = PlanState(
        plan=plan,
        cursor=0,
        history=[
            _event(EventType.ESCALATED, "s1", reason="高風險", attempts_exhausted=False)
        ],
        status="blocked",
        pending_human=HumanGate(step_id="s1", reason="高風險", asked_at="t"),
    )
    md = to_markdown(state)
    assert "## 人工關卡" in md
    assert "s1" in md and "pending" in md
    assert "blocked" in md


# ---------------------------------------------------------------------------
# write_audit:落檔
# ---------------------------------------------------------------------------


def test_write_audit_creates_json_and_md(tmp_path):
    state = sample_state()
    json_path, md_path = write_audit(state, tmp_path)
    assert json_path.exists() and md_path.exists()

    envelope = json.loads(json_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == EVENT_LOG_SCHEMA_VERSION
    assert envelope["goal"] == "產出市場分析報告"
    assert envelope["status"] == "done"
    assert envelope["plan_version"] == 2
    assert envelope["thread_id"] == "job-42"
    assert envelope["log"] == state.history

    md = md_path.read_text(encoding="utf-8")
    assert md == to_markdown(state)


def test_write_audit_custom_basename(tmp_path):
    json_path, md_path = write_audit(sample_state(), tmp_path, basename="report")
    assert json_path.name == "report.json"
    assert md_path.name == "report.md"


# ---------------------------------------------------------------------------
# §4.1 payload schema:每種轉移事件的 payload 必含欄位齊全(I4)
# ---------------------------------------------------------------------------


def test_event_payloads_have_required_fields():
    """跑一段涵蓋多路徑的 engine 流程,檢查關鍵事件 payload 欄位齊全。"""
    plan = Plan(
        goal="G",
        steps=[
            Step(
                id="s1",
                description="一",
                acceptance=AcceptanceCriterion(description="一"),
            ),
            Step(
                id="s2",
                description="二",
                acceptance=AcceptanceCriterion(description="二"),
            ),
        ],
    )
    dec = engine.initialize(plan, thread_id="t")
    # s1 執行 → 驗收通過 → 前進到 s2
    dec = engine.on_executed(dec.state, StepOutput(content="ok"))
    dec = engine.on_verified(
        dec.state, VerificationResult(passed=True, score=1.0, layer="soft")
    )
    # s2 執行 → 驗收失敗 → retry
    dec = engine.on_executed(dec.state, StepOutput(content="bad"))
    dec = engine.on_verified(
        dec.state,
        VerificationResult(passed=False, score=0.0, layer="hard", feedback="不行"),
    )

    log = to_event_log(dec.state)
    required = {
        EventType.PLAN_CREATED.value: {"goal", "step_ids"},
        EventType.STEP_STARTED.value: {"description", "attempt"},
        EventType.STEP_OUTPUT.value: {"content", "error"},
        EventType.VERIFY_PASSED.value: {"score", "layer", "feedback"},
        EventType.VERIFY_FAILED.value: {"score", "layer", "feedback"},
        EventType.STEP_RETRIED.value: {"attempt", "feedback"},
        EventType.STEP_DONE.value: set(),
    }
    seen = set()
    for e in log:
        etype = e["type"]
        seen.add(etype)
        if etype in required:
            missing = required[etype] - set(e["payload"])
            assert not missing, f"{etype} payload 缺欄位:{missing}"
    # 確認上述路徑該出現的事件都出現了
    assert {
        "plan_created",
        "step_started",
        "verify_passed",
        "verify_failed",
        "step_retried",
        "step_done",
    } <= seen
