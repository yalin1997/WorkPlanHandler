"""WorkPlanHandler — Planner 實作(D1:可插拔)。

M1 僅含 MockPlanner;LLMPlanner / ExternalPlanner 於 M4 實作(規格 04)。
"""

from .mock import MockPlanner

__all__ = ["MockPlanner"]
