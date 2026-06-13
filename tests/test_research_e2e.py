"""M5b 真 LLM 階段 E2E(離線 stub,不燒 key)——doc 00 §5「真 LLM 階段」。

用「研究報告」任務證明 doc 00 §5 的賣點:**分層驗收(programmatic 字數/URL
+ llm_judge 切題)實際攔截並修正一次失敗**。

接線完全是真路徑(LayeredVerifier + ProgrammaticVerifier + LLMJudgeVerifier
+ CallableExecutor,經 LangGraph adapter + SqliteSaver),只把「會呼叫 LLM 的
兩處」換成離線 stub:
  - executor 的使用者函式:不真連 LLM,依 ctx.attempt 回固定草稿/修訂稿;
  - soft 層 judge:注入 StubChatModel 回固定 verdict。
真連線只需把這兩處換成 ChatAnthropic 實例(見 examples/demo_research_llm.py)。

依賴 langgraph + llm extra;未裝則整檔 skip。
"""

from __future__ import annotations

import pytest
from stub_chat_model import StubChatModel

from workplan.audit import to_markdown
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.protocols import StepOutput

pytest.importorskip("langgraph")
pytest.importorskip("langchain", reason="需要 llm extra:pip install 'workplan[llm]'")

from workplan.adapters.langgraph import WorkPlanRunner  # noqa: E402
from workplan.executors import CallableExecutor  # noqa: E402
from workplan.verifiers import LayeredVerifier, ProgrammaticVerifier  # noqa: E402
from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier  # noqa: E402


def _min_urls(output: StepOutput, state) -> tuple[bool, float, str]:
    """hard check:報告需引用 ≥3 個來源連結(programmatic 字數/URL 一類)。"""
    n = str(output.content or "").count("http")
    if n >= 3:
        return (True, 1.0, "")
    return (False, 0.0, f"hard:來源連結不足({n}/3),請補足可查證來源")


def _research_step() -> Step:
    # 單一 criterion 同時驅動 hard(spec 有 check)與 soft(rubric)兩層(規格 03)。
    return Step(
        id="s2",
        description="撰寫研究報告段落",
        acceptance=AcceptanceCriterion(
            description="段落需切題、具體,且引用 ≥3 個來源",
            kind="llm_judge",
            spec={"check": "min_urls", "rubric": "段落需切題且有依據"},
            threshold=0.8,
            layer="soft",
        ),
    )


def _plan() -> Plan:
    s1 = Step(
        id="s1",
        description="蒐集主題資料",
        acceptance=AcceptanceCriterion(
            description="主題涵蓋充分",
            kind="llm_judge",
            spec={"rubric": "切題"},
            threshold=0.8,
        ),
    )
    return Plan(goal="產出 LLM 長任務管理綜述", steps=[s1, _research_step()])


def _research_fn(step, state, ctx):
    """stub 版執行函式:依嘗試次數回草稿/修訂稿(真連線會改呼叫 LLM)。"""
    # recitation 注入點(survey §4.1):真連線會把 prompt 餵給 LLM
    _prompt = ctx.with_recitation(
        f"請就『{state.plan.goal}』撰寫『{step.description}』。"
        f"前次回饋:{ctx.feedback or '無'}"
    )
    if step.id == "s1":
        return StepOutput(content="已蒐集主題資料:背景、現況、挑戰。")
    if ctx.attempt == 0:  # 初稿:只有 1 個來源 → hard 層攔截
        return StepOutput(content="初稿草草帶過。來源:https://a.example")
    # 修訂稿:依回饋補足 3 個來源 → 通過
    return StepOutput(
        content="修訂稿,已補足來源:https://a.example https://b.example https://c.example"
    )


def _verifier() -> LayeredVerifier:
    return LayeredVerifier(
        layers=[
            ("hard", ProgrammaticVerifier(checks={"min_urls": _min_urls}), True),
            (
                "soft",
                LLMJudgeVerifier(
                    model=StubChatModel(
                        JudgeVerdict(score=0.9, passed=True, feedback="切題且具體")
                    )
                ),
                True,
            ),
        ]
    )


def test_layered_verification_intercepts_and_fixes_once(tmp_path):
    db = str(tmp_path / "research.sqlite")
    with WorkPlanRunner(
        executor=CallableExecutor(_research_fn),
        verifier=_verifier(),
        db_path=db,
    ) as runner:
        res = runner.run(plan=_plan(), thread_id="research-1")

    assert res.status == "done"
    assert res.state.cursor == 2  # 兩步皆 DONE

    history = res.state.history
    s2_failed = [
        e for e in history if e["type"] == "verify_failed" and e["step_id"] == "s2"
    ]
    s2_passed = [
        e for e in history if e["type"] == "verify_passed" and e["step_id"] == "s2"
    ]
    # 恰好攔截一次(hard 層),修正後通過
    assert len(s2_failed) == 1
    assert s2_failed[0]["payload"]["layer"] == "hard"
    assert len(s2_passed) == 1

    s2 = res.state.plan.steps[1]
    assert s2.attempts == 1  # 一次失敗後重試一次即過

    # 審計摘要可產出且含目標
    md = to_markdown(res.state)
    assert "產出 LLM 長任務管理綜述" in md
