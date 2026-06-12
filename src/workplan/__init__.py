"""WorkPlanHandler — 為 agent planner 設計的長任務執行管理模組。

M1 交付:核心資料模型(models/events)、純函式狀態機(engine)、
可插拔協定(protocols)與 mock 元件(executors/verifiers/planners)。
本套件核心零框架依賴(D9);adapters/langgraph 於 M2 實作。
"""

from . import engine
from .engine import MAX_REPLANS, Action, Decision
from .errors import (
    IllegalTransitionError,
    PlanIntegrityError,
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

__version__ = "0.1.0.dev0"
