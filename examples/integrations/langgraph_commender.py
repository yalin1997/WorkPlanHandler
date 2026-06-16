"""整合紀錄:LangGraph commender 提早結束問題

原始架構:
    planner(生成計劃) → commender(決定 tool 或直接回覆)
        → [tool 路徑] ToolCall → Summary
        → [直接回覆] Summary
    不管哪條路最終都過 Summary 結束。

問題:
    commender 依當下 context 判斷「差不多了」就跳去 Summary,
    不會確認 planner 的所有步驟是否真的完成。計劃越長、context
    越多,提早收手的機率越高。

解法:
    引入 WorkPlanHandler engine 管「外迴圈」:
    - planner 輸出結構化 Plan(每步有驗收條件)
    - engine 持有 cursor,知道現在執行第幾步
    - commender 只負責「執行當前 step」,不再決定計劃是否完成
    - verify 節點驗收後,engine 決定下一步:還有步驟→回 commender、
      全部通過→才能去 Summary
    - 驗收失敗時 engine 帶著 feedback 踢回 commender 重試

新架構:
    planner → commender → verify ─→ (更多步驟?) commender
                                └→ (全完成)    Summary → END

執行(離線,不需 key):
    python examples/integrations/langgraph_commender.py

依賴:workplan[langgraph]  (pip install -e ".[langgraph,dev]")
"""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage

from workplan import Action, StepOutput, engine
from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.verifiers import LayeredVerifier
from workplan.verifiers.programmatic import ProgrammaticVerifier

# ── 離線 stub:模擬 commender 的 LLM 呼叫(不燒 key)─────────────────────────

_STEP_OUTPUTS: dict[str, str] = {
    "s1": "競品分析:A 公司主打低價、B 公司主打功能、C 公司主打服務。",
    "s2": "目標客群:25–40 歲中小企業主,重視 CP 值與易用性。",
    "s3": "策略建議:(1)差異化定價 (2)強化售後服務 (3)簡化 UI 降低上手門檻。",
}


def _fake_commender(step_description: str, step_id: str, feedback: str) -> str:
    """離線假 commender:模擬執行當前 step。"""
    base = _STEP_OUTPUTS.get(step_id, f"完成步驟:{step_description}")
    if feedback:
        # 模擬「收到 feedback 後修正」
        return base + f" [已依回饋修正:{feedback[:20]}]"
    return base


# ── 驗收函式(hard 層)──────────────────────────────────────────────────────


def _not_empty(output: StepOutput, state) -> bool:
    return len(str(output.content).strip()) > 10


def _has_recommendation(output: StepOutput, state) -> tuple[bool, float, str]:
    text = str(output.content)
    if "建議" in text or "策略" in text:
        return True, 1.0, ""
    return False, 0.0, "第三步需包含明確的策略建議,目前尚未看到。"


CHECKS = {
    "not_empty": _not_empty,
    "has_recommendation": _has_recommendation,
}

# ── LangGraph State ────────────────────────────────────────────────────────

try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict


class State(TypedDict):
    goal: str
    messages: list[BaseMessage]
    plan_state: Optional[dict]  # PlanState.to_dict()；I2:可序列化
    last_action: str  # engine Action value,供路由函式判斷


# ── 節點函式 ───────────────────────────────────────────────────────────────


def planner_node(state: State) -> dict:
    """生成結構化 Plan,初始化 engine。"""
    # 真實場景:換成 LLMPlanner(model=ChatAnthropic(...))
    plan = Plan(
        goal=state["goal"],
        steps=[
            Step(
                id="s1",
                description="蒐集主要競品資料",
                acceptance=AcceptanceCriterion(
                    description="內容非空",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "not_empty"},
                ),
            ),
            Step(
                id="s2",
                description="分析目標客群",
                acceptance=AcceptanceCriterion(
                    description="內容非空",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "not_empty"},
                ),
            ),
            Step(
                id="s3",
                description="提出差異化策略建議",
                acceptance=AcceptanceCriterion(
                    description="含策略建議",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "has_recommendation"},
                ),
            ),
        ],
    )

    decision = engine.initialize(plan, thread_id=uuid.uuid4().hex)
    print(f"\n[planner] 計劃已生成,共 {len(plan.steps)} 步")

    return {
        "plan_state": decision.state.to_dict(),
        "last_action": decision.action.value,
    }


def commender_node(state: State) -> dict:
    """執行當前 step(tool 或直接回答),不決定計劃是否完成。"""
    plan_state = PlanState.from_dict(state["plan_state"])
    current_step = plan_state.current_step
    feedback = current_step.notes[-1] if current_step.notes else ""

    print(f"\n[commender] 執行 {current_step.id}: {current_step.description}")
    if feedback:
        print(f"            ↻ retry,feedback: {feedback}")

    # 真實場景:呼叫 llm.bind_tools([...]).invoke(messages) 並跑工具迴圈
    content = _fake_commender(current_step.description, current_step.id, feedback)

    step_output = StepOutput(content=content)
    decision = engine.on_executed(plan_state, step_output)

    return {
        "messages": state["messages"] + [AIMessage(content=content)],
        "plan_state": decision.state.to_dict(),
        "last_action": decision.action.value,
    }


def verify_node(state: State) -> dict:
    """驗收當前 step;engine 依結果決定路由。"""
    plan_state = PlanState.from_dict(state["plan_state"])
    current_step = plan_state.current_step

    verifier = LayeredVerifier(
        layers=[
            (
                "hard",
                ProgrammaticVerifier(checks=CHECKS),
                True,
            ),
        ]
    )

    output = StepOutput(content=current_step.output)
    result = verifier.verify(current_step, output, plan_state)
    decision = engine.on_verified(plan_state, result)

    status = "PASS" if result.passed else f"FAIL({result.feedback[:30]})"
    print(f"[verify]    {current_step.id} → {status} | engine: {decision.action.value}")

    return {
        "plan_state": decision.state.to_dict(),
        "last_action": decision.action.value,
    }


def summary_node(state: State) -> dict:
    """所有步驟通過驗收後才到這裡,產出最終摘要。"""
    plan_state = PlanState.from_dict(state["plan_state"])
    lines = [f"目標:{plan_state.plan.goal}\n"]
    for s in plan_state.plan.steps:
        lines.append(f"  [{s.status.value}] {s.id}: {str(s.output)[:60]}")
    summary = "\n".join(lines)

    print(f"\n[summary] 全部完成!\n{summary}")
    return {"messages": state["messages"] + [AIMessage(content=summary)]}


# ── 路由函式 ───────────────────────────────────────────────────────────────


def route_after_verify(
    state: State,
) -> Literal["commender", "summary", "planner"]:
    action = state["last_action"]
    if action == Action.DONE.value:
        return "summary"
    if action in (Action.EXECUTE.value, Action.RETRY.value, Action.VERIFY.value):
        return "commender"
    if action == Action.REPLAN.value:
        return "planner"
    return "summary"  # ESCALATE 等:結束或等人工


# ── Graph 組裝 ─────────────────────────────────────────────────────────────


def build_graph():
    from langgraph.graph import END, StateGraph

    builder = StateGraph(State)
    builder.add_node("planner", planner_node)
    builder.add_node("commender", commender_node)
    builder.add_node("verify", verify_node)
    builder.add_node("summary", summary_node)

    builder.set_entry_point("planner")
    builder.add_edge("planner", "commender")
    builder.add_edge("commender", "verify")  # 執行完一定先驗收
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {"commender": "commender", "summary": "summary", "planner": "planner"},
    )
    builder.add_edge("summary", END)

    return builder.compile()


# ── 執行 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    graph = build_graph()

    final = graph.invoke(
        {
            "goal": "撰寫一份市場進入策略分析",
            "messages": [],
            "plan_state": None,
            "last_action": "",
        }
    )

    print(f"\n最終訊息數: {len(final['messages'])}")
    plan_state = PlanState.from_dict(final["plan_state"])
    print(f"計劃狀態: {plan_state.status}  cursor={plan_state.cursor}")
    for s in plan_state.plan.steps:
        print(f"  {s.id} [{s.status.value}] attempts={s.attempts}")
