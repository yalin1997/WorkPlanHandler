"""LLMPlanner(真 LLM 規劃 / 重規劃,規格 04,M4)。

把 goal(+context)交給 LLM 產出結構化的 ``Plan``:每個 ``Step`` 自帶
``AcceptanceCriterion``(I5:驗收條件於規劃期生成,不在執行後補)。
LLM 產不出 callable,故 planner 一律產 ``kind="llm_judge"`` 的 **soft** 驗收
(rubric 寫進 ``spec["rubric"]``,交給 LLMJudgeVerifier 評分);hard check
仍由使用者用註冊名提供(沿用 M3 的 ProgrammaticVerifier)。

provider-agnostic 模型注入(與 LLMJudgeVerifier 共用模式,需求方核心要求):

  - 路徑一(主):注入任意 LangChain ``BaseChatModel`` 實例
    (``ChatAnthropic`` / ``ChatOpenAI`` / ``ChatGoogleGenerativeAI`` …)。
  - 路徑二:給 ``model_name`` 字串走 ``init_chat_model`` 按 prefix 路由 provider。

  唯一依賴的契約是 ``model.with_structured_output(Schema).invoke(msgs)``
  ——LangChain ``Runnable`` 標準介面,跨 provider 相容是免費的。

預設 planner = ``anthropic:claude-sonnet-4-6``(較強模型;judge 用較輕的
haiku,兩者刻意不同模型,對應 spec 03 §3.2 減 self-preference)。

D6 replan 契約:``replan`` 只回「未完成尾巴」的新步序,**新步一律用全新 id**
(engine ``on_replanned`` 會 assert 不可覆寫 DONE 步,違反 raise
``PlanIntegrityError``),並附 ``revision_note``。

⚠️ 本檔與 verifiers/llm_judge.py 是核心中**唯二**可 import langchain 的檔案
(D4/D9);planners/__init__.py **不得** eager import 本檔。請以顯式路徑
``from workplan.planners.llm_planner import LLMPlanner`` 取用。
"""

from __future__ import annotations

from typing import Any, Protocol

try:
    from langchain.chat_models import init_chat_model
    from pydantic import BaseModel, Field
except ImportError as exc:  # 同 adapters/langgraph.py 手法:給安裝提示
    raise ImportError(
        "LLMPlanner 需要額外依賴:pip install 'workplan[llm]'"
        "(接 OpenAI/Google 等 provider 另裝對應 langchain 整合並傳 model 實例)"
    ) from exc

from ..models import AcceptanceCriterion, Plan, PlanState, Step, StepStatus
from ..protocols import VerificationResult

DEFAULT_PLANNER_MODEL = "anthropic:claude-sonnet-4-6"

_DEFAULT_THRESHOLD = 0.7  # 缺 threshold 時的軟驗收門檻
_WARNING_NOTE = (
    "[planner-warning] 步驟未自帶驗收條件,已補預設 soft llm_judge rubric(I5)"
)


class StructuredChatModel(Protocol):
    """文件化用途:本模組對注入模型的**唯一**契約(任何 BaseChatModel 皆符合)。"""

    def with_structured_output(self, schema: Any) -> Any: ...


class AcceptanceDraft(BaseModel):
    """LLM 為單一步驟產出的驗收條件草稿(soft / llm_judge)。"""

    rubric: str = Field(description="可被另一個 LLM 據以評分的具體完成定義")
    threshold: float = Field(
        default=_DEFAULT_THRESHOLD, description="0~1 通過門檻;留白用預設"
    )
    required: bool = Field(default=True, description="是否為必過層(advisory 設 False)")


class StepDraft(BaseModel):
    description: str = Field(description="這一步要做什麼(動作導向)")
    acceptance: AcceptanceDraft | None = Field(
        default=None, description="完成定義;缺省則由系統補預設 rubric"
    )


class PlanDraft(BaseModel):
    """make_plan 的結構化輸出 schema(模組內部持有)。"""

    steps: list[StepDraft] = Field(description="依序執行的子任務,每步可獨立驗收")


class ReplanDraft(BaseModel):
    """replan 的結構化輸出 schema:只含未完成尾巴 + 修訂理由。"""

    revision_note: str = Field(description="為何重規劃(D6 審計理由)")
    steps: list[StepDraft] = Field(description="僅未完成尾巴的新步序(全新內容)")


_PLAN_SYSTEM = (
    "你是長任務規劃器。把目標拆成可『獨立驗收』的有序子任務。"
    "每一步都要附『驗收標準(rubric)』——一段能讓另一個 LLM 客觀判定是否做完的具體定義。"
    "步驟要動作導向、粒度適中(可被單次執行與驗收),彼此盡量無歧義依賴。"
)
_REPLAN_SYSTEM = (
    "你是長任務的重規劃器。前面已完成的步驟必須保留、不可重做;"
    "你只需針對『尚未完成的尾巴』提出修訂後的新步序,並說明修訂理由。"
    "同樣為每一步附可客觀判定的驗收 rubric。"
)


class LLMPlanner:
    """LLM 驅動的 Planner。實作 protocols.Planner(make_plan + replan)。

    Args:
        model: 注入的 LangChain chat model 實例(主要路徑)。給定時忽略 model_name。
        model_name: 未注入實例時用此字串走 init_chat_model(預設較強的 planner 模型)。

    屬性:
        last_warnings: 最近一次 make_plan/replan 補預設驗收的警告清單(I5 可觀測)。
    """

    def __init__(
        self,
        model: Any | None = None,
        *,
        model_name: str = DEFAULT_PLANNER_MODEL,
    ) -> None:
        base = model if model is not None else init_chat_model(model_name)
        self._plan_model = base.with_structured_output(PlanDraft)
        self._replan_model = base.with_structured_output(ReplanDraft)
        self.last_warnings: list[str] = []

    # ---- Planner 協定 ----

    def make_plan(self, goal: str, context: dict[str, Any]) -> Plan:
        self.last_warnings = []
        user = self._render_context(goal, context)
        draft: PlanDraft = self._plan_model.invoke(
            [("system", _PLAN_SYSTEM), ("user", user)]
        )
        steps = [self._to_step(f"s{i + 1}", d) for i, d in enumerate(draft.steps)]
        return Plan(goal=goal, steps=steps)

    def replan(self, state: PlanState, failure: VerificationResult) -> Plan:
        self.last_warnings = []
        user = self._render_replan(state, failure)
        draft: ReplanDraft = self._replan_model.invoke(
            [("system", _REPLAN_SYSTEM), ("user", user)]
        )
        # D6:新步一律用全新 id(不得撞既有 id;engine 會 assert 不覆寫 DONE)。
        existing = {s.id for s in state.plan.steps}
        steps: list[Step] = []
        for i, d in enumerate(draft.steps):
            steps.append(self._to_step(self._fresh_id(state, i, existing), d))
        return Plan(
            goal=state.plan.goal, steps=steps, revision_note=draft.revision_note
        )

    # ---- 可選:動態拆步(D5;M4 只實作函式,engine 串接留後續) ----

    def decompose(
        self, step: Step, context: dict[str, Any] | None = None
    ) -> list[Step]:
        """把一個過粗的 step 拆成子步(ADaPT 式)。回傳帶新 id 的子步清單。

        engine/adapter 串接(insert_steps)屬 D5,M4 暫不串圖。
        """
        self.last_warnings = []
        user = (
            f"把以下這一步拆成更小、可獨立驗收的子步(動作導向 + 各自 rubric):\n"
            f"步驟:{step.description}\n"
            f"原驗收標準:{step.acceptance.description}"
        )
        if context:
            user += f"\n背景:{context}"
        draft: PlanDraft = self._plan_model.invoke(
            [("system", _PLAN_SYSTEM), ("user", user)]
        )
        subs: list[Step] = []
        for i, d in enumerate(draft.steps):
            sub = self._to_step(f"{step.id}.{i + 1}", d)
            sub.origin = "insert"
            sub.parent_id = step.id
            subs.append(sub)
        return subs

    # ---- 內部 helper ----

    def _to_step(self, step_id: str, draft: StepDraft) -> Step:
        criterion = self._to_criterion(draft.acceptance, draft.description, step_id)
        step = Step(id=step_id, description=draft.description, acceptance=criterion)
        if draft.acceptance is None:
            step.notes.append(_WARNING_NOTE)
        return step

    def _to_criterion(
        self, draft: AcceptanceDraft | None, description: str, step_id: str
    ) -> AcceptanceCriterion:
        if draft is None:  # I5:缺驗收 → 補預設 soft rubric + 記 warning
            self.last_warnings.append(f"步驟 {step_id} 缺驗收條件,已補預設 rubric")
            rubric, threshold, required = description, _DEFAULT_THRESHOLD, True
        else:
            rubric = draft.rubric or description
            threshold, required = draft.threshold, draft.required
        return AcceptanceCriterion(
            description=description,
            kind="llm_judge",
            spec={"rubric": rubric},
            threshold=threshold,
            required=required,
            layer="soft",
        )

    @staticmethod
    def _fresh_id(state: PlanState, idx: int, existing: set[str]) -> str:
        base = f"r{state.plan.version + 1}-{idx + 1}"
        cand, k = base, 0
        while cand in existing:
            k += 1
            cand = f"{base}-{k}"
        existing.add(cand)
        return cand

    @staticmethod
    def _render_context(goal: str, context: dict[str, Any]) -> str:
        lines = [f"目標:{goal}"]
        if context:
            lines.append(f"背景/約束:{context}")
        return "\n".join(lines)

    @staticmethod
    def _render_replan(state: PlanState, failure: VerificationResult) -> str:
        done = [s for s in state.plan.steps if s.status == StepStatus.DONE]
        cur = state.current_step
        lines = [
            f"總目標:{state.plan.goal}",
            f"已完成(請保留、勿重做):{[s.description for s in done] or '無'}",
        ]
        if cur is not None:
            lines.append(f"卡住的步驟:{cur.description}")
        lines.append(f"驗收失敗回饋:{failure.feedback or '(無)'}")
        return "\n".join(lines)
