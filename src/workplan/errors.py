"""WorkPlanHandler — 自訂例外(規格 02 §6)。

Engine 拋例外而非吞掉——讓 adapter 決定如何記錄/中止(I6 保持純粹)。
"""
from __future__ import annotations


class WorkPlanError(Exception):
    """所有 workplan 例外的基底。"""


class PlanIntegrityError(WorkPlanError):
    """違反 I1 單調進度:已 DONE 的步驟被覆寫/移除(replan 絕不容忍)。"""


class IllegalTransitionError(WorkPlanError):
    """狀態機收到當前狀態不允許的轉移(如 blocked 時收到非人工裁決)。"""
