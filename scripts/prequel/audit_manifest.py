from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

from .run_manifest import utc_now
from .state_store import atomic_save_json


T = TypeVar("T")


@dataclass
class AuditRunManifest:
    """Small persistent manifest implementing the protocol required by CallBudget."""

    path: Path
    data: dict[str, Any]
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def create(
        cls, path: Path, audit_type: str, through_chapter: int, call_limit: int = 1
    ) -> "AuditRunManifest":
        value = {
            "kind": "audit",
            "audit_type": audit_type,
            "through_chapter": through_chapter,
            "status": "RUNNING",
            "started_at": utc_now(),
            "finished_at": None,
            "budget": {
                "limit": call_limit,
                "next_call_id": 1,
                "active": [],
                "calls": {},
                "spent": 0,
                "remaining": call_limit,
            },
        }
        atomic_save_json(path, value)
        return cls(path, value)

    def mutate(self, callback: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            result = callback(self.data)
            atomic_save_json(self.path, self.data)
            return result

    def finish(self, status: str, error: str | None = None) -> None:
        def update(data: dict[str, Any]) -> None:
            data["status"] = status
            data["finished_at"] = utc_now()
            data["error"] = error

        self.mutate(update)
