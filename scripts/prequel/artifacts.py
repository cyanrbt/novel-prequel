from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ArtifactValidationError
from .state_store import atomic_save_json, atomic_save_text


ALLOWED_ARTIFACTS = {
    "context.json",
    "plan.json",
    "draft.txt",
    "static_review.json",
    "semantic_review.json",
    "promotion_manifest.json",
    "run_manifest.json",
    "decision.json",
    "decision.md",
    "context_metrics.json",
}

NESTED_PATTERNS = (
    re.compile(r"^candidates/candidate_\d{2}/(?:draft\.txt|generation\.json|static_review\.json|scorecard\.json|integrated_review\.json|manual_review\.json)$"),
    re.compile(r"^candidates/candidate_\d{2}/reviews/(?:continuity|character|craft|anti_slop)\.json$"),
    re.compile(
        r"^candidates/candidate_\d{2}/diagnostics/"
        r"(?:integrated_review|(?:continuity|character|craft|anti_slop)_review)\.invalid\.txt$"
    ),
    re.compile(r"^comparisons/(?:initial|revision_\d{2})/ballot_\d{2}\.json$"),
    re.compile(r"^revisions/round_\d{2}/(?:brief\.json|draft\.txt|static_review\.json|scorecard\.json|verification\.json)$"),
    re.compile(r"^revisions/round_\d{2}/reviews/(?:continuity|character|craft|anti_slop)\.json$"),
)


def _safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ArtifactValidationError(f"不安全的章节工件路径: {name}")
    if name not in ALLOWED_ARTIFACTS and not any(
        pattern.fullmatch(name) for pattern in NESTED_PATTERNS
    ):
        raise ArtifactValidationError(f"不允许的章节工件: {name}")
    return name


@dataclass(frozen=True)
class ChapterWorkspace:
    path: Path
    chapter_number: int

    @classmethod
    def create(
        cls,
        work_root: Path,
        chapter_number: int,
        attempt: int | None = None,
    ) -> "ChapterWorkspace":
        if chapter_number < 1:
            raise ArtifactValidationError("章节号必须大于0")
        path = work_root / f"chapter_{chapter_number:03d}"
        if attempt is not None:
            if attempt < 1:
                raise ArtifactValidationError("尝试次数必须大于0")
            path = path / f"attempt_{attempt:02d}"
        path.mkdir(parents=True, exist_ok=True)
        return cls(path, chapter_number)

    def _target(self, name: str) -> Path:
        target = self.path / _safe_relative(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_text(self, name: str, content: str) -> Path:
        target = self._target(name)
        if not isinstance(content, str) or not content.strip():
            raise ArtifactValidationError(f"工件为空: {name}")
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        return target

    def write_raw_text(self, name: str, content: str) -> Path:
        """Write diagnostic model output without normalizing whitespace."""
        target = self._target(name)
        if not isinstance(content, str) or not content.strip():
            raise ArtifactValidationError(f"工件为空: {name}")
        atomic_save_text(target, content)
        return target

    def write_json(self, name: str, value: Any) -> Path:
        target = self._target(name)
        atomic_save_json(target, value)
        return target

    def read_json(self, name: str) -> Any:
        target = self._target(name)
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"无法读取工件 {name}: {exc}") from exc

    def read_text(self, name: str) -> str:
        target = self._target(name)
        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactValidationError(f"无法读取工件 {name}: {exc}") from exc

    def exists(self, name: str) -> bool:
        return self._target(name).is_file()

    def digest(self, name: str) -> str:
        target = self._target(name)
        try:
            return hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError as exc:
            raise ArtifactValidationError(f"无法计算工件哈希 {name}: {exc}") from exc
