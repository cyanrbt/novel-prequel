from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .state_store import atomic_save_json


ALLOWED_ARTIFACTS = {
    "context.json",
    "plan.json",
    "draft.txt",
    "static_review.json",
    "semantic_review.json",
    "promotion_manifest.json",
}


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
        if name not in ALLOWED_ARTIFACTS:
            raise ArtifactValidationError(f"不允许的章节工件: {name}")
        return self.path / name

    def write_text(self, name: str, content: str) -> Path:
        target = self._target(name)
        if not isinstance(content, str) or not content.strip():
            raise ArtifactValidationError(f"工件为空: {name}")
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
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
