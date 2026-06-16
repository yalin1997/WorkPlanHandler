"""WorkPlanHandler — 可插拔策略介面(stable public contract,0.1.0)。

本模組定義 Planner / Executor / Verifier / PlanStore 四個可插拔點,以及執行迴圈
交換的兩個資料載體 StepOutput / VerificationResult。**這是 MVP 對外整合者依賴
的穩定契約**:任何整合者自帶的元件只要滿足這些 Protocol 即可插入,核心執行
迴圈(engine)不變。簽章變動視為破壞性變更,須隨版號管理(見 tests/test_public_api.py)。

四個可插拔點:
  - Planner  : 規劃 / 重規劃(可換不同 LLM 或符號 planner)
  - Executor : 落地執行(可換不同工具集 / agent runtime)
  - Verifier : 驗收(hard / soft / human,可組合)
  - PlanStore: 持久化(MVP=LangGraph checkpointer;Phase 3 視需求接 Temporal/Postgres)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import Plan, PlanState, Step


@dataclass
class StepOutput:
    """Executor 的執行結果。"""

    content: Any
    artifacts: dict[str, Any] | None = None  # 產生的檔案 / 中間物
    error: str | None = None  # 執行期錯誤(非驗收失敗)


@dataclass
class VerificationResult:
    """Verifier 的判定結果 —— 驅動 advance / retry / replan / escalate。"""

    passed: bool
    score: float = 0.0  # 0~1，軟驗收用
    feedback: str = ""  # 失敗時回饋給 Planner/Executor 做反思
    needs_human: bool = False  # human gate 觸發
    layer: str = "soft"  # D10:判定所屬層(hard/soft/human)


@runtime_checkable
class Planner(Protocol):
    """產生與修訂結構化計劃。"""

    def make_plan(self, goal: str, context: dict[str, Any]) -> Plan: ...

    def replan(self, state: PlanState, failure: VerificationResult) -> Plan: ...


@runtime_checkable
class Executor(Protocol):
    """執行單一 step(呼叫工具 / 子 agent)。"""

    def execute(self, step: Step, state: PlanState) -> StepOutput: ...


@runtime_checkable
class Verifier(Protocol):
    """判定一個 step 是否達成其 AcceptanceCriterion。"""

    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult: ...


@runtime_checkable
class PlanStore(Protocol):
    """持久化執行狀態,支援長任務中斷續跑。"""

    def save(self, thread_id: str, state: PlanState) -> None: ...

    def load(self, thread_id: str) -> PlanState | None: ...
