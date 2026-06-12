"""tests 共用 helper(自 test_engine.py 的同名函式抽出,語意完全相同)。

test_engine.py 自帶同名函式、不依賴本檔(不衝突);
test_adapter_langgraph.py 以 `from conftest import ...` 取用
(pytest 會把 conftest.py 所在目錄加入 sys.path)。
"""

from __future__ import annotations

from workplan.models import AcceptanceCriterion, Plan, PlanState, Step


def make_step(step_id: str, description: str = "") -> Step:
    return Step(
        id=step_id,
        description=description or f"步驟 {step_id}",
        acceptance=AcceptanceCriterion(description=f"{step_id} 完成"),
    )


def make_plan(*step_ids: str, goal: str = "測試目標") -> Plan:
    return Plan(goal=goal, steps=[make_step(s) for s in step_ids])


def event_types(state: PlanState) -> list[str]:
    return [e["type"] for e in state.history]
