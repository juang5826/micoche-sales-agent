from __future__ import annotations

import threading
from collections import defaultdict


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def as_prometheus(self) -> str:
        rows: list[str] = []
        for key, value in sorted(self.snapshot().items()):
            metric_name = f"micoche_agent_{key}".replace(".", "_").replace("-", "_")
            rows.append(f"# TYPE {metric_name} counter")
            rows.append(f"{metric_name} {value}")
        return "\n".join(rows) + ("\n" if rows else "")

