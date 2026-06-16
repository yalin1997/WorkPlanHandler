"""整合範例:MCP gatekeeper —— 把驗收閘門當成 agent 可呼叫的 tool。

對照 ``langgraph_commender.py``(engine 當外迴圈 driver、agent 是 graph 節點),
本範例是相反範式:**agent 自己當 driver**,每完成一步就 ``submit`` 交件,server
端驗收後才告知能否前進——「沒過不發下一步」。這就是 Claude Code ``TodoWrite`` /
Codex ``update_plan`` 的「便條紙」加上一道**真驗收閘門**。

本檔離線直接驅動 ``Gatekeeper``(MCP tool 的純邏輯層),不需網路、不需 key,
展示閘門攔截「弱輸出」並要求修正。真實部署時,這些方法由 ``build_server()``
包成 MCP tool 經 HTTP 暴露:

    python -m workplan.adapters.mcp_server --host 127.0.0.1 --port 8000

然後任何 MCP client(Claude Code / Cursor / Codex / 自家 agent)接上即可。

執行(離線):
    python examples/integrations/mcp_gatekeeper.py
依賴:純核心即可跑本檔;跑真 server 需 workplan[mcp](fastmcp)。
"""

from __future__ import annotations

import tempfile

from workplan.adapters.mcp_server import Gatekeeper
from workplan.stores.json_store import JsonFilePlanStore

# agent 自己拆出的計劃(經 MCP 時這是一段 JSON)。每步的 acceptance 帶宣告式
# check:hard 層在 server 端以 name+args 解析,agent 無從繞過。
PLAN_SPEC = [
    {
        "id": "s1",
        "description": "蒐集主要競品資料",
        "acceptance": {
            "description": "至少 5 個字、且提到三家競品",
            "spec": {"check": "min_words", "args": {"n": 5}},
        },
    },
    {
        "id": "s2",
        "description": "提出差異化策略建議",
        "acceptance": {
            "description": "需明確含『建議』",
            "spec": {"check": "contains", "args": {"all": ["建議"]}},
        },
    },
]


def _print_verdict(label: str, v: dict) -> None:
    line = f"[{label}] result={v['result']} may_advance={v['may_advance']}"
    if v["feedback"]:
        line += f"\n        ↳ feedback: {v['feedback']}"
    if v["next_step"]:
        ns = v["next_step"]
        line += f"\n        → next: {ns['step_id']} {ns['description']}"
    print(line)


def main() -> None:
    # 用臨時目錄當 store,離線、可重複跑。
    store = JsonFilePlanStore(root=tempfile.mkdtemp(prefix="wph_mcp_demo_"))
    gk = Gatekeeper(store=store)  # 預設 hard 層 = 內建宣告式 check

    started = gk.start("撰寫市場進入策略分析", PLAN_SPEC)
    tid = started["thread_id"]
    n_steps, warns = len(PLAN_SPEC), started["warnings"]
    print(f"[start] thread={tid[:8]} 共 {n_steps} 步;warnings={warns}")
    print(started["recitation"], "\n")

    # s1:agent 先交一個「弱輸出」→ 被閘門擋下,拿到 feedback。
    _print_verdict("submit s1 (弱)", gk.submit(tid, "競品很多"))
    # agent 依 feedback 修正後再交 → 通過,拿到下一步。
    _print_verdict(
        "submit s1 (修正)",
        gk.submit(tid, "競品 A 主打低價、B 主打功能、C 主打服務"),
    )

    # s2:漏了「建議」二字 → 擋;補上 → 全部完成。
    _print_verdict("submit s2 (漏關鍵字)", gk.submit(tid, "我認為應該強化售後服務"))
    _print_verdict(
        "submit s2 (修正)", gk.submit(tid, "我的策略建議:差異化定價 + 強化售後")
    )

    print()
    view = gk.plan(tid)
    print(f"[plan] status={view['status']} cursor={view['cursor']}")
    for s in view["steps"]:
        print(f"    {s['id']} [{s['status']}] attempts={s['attempts']}")


if __name__ == "__main__":
    main()
