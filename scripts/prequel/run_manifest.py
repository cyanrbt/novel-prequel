from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

from .artifacts import ChapterWorkspace
from .errors import ArtifactValidationError


def fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


T = TypeVar("T")


@dataclass
class RunManifest:
    workspace: ChapterWorkspace
    data: dict[str, Any]
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def create(
        cls,
        workspace: ChapterWorkspace,
        chapter: int,
        state_hash: str,
        *,
        call_limit: int = 10,
        mode: str = "balanced",
    ) -> "RunManifest":
        if not isinstance(call_limit, int) or isinstance(call_limit, bool) or call_limit < 1:
            raise ArtifactValidationError("调用预算必须是正整数")
        value = {
            "chapter": chapter,
            "state_hash": state_hash,
            "status": "RUNNING",
            "current_stage": "init",
            "valid_candidates": 0,
            "waiting_reason": None,
            "stages": {},
            "failures": [],
            "mode": mode,
            "context_metrics": {},
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
        workspace.write_json("run_manifest.json", value)
        return cls(workspace, value)

    @classmethod
    def load(cls, workspace: ChapterWorkspace) -> "RunManifest":
        value = workspace.read_json("run_manifest.json")
        if (
            value.get("chapter") != workspace.chapter_number
            or not isinstance(value.get("stages"), dict)
        ):
            raise ArtifactValidationError("run_manifest 与工作区不匹配")
        return cls(workspace, value)

    def _save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.workspace.write_json("run_manifest.json", self.data)

    def mutate(self, callback: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            result = callback(self.data)
            self._save_unlocked()
            return result

    def begin(self, stage: str) -> None:
        self.mutate(lambda data: data.__setitem__("current_stage", stage))

    def reopen(self, stage: str) -> None:
        """Mark a previously waiting run active again without changing budget."""
        def update(data: dict[str, Any]) -> None:
            data["status"] = "RUNNING"
            data["current_stage"] = stage
            data["waiting_reason"] = None
            data["finished_at"] = None

        self.mutate(update)

    def stage_failed(self, stage: str) -> bool:
        with self._lock:
            return self.data.get("stages", {}).get(stage, {}).get("status") == "FAILED"

    def can_reuse(
        self,
        stage: str,
        input_hash: str,
        route_fingerprint: str | None = None,
    ) -> bool:
        with self._lock:
            record = self.data["stages"].get(stage)
        if (
            not record
            or record.get("status") != "COMPLETED"
            or record.get("input_hash") != input_hash
            or (
                route_fingerprint is not None
                and record.get("route_fingerprint") != route_fingerprint
            )
        ):
            return False
        return all(
            self.workspace.exists(path)
            and self.workspace.digest(path) == digest
            for path, digest in record.get("outputs", {}).items()
        )

    def require_stage_outputs(self, stage: str) -> dict[str, Any]:
        """Return a completed stage record only when every output still matches.

        ``can_reuse`` answers whether a stage may be resumed for a particular
        input.  Promotion needs a stricter, input-independent integrity check:
        the reviewed artifacts themselves must still be the bytes recorded by
        the manifest.
        """
        with self._lock:
            record = dict(self.data.get("stages", {}).get(stage) or {})
        if record.get("status") != "COMPLETED":
            raise ArtifactValidationError(f"运行阶段未完成: {stage}")
        outputs = record.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise ArtifactValidationError(f"运行阶段没有可验证输出: {stage}")
        for path, expected in outputs.items():
            if not isinstance(path, str) or not isinstance(expected, str):
                raise ArtifactValidationError(f"运行阶段输出记录无效: {stage}")
            if not self.workspace.exists(path):
                raise ArtifactValidationError(f"运行阶段输出缺失: {stage} / {path}")
            actual = self.workspace.digest(path)
            if actual != expected:
                raise ArtifactValidationError(
                    f"运行阶段输出哈希失配: {stage} / {path} "
                    f"(expected={expected}, actual={actual})"
                )
        return record

    def complete(
        self,
        stage: str,
        input_hash: str,
        outputs: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata = metadata or {
            "model_profile": "deterministic",
            "prompt_version": "none",
            "call_count": 0,
        }
        required = {"model_profile", "prompt_version", "call_count"}
        allowed = {*required, "route_fingerprint"}
        if (
            not required.issubset(metadata)
            or not set(metadata).issubset(allowed)
            or not isinstance(metadata["call_count"], int)
            or metadata["call_count"] < 0
        ):
            raise ArtifactValidationError(
                "阶段 metadata 必须包含模型档案、提示词版本和非负调用数"
            )
        output_hashes = {path: self.workspace.digest(path) for path in outputs}

        def update(data: dict[str, Any]) -> None:
            data["stages"][stage] = {
                "status": "COMPLETED",
                "input_hash": input_hash,
                "outputs": output_hashes,
                **metadata,
            }

        self.mutate(update)

    def fail(self, stage: str, message: str) -> None:
        def update(data: dict[str, Any]) -> None:
            data["failures"].append({"stage": stage, "message": message})
            data["stages"][stage] = {"status": "FAILED"}

        self.mutate(update)

    def set_status(
        self,
        status: str,
        *,
        valid_candidates: int,
        waiting_reason: str | None = None,
    ) -> None:
        valid = {
            "RUNNING",
            "WAITING_USER",
            "AUTO_PROMOTE",
            "REPLAN",
            "COMPLETED",
            "BUDGET_EXHAUSTED",
        }
        if status not in valid:
            raise ArtifactValidationError(f"无效运行状态: {status}")
        def update(data: dict[str, Any]) -> None:
            data.update(
                {
                    "status": status,
                    "valid_candidates": valid_candidates,
                    "waiting_reason": waiting_reason,
                }
            )
            if status in {"WAITING_USER", "AUTO_PROMOTE", "COMPLETED", "BUDGET_EXHAUSTED"}:
                data["finished_at"] = utc_now()

        self.mutate(update)

    def display_status(self) -> str:
        return "LEGACY_REPLAN" if self.data.get("status") == "REPLAN" else str(
            self.data.get("status")
        )
