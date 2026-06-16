"""JsonFilePlanStore —— 以檔案系統持久化 PlanState(零依賴,stdlib only)。

每個 thread 一個 JSON 檔(``{root}/{thread_id}.json``),序列化沿用既有
``PlanState.to_dict()`` / ``PlanState.from_dict()``(I2),無自訂編解碼。

設計重點:
  - **atomic write**:先寫 ``.tmp`` 再 ``os.replace``,避免寫到一半被讀到半截檔。
  - **per-thread lock**:同 thread 的並發 save/load **各自**序列化、不同 thread 互不
    阻塞。注意:本鎖只保護**單次** save 或 load;跨 load→compute→save 的原子性由
    上層(``Gatekeeper`` 全程持鎖,B2)負責,不在此層承諾。
  - **防目錄穿越**:thread_id 不得含路徑分隔符或為 ``.``/``..``。

不依賴任何 agent 框架(framework-agnostic):core 檔,受 import 邊界鐵則約束。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ..models import PlanState


class JsonFilePlanStore:
    """檔案級 PlanStore(實作 protocols.PlanStore 的 save/load)。

    Args:
        root: 存放目錄,不存在則建立。每個 ``thread_id`` 對應一個 JSON 檔。
    """

    def __init__(self, root: str | os.PathLike[str] = ".workplan_store") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, thread_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(thread_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[thread_id] = lock
            return lock

    def _path(self, thread_id: str) -> Path:
        if (
            not thread_id
            or thread_id in (".", "..")
            or "/" in thread_id
            or "\\" in thread_id
            or os.sep in thread_id
        ):
            raise ValueError(
                f"非法 thread_id:{thread_id!r}(不得為空、'.'/'..' 或含路徑分隔符)"
            )
        return self.root / f"{thread_id}.json"

    def save(self, thread_id: str, state: PlanState) -> None:
        path = self._path(thread_id)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        with self._lock_for(thread_id):
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)  # 同檔案系統上為原子操作

    def load(self, thread_id: str) -> PlanState | None:
        path = self._path(thread_id)
        with self._lock_for(thread_id):
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        return PlanState.from_dict(data)
