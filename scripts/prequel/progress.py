from __future__ import annotations

import threading
from typing import Any, Callable

from .run_manifest import utc_now


ProgressSink = Callable[[dict[str, Any]], None]


class ProgressReporter:
    """Serialize small, prompt-free runtime events for optional observers."""

    def __init__(self, sink: ProgressSink | None = None):
        self._sink = sink
        self._lock = threading.Lock()

    def emit(self, kind: str, **fields: Any) -> None:
        if self._sink is None:
            return
        event = {"kind": kind, "at": utc_now(), **fields}
        with self._lock:
            try:
                self._sink(event)
            except Exception:
                # Observability must never alter call budgeting or pipeline state.
                return
