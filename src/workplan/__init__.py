"""WorkPlanHandler — 為 agent planner 設計的長任務執行管理模組。

Phase 1 交付:本套件目前僅含「介面草圖」(models / protocols),
用以具體化 docs/02-mvp-proposal.md 的設計提案。執行迴圈(engine)、
verifiers、planners、adapters/langgraph 將於 Phase 2 實作。
"""
from .models import (
    AcceptanceCriterion,
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
    Verifier,
    VerificationResult,
)

__all__ = [
    "AcceptanceCriterion",
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

__version__ = "0.0.1.dev0"  # Phase 1: design sketch only
