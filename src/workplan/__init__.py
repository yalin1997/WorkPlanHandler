"""WorkPlanHandler — 為 agent planner 設計的長任務執行管理模組(0.1.0)。

本頂層命名空間是**零框架依賴的穩定公開面**(見 ``__all__`` 與
tests/test_public_api.py):核心資料模型(models/events)、純函式狀態機
(engine)、可插拔協定(protocols)與 mock 元件。

需要 LangGraph 整合或真 LLM 元件時,以顯式路徑取用(刻意不在此 eager import,
以維持核心零依賴 D9):
  - ``from workplan.adapters.langgraph import WorkPlanRunner``  # extra: langgraph
  - ``from workplan.planners.llm_planner import LLMPlanner``    # extra: llm
  - ``from workplan.verifiers.llm_judge import LLMJudgeVerifier``  # extra: llm
"""

from . import engine
from .engine import MAX_REPLANS, Action, Decision
from .errors import (
    IllegalTransitionError,
    PlanIntegrityError,
    ReplanNotSupported,
    WorkPlanError,
)
from .events import Event, EventType
from .models import (
    AcceptanceCriterion,
    HumanGate,
    Plan,
    PlanState,
    Step,
    StepStatus,
)
from .protocols import (
    Executor,
    Planner,
    PlanStore,
    StepOutput,
    VerificationResult,
    Verifier,
)

__all__ = [
    "engine",
    "Action",
    "Decision",
    "MAX_REPLANS",
    "WorkPlanError",
    "PlanIntegrityError",
    "IllegalTransitionError",
    "ReplanNotSupported",
    "Event",
    "EventType",
    "AcceptanceCriterion",
    "HumanGate",
    "Plan",
    "PlanState",
    "Step",
    "StepStatus",
    "Executor",
    "Planner",
    "PlanStore",
    "StepOutput",
    "Verifier",
    "VerificationResult",
]

__version__ = "0.1.0"
