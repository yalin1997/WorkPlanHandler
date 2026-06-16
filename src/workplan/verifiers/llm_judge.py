"""LLMJudgeVerifier(soft 層真 LLM 評分,規格 03 §3.2,M4)。

把 step 的輸出交給 LLM 依「規劃期就寫好的 rubric」評分(I5:rubric 不在
此生成,讀自 ``criterion.spec["rubric"]``)。回傳 0~1 分數;
``passed = score >= criterion.threshold``(邊界 ``==`` 視為 pass)。

provider-agnostic 模型注入(本次需求方核心要求):

  - 路徑一(主):使用者注入任意 LangChain ``BaseChatModel`` 實例——
    ``ChatAnthropic(...)`` / ``ChatOpenAI(...)`` / ``ChatGoogleGenerativeAI(...)``
    皆可。本類別只依賴 ``model.with_structured_output(Schema).invoke(msgs)``
    這個 LangChain ``Runnable`` 標準介面,故跨 provider 相容是免費的。
  - 路徑二:給 ``model_name`` 字串走 ``init_chat_model``,按 prefix 路由
    provider(``"anthropic:..."`` / ``"openai:..."`` / ``"google_genai:..."``)。

預設 judge = ``anthropic:claude-haiku-4-5``(較輕模型,對應 spec 03 §3.2
減 self-preference;planner 用較強的 sonnet)。

fail-closed(規格 03 §5):judge 呼叫失敗/逾時 → 重試 1 次 → 再失敗則
``passed=False, feedback="judge 不可用"``;回傳非結構化(None)亦視為 fail。
偏誤緩解(規格 03 §3.1):prompt 固定欄位順序、rubric 由 criterion 帶入。

⚠️ 本檔與 planners/llm_planner.py 是核心中**唯二**可 import langchain 的檔案
(D4/D9);因此 verifiers/__init__.py **不得** eager import 本檔(會把
langchain 拖進零依賴核心)。請以顯式路徑
``from workplan.verifiers.llm_judge import LLMJudgeVerifier`` 取用。
"""

from __future__ import annotations

from typing import Any, Protocol

try:
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel, Field
except ImportError as exc:  # 同 adapters/langgraph.py 手法:給安裝提示
    raise ImportError(
        "LLMJudgeVerifier 需要額外依賴:pip install 'workplan[llm]'"
        "(接 OpenAI/Google 等 provider 另裝對應 langchain 整合並傳 model 實例)"
    ) from exc

from ..models import PlanState, Step
from ..protocols import StepOutput, VerificationResult

DEFAULT_JUDGE_MODEL = "anthropic:claude-haiku-4-5"

_OUTPUT_CHAR_LIMIT = 4000  # 餵進 judge 的 output 截斷上限(控 token / 避免超長)


class StructuredChatModel(Protocol):
    """文件化用途:說明本模組對注入模型的**唯一**契約(不強制繼承)。

    任何 LangChain ``BaseChatModel`` 都已實作此介面。
    """

    def with_structured_output(self, schema: Any) -> Any: ...


class JudgeVerdict(BaseModel):
    """judge 的結構化輸出 schema(模組內部持有,使用者不需理解)。

    欄位順序固定(score → passed → feedback)以穩定 prompt / 減偏誤;
    最終 ``passed`` 由 threshold 重算,不直接信任模型的 ``passed``。
    """

    score: float = Field(description="0~1 之間的達成度分數")
    passed: bool = Field(description="是否達成驗收(僅供參考,最終以 threshold 重算)")
    feedback: str = Field(description="可行動的回饋:未達標時指出缺什麼、怎麼補")


_SYSTEM_PROMPT = (
    "你是嚴謹的驗收評審(acceptance judge)。依據給定的『驗收標準(rubric)』"
    "對『步驟輸出』評 0~1 分,並給出可行動的回饋。只根據 rubric 判定,"
    "不臆測 rubric 未涵蓋的要求;不確定時從嚴(寧可低分)。"
)


def _fail(feedback: str) -> VerificationResult:
    return VerificationResult(passed=False, score=0.0, feedback=feedback, layer="soft")


class LLMJudgeVerifier:
    """soft 層 LLM 評審。直接替換 LayeredVerifier 的 soft 槽位,介面零變動。

    Args:
        model: 注入的 LangChain chat model 實例(主要路徑)。給定時忽略 model_name。
        model_name: 未注入實例時用此字串走 init_chat_model(預設輕量 judge 模型)。
    """

    def __init__(
        self,
        model: Any | None = None,
        *,
        model_name: str = DEFAULT_JUDGE_MODEL,
    ) -> None:
        base = model if model is not None else init_chat_model(model_name)
        self._model = base.with_structured_output(JudgeVerdict)

    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult:
        criterion = step.acceptance
        rubric = criterion.spec.get("rubric") or criterion.description
        messages = self._build_messages(rubric, step, output)

        verdict = self._invoke_with_retry(messages)
        if verdict is None:  # 兩次皆失敗 / 非結構化 → fail-closed(規格 03 §5)
            return _fail("judge 不可用(呼叫失敗或回傳非結構化),fail-closed 不放行")

        score = float(verdict.score)
        passed = score >= criterion.threshold  # 邊界 == 視為 pass
        feedback = verdict.feedback
        if not passed and not feedback:
            feedback = (
                f"未達 rubric(門檻 {criterion.threshold}):{criterion.description}"
            )
        return VerificationResult(
            passed=passed, score=score, feedback=feedback, layer="soft"
        )

    def _build_messages(
        self, rubric: str, step: Step, output: StepOutput
    ) -> list[tuple[str, str]]:
        """固定欄位順序的 prompt(減 position/verbosity 偏誤,規格 03 §3.1)。"""
        content = "" if output.content is None else str(output.content)
        content = content[:_OUTPUT_CHAR_LIMIT]
        user = (
            f"步驟目標:{step.description}\n\n"
            f"驗收標準(rubric):{rubric}\n\n"
            f"步驟輸出:\n{content}"
        )
        return [("system", _SYSTEM_PROMPT), ("user", user)]

    def _invoke_with_retry(
        self, messages: list[tuple[str, str]]
    ) -> JudgeVerdict | None:
        """呼叫 judge;失敗重試 1 次(共 2 次);仍失敗回 None(fail-closed)。"""
        for _ in range(2):
            try:
                result = self._model.invoke(messages)
            except Exception:
                continue
            if isinstance(result, JudgeVerdict):
                return result
            # 非結構化(None / 其他)→ 視為一次失敗,再試
        return None
