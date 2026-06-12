"""WorkPlanHandler — 核心資料模型(Phase 2:規格 01)。

設計重點:
  - `AcceptanceCriterion` 是一等公民,內建於每個 `Step` —— 把「驗收目標」結構化。
  - 整個 `PlanState` 可序列化(I2),交給 PlanStore 持久化以支援長任務的中斷續跑。
  - 本檔不依賴任何 agent 框架(framework-agnostic),確保可插拔。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Literal

from .errors import PlanIntegrityError


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    DONE = "done"  # 已通過驗收
    FAILED = "failed"  # 重試 / 重規劃次數用盡
    BLOCKED = "blocked"  # 等待人工(human gate)


# 驗收方式:硬驗收(可程式判定)/ 軟驗收(LLM 評分)/ 人工驗收
AcceptanceKind = Literal["programmatic", "llm_judge", "human"]

StepOrigin = Literal["initial", "replan", "insert"]


@dataclass
class AcceptanceCriterion:
    """階段性驗收目標(Definition of Done)。

    遵循 survey §3.1 的最佳實務:於「規劃期」生成,而非執行後才補(I5),
    以避免驗收標準被既有輸出合理化。
    """

    description: str  # 人類可讀的完成定義
    kind: AcceptanceKind = "llm_judge"
    spec: dict[str, Any] = field(default_factory=dict)
    # programmatic: {"callable": <name/ref>} ；llm_judge: {"rubric": "..."}
    threshold: float = 1.0  # llm_judge 通過分數(0~1)
    required: bool = True  # D10:required 層失敗即短路;False=advisory
    layer: Literal["hard", "soft", "human"] = "soft"  # D10 分層歸屬


@dataclass
class Step:
    """一個階段性子任務。

    `id` 規範:`s{n}` 或 UUID;全 Plan 唯一且永不重用
    (replan 插入新步用新 id,利於審計對齊)。
    """

    id: str
    description: str  # 要做什麼
    acceptance: AcceptanceCriterion  # 怎樣算做完
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0  # 已嘗試次數(retry 計數)
    output: Any = None  # 最近一次執行輸出
    notes: list[str] = field(default_factory=list)
    # notes 存放 Reflexion 式的 verbal feedback / 失敗教訓(episodic)
    origin: StepOrigin = "initial"  # D5/D6 來源追溯
    parent_id: str | None = None  # 動態插步時記錄被誰拆出(ADaPT 式)
    max_attempts: int = 2  # 此步的 retry 上限(覆寫全域 K)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Step":
        return cls(
            id=d["id"],
            description=d["description"],
            acceptance=AcceptanceCriterion(**d["acceptance"]),
            status=StepStatus(d["status"]),
            attempts=d.get("attempts", 0),
            output=d.get("output"),
            notes=list(d.get("notes", [])),
            origin=d.get("origin", "initial"),
            parent_id=d.get("parent_id"),
            max_attempts=d.get("max_attempts", 2),
        )


@dataclass
class Plan:
    """結構化計劃 —— 同時是控制流與外部化記憶(recitation 來源)。"""

    goal: str
    steps: list[Step]
    version: int = 1  # 每次 replan 遞增,保留審計軌跡(D6)
    revision_note: str = ""  # D6 本版為何被改(replan 理由)

    def insert_after(self, step_id: str, new_steps: list[Step]) -> None:
        """D5:在指定步驟後插入;new_steps 標記 origin="insert"。"""
        existing_ids = {s.id for s in self.steps}
        for idx, s in enumerate(self.steps):
            if s.id == step_id:
                for ns in new_steps:
                    if ns.id in existing_ids:
                        raise PlanIntegrityError(
                            f"插入步驟 id 重複:{ns.id!r}(id 全 Plan 唯一且永不重用)"
                        )
                    ns.origin = "insert"
                    if ns.parent_id is None:
                        ns.parent_id = step_id
                self.steps[idx + 1 : idx + 1] = new_steps
                return
        raise ValueError(f"insert_after:找不到步驟 {step_id!r}")

    def replace_tail_from(self, cursor: int, new_tail: list[Step]) -> None:
        """D6:保留 [0:cursor] 已完成,替換尾巴。version 由 engine 負責 ++。

        I1 守恆:不得動到任何 status==DONE 的步驟,違反 raise PlanIntegrityError。
        """
        removed = self.steps[cursor:]
        done_removed = [s.id for s in removed if s.status == StepStatus.DONE]
        if done_removed:
            raise PlanIntegrityError(
                f"replace_tail_from 會移除已 DONE 的步驟:{done_removed}(I1)"
            )
        kept_ids = {s.id for s in self.steps[:cursor]}
        overwritten = [s.id for s in new_tail if s.id in kept_ids]
        if overwritten:
            raise PlanIntegrityError(
                f"新尾巴覆寫了已保留步驟的 id:{overwritten}(I1,id 永不重用)"
            )
        for s in new_tail:
            if s.origin == "initial":
                s.origin = "replan"
        self.steps = self.steps[:cursor] + list(new_tail)

    def completed_steps(self) -> list[Step]:
        return [s for s in self.steps if s.status == StepStatus.DONE]

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
            lines.append(f"{mark} {i + 1}. {s.description}")
        return "\n".join(lines)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        return cls(
            goal=d["goal"],
            steps=[Step.from_dict(s) for s in d["steps"]],
            version=d.get("version", 1),
            revision_note=d.get("revision_note", ""),
        )


@dataclass
class HumanGate:
    """D8:escalate 時記錄等待中的人工關卡。"""

    step_id: str
    reason: str  # 為何卡(連續失敗 / 高風險步驟)
    asked_at: str  # ISO 時間
    resolution: Literal["pending", "approved", "rejected", "edited"] = "pending"
    human_note: str = ""


PlanRunStatus = Literal["running", "done", "failed", "blocked"]


@dataclass
class PlanState:
    """整個可序列化的執行狀態 —— PlanStore 持久化的對象(I2)。

    亦作為 LangGraph adapter 的 state schema(見 docs/02-mvp-proposal.md §5)。
    `history` 存 `Event.to_dict()`(I4 審計來源)。
    """

    plan: Plan
    cursor: int = 0  # 目前執行到第幾個 step(index)
    history: list[dict[str, Any]] = field(default_factory=list)
    replans: int = 0  # 已重規劃次數
    status: PlanRunStatus = "running"
    thread_id: str | None = None  # P3:對應持久化 thread
    pending_human: HumanGate | None = None  # D8:escalate 時填

    @property
    def current_step(self) -> Step | None:
        if 0 <= self.cursor < len(self.plan.steps):
            return self.plan.steps[self.cursor]
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanState":
        pending = d.get("pending_human")
        return cls(
            plan=Plan.from_dict(d["plan"]),
            cursor=d.get("cursor", 0),
            history=[dict(e) for e in d.get("history", [])],
            replans=d.get("replans", 0),
            status=d.get("status", "running"),
            thread_id=d.get("thread_id"),
            pending_human=HumanGate(**pending) if pending else None,
        )
