"""WorkPlanHandler — Executor 實作。

D3:MockExecutor(mock 首發);M5:CallableExecutor(通用橋接,規格 04 §B.3,
recitation 注入 + retry feedback,零依賴;LLM 接線留在使用者函式)。
"""

from .callable import CallableExecutor, ExecContext
from .mock import MockExecutor

__all__ = ["CallableExecutor", "ExecContext", "MockExecutor"]
