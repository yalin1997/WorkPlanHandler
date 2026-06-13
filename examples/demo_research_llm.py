"""WorkPlanHandler M5 demo:真 LLM 階段「研究報告」任務(需 workplan[langgraph,llm])。

對應 doc 00 §5「真 LLM 階段」的賣點——**分層驗收實際攔截並修正一次失敗**:
  - hard 層(ProgrammaticVerifier):程式判定來源連結數(字數/URL 一類),便宜先跑。
  - soft 層(LLMJudgeVerifier):真 LLM 評「切題」。
  - executor(CallableExecutor):negotiate recitation 注入 + retry feedback。

本 demo 用**離線 stub** 代替兩處 LLM 呼叫(executor 的草稿生成、soft judge),
證明接線正確而不燒 key。換真連線只要:

    from langchain_anthropic import ChatAnthropic
    judge = LLMJudgeVerifier(model=ChatAnthropic(model="claude-haiku-4-5"))
    # executor 的 _research_fn 內把 stub 草稿換成:
    #   resp = ChatAnthropic(model="claude-sonnet-4-6").invoke(prompt)
    #   return StepOutput(content=resp.content)

執行:python examples/demo_research_llm.py
注意:SQLite db 放 /tmp(WSL2 下 /mnt/c 的檔案鎖不可靠)。
"""

import tempfile
import uuid
from pathlib import Path

from workplan.adapters.langgraph import WorkPlanRunner
from workplan.audit import to_markdown, write_audit
from workplan.executors import CallableExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.protocols import StepOutput
from workplan.verifiers import LayeredVerifier, ProgrammaticVerifier
from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier


# ── 離線 stub:模擬任一 LangChain BaseChatModel(只實作 judge 用到的契約)──
class _Runnable:
    def __init__(self, result):
        self._result = result

    def invoke(self, messages):
        return self._result


class StubChatModel:
    def __init__(self, result):
        self._result = result

    def with_structured_output(self, schema):
        return _Runnable(self._result)


# ── hard check:報告需引用 ≥3 個來源連結 ────────────────────────────
def min_urls(output, state):
    n = str(output.content or "").count("http")
    if n >= 3:
        return (True, 1.0, "")
    return (False, 0.0, f"hard:來源連結不足({n}/3),請補足可查證來源")


# ── executor 使用者函式:真連線會在此呼叫 LLM;此處依嘗試次數回固定稿 ──
def research_fn(step, state, ctx):
    _prompt = ctx.with_recitation(  # 真連線會把此 prompt 餵給 LLM(尾端已含 recitation)
        f"請就『{state.plan.goal}』撰寫『{step.description}』。"
        f"前次回饋:{ctx.feedback or '無'}"
    )
    print(f"  · 執行 {step.id}(第 {ctx.attempt + 1} 次);prompt 尾端已注入 recitation")
    if step.id == "s1":
        return StepOutput(content="已蒐集主題資料:背景、現況、挑戰、代表性論文。")
    if ctx.attempt == 0:
        return StepOutput(
            content="初稿草草帶過。來源:https://a.example"
        )  # 1 URL → 被擋
    return StepOutput(
        content=(
            "修訂稿:依回饋補足三個可查證來源。"
            "來源:https://a.example https://b.example https://c.example"
        )
    )


def make_plan() -> Plan:
    return Plan(
        goal="產出 LLM 長任務管理綜述",
        steps=[
            Step(
                id="s1",
                description="蒐集主題資料",
                acceptance=AcceptanceCriterion(
                    description="主題涵蓋充分",
                    kind="llm_judge",
                    spec={"rubric": "涵蓋背景/現況/挑戰"},
                    threshold=0.8,
                ),
            ),
            Step(
                id="s2",
                description="撰寫研究報告段落",
                acceptance=AcceptanceCriterion(
                    description="段落需切題、具體,且引用 ≥3 個來源",
                    kind="llm_judge",
                    spec={"check": "min_urls", "rubric": "段落需切題且有依據"},
                    threshold=0.8,  # soft judge stub 回 0.9 → 通過
                    layer="soft",
                ),
            ),
        ],
    )


# 分層 verifier:hard(URL 數)→ soft(真 LLM judge;此處離線 stub)
verifier = LayeredVerifier(
    layers=[
        ("hard", ProgrammaticVerifier(checks={"min_urls": min_urls}), True),
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

db = str(Path(tempfile.mkdtemp()) / "research.sqlite")
tid = uuid.uuid4().hex

print("=== 研究報告任務(真 LLM 階段,離線 stub)===")
with WorkPlanRunner(
    executor=CallableExecutor(research_fn), verifier=verifier, db_path=db
) as runner:
    res = runner.run(plan=make_plan(), thread_id=tid)

s2 = res.state.plan.steps[1]
print(f"\n結果:status={res.status}, cursor={res.state.cursor}")
print(f"s2 attempts={s2.attempts}(hard 層攔截一次缺來源 → 修正後通過)")

print("\n— 分層驗收軌跡 —")
for e in res.state.history:
    if e["type"] in ("verify_failed", "step_retried", "verify_passed"):
        layer = e["payload"].get("layer", "")
        extra = f" layer={layer}" if layer else ""
        print(f"  {e['type']:<14} step={e['step_id'] or '-'}{extra}")

out_dir = Path(tempfile.mkdtemp())
json_path, md_path = write_audit(res.state, out_dir, basename="research_audit")
print(f"\n審計輸出:\n  {json_path}\n  {md_path}")
print("\n— Markdown 驗收摘要 —")
print(to_markdown(res.state))
