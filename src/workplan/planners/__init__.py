"""WorkPlanHandler — Planner 實作(D1:可插拔)。

M1:MockPlanner;M4:ExternalPlanner(D1 ingest,零依賴)。

LLMPlanner **刻意不在此 eager import**——它依賴 langchain(optional extra),
eager import 會把 langchain 拖進零依賴核心、破壞 D9。請以顯式路徑取用:

    from workplan.planners.llm_planner import LLMPlanner
"""

from .external import ExternalPlanner
from .mock import MockPlanner

__all__ = ["ExternalPlanner", "MockPlanner"]
