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
        mode: str = "prompt_native",
    ) -> "RunManifest":
        value = {
            "schema": "creative-run-manifest/1",
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

    def can_reuse(
        self,
        stage: str,
        input_hash: str,
    ) -> bool:
        with self._lock:
            record = self.data["stages"].get(stage)
        if (
            not record
            or record.get("status") != "COMPLETED"
            or record.get("input_hash") != input_hash
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
        metadata = metadata or {}
        if not isinstance(metadata, dict):
            raise ArtifactValidationError("阶段 metadata 必须是 object")
        retired_keys = {"model_profile", "route_fingerprint", "call_count"}
        if retired_keys.intersection(metadata):
            raise ArtifactValidationError("运行清单不得记录仓库内模型执行字段")
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
            "READY",
            "COMPLETED",
            "FAILED",
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
            if status in {"WAITING_USER", "READY", "COMPLETED", "FAILED"}:
                data["finished_at"] = utc_now()

        self.mutate(update)

    def display_status(self) -> str:
        return str(self.data.get("status"))
