"""離線確定性的 stub chat model(M4 測試用,不燒 key)。

只實作 LLM 元件依賴的**唯一**契約:
``model.with_structured_output(Schema).invoke(messages) -> pydantic 物件``。
注入 ``model=StubChatModel(...)`` 即可離線跑 LLMPlanner / LLMJudgeVerifier,
證明接線正確而不需真連線。
"""

from __future__ import annotations

from typing import Any


class _StubRunnable:
    """模擬 with_structured_output(Schema) 回傳的 Runnable。"""

    def __init__(self, result: Any, *, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.calls: list[Any] = []

    def invoke(self, messages: Any) -> Any:
        self.calls.append(messages)
        if self._raises is not None:
            raise self._raises
        return self._result  # 可為 pydantic 物件,或 None 模擬非結構化回應


class StubChatModel:
    """模擬 LangChain BaseChatModel:只實作 with_structured_output。

    Args:
        result: 單一罐頭回傳物件;或 dict[schema 類別名 -> 物件](planner 會綁
            PlanDraft 與 ReplanDraft 兩個 schema,用 dict 分別給)。
        raises: 設定後每次 invoke 都丟此例外(測 fail-closed)。
    """

    def __init__(self, result: Any = None, *, raises: Exception | None = None) -> None:
        self._result = result
        self._raises = raises
        self.bound: list[tuple[type, _StubRunnable]] = []

    def with_structured_output(self, schema: type) -> _StubRunnable:
        if isinstance(self._result, dict):
            result = self._result.get(schema.__name__)
        else:
            result = self._result
        runnable = _StubRunnable(result, raises=self._raises)
        self.bound.append((schema, runnable))
        return runnable
