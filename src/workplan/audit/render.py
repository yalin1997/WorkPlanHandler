"""審計渲染純函式(規格 01 §5,對應 D11/I4)。

設計原則:
  - **零依賴**:只用標準庫(json/pathlib/datetime),維持核心可插拔。
  - **純函式**:輸入 ``PlanState``,輸出 list/str/檔案;不改動傳入的 state。
  - **由事件流推導**:每步的驗收層與分數讀自 ``history`` 內最後一次驗收事件
    (I4:每個轉移都對應至少一個 Event),不另外維護衍生欄位。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..events import EventType
from ..models import PlanState, Step

# JSON 事件流的穩定 schema 版本標頭(欄位結構若變更則 bump)。
EVENT_LOG_SCHEMA_VERSION = "1"

_STATUS_ICON = {
    "done": "✅ done",
    "failed": "❌ failed",
    "blocked": "⏸️ blocked",
    "in_progress": "🔄 in_progress",
    "verifying": "🔍 verifying",
    "pending": "⬜ pending",
}

_VERIFY_TYPES = {EventType.VERIFY_PASSED.value, EventType.VERIFY_FAILED.value}


def to_event_log(state: PlanState) -> list[dict[str, Any]]:
    """回傳完整 JSON 事件流(``state.history`` 的深拷貝)。

    history 本身即 append-only 的審計來源(I4);回傳拷貝以免呼叫端意外
    污染原 state(審計來源不可變)。
    """
    return [dict(e) for e in state.history]


def _last_verify(history: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
    found = None
    for e in history:
        if e.get("step_id") == step_id and e["type"] in _VERIFY_TYPES:
            found = e
    return found


def _last_human(history: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
    found = None
    for e in history:
        if e.get("step_id") == step_id and e["type"] == EventType.HUMAN_RESOLVED.value:
            found = e
    return found


def _step_row(step: Step, idx: int, history: list[dict[str, Any]]) -> str:
    icon = _STATUS_ICON.get(step.status.value, step.status.value)
    verify = _last_verify(history, step.id)
    if verify is not None:
        layer = verify["payload"].get("layer", "-") or "-"
        score = f"{float(verify['payload'].get('score', 0.0)):.2f}"
    elif _last_human(history, step.id) is not None:
        layer, score = "human", "-"
    else:
        layer, score = "-", "-"
    return (
        f"| {idx} | {step.description} | {icon} | {layer} | {step.attempts} | {score} |"
    )


def _revision_lines(history: list[dict[str, Any]]) -> list[str]:
    lines = []
    for e in history:
        if e["type"] == EventType.PLAN_REVISED.value:
            p = e["payload"]
            reason = p.get("reason") or "(未註明理由)"
            lines.append(
                f"- v{p.get('from_version')} → v{p.get('to_version')}:{reason}"
            )
    return lines


def _human_lines(state: PlanState) -> list[str]:
    lines = []
    for e in state.history:
        if e["type"] == EventType.HUMAN_RESOLVED.value:
            p = e["payload"]
            note = p.get("human_note") or ""
            suffix = f"({note})" if note else ""
            lines.append(f"- {e.get('step_id')}:{p.get('resolution')}{suffix}")
    if state.pending_human is not None:
        g = state.pending_human
        lines.append(f"- {g.step_id}:pending(原因:{g.reason})")
    return lines


def _conclusion(state: PlanState) -> str:
    n = len(state.plan.steps)
    done = len(state.plan.completed_steps())
    if state.status == "done":
        return f"全部 {n} 個步驟通過驗收,任務完成(done)。"
    if state.status == "failed":
        return f"任務失敗(failed):{done}/{n} 步通過,於未完成步驟終止。"
    if state.status == "blocked":
        return f"任務暫停,等待人工裁決(blocked):已完成 {done}/{n} 步。"
    return f"任務進行中(running):已完成 {done}/{n} 步。"


def to_markdown(state: PlanState) -> str:
    """渲染人讀驗收摘要(規格 01 §5 範本)。不含 timestamp,快照穩定。"""
    plan = state.plan
    lines: list[str] = [
        f"# 驗收報告:{plan.goal}",
        "",
        f"- plan 版本:v{plan.version}",
        f"- 執行狀態:{state.status}",
        f"- 步驟數:{len(plan.steps)}",
        f"- replan 次數:{state.replans}",
        "",
        "## 步驟驗收明細",
        "",
        "| # | 步驟 | 狀態 | 驗收層 | 嘗試 | 分數 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, step in enumerate(plan.steps, start=1):
        lines.append(_step_row(step, i, state.history))

    revisions = _revision_lines(state.history)
    if revisions:
        lines += ["", "## 重規劃紀錄", "", *revisions]

    humans = _human_lines(state)
    if humans:
        lines += ["", "## 人工關卡", "", *humans]

    lines += ["", "## 結論", "", _conclusion(state), ""]
    return "\n".join(lines)


def write_audit(
    state: PlanState, out_dir: str | Path, *, basename: str = "audit"
) -> tuple[Path, Path]:
    """把審計產物落檔:``<basename>.json``(envelope)+ ``<basename>.md``。

    JSON envelope 帶穩定 ``schema_version`` 與摘要欄位,方便外部工具索引;
    完整事件流在 ``log``。回傳 ``(json_path, md_path)``。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{basename}.json"
    md_path = out / f"{basename}.md"

    envelope = {
        "schema_version": EVENT_LOG_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thread_id": state.thread_id,
        "goal": state.plan.goal,
        "plan_version": state.plan.version,
        "status": state.status,
        "log": to_event_log(state),
    }
    json_path.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(to_markdown(state), encoding="utf-8")
    return json_path, md_path
