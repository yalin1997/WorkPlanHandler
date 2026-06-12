"""WorkPlanHandler — 核心資料模型(介面草圖 / interface sketch)。

⚠️ Phase 1 交付物:這是「設計草圖」,用以具體化 docs/02-mvp-proposal.md 的提案,
   尚非可執行成品(無真正的 LLM / 工具實作)。Phase 2 才會補上行為。

設計重點:
  - `AcceptanceCriterion` 是一等公民,內建於每個 `Step` —— 把「驗收目標」結構化。
  - 整個 `PlanState` 可序列化,交給 PlanStore 持久化以支援長任務的中斷續跑。
  - 本檔不依賴任何 agent 框架(framework-agnostic),確保可插拔。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    DONE = "done"          # 已通過驗收
    FAILED = "failed"      # 重試 / 重規劃次數用盡
    BLOCKED = "blocked"    # 等待人工(human gate)


# 驗收方式:硬驗收(可程式判定)/ 軟驗收(LLM 評分)/ 人工驗收
AcceptanceKind = Literal["programmatic", "llm_judge", "human"]


@dataclass
class AcceptanceCriterion:
    """階段性驗收目標(Definition of Done)。

    遵循 survey §3.1 的最佳實務:於「規劃期」生成,而非執行後才補,
    以避免驗收標準被既有輸出合理化。
    """

    description: str                       # 人類可讀的完成定義
    kind: AcceptanceKind = "llm_judge"
    spec: dict[str, Any] = field(default_factory=dict)
    # programmatic: {"callable": <name/ref>} ；llm_judge: {"rubric": "..."}
    threshold: float = 1.0                 # llm_judge 通過分數(0~1)


@dataclass
class Step:
    """一個階段性子任務。"""

    id: str
    description: str                       # 要做什麼
    acceptance: AcceptanceCriterion        # 怎樣算做完
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0                      # 已嘗試次數(retry 計數)
    output: Any = None                     # 最近一次執行輸出
    notes: list[str] = field(default_factory=list)
    # notes 存放 Reflexion 式的 verbal feedback / 失敗教訓(episodic)


@dataclass
class Plan:
    """結構化計劃 —— 同時是控制流與外部化記憶(recitation 來源)。"""

    goal: str
    steps: list[Step]
    version: int = 1                       # 每次 replan 遞增,保留審計軌跡

    def render_for_recitation(self) -> str:
        """產生精簡計劃摘要,注入 prompt 尾端以對抗長 context 目標漂移。"""
        lines = [f"GOAL: {self.goal}  (plan v{self.version})"]
        for i, s in enumerate(self.steps):
            mark = {
                StepStatus.DONE: "[x]",
                StepStatus.IN_PROGRESS: "[~]",
                StepStatus.BLOCKED: "[!]",
                StepStatus.FAILED: "[✗]",
            }.get(s.status, "[ ]")
            lines.append(f"{mark} {i+1}. {s.description}")
        return "\n".join(lines)


PlanRunStatus = Literal["running", "done", "failed", "blocked"]


@dataclass
class PlanState:
    """整個可序列化的執行狀態 —— PlanStore 持久化的對象。

    亦作為 LangGraph adapter 的 state schema(見 docs/02-mvp-proposal.md §5)。
    """

    plan: Plan
    cursor: int = 0                        # 目前執行到第幾個 step(index)
    history: list[dict[str, Any]] = field(default_factory=list)  # episodic 軌跡
    replans: int = 0                       # 已重規劃次數
    status: PlanRunStatus = "running"

    @property
    def current_step(self) -> Step | None:
        if 0 <= self.cursor < len(self.plan.steps):
            return self.plan.steps[self.cursor]
        return None
