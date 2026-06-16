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

import threading
import uuid
from typing import Any

from .. import engine
from ..engine import Action, Decision
from ..models import AcceptanceCriterion, Plan, PlanState, Step
from ..protocols import StepOutput, VerificationResult
from ..stores.json_store import JsonFilePlanStore
from ..verifiers import LayeredVerifier
from ..verifiers.builtin_checks import BUILTIN_CHECKS
from ..verifiers.programmatic import ProgrammaticVerifier

# server 能力集:acceptance.kind → 要能擋住它所需的 verifier 層名。
# programmatic 走 hard 層(離線恆在);llm_judge 需 soft 層;human 需 human 層。
_KIND_REQUIRES_LAYER = {"llm_judge": "soft", "human": "human"}

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


def _is_gated(crit: AcceptanceCriterion, capabilities: set[str]) -> bool:
    """此驗收條件在「server 實際能力集」下是否真能構成閘門(否則只是便條紙)。

    關鍵:只看「宣告的 kind」不夠——必須問 server 有沒有對應層擋得住。
    llm_judge 需 soft 層、human 需 human 層;programmatic 走恆在的 hard 層,
    但仍需具名 check 才有東西可驗。
    """
    need = _KIND_REQUIRES_LAYER.get(crit.kind)
    if need is not None:
        return need in capabilities
    return "check" in crit.spec  # programmatic 必須有具名 check


def _require_enforceable(step: Step, capabilities: set[str]) -> None:
    """fail-closed:若某步宣告了 server 能力集擋不住的驗收層 → raise(B1 修法核心)。

    這擋掉「宣告 llm_judge/human 閘門、server 卻偷偷沒擋」的 fail-open:
    agent 無法靠假閘門開跑。要正當關閉閘門只有唯一入口 mode="advisory"。
    """
    crit = step.acceptance
    need = _KIND_REQUIRES_LAYER.get(crit.kind)
    if need is not None and need not in capabilities:
        raise ValueError(
            f"步驟 {step.id} 宣告 {crit.kind!r} 驗收(需 server 啟用 {need!r} 層),"
            f"但目前能力集為 {sorted(capabilities)}。"
            f"請為 server 掛上對應層(soft 需 judge_model;human 需 enable_human),"
            f"或改用 mode='advisory' 由 operator 有意識地關閉閘門。"
        )


def _plan_from_spec(
    goal: str,
    steps_spec: list[dict],
    *,
    capabilities: set[str],
    mode: str = "gated",
    revision_note: str = "",
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
        if mode == "advisory":
            continue  # operator 有意識關閘門:跳過能力校驗與逐步 warning(start 統一標示)
        # gated:宣告了擋不住的層 → fail-closed;programmatic 無 check → 僅 warning(守 I5)
        _require_enforceable(step, capabilities)
        if not _is_gated(crit, capabilities):
            warnings.append(
                f"步驟 {step.id} 無可強制驗收條件(acceptance.spec 無 'check'),"
                f"此步不設閘門、submit 會直接放行。"
            )
    return Plan(goal=goal, steps=steps, revision_note=revision_note), warnings


def _derive_capabilities(verifier: Any) -> set[str]:
    """從 verifier 推導 server 能力集:LayeredVerifier 取其層名;否則保守視為 hard。"""
    layers = getattr(verifier, "layers", None)
    if layers is not None:
        return {name for name, _, _ in layers}
    return {"hard"}


# ── 純邏輯層(不依賴 fastmcp,離線可測)──────────────────────────────────


class Gatekeeper:
    """工作計劃 + 驗收閘門的純邏輯(MCP tool 都薄薄包這層)。

    Args:
        store: PlanStore 實作;預設 JsonFilePlanStore()。
        verifier: server 端 verifier;預設 hard 層 = 內建宣告式 check 註冊表。
        capabilities: server 能擋住的驗收層集合;預設由 verifier 推導
            (LayeredVerifier 取其層名)。決定 start 能力校驗與 _is_gated 判定。
        mode: "gated"(預設,server 端閘門生效)或 "advisory"(關閉閘門、
            submit 一律放行,由 agent 自決完成,= TodoWrite 式便條紙)。
        max_replans: 重規劃上限(沿用 engine 預設)。
    """

    def __init__(
        self,
        *,
        store: Any,
        verifier: Any = None,
        capabilities: set[str] | None = None,
        mode: str = "gated",
        max_replans: int = engine.MAX_REPLANS,
    ) -> None:
        if mode not in ("gated", "advisory"):
            raise ValueError(f"未知 mode:{mode!r}(允許 'gated' / 'advisory')。")
        self.store = store
        self.verifier = (
            verifier
            if verifier is not None
            else LayeredVerifier([("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True)])
        )
        self.capabilities = (
            set(capabilities)
            if capabilities is not None
            else _derive_capabilities(self.verifier)
        )
        self.mode = mode
        self.max_replans = max_replans
        # B2:per-thread 鎖,包住整個 load→compute→save(避免 read-modify-write race)。
        # 限制:threading.Lock 僅單一 process 內有效;多 worker process 需檔案鎖/CAS。
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, thread_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[thread_id] = lock
            return lock

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
        with self._lock_for(tid):
            if self.store.load(tid) is not None:
                raise ValueError(
                    f"thread_id {tid!r} 已存在;請改用 current/submit 續用。"
                )
            # 能力校驗在建構計劃時進行:宣告了擋不住的層即 fail-closed(B1)。
            p, warnings = _plan_from_spec(
                goal, plan, capabilities=self.capabilities, mode=self.mode
            )
            if self.mode == "advisory":
                warnings = [
                    "advisory 模式:server 端不設任何驗收閘門,submit 一律放行"
                    "(由 agent 自決完成,等同 TodoWrite 式便條紙)。"
                ]
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
        with self._lock_for(thread_id):  # B2:load→compute→save 全程持鎖
            state = self._load(thread_id)
            if state.status != "running":
                raise ValueError(
                    f"thread {thread_id} 狀態 {state.status},submit 需 running。"
                )
            out = StepOutput(content=output, artifacts=artifacts)
            dec = engine.on_executed(state, out, max_replans=self.max_replans)
            if dec.action == Action.VERIFY:
                if self.mode == "advisory":
                    # advisory:不跑驗收,逕記為通過(Tier 1 便條紙)。
                    result = VerificationResult(
                        passed=True,
                        score=1.0,
                        feedback="advisory:無 server 端閘門,逕行推進。",
                        layer="advisory",
                    )
                else:
                    step = dec.state.current_step
                    result = self.verifier.verify(step, out, dec.state)
                dec = engine.on_verified(
                    dec.state, result, max_replans=self.max_replans
                )
            self.store.save(thread_id, dec.state)
            verdict = self._verdict(thread_id, dec)
            if self.mode == "advisory":
                verdict["warnings"] = [
                    "advisory:此 verdict 未經 server 端驗收,may_advance 恆為 true。"
                ]
            return verdict

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
        with self._lock_for(thread_id):  # B2:全程持鎖
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
        with self._lock_for(thread_id):  # B2:全程持鎖
            state = self._load(thread_id)
            if state.status != "running":
                raise ValueError(
                    f"thread {thread_id} 狀態 {state.status},replan 需 running。"
                )
            steps_spec = (
                new_plan.get("steps", []) if isinstance(new_plan, dict) else new_plan
            )
            note = (
                new_plan.get("revision_note", "") if isinstance(new_plan, dict) else ""
            )
            p, _ = _plan_from_spec(
                state.plan.goal,
                steps_spec,
                capabilities=self.capabilities,
                mode=self.mode,
                revision_note=note,
            )
            dec = engine.on_replanned(state, p)
            self.store.save(thread_id, dec.state)
            return self._verdict(thread_id, dec)


# ── fastmcp 包裝(唯一 import fastmcp 之處;延遲 import)─────────────────────


def build_server(
    *,
    store: Any = None,
    verifier: Any = None,
    judge_model: Any = None,
    enable_human: bool = False,
    mode: str = "gated",
    max_replans: int = engine.MAX_REPLANS,
    name: str = "workplan",
) -> Any:
    """組裝 fastmcp server,把 Gatekeeper 的六個方法註冊為 MCP tool。

    驗收層可配置(F1):
      - hard:永遠啟用(離線宣告式 check 註冊表,零 key)。
      - soft:``judge_model`` 給定時(需 ``[llm]`` extra)附加 LLM-judge。
      - human:``enable_human=True`` 時附加 HumanGateVerifier(對映 resolve 流程)。

    閘門開關(F2):``mode="gated"``(預設,server 端驗收生效)或 ``"advisory"``
    (關閉閘門、submit 一律放行,由 agent 自決完成)。

    能力集由所掛層自動推導:宣告了 server 未掛之層的步驟,``start`` 會 fail-closed
    報錯(B1)——不會出現「宣告了閘門卻偷偷放行」的 fail-open。
    """
    if store is None:
        store = JsonFilePlanStore()
    if verifier is None:
        layers = [("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True)]
        if judge_model is not None:
            from ..verifiers.llm_judge import LLMJudgeVerifier  # 延遲:需 [llm]

            layers.append(("soft", LLMJudgeVerifier(model=judge_model), True))
        if enable_human:
            from ..verifiers.human_gate import HumanGateVerifier

            layers.append(("human", HumanGateVerifier(), True))
        verifier = LayeredVerifier(layers)
    gk = Gatekeeper(store=store, verifier=verifier, mode=mode, max_replans=max_replans)

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
