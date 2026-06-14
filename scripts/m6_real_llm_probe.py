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

**執行**:三種 provider 任選一或多個,皆可只跑部分:

  Anthropic API key(燒 key):
    export ANTHROPIC_API_KEY=sk-...
    python scripts/m6_real_llm_probe.py

  OpenAI API key(選配):
    export OPENAI_API_KEY=sk-...
    python scripts/m6_real_llm_probe.py

  Ollama 本地模型(免 key;需先啟動 ollama serve 並 pull 模型):
    pip install "workplan[ollama]"         # 一次性
    export OLLAMA_MODEL=gemma4:26b         # 或 gemma4:26b 等;預設 gemma4:26b
    python scripts/m6_real_llm_probe.py

三個 provider 互為選配,未設的自動跳過。可同時設多個。
產出:一份可審計實測紀錄(JSON)寫到 ./m6_probe_out/(需從 repo 根執行)。
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

    messages = judge._build_messages(
        _PROBE_STEP.acceptance.spec["rubric"], _PROBE_STEP, _PROBE_OUTPUT
    )
    print("[judge prompt >>>]")
    for role, content in messages:
        print(f"  {role}: {content}")
    print("[<<<]")

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


def _build_google():
    """Gemini API(以 GOOGLE_API_KEY 控制;預設 gemini-3.5-flash)。"""
    if not os.getenv("GOOGLE_API_KEY"):
        return None
    model_name = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        print("  · 未裝 langchain-google-genai,跳過 Google 測試")
        print("    pip install 'workplan[google]' 後再執行")
        return None
    return ChatGoogleGenerativeAI(model=model_name, temperature=0)


def _build_ollama():
    """本地 Ollama 模型(預設 gemma4:26b;以 OLLAMA_MODEL / OLLAMA_BASE_URL 覆寫)。

    執行前確認 Ollama 已啟動且模型已 pull:
        ollama serve                      # 啟動服務(預設 localhost:11434)
        ollama pull <model>               # 首次需拉取
    環境變數:
        OLLAMA_MODEL    模型名稱(預設 gemma4:26b;Gemma4 26b 對應 gemma4:26b)
        OLLAMA_BASE_URL Ollama 服務位址(預設 http://localhost:11434)
    """
    model_name = os.getenv("OLLAMA_MODEL", "gemma4:26b")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        print("  · 未裝 langchain-ollama,跳過 Ollama 測試")
        print("    pip install 'workplan[ollama]' 後再執行")
        return None
    return ChatOllama(model=model_name, base_url=base_url, temperature=0)


def _probe_planner(model: Any, *, timeout_secs: int = 120) -> dict[str, Any]:
    """呼叫 LLMPlanner.make_plan 一次,驗收巢狀 PlanDraft 結構化輸出是否完整。

    純函式:注入任意 BaseChatModel;失敗一律回 {"success": False, "error": ...}
    而非 raise,讓 main() 可以繼續記錄其他 provider 結果。
    本地大模型 structured_output 推理慢,逾 timeout_secs 秒視為 timeout。
    """
    import concurrent.futures

    from workplan.planners.llm_planner import LLMPlanner

    def _run():
        from workplan.planners.llm_planner import _PLAN_SYSTEM

        goal = "上傳 file 到 google cloud（測試用，請只產 3 個步驟）"
        ctx = {"max_steps": 3, "note": "probe only, keep it minimal"}
        user_msg = f"目標:{goal}\n背景/約束:{ctx}"
        print("[planner prompt >>>]")
        print(f"  system: {_PLAN_SYSTEM}")
        print(f"  user:   {user_msg}")
        print("[<<<]")

        planner = LLMPlanner(model=model)
        plan = planner.make_plan(goal, ctx)
        return {
            "success": True,
            "step_count": len(plan.steps),
            "all_have_acceptance": all(s.acceptance is not None for s in plan.steps),
            "warnings": planner.last_warnings,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            return fut.result(timeout=timeout_secs)
        except concurrent.futures.TimeoutError:
            return {
                "success": False,
                "error": f"timeout after {timeout_secs}s (巢狀 schema 對本地模型太重)",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def _probe_recitation_injection() -> bool:
    """確認 CallableExecutor 把 recitation 接到 prompt 尾端(離線即可驗,不燒 key)。"""
    from workplan.executors.callable import CallableExecutor

    seen: dict[str, str] = {}

    def fn(step, _state, ctx):
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

    # (5) Google Gemini:judge 重現性(5 次) + planner 煙霧測(1 次)
    google = _build_google()
    google_model_name = os.getenv("GOOGLE_MODEL", "gemini-3.5-flash")
    if google is None:
        print("⚠ 未設 GOOGLE_API_KEY(或未裝 langchain-google-genai),跳過 Google 測試")
        record["google"] = "skipped (no GOOGLE_API_KEY)"
    else:
        print(f"== Google judge 重現性(model={google_model_name},連跑 1 次)==")
        g_judge = run_judge_probe(LLMJudgeVerifier(model=google), runs=5)
        print(
            f"  scores={g_judge.scores} mean={g_judge.mean:.3f} "
            f"spread={g_judge.spread:.3f} non_pass={g_judge.non_pass_count}"
        )

        print(f"== Google planner 煙霧測(model={google_model_name},1 次)==")
        g_planner = _probe_planner(google)
        print(
            f"  success={g_planner['success']} "
            f"steps={g_planner.get('step_count', '-')} "
            f"all_have_acceptance={g_planner.get('all_have_acceptance', '-')}"
        )
        if g_planner.get("warnings"):
            print(f"  warnings={g_planner['warnings']}")

        record["google"] = {
            "model": google_model_name,
            "judge": asdict(g_judge),
            "planner_smoke": g_planner,
        }

    json_path = out_dir / "m6_probe_record.json"
    json_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n實測紀錄寫入:{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
