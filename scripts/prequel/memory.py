from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .project import project_path
from .state_store import atomic_save_json


class MemoryStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.index_path = project_path(self.project_root, "memory_index")
        self.lessons_path = project_path(self.project_root, "quality_lessons")
        self.debts_path = project_path(self.project_root, "creative_debts")
        self._ensure_stores()

    def _ensure_stores(self) -> None:
        defaults = {
            self.index_path: {"schema": "novel-memory-index", "entries": []},
            self.lessons_path: {
                "schema": "novel-quality-lessons",
                "lessons": [],
            },
            self.debts_path: {"schema": "novel-creative-debts", "debts": []},
        }
        for path, value in defaults.items():
            if not path.exists():
                atomic_save_json(path, value)

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"长期记忆文件无效 {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ArtifactValidationError(f"长期记忆根节点必须是object: {path}")
        return value

    def all_lessons(self) -> list[dict[str, Any]]:
        return self._load(self.lessons_path).get("lessons", [])

    def active_lessons(self) -> list[dict[str, Any]]:
        return [item for item in self.all_lessons() if item.get("status") == "active"]

    def valid_entries(self) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for item in self._load(self.index_path).get("entries", []):
            source = self.project_root / item.get("source_path", "")
            if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == item.get(
                "source_sha256"
            ):
                valid.append(item)
        return valid

    def retrieve(
        self, query: dict[str, Any], limit: int = 20
    ) -> list[dict[str, Any]]:
        def score(item: dict[str, Any]) -> int:
            value = 3 if item.get("event_id") == query.get("event_id") else 0
            value += 2 * len(
                set(item.get("characters", [])) & set(query.get("characters", []))
            )
            value += 2 * len(
                set(item.get("locations", [])) & set(query.get("locations", []))
            )
            value += 3 * len(
                set(item.get("foreshadows", [])) & set(query.get("foreshadows", []))
            )
            return value

        ranked = [(score(item), item) for item in self.valid_entries()]
        ranked.sort(key=lambda pair: (pair[0], pair[1].get("chapter", 0)), reverse=True)
        return [item for value, item in ranked if value > 0][:limit]

    def update_lessons(
        self, chapter: int, findings: list[dict[str, Any]]
    ) -> None:
        data = self._load(self.lessons_path)
        by_code = {item["code"]: item for item in data.get("lessons", [])}
        for finding in findings:
            code = finding.get("code")
            quote = finding.get("quote")
            if not code or not quote:
                continue
            item = by_code.setdefault(
                code,
                {
                    "code": code,
                    "scope": finding.get("scope", {}),
                    "instruction": finding.get("instruction", "避免同类问题复发"),
                    "first_seen": chapter,
                    "last_seen": chapter,
                    "occurrences": [],
                    "evidence": [],
                    "status": "candidate",
                },
            )
            item["last_seen"] = chapter
            item["instruction"] = finding.get("instruction", item["instruction"])
            item["occurrences"] = [
                value for value in item.get("occurrences", []) if value >= chapter - 9
            ]
            if chapter not in item["occurrences"]:
                item["occurrences"].append(chapter)
            item["evidence"] = (
                item.get("evidence", []) + [{"chapter": chapter, "quote": quote}]
            )[-10:]
            if len(set(item["occurrences"])) >= 3:
                item["status"] = "active"
        data["lessons"] = sorted(by_code.values(), key=lambda item: item["code"])
        atomic_save_json(self.lessons_path, data)

    def retire_lessons(self, current_chapter: int) -> None:
        data = self._load(self.lessons_path)
        for item in data.get("lessons", []):
            if (
                item.get("status") == "active"
                and current_chapter - item.get("last_seen", current_chapter) >= 10
            ):
                item["status"] = "retired"
        atomic_save_json(self.lessons_path, data)

    def core_context(self, plan: dict[str, Any]) -> dict[str, Any]:
        characters = sorted(
            {
                name
                for scene in plan.get("scenes", [])
                for name in scene.get("characters", [])
            }
        )
        locations = sorted(
            {
                scene.get("location")
                for scene in plan.get("scenes", [])
                if scene.get("location")
            }
        )
        operations = plan.get("foreshadow_operations", {})
        foreshadows = operations.get("plant", []) + operations.get("recover", [])
        query = {
            "characters": characters,
            "locations": locations,
            "event_id": plan.get("event_id"),
            "foreshadows": foreshadows,
        }
        relevant: list[dict[str, Any]] = []
        for item in self.active_lessons():
            scope = item.get("scope", {})
            if (
                not scope
                or set(scope.get("characters", [])) & set(characters)
                or scope.get("event_id") == plan.get("event_id")
            ):
                relevant.append(item)
        relevant.sort(key=lambda item: item.get("last_seen", 0), reverse=True)
        return {
            "archive": self.retrieve(query),
            "lessons": relevant[:8],
            "debts": self._load(self.debts_path).get("debts", []),
        }

    def context_for_state(self, state: dict[str, Any]) -> dict[str, Any]:
        characters = list(state.get("characters", {}).get("active", {}).keys())
        protagonist_name = state.get("protagonist", {}).get("name")
        if isinstance(protagonist_name, str) and protagonist_name:
            characters.append(protagonist_name)
        pseudo_plan = {
            "event_id": state["chapter"]["current_event"],
            "scenes": [
                {
                    "characters": characters,
                    "location": state.get("protagonist", {}).get("location"),
                }
            ],
            "foreshadow_operations": {
                "plant": [],
                "recover": list(state.get("active_foreshadows", {})),
            },
        }
        return self.core_context(pseudo_plan)

    def record_promoted_chapter(
        self,
        chapter: int,
        source_path: Path,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        record = memory_record(plan)
        entry = {
            **record,
            "chapter": chapter,
            "source_path": str(source_path.relative_to(self.project_root)),
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        }
        data = self._load(self.index_path)
        entries = [
            item for item in data.get("entries", []) if item.get("chapter") != chapter
        ]
        entries.append(entry)
        entries.sort(key=lambda item: item["chapter"])
        data["entries"] = entries
        atomic_save_json(self.index_path, data)
        return entry

    def rebuild_index(self) -> int:
        entries: list[dict[str, Any]] = []
        for meta in sorted(project_path(self.project_root, "chapter_meta_dir").glob("chapter_*.md")):
            match = re.search(r"## Memory Record\s*```json\s*(\{.*?\})\s*```", meta.read_text(encoding="utf-8"), re.S)
            if not match:
                continue
            record = json.loads(match.group(1))
            chapter = int(re.search(r"chapter_(\d+)", meta.name).group(1))
            sources = list(project_path(self.project_root, "chapters_dir").glob(f"vol_*/chapter_{chapter:03d}.txt"))
            if len(sources) != 1:
                continue
            source = sources[0]
            entries.append(
                {
                    **record,
                    "chapter": chapter,
                    "source_path": str(source.relative_to(self.project_root)),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
        atomic_save_json(
            self.index_path, {"schema": "novel-memory-index", "entries": entries}
        )
        return len(entries)


def memory_record(plan: dict[str, Any]) -> dict[str, Any]:
    scenes = plan.get("scenes", [])
    operations = plan.get("foreshadow_operations", {})
    return {
        "characters": sorted(
            {name for scene in scenes for name in scene.get("characters", [])}
        ),
        "locations": sorted(
            {scene.get("location") for scene in scenes if scene.get("location")}
        ),
        "event_id": plan.get("event_id"),
        "foreshadows": operations.get("plant", []) + operations.get("recover", []),
        "irreversible_changes": [
            key
            for key, value in plan.get("state_changes", {}).items()
            if value not in (None, [], {})
        ],
        "hook_type": plan.get("hook", {}).get("type"),
        "summary": plan.get("chapter_purpose", "")[:50],
    }
