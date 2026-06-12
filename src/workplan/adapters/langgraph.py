"""WorkPlanHandler — LangGraph adapter 與持久化(規格 05,M2/M3)。

⚠️ 本檔是整個套件中**唯一**可 `import langgraph` 的檔案(D9 依賴方向鐵則)。
engine / models / verifiers / planners 一概不得 import 本檔或 langgraph。

職責(D2 薄殼):把純函式 engine「編譯」成一張 LangGraph StateGraph——
  - 節點 = engine reducer 的薄包裝(副作用:executor/verifier/planner 呼叫);
  - conditional edges = engine `Action` 的路由(`_route` 只讀字串,不做決策);
  - SqliteSaver checkpointer = 持久化(D7),每個 super-step 後 `durability="sync"`
    落盤,等同 I3「轉移前落盤」;
  - `interrupt()` = D8 human gate(escalate 時暫停,`Command(resume=...)` 續跑)。

所有決策邏輯都在 engine,圖只負責接線。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, TypedDict

try:
    # RunnableConfig 來自 langchain-core(langgraph 的必要依賴):
    # langgraph 1.x 依「參數型別註記」決定是否注入 config,缺註記不會注入。
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
except ImportError as exc:
    raise ImportError(
        "LangGraph adapter 需要額外依賴:pip install 'workplan[langgraph]'"
    ) from exc

from .. import engine
from ..engine import Action
from ..models import Plan, PlanState
from ..protocols import Executor, Planner, StepOutput, VerificationResult, Verifier


class GraphState(TypedDict, total=False):
    """LangGraph 單一 channel state(P1)。

    channel 永遠只存純 JSON dict(經 `_jsonable` round-trip),
    讓 checkpointer 的序列化器不必認得任何自訂類別。
    """

    plan: dict[str, Any]  # 輸入 channel:呼叫端給的 Plan JSON dict(僅 n_plan 消費)
    ps: dict[str, Any]  # PlanState.to_dict() 經 JSON round-trip 的純 dict
    action: str  # engine Action.value(`_route` 路由依據)
    feedback: str  # Decision.feedback(replan 節點用)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    """JSON round-trip:把 asdict 殘留的 Enum 打平成 str。

    channel 只存純 JSON,繞開 checkpointer 序列化器對自訂類別的問題。
    """
    return json.loads(json.dumps(d, ensure_ascii=False))


def _decision_update(dec: engine.Decision) -> GraphState:
    """把 engine Decision 轉成 channel 更新(節點回傳值的統一出口)。"""
    return {
        "ps": _jsonable(dec.state.to_dict()),
        "action": dec.action.value,
        "feedback": dec.feedback,
    }


# ---------------------------------------------------------------------------
# 節點工廠(全是 engine 薄包裝,D2:決策全在 engine,節點只做副作用 + 轉手)
# ---------------------------------------------------------------------------


def _make_plan_node() -> Callable[..., GraphState]:
    def n_plan(gs: GraphState, config: RunnableConfig) -> GraphState:
        if gs.get("ps"):  # 防衛:同 thread 重複 invoke 時不重置狀態
            return {}
        plan = Plan.from_dict(gs["plan"])
        dec = engine.initialize(plan, thread_id=config["configurable"]["thread_id"])
        return _decision_update(dec)

    return n_plan


def _make_execute_node(
    executor: Executor, max_replans: int
) -> Callable[[GraphState], GraphState]:
    def n_execute(gs: GraphState) -> GraphState:
        ps = PlanState.from_dict(gs["ps"])
        step = ps.current_step
        assert step is not None  # engine 路由保證:進到此節點必有 current_step
        out = executor.execute(step, ps)  # 副作用都在 executor
        dec = engine.on_executed(ps, out, max_replans=max_replans)
        return _decision_update(dec)

    return n_execute


def _make_verify_node(
    verifier: Verifier, max_replans: int
) -> Callable[[GraphState], GraphState]:
    def n_verify(gs: GraphState) -> GraphState:
        ps = PlanState.from_dict(gs["ps"])
        step = ps.current_step
        assert step is not None
        # engine.on_executed 已把輸出寫回 step.output,在此重建 StepOutput
        out = StepOutput(content=step.output)
        res = verifier.verify(step, out, ps)
        dec = engine.on_verified(ps, res, max_replans=max_replans)
        return _decision_update(dec)

    return n_verify


def _make_replan_node(planner: Planner | None) -> Callable[[GraphState], GraphState]:
    def n_replan(gs: GraphState) -> GraphState:
        if planner is None:
            raise RuntimeError(
                "engine 要求 REPLAN,但未提供 planner(build_graph(planner=...))"
            )
        ps = PlanState.from_dict(gs["ps"])
        failure = VerificationResult(passed=False, feedback=gs.get("feedback", ""))
        new_plan = planner.replan(ps, failure)
        dec = engine.on_replanned(ps, new_plan)
        return _decision_update(dec)

    return n_replan


def _make_human_node() -> Callable[[GraphState], GraphState]:
    def n_human(gs: GraphState) -> GraphState:
        # interrupt 之前零副作用、只反序列化:resume 時整個節點會重跑
        ps = PlanState.from_dict(gs["ps"])
        gate = ps.pending_human
        step = ps.current_step
        assert gate is not None and step is not None
        answer = interrupt(
            {
                "type": "human_gate",
                "step_id": gate.step_id,
                "reason": gate.reason,
                "question": step.acceptance.description,
            }
        )
        # answer 形狀:{"resolution": "approved|rejected|edited", "note": "..."}
        gate = dataclasses.replace(
            gate,
            resolution=answer["resolution"],
            human_note=answer.get("note", ""),
        )
        dec = engine.on_human_resolved(ps, gate)
        return _decision_update(dec)

    return n_human


def _route(gs: GraphState) -> str:
    """conditional edge 路由:只讀 engine 已決定的 Action 字串(D2)。"""
    return gs["action"]


# ---------------------------------------------------------------------------
# 圖組裝
# ---------------------------------------------------------------------------


def build_graph(
    *,
    executor: Executor,
    verifier: Verifier,
    planner: Planner | None = None,
    checkpointer: Any | None = None,
    max_replans: int = engine.MAX_REPLANS,
):
    """把 engine 編成 LangGraph CompiledStateGraph(規格 05 §3)。

    拓撲對映 engine Action:
      START→plan→execute;execute/verify/human 依 action 條件路由;
      replan→execute 固定邊;DONE/FAILED→END。
    """
    g = StateGraph(GraphState)
    g.add_node("plan", _make_plan_node())
    g.add_node("execute", _make_execute_node(executor, max_replans))
    g.add_node("verify", _make_verify_node(verifier, max_replans))
    g.add_node("replan", _make_replan_node(planner))
    g.add_node("human", _make_human_node())  # D8:interrupt 點

    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_conditional_edges(
        "execute",
        _route,
        {
            Action.VERIFY.value: "verify",
            Action.RETRY.value: "execute",  # 執行期硬錯的同步重試
            Action.REPLAN.value: "replan",
            Action.ESCALATE.value: "human",
        },
    )
    g.add_conditional_edges(
        "verify",
        _route,
        {
            Action.EXECUTE.value: "execute",  # advance:下一步
            Action.RETRY.value: "execute",  # 同步重試(feedback 已在 step.notes)
            Action.REPLAN.value: "replan",
            Action.ESCALATE.value: "human",
            Action.DONE.value: END,
        },
    )
    g.add_edge("replan", "execute")
    g.add_conditional_edges(
        "human",
        _route,
        {
            Action.EXECUTE.value: "execute",  # approved → 續跑下一步
            Action.RETRY.value: "execute",  # edited → 同步重做本步
            Action.DONE.value: END,
            Action.FAILED.value: END,  # rejected → 終止
        },
    )
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# 對外門面
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """一次 run/resume 的結果摘要(藏掉 LangGraph 細節)。"""

    thread_id: str
    status: str  # PlanState.status:running/done/failed/blocked
    state: PlanState
    interrupted: bool = False  # D8:是否停在 human gate 等人
    interrupt_payload: dict | None = None  # interrupt() 丟出的提問內容


class WorkPlanRunner:
    """最小對外介面:run / resume / get_state(規格 05 §6)。

    持久化注意事項:
      - WSL2 下 ``db_path`` 應放 Linux 原生路徑(如 /tmp、$HOME);
        /mnt/c 走 9P 協定,SQLite 檔案鎖不可靠。
      - 本 runner 為單 process 設計;多 process 並發寫同一 db 不在保證內。
      - crash 續跑為 checkpoint 級 at-least-once:進行中的節點 resume 後
        會整個重跑,executor 的副作用需自行設計成冪等(規格 05 §4)。
    """

    def __init__(
        self,
        *,
        executor: Executor,
        verifier: Verifier,
        planner: Planner | None = None,
        db_path: str = "workplan.sqlite",
        max_replans: int = engine.MAX_REPLANS,
    ) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._saver = SqliteSaver(self._conn)  # D7
        self._app = build_graph(
            executor=executor,
            verifier=verifier,
            planner=planner,
            checkpointer=self._saver,
            max_replans=max_replans,
        )

    # -- 公開 API ----------------------------------------------------------

    def run(self, *, plan: Plan, thread_id: str | None = None) -> RunResult:
        """以給定 Plan 啟動(或在既有 thread 上繼續)一次執行。

        ``thread_id`` 未給則自動產生(P3),由回傳值告知。
        """
        tid = thread_id or uuid.uuid4().hex
        payload: dict[str, Any] = {"plan": _jsonable(asdict(plan))}
        return self._invoke(payload, tid)

    def resume(
        self, thread_id: str, *, resolution: str | None = None, note: str = ""
    ) -> RunResult:
        """從最後一個 checkpoint 續跑同一 thread。

        - ``resolution=None``:crash 後續跑(從中斷的節點重跑)。
        - ``resolution`` 給定:回覆 human gate(approved/rejected/edited),
          以 ``Command(resume=...)`` 喚醒 interrupt 中的 human 節點。
        """
        if resolution is None:
            return self._invoke(None, thread_id)
        return self._invoke(
            Command(resume={"resolution": resolution, "note": note}), thread_id
        )

    def get_state(self, thread_id: str) -> PlanState | None:
        """讀回某 thread 最新 checkpoint 的 PlanState(無紀錄回 None)。"""
        snapshot = self._app.get_state(self._config(thread_id))
        values = snapshot.values if snapshot else None
        if not values or "ps" not in values:
            return None
        return PlanState.from_dict(values["ps"])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorkPlanRunner":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 內部 --------------------------------------------------------------

    def _config(self, tid: str) -> dict[str, Any]:
        # recursion_limit:長計劃(數十步 × retry/replan)會超過預設 25 個 super-step
        return {"configurable": {"thread_id": tid}, "recursion_limit": 200}

    def _invoke(self, payload: Any, tid: str) -> RunResult:
        result = self._app.invoke(
            payload,
            config=self._config(tid),
            durability="sync",  # I3:每步落盤
        )
        state = PlanState.from_dict(result["ps"])

        # interrupt 偵測:langgraph 1.x 在回傳 dict 放 __interrupt__
        # (list[Interrupt],payload 在 .value);保險起見備援查 get_state。
        interrupted = False
        interrupt_payload: dict | None = None
        pending = result.get("__interrupt__")
        if not pending and state.status == "blocked":
            snapshot = self._app.get_state(self._config(tid))
            pending = list(snapshot.interrupts) if snapshot else None
        if pending:
            interrupted = True
            first = pending[0]
            value = getattr(first, "value", first)
            interrupt_payload = value if isinstance(value, dict) else {"value": value}

        return RunResult(
            thread_id=tid,
            status=state.status,
            state=state,
            interrupted=interrupted,
            interrupt_payload=interrupt_payload,
        )
