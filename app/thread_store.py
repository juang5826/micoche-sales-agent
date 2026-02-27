from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


class ThreadStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add_message(self, thread_id: str, role: str, content: str) -> None:
        with self._lock:
            self._messages[thread_id].append(
                {
                    "role": role,
                    "content": content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    def get_messages(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._messages.get(thread_id, []))

    def delete(self, thread_id: str) -> bool:
        with self._lock:
            existed = thread_id in self._messages
            if existed:
                del self._messages[thread_id]
            return existed

