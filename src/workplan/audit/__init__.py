"""WorkPlanHandler — 審計輸出(規格 01 §5,對應 D11)。

把 append-only 的事件流(``PlanState.history``)渲染成兩種審計產物:

  - ``to_event_log``:完整 JSON 事件流(機器可讀,I2 可序列化往返)。
  - ``to_markdown``:人讀驗收摘要(目標、各步驟驗收結果、replan/人工關卡、結論)。
  - ``write_audit``:把上述兩者落檔(``.json`` envelope + ``.md``)。

本模組**零框架依賴**(只用標準庫),與 engine/models 同屬核心。
"""

from .render import (
    EVENT_LOG_SCHEMA_VERSION,
    to_event_log,
    to_markdown,
    write_audit,
)

__all__ = [
    "EVENT_LOG_SCHEMA_VERSION",
    "to_event_log",
    "to_markdown",
    "write_audit",
]
