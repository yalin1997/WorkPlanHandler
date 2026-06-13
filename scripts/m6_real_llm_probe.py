"""M6-3:跨 provider 真 LLM 穩定度 probe(docs/phase2/00 §4.1 第 3 項)。

**目的**:M4/M5 的 LLM 元件至今只用離線 stub 驗證——「接線正確」≠「真效果」。
本 probe 燒一次 key,量測真模型下的:

  1. **judge 評分重現性**:對同一份(step, output)連跑 N 次 ``LLMJudgeVerifier``,
     記錄分數的 mean / min / max / **spread(max−min)**。spread 越小越可信。
  2. **fail-closed 率**:非通過(含模型呼叫失敗/非結構化 → fail-closed)次數。
  3. **recitation 注入**:確認計劃摘要確實出現在送給 LLM 的 prompt 尾端。
  4. **OpenAI 煙霧測**:確認 ``with_structured_output`` 跨 provider 真的通,
     且 fail-closed 不被無故觸發。

**設計**:量測聚合(``summarize`` / ``run_judge_probe``)是純邏輯,離線可單測
(tests/test_real_llm_probe.py);``main()`` 才真連線。無 key 時 main 直接跳過
對應 provider,不會炸。

**執行(燒 key,由人手動跑)**:
    export ANTHROPIC_API_KEY=sk-...        # 必需
    export OPENAI_API_KEY=sk-...           # 選配(OpenAI 煙霧測)
    python scripts/m6_real_llm_probe.py
產出:一份可審計實測紀錄(JSON + Markdown)寫到 ./m6_probe_out/。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workplan.models import AcceptanceCriterion, Plan, PlanState, Step
from workplan.protocols import StepOutput

# 固定 judge 探針輸入:一份「應該通過」的切題、具來源段落。
_PROBE_STEP = Step(
    id="probe",
    description="撰寫研究報告段落",
    acceptance=AcceptanceCriterion(
        description="段落需切題、具體,且有可查證依據",
        kind="llm_judge",
        spec={"rubric": "段落需切題、具體,且有依據"},
        threshold=0.8,
        layer="soft",
    ),
)
_PROBE_OUTPUT = StepOutput(
    content=(
        "本段落聚焦 LLM 長任務管理的驗收閘門:以 verifier-as-gate 取代事後評測,"
        "確保每階段達成 DoD 才推進。來源:https://a.example https://b.example "
        "https://c.example"
    )
)


@dataclass
class JudgeProbeReport:
    """judge 重現性量測結果(純資料,可序列化)。"""

    n: int
    scores: list[float]
    passed: list[bool]
    mean: float
    score_min: float
    score_max: float
    spread: float  # max − min;越小代表 judge 越可重現
    non_pass_count: int  # 非通過次數(含 fail-closed),供判讀可信度


def summarize(*, scores: list[float], passed: list[bool]) -> JudgeProbeReport:
    """把 N 次 verify 的 (scores, passed) 聚合成報告(純函式)。"""
    n = len(scores)
    if n == 0:
        return JudgeProbeReport(0, [], [], 0.0, 0.0, 0.0, 0.0, 0)
    return JudgeProbeReport(
        n=n,
        scores=list(scores),
        passed=list(passed),
        mean=sum(scores) / n,
        score_min=min(scores),
        score_max=max(scores),
        spread=max(scores) - min(scores),
        non_pass_count=sum(1 for p in passed if not p),
    )


def run_judge_probe(judge: Any, *, runs: int = 5) -> JudgeProbeReport:
    """對固定輸入連跑 judge N 次,聚合分數與通過與否。"""
    state = PlanState(plan=Plan(goal="LLM 長任務管理綜述", steps=[_PROBE_STEP]))
    scores: list[float] = []
    passed: list[bool] = []
    for _ in range(runs):
        result = judge.verify(_PROBE_STEP, _PROBE_OUTPUT, state)
        scores.append(result.score)
        passed.append(result.passed)
    return summarize(scores=scores, passed=passed)


# ── 真連線部分(main 才用;無 key 自動跳過)────────────────────────────────
def _build_anthropic():
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-haiku-4-5", temperature=0)


def _build_openai():
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("  · 未裝 langchain-openai,跳過 OpenAI 煙霧測")
        return None
    return ChatOpenAI(model="gpt-4.1-mini", temperature=0)


def _probe_recitation_injection() -> bool:
    """確認 CallableExecutor 把 recitation 接到 prompt 尾端(離線即可驗,不燒 key)。"""
    from workplan.executors.callable import CallableExecutor

    seen: dict[str, str] = {}

    def fn(step, state, ctx):
        seen["prompt"] = ctx.with_recitation(f"做:{step.description}")
        return StepOutput(content="ok")

    state = PlanState(plan=Plan(goal="G", steps=[_PROBE_STEP]))
    CallableExecutor(fn).execute(_PROBE_STEP, state)
    return "GOAL:" in seen.get("prompt", "")


def main() -> int:
    from workplan.verifiers.llm_judge import LLMJudgeVerifier

    out_dir = Path("m6_probe_out")
    out_dir.mkdir(exist_ok=True)
    record: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "M6-3 真 LLM 穩定度實測紀錄",
    }

    # (3) recitation 注入(離線可驗)
    record["recitation_injected"] = _probe_recitation_injection()
    print(f"recitation 注入:{record['recitation_injected']}")

    # (1)(2) Anthropic judge 重現性 + fail-closed
    model = _build_anthropic()
    if model is None:
        print("⚠ 未設 ANTHROPIC_API_KEY,跳過 Anthropic judge 重現性實測")
        record["anthropic"] = "skipped (no ANTHROPIC_API_KEY)"
    else:
        print("== Anthropic judge 重現性(連跑 5 次)==")
        report = run_judge_probe(LLMJudgeVerifier(model=model), runs=5)
        record["anthropic"] = asdict(report)
        print(
            f"  scores={report.scores} mean={report.mean:.3f} "
            f"spread={report.spread:.3f} non_pass={report.non_pass_count}"
        )

    # (4) OpenAI 煙霧測:一次 judge 呼叫,確認結構化輸出通且非無故 fail-closed
    oai = _build_openai()
    if oai is None:
        print("⚠ 未設 OPENAI_API_KEY(或未裝 langchain-openai),跳過 OpenAI 煙霧測")
        record["openai_smoke"] = "skipped"
    else:
        print("== OpenAI 煙霧測(1 次)==")
        r = run_judge_probe(LLMJudgeVerifier(model=oai), runs=1)
        record["openai_smoke"] = asdict(r)
        print(f"  score={r.scores} passed={r.passed}")

    json_path = out_dir / "m6_probe_record.json"
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n實測紀錄寫入:{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
