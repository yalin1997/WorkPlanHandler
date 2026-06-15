"""框架無關的 PlanStore 實作(零依賴,實作 protocols.PlanStore)。

MVP 之前持久化只透過 ``adapters/langgraph.py``(SqliteSaver,綁 LangGraph)。
本子套件補上「非 LangGraph 一級持久化路徑」:純 stdlib、可在任何 host
(MCP server / 自家 runner)使用,以支援長任務中斷續跑(I2/I3)。
"""

from .json_store import JsonFilePlanStore

__all__ = ["JsonFilePlanStore"]
