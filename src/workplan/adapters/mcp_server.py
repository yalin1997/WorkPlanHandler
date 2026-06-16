"""MCP gatekeeper server —— 把驗收閘門包成一組 MCP tool(HTTP / fastmcp)。

定位(Tier 2 gatekeeper):agent 自己當 driver、自己執行每一步,但每完成一步
**必須** ``submit`` 交件;server 端跑驗收後才告知能否前進——「沒過不發下一步」。
這對抗 commender 式的「提早收手」,同時把 WorkPlanHandler 的驗收賣點保留在
tool 介面裡(對比 Claude Code ``TodoWrite`` / Codex ``update_plan`` 這類「只記不驗」
的便條紙)。

**閘門強度誠實說明**:tool 終究是 agent 自願呼叫,擋不了它「耍賴不 submit」或
無視 ``may_advance=false``。靠三道兜底把「軟閘門」拉到實務可用:(1) 資訊槓桿——
不 submit 就拿不到 ``next_step``;(2) host hook 加固(選配);(3) 稽核留痕
(``audit_tail`` / event log)。要結構上不可繞過的硬閘門,請用 LangGraph adapter。

架構:engine 是純函式狀態機(D2/I6),本檔是與 ``adapters/langgraph.py`` 平行的
adapter——核心一行不改。tool 邏輯在純 ``Gatekeeper`` 類(不依賴 fastmcp,離線可測);
``build_server`` 才延遲 import fastmcp 把它包成 MCP tool。依賴鐵則(D9):本檔是唯一
可 import fastmcp 的檔;LLM-judge(選配 soft 層)亦延遲 import,純宣告式驗收零 key。

執行:``python -m workplan.adapters.mcp_server --host 127.0.0.1 --port 8000``
依賴:``pip install "workplan[mcp]"``(要 soft 層 LLM-judge 再加 ``[llm]``)
"""

from __future__ import annotations

import uuid
from typing import Any

from .. import engine
from ..engine import Action, Decision
from ..models import AcceptanceCriterion, Plan, PlanState, Step
from ..protocols import StepOutput
from ..stores.json_store import JsonFilePlanStore
from ..verifiers import LayeredVerifier
from ..verifiers.builtin_checks import BUILTIN_CHECKS
from ..verifiers.programmatic import ProgrammaticVerifier

# ── plan spec(agent 經 JSON 送來)→ 內部 Plan ─────────────────────────────


def _acceptance_from_spec(acc: dict | None) -> AcceptanceCriterion:
    """把 agent 的 acceptance dict 轉成 AcceptanceCriterion。

    gatekeeper 偏好宣告式 hard 驗收,故未指定時 kind 預設 "programmatic"、
    layer 預設 "hard"。spec 形如 ``{"check": "min_words", "args": {"n": 50}}``
    (programmatic)或 ``{"rubric": "..."}``(llm_judge)。
    """
    acc = acc or {}
    return AcceptanceCriterion(
        description=acc.get("description", ""),
        kind=acc.get("kind", "programmatic"),
        layer=acc.get("layer", "hard"),
        spec=dict(acc.get("spec", {}) or {}),
        threshold=acc.get("threshold", 1.0),
        required=acc.get("required", True),
    )


def _is_gated(crit: AcceptanceCriterion) -> bool:
    """此驗收條件是否真能構成閘門(否則只是便條紙)。"""
    if crit.kind in ("human", "llm_judge"):
        return True
    return "check" in crit.spec  # programmatic 必須有具名 check


def _plan_from_spec(
    goal: str, steps_spec: list[dict], *, revision_note: str = ""
) -> tuple[Plan, list[str]]:
    if not steps_spec:
        raise ValueError("plan 至少需要一個步驟。")
    steps: list[Step] = []
    warnings: list[str] = []
    for i, s in enumerate(steps_spec):
        if "description" not in s:
            raise ValueError(f"步驟 #{i + 1} 缺 'description'。")
        crit = _acceptance_from_spec(s.get("acceptance"))
        step = Step(
            id=s.get("id") or f"s{i + 1}",
            description=s["description"],
            acceptance=crit,
            max_attempts=int(s.get("max_attempts", 2)),
        )
        steps.append(step)
        if not _is_gated(crit):
            warnings.append(
                f"步驟 {step.id} 無可強制驗收條件(acceptance.spec 無 'check',"
                f"kind 非 llm_judge/human),此步不設閘門、submit 會直接放行。"
            )
    return Plan(goal=goal, steps=steps, revision_note=revision_note), warnings


# ── 純邏輯層(不依賴 fastmcp,離線可測)──────────────────────────────────


class Gatekeeper:
    """工作計劃 + 驗收閘門的純邏輯(MCP tool 都薄薄包這層)。

    Args:
        store: PlanStore 實作;預設 JsonFilePlanStore()。
        verifier: server 端 verifier;預設 hard 層 = 內建宣告式 check 註冊表。
        max_replans: 重規劃上限(沿用 engine 預設)。
    """

    def __init__(
        self,
        *,
        store: Any,
        verifier: Any = None,
        max_replans: int = engine.MAX_REPLANS,
    ) -> None:
        self.store = store
        self.verifier = (
            verifier
            if verifier is not None
            else LayeredVerifier([("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True)])
        )
        self.max_replans = max_replans

    # -- 讀取 / 視圖 --------------------------------------------------------

    def _load(self, thread_id: str) -> PlanState:
        state = self.store.load(thread_id)
        if state is None:
            raise ValueError(f"未知 thread_id:{thread_id!r}(請先呼叫 start)。")
        return state

    def _step_view(self, state: PlanState) -> dict | None:
        step = state.current_step
        if step is None:
            return None
        return {
            "step_id": step.id,
            "description": step.description,
            "acceptance": {
                "description": step.acceptance.description,
                "layer": step.acceptance.layer,
                "kind": step.acceptance.kind,
            },
            "attempt": step.attempts,
            "recitation": state.plan.render_for_recitation(),
        }

    def _verdict(self, thread_id: str, dec: Decision) -> dict:
        a = dec.action
        state = dec.state
        result = {
            Action.EXECUTE: "advanced",
            Action.RETRY: "retry",
            Action.REPLAN: "replan_needed",
            Action.ESCALATE: "escalated",
            Action.DONE: "done",
            Action.FAILED: "failed",
            Action.VERIFY: "verify",  # 理論上不會外露
        }[a]
        next_step = (
            self._step_view(state) if a in (Action.EXECUTE, Action.RETRY) else None
        )
        return {
            "result": result,
            "passed": a in (Action.EXECUTE, Action.DONE),
            "may_advance": a in (Action.EXECUTE, Action.DONE),
            "feedback": dec.feedback,
            "next_step": next_step,
            "recitation": state.plan.render_for_recitation(),
            "audit_tail": [e.to_dict() for e in dec.events],
            "thread_id": thread_id,
            "status": state.status,
        }

    # -- tool 邏輯 ----------------------------------------------------------

    def start(self, goal: str, plan: list[dict], thread_id: str | None = None) -> dict:
        tid = thread_id or uuid.uuid4().hex
        if self.store.load(tid) is not None:
            raise ValueError(f"thread_id {tid!r} 已存在;請改用 current/submit 續用。")
        p, warnings = _plan_from_spec(goal, plan)
        dec = engine.initialize(p, thread_id=tid)
        self.store.save(tid, dec.state)
        return {
            "thread_id": tid,
            "current_step": self._step_view(dec.state),
            "recitation": dec.state.plan.render_for_recitation(),
            "warnings": warnings,
        }

    def submit(
        self, thread_id: str, output: str, artifacts: dict | None = None
    ) -> dict:
        state = self._load(thread_id)
        if state.status != "running":
            raise ValueError(
                f"thread {thread_id} 狀態為 {state.status},不接受 submit(需 running)。"
            )
        out = StepOutput(content=output, artifacts=artifacts)
        dec = engine.on_executed(state, out, max_replans=self.max_replans)
        if dec.action == Action.VERIFY:
            step = dec.state.current_step
            result = self.verifier.verify(step, out, dec.state)
            dec = engine.on_verified(dec.state, result, max_replans=self.max_replans)
        self.store.save(thread_id, dec.state)
        return self._verdict(thread_id, dec)

    def current(self, thread_id: str) -> dict:
        state = self._load(thread_id)
        view = {
            "thread_id": thread_id,
            "status": state.status,
            "recitation": state.plan.render_for_recitation(),
        }
        if state.status == "running":
            view["step"] = self._step_view(state)
        elif state.status == "blocked" and state.pending_human is not None:
            g = state.pending_human
            view["pending_human"] = {
                "step_id": g.step_id,
                "reason": g.reason,
                "asked_at": g.asked_at,
            }
        return view

    def plan(self, thread_id: str) -> dict:
        state = self._load(thread_id)
        return {
            "thread_id": thread_id,
            "goal": state.plan.goal,
            "version": state.plan.version,
            "status": state.status,
            "cursor": state.cursor,
            "replans": state.replans,
            "recitation": state.plan.render_for_recitation(),
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "status": s.status.value,
                    "attempts": s.attempts,
                    "output_summary": (
                        str(s.output)[:200] if s.output is not None else None
                    ),
                }
                for s in state.plan.steps
            ],
        }

    def resolve(self, thread_id: str, resolution: str, note: str = "") -> dict:
        if resolution not in ("approved", "rejected", "edited"):
            raise ValueError("resolution 須為 approved / rejected / edited。")
        state = self._load(thread_id)
        if state.status != "blocked" or state.pending_human is None:
            raise ValueError(
                f"thread {thread_id} 無待解人工關卡(status={state.status})。"
            )
        gate = state.pending_human
        gate.resolution = resolution
        gate.human_note = note or ""
        dec = engine.on_human_resolved(state, gate)
        self.store.save(thread_id, dec.state)
        return self._verdict(thread_id, dec)

    def replan(self, thread_id: str, new_plan: dict) -> dict:
        state = self._load(thread_id)
        if state.status != "running":
            raise ValueError(
                f"thread {thread_id} 狀態為 {state.status},不接受 replan(需 running)。"
            )
        steps_spec = (
            new_plan.get("steps", []) if isinstance(new_plan, dict) else new_plan
        )
        note = new_plan.get("revision_note", "") if isinstance(new_plan, dict) else ""
        p, _ = _plan_from_spec(state.plan.goal, steps_spec, revision_note=note)
        dec = engine.on_replanned(state, p)
        self.store.save(thread_id, dec.state)
        return self._verdict(thread_id, dec)


# ── fastmcp 包裝(唯一 import fastmcp 之處;延遲 import)─────────────────────


def build_server(
    *,
    store: Any = None,
    verifier: Any = None,
    judge_model: Any = None,
    max_replans: int = engine.MAX_REPLANS,
    name: str = "workplan",
) -> Any:
    """組裝 fastmcp server,把 Gatekeeper 的六個方法註冊為 MCP tool。

    judge_model 給定時(需 ``[llm]`` extra)附加 soft 層 LLM-judge;否則純宣告式
    hard 驗收(零 key、離線可測)。
    """
    if store is None:
        store = JsonFilePlanStore()
    if verifier is None:
        layers = [("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True)]
        if judge_model is not None:
            from ..verifiers.llm_judge import LLMJudgeVerifier  # 延遲:需 [llm]

            layers.append(("soft", LLMJudgeVerifier(model=judge_model), True))
        verifier = LayeredVerifier(layers)
    gk = Gatekeeper(store=store, verifier=verifier, max_replans=max_replans)

    from fastmcp import FastMCP  # 延遲:唯一 framework import,需 [mcp]

    mcp = FastMCP(name)

    @mcp.tool
    def start(goal: str, plan: list[dict], thread_id: str | None = None) -> dict:
        """建立工作計劃並回傳第一個待執行步驟。

        plan 為步驟清單,每步形如 {"id"?, "description", "acceptance":
        {"description"?, "kind"?, "layer"?, "spec": {"check": "min_words",
        "args": {"n": 50}}, "max_attempts"?}}。回傳含 thread_id 與 warnings
        (列出無閘門的步驟)。
        """
        return gk.start(goal, plan, thread_id)

    @mcp.tool
    def submit(thread_id: str, output: str, artifacts: dict | None = None) -> dict:
        """交出當前步驟成果並接受驗收。

        回傳 Verdict:result ∈ advanced/retry/replan_needed/escalated/done/
        failed;may_advance=false 代表還不能前進(依 feedback 修正後再 submit);
        next_step 為接下來該做的步驟(retry 時為同一步)。
        """
        return gk.submit(thread_id, output, artifacts)

    @mcp.tool
    def current(thread_id: str) -> dict:
        """取得當前狀態與待執行步驟(含計劃 recitation,對抗目標漂移)。"""
        return gk.current(thread_id)

    @mcp.tool
    def plan(thread_id: str) -> dict:
        """巡檢整個計劃(各步狀態、游標、版本)。"""
        return gk.plan(thread_id)

    @mcp.tool
    def resolve(thread_id: str, resolution: str, note: str = "") -> dict:
        """escalated(blocked)時的人工裁決:approved / rejected / edited。"""
        return gk.resolve(thread_id, resolution, note)

    @mcp.tool
    def replan(thread_id: str, new_plan: dict) -> dict:
        """收到 replan_needed 後提供未完成尾巴的新步驟(保留已完成步,版本遞增)。

        new_plan 形如 {"steps": [...], "revision_note": "..."};steps 用全新
        id(不得與已保留步驟撞名)。
        """
        return gk.replan(thread_id, new_plan)

    return mcp


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="workplan-mcp",
        description="WorkPlanHandler MCP gatekeeper server (HTTP)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--store-root", default=".workplan_store")
    args = parser.parse_args(argv)

    server = build_server(store=JsonFilePlanStore(root=args.store_root))
    server.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
