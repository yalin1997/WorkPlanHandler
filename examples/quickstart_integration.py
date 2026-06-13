"""WorkPlanHandler 整合 quickstart(繁中)——把你自己的 agent + 你的 LLM 接進來。

這支範例回答整合者最常問的一句:**「我已經有自己的 agent 跟 LLM,要怎麼讓它
照計劃走、逐階段被驗收?」** 你需要寫的「膠水」只有兩塊:

  1. **單步執行函式** ``my_agent_step(step, state, ctx)``:呼叫**你自己的 LLM /
     工具 / 子 agent** 產出這一步的內容。用 ``CallableExecutor`` 包起來即可——
     模組會替你做兩件事:把計劃摘要(recitation)注入 prompt 對抗目標漂移、
     把上一次驗收失敗的 feedback 交給你做修正(Reflexion 式 retry)。
  2. **驗收條件(DoD)**:每個 ``Step`` 自帶 ``AcceptanceCriterion``。這裡示範最
     便宜可信的 hard 層 ``ProgrammaticVerifier``(跑你的判定函式);要更語意化
     可再疊 soft 層 ``LLMJudgeVerifier`` 或 human 層 ``HumanGateVerifier``。

組裝完交給 ``WorkPlanRunner``(LangGraph 外掛),就免費獲得:SQLite 持久化、
kill 後同 thread_id 續跑、human gate ``interrupt()``。

執行(離線,不需 key):``python examples/quickstart_integration.py``
需要 ``workplan[langgraph]``;真接 LLM 再加 ``workplan[llm]``。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Callable

from workplan import AcceptanceCriterion, Plan, Step, StepOutput
from workplan.adapters.langgraph import RunResult, WorkPlanRunner
from workplan.executors.callable import CallableExecutor, ExecContext
from workplan.verifiers import LayeredVerifier
from workplan.verifiers.programmatic import ProgrammaticVerifier

# 你的 LLM 呼叫:簽名 (prompt:str) -> str。預設用離線假實作讓 quickstart 免 key 跑;
# 真接線只要換成下面註解那兩行(或 OpenAI / Google 的對應 ChatModel):
#
#     from langchain_anthropic import ChatAnthropic
#     model = ChatAnthropic(model="claude-sonnet-4-6")
#     real_llm = lambda prompt: model.invoke(prompt).content
#
LLMCall = Callable[[str], str]


def _fake_llm(prompt: str) -> str:
    """離線假 LLM:示範用。首稿漏「客群」欄位;一旦 prompt 帶了驗收 feedback
    就補上——藉此確定性地展示「hard 驗收攔截 → retry 帶 feedback → 修正通過」。"""
    base = "市場分析:競品 A/B/C 比較,價格與功能定位。"
    if "請修正" in prompt:  # prompt 裡出現 feedback 區段 → 補上缺漏欄位
        return base + " 目標客群:25–35 歲上班族。"
    return base


# ── 膠水 1:你的單步執行函式(這裡面才是「你的 agent」)──────────────────
def my_agent_step(llm_call: LLMCall):
    """回傳一個 fn(step, state, ctx) -> StepOutput,內部呼叫你的 LLM。"""

    def _step(step: Step, state, ctx: ExecContext) -> StepOutput:
        prompt = f"請完成這個步驟:{step.description}"
        # ctx.feedback:上一次驗收失敗的可行動回饋(首次為空)
        if ctx.feedback:
            prompt += f"\n\n上次驗收未過,請修正:{ctx.feedback}"
        # ctx.with_recitation:把整體計劃摘要接到 prompt 尾端(對抗長任務目標漂移)
        prompt = ctx.with_recitation(prompt)
        content = llm_call(prompt)  # ← 這行就是你的 LLM / 工具 / 子 agent
        return StepOutput(content=content)

    return _step


# ── 膠水 2:驗收條件(DoD)+ 對應的 hard check ─────────────────────────────
def _has_segment(output: StepOutput, state) -> tuple[bool, float, str]:
    """hard check:報告必須含「客群」欄位,否則擋下並回可行動 feedback。"""
    text = str(output.content)
    if "客群" in text:
        return True, 1.0, ""
    return False, 0.0, "報告缺少『目標客群』欄位,請補上。"


def _min_length(output: StepOutput, state) -> bool:
    return len(str(output.content)) >= 10


def build_plan() -> Plan:
    """3 步計劃;第 2 步的 hard 驗收要求含『客群』(首稿故意漏 → 觸發一次 retry)。"""
    return Plan(
        goal="產出市場分析報告",
        steps=[
            Step(
                id="s1",
                description="蒐集競品基礎資料",
                acceptance=AcceptanceCriterion(
                    description="內容非空",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "min_length"},
                ),
            ),
            Step(
                id="s2",
                description="撰寫市場分析(需含目標客群)",
                acceptance=AcceptanceCriterion(
                    description="含『目標客群』欄位",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "has_segment"},
                ),
            ),
            Step(
                id="s3",
                description="彙整結論",
                acceptance=AcceptanceCriterion(
                    description="內容非空",
                    kind="programmatic",
                    layer="hard",
                    spec={"check": "min_length"},
                ),
            ),
        ],
    )


def run_quickstart(
    *, llm_call: LLMCall = _fake_llm, db_path: str | None = None
) -> RunResult:
    """組裝 executor + verifier + runner 並跑完一次,回傳結果摘要。"""
    # check 以「註冊名」引用(經 adapter 持久化時 spec 必須保持純 JSON,I2)
    verifier = LayeredVerifier(
        layers=[
            (
                "hard",
                ProgrammaticVerifier(
                    checks={"min_length": _min_length, "has_segment": _has_segment}
                ),
                True,  # required:hard 層失敗即短路(fail-closed)
            ),
            # 想要語意驗收:再加 ("soft", LLMJudgeVerifier(model=...), True)
            # 想要人工關卡:再加 ("human", HumanGateVerifier(), False)
        ]
    )
    executor = CallableExecutor(my_agent_step(llm_call))  # 預設注入 recitation

    db = db_path or str(Path(tempfile.mkdtemp()) / "quickstart.sqlite")
    with WorkPlanRunner(executor=executor, verifier=verifier, db_path=db) as runner:
        return runner.run(plan=build_plan(), thread_id=uuid.uuid4().hex)


if __name__ == "__main__":
    res = run_quickstart()
    print(f"status={res.status}, cursor={res.state.cursor}")
    s2 = res.state.plan.steps[1]
    print(f"s2 attempts={s2.attempts}(首稿漏客群 → retry → 補上後通過)")
    print(f"s2 notes={s2.notes}")
    print("\n最終各步產出:")
    for s in res.state.plan.steps:
        print(f"  {s.id} [{s.status}] {str(s.output)[:50]}")
