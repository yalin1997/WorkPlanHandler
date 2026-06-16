"""整合範例:MCP gatekeeper 的驗收層配置與 advisory 模式(B1/B2 修復後)。

承 ``mcp_gatekeeper.py``(純 hard 層的「沒過不發下一步」),本檔聚焦修復後新增的
三種能力,全部離線、零 key:

  1. **fail-closed 能力校驗(B1)**:預設 server 只有 hard 層;某步宣告了
     ``llm_judge`` 卻沒掛 soft 層 → ``start`` 直接報錯(不讓假閘門開跑)。
  2. **advisory 全局開關(F2)**:operator 有意識地關閘門 → ``submit`` 一律放行、
     warnings 標明「無 server 端閘門」(= TodoWrite 式便條紙,由 agent 自決)。
  3. **配置 soft 層**:給 ``Gatekeeper`` 掛上 LLM-judge(本檔用離線 stub model 取代
     真 LLM),``llm_judge`` 步即可真正過/擋 soft 驗收。

真實部署時,層配置經 ``build_server(judge_model=..., enable_human=..., mode=...)``
完成;本檔直接驅動純邏輯層 ``Gatekeeper`` 以便離線觀察。

執行(離線):
    python examples/integrations/mcp_gatekeeper_modes.py
依賴:純核心 + workplan[llm](LLMJudgeVerifier);本檔以 stub model 注入,不連網。
"""

from __future__ import annotations

import tempfile

from workplan.adapters.mcp_server import Gatekeeper
from workplan.stores.json_store import JsonFilePlanStore
from workplan.verifiers import LayeredVerifier
from workplan.verifiers.builtin_checks import BUILTIN_CHECKS
from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier
from workplan.verifiers.programmatic import ProgrammaticVerifier

# 宣告了 soft 層(llm_judge)驗收的計劃——server 必須有 soft 層才擋得住。
LLM_JUDGE_PLAN = [
    {
        "id": "s1",
        "description": "撰寫一段市場洞察",
        "acceptance": {
            "description": "內容須完整、有洞察",
            "kind": "llm_judge",
            "layer": "soft",
            "spec": {"rubric": "是否提出有根據的市場洞察"},
            "threshold": 0.7,
        },
    }
]


def _store() -> JsonFilePlanStore:
    return JsonFilePlanStore(root=tempfile.mkdtemp(prefix="wph_mcp_modes_"))


class _StubJudgeModel:
    """離線 stub:模擬 LangChain model 的 with_structured_output 契約,回固定 verdict。

    真實部署改注入 init_chat_model(...) 即可,gatekeeper 程式碼一行不用改。
    """

    def __init__(self, verdict: JudgeVerdict) -> None:
        self._verdict = verdict

    def with_structured_output(self, _schema: type) -> "_StubJudgeModel":
        return self

    def invoke(self, _messages: object) -> JudgeVerdict:
        return self._verdict


def demo_fail_closed() -> None:
    """B1:純 hard server 遇到 llm_judge 步 → start fail-closed 報錯。"""
    print("=== 1) fail-closed 能力校驗(B1)===")
    gk = Gatekeeper(store=_store())  # 預設僅 hard 層
    try:
        gk.start("市場研究", LLM_JUDGE_PLAN)
        print("    !! 不該走到這(應已報錯)")
    except ValueError as exc:
        print(f"    ✓ start 被擋下(fail-closed):{exc}")


def demo_advisory() -> None:
    """F2:advisory 模式 → 同一計劃可開跑,submit 一律放行、warnings 標明無閘門。"""
    print("\n=== 2) advisory 全局開關(F2)===")
    gk = Gatekeeper(store=_store(), mode="advisory")
    started = gk.start("市場研究", LLM_JUDGE_PLAN)
    print(f"    start warnings={started['warnings']}")
    v = gk.submit(started["thread_id"], "（隨便寫的內容）")
    print(
        f"    ✓ submit result={v['result']} may_advance={v['may_advance']} "
        f"warnings={v.get('warnings')}"
    )


def demo_soft_layer() -> None:
    """配置 soft 層(離線 stub judge)→ llm_judge 步真的過/擋 soft 驗收。"""
    print("\n=== 3) 配置 soft 層後 llm_judge 真驗收 ===")
    for label, passed in (("judge 判過", True), ("judge 判不過", False)):
        judge = LLMJudgeVerifier(
            model=_StubJudgeModel(
                JudgeVerdict(
                    score=0.9 if passed else 0.2,
                    passed=passed,
                    feedback="" if passed else "缺乏具體數據支撐,洞察不足。",
                )
            )
        )
        verifier = LayeredVerifier(
            [
                ("hard", ProgrammaticVerifier(BUILTIN_CHECKS), True),
                ("soft", judge, True),
            ]
        )
        gk = Gatekeeper(store=_store(), verifier=verifier)
        tid = gk.start("市場研究", LLM_JUDGE_PLAN)["thread_id"]  # 不再 fail-closed
        v = gk.submit(tid, "本季智慧手錶滲透率達 18%,健康監測為主要驅動。")
        line = f"    [{label}] result={v['result']} may_advance={v['may_advance']}"
        if v["feedback"]:
            line += f"\n        ↳ feedback: {v['feedback']}"
        print(line)


def main() -> None:
    demo_fail_closed()
    demo_advisory()
    demo_soft_layer()
    print(
        "\n結論:閘門要嘛真擋(hard/soft/human 任一層擋得住),要嘛由 operator 經 "
        "mode='advisory' 明確關閉——絕不會出現「宣告了閘門卻偷偷放行」的 fail-open。"
    )


if __name__ == "__main__":
    main()
