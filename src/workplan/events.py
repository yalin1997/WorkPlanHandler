"""WorkPlanHandler — 事件型別與審計(規格 01 §4,對應 D11/I4)。

engine 每次轉移產出一或多個 `Event`,append 到 `PlanState.history`
(以 `to_dict()` 形式存放,維持 I2 可序列化)。事件是 append-only、
不可變的審計來源。

`payload` 必須可 JSON 序列化(不得放物件參考);大型 output 存摘要。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    PLAN_CREATED = "plan_created"
    PLAN_REVISED = "plan_revised"  # D6 replan / criteria 修改
    STEP_STARTED = "step_started"
    STEP_OUTPUT = "step_output"
    VERIFY_PASSED = "verify_passed"
    VERIFY_FAILED = "verify_failed"
    STEP_RETRIED = "step_retried"
    STEPS_INSERTED = "steps_inserted"  # D5
    STEP_DONE = "step_done"
    ESCALATED = "escalated"  # D8
    HUMAN_RESOLVED = "human_resolved"  # D8
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


@dataclass(frozen=True)
class Event:
    """單一審計事件。payload 必含欄位依 type 而定(規格 01 §4.1)。"""

    type: EventType
    ts: str  # ISO-8601 UTC
    step_id: str | None = None
    plan_version: int = 1
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "ts": self.ts,
            "step_id": self.step_id,
            "plan_version": self.plan_version,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            type=EventType(d["type"]),
            ts=d["ts"],
            step_id=d.get("step_id"),
            plan_version=d.get("plan_version", 1),
            payload=dict(d.get("payload", {})),
        )
