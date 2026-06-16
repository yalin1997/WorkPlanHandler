"""WorkPlanHandler M4 demo:provider-agnostic 模型注入(離線,不燒 key)。

展示本次 M4 的核心賣點——**使用者自己注入模型實例接通 LLM**:
LLMPlanner / LLMJudgeVerifier 只依賴 LangChain 的標準介面
``model.with_structured_output(Schema).invoke(msgs)``,因此 Anthropic /
OpenAI / Google 的 ChatModel 都能直接傳進來。

本 demo 用一個**離線 stub model**(回罐頭結構化結果)代替真連線,證明接線
正確而不需 API key。真連線只要把 stub 換成:

    from langchain_anthropic import ChatAnthropic
    planner = LLMPlanner(model=ChatAnthropic(model="claude-sonnet-4-6"))
    judge   = LLMJudgeVerifier(model=ChatAnthropic(model="claude-haiku-4-5"))
    # 或不傳 model,用預設 model_name 走 init_chat_model(需設好 API key)
    # 接 OpenAI:LLMPlanner(model_name="openai:gpt-4.1")(需裝 langchain-openai)

執行:python examples/demo_llm_injection.py(需安裝 workplan[llm])
"""

from workplan.planners.llm_planner import (
    AcceptanceDraft,
    LLMPlanner,
    PlanDraft,
    StepDraft,
)
from workplan.protocols import StepOutput
from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier


class _Runnable:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class StubChatModel:
    """離線 stub:模擬任一 LangChain BaseChatModel(只實作必要的契約)。"""

    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return _Runnable(self._result)


def main() -> None:
    # ── 1) 注入「planner 模型」:回罐頭 PlanDraft ────────────────────
    plan_draft = PlanDraft(
        steps=[
            StepDraft(
                description="蒐集三家競品的定價與功能",
                acceptance=AcceptanceDraft(
                    rubric="至少三家競品,各含定價與兩項功能,附來源連結",
                    threshold=0.8,
                ),
            ),
            StepDraft(description="彙整成一頁摘要"),  # 故意不給 acceptance → 補預設
        ]
    )
    planner = LLMPlanner(model=StubChatModel(plan_draft))
    plan = planner.make_plan("產出競品分析", {"deadline": "本週五"})

    print("=== LLMPlanner.make_plan(注入模型,離線)===")
    print(plan.render_for_recitation())
    for s in plan.steps:
        print(
            f"  - {s.id}: kind={s.acceptance.kind} layer={s.acceptance.layer} "
            f"threshold={s.acceptance.threshold} rubric={s.acceptance.spec['rubric']!r}"
        )
    if planner.last_warnings:
        print("  I5 警告(已補預設驗收):", planner.last_warnings)

    # ── 2) 注入「judge 模型」(刻意用較輕模型);離線回固定 verdict ──
    judge = LLMJudgeVerifier(
        model=StubChatModel(
            JudgeVerdict(score=0.85, passed=True, feedback="三家齊全且附來源")
        )
    )
    step = plan.steps[0]
    result = judge.verify(
        step, StepOutput(content="competitor A/B/C ... 來源:..."), None
    )
    print("\n=== LLMJudgeVerifier.verify(soft 層,離線)===")
    print(
        f"  passed={result.passed} score={result.score} "
        f"layer={result.layer} feedback={result.feedback!r}"
    )
    print(
        f"  (passed 由 threshold {step.acceptance.threshold} 重算,不直接信任模型旗標)"
    )

    print(
        "\n換真連線只需把 StubChatModel 換成 ChatAnthropic/ChatOpenAI 等實例(見檔頭)。"
    )


if __name__ == "__main__":
    main()
