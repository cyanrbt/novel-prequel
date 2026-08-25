from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .audit_manifest import AuditRunManifest
from .errors import ArtifactValidationError, ProviderError
from .model_router import StageModelRouter
from .model_calls import ModelCallExecutor
from .project import load_role_text, project_path
from .state_store import atomic_save_json


def due_audits(
    last_chapter: int, health_interval: int = 10, arc_interval: int = 20
) -> dict[str, bool]:
    return {
        "health": last_chapter > 0 and last_chapter % health_interval == 0,
        "arc": last_chapter > 0 and last_chapter % arc_interval == 0,
    }


class AuditRunner:
    def __init__(self, project_root: Path, router: StageModelRouter):
        self.project_root = Path(project_root)
        self.router = router

    def run_health(self, through_chapter: int) -> Path:
        return self._run("health", through_chapter, 10)

    def run_arc(self, through_chapter: int) -> Path:
        return self._run("arc", through_chapter, 20)

    def _run(self, audit_type: str, through_chapter: int, window: int) -> Path:
        chapter_paths = sorted(
            project_path(self.project_root, "chapters_dir").glob("vol_*/chapter_*.txt"),
            key=lambda path: int(re.search(r"chapter_(\d+)", path.name).group(1)),
        )
        selected = [
            path
            for path in chapter_paths
            if int(re.search(r"chapter_(\d+)", path.name).group(1))
            <= through_chapter
        ][-window:]
        chapters = {
            int(re.search(r"chapter_(\d+)", path.name).group(1)): path.read_text(
                encoding="utf-8"
            )
            for path in selected
        }
        if not chapters or max(chapters) != through_chapter:
            raise ArtifactValidationError("审计截止章节不存在于正式章节集")
        memory = self._read_store(project_path(self.project_root, "memory_index"), "entries")
        lessons = self._read_store(project_path(self.project_root, "quality_lessons"), "lessons")
        debts_path = project_path(self.project_root, "creative_debts")
        debts_data = self._read_store(debts_path, "debts")
        packet = {
            "audit_type": audit_type,
            "through_chapter": through_chapter,
            "chapters": chapters,
            "memory_entries": [
                item for item in memory if item.get("chapter") in chapters
            ],
            "active_lessons": [
                item for item in lessons if item.get("status") == "active"
            ],
            "existing_debts": debts_data,
        }
        try:
            role = load_role_text(self.project_root, "arc_reviewer")
        except OSError as exc:
            raise ArtifactValidationError(f"无法读取阶段审计指令: {exc}") from exc
        prompt = (
            role.rstrip()
            + "\n\n# 唯一输入工件\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        report_path = (
            project_path(self.project_root, "reviews_dir")
            / audit_type
            / f"chapter_{through_chapter:03d}.json"
        )
        manifest = AuditRunManifest.create(
            report_path.with_suffix(".run.json"), audit_type, through_chapter
        )
        caller = ModelCallExecutor(self.router, manifest)  # type: ignore[arg-type]
        try:
            raw = caller.call(
                "arc_reviewer",
                prompt,
                self.project_root / "schemas/audit.schema.json",
                f"EXPLICIT_{audit_type.upper()}_AUDIT",
            )
            report = self._parse(raw)
            self._validate(report, audit_type, through_chapter, chapters)
        except Exception as exc:
            manifest.finish("FAILED", str(exc))
            raise
        atomic_save_json(report_path, report)
        merged = {item["id"]: item for item in debts_data}
        for item in report.get("debts", []):
            merged[item["id"]] = item
        atomic_save_json(
            debts_path,
            {
                "schema": "novel-creative-debts",
                "debts": sorted(merged.values(), key=lambda item: item["id"]),
            },
        )
        manifest.finish("COMPLETED")
        return report_path

    @staticmethod
    def _read_store(path: Path, field: str) -> list[dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"无法读取审计依赖 {path}: {exc}") from exc
        items = value.get(field) if isinstance(value, dict) else None
        if not isinstance(items, list):
            raise ArtifactValidationError(f"审计依赖缺少数组 {field}: {path}")
        return items

    @staticmethod
    def _parse(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError(f"audit不是合法JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ArtifactValidationError("audit根节点必须是object")
        return value

    @staticmethod
    def _validate(
        report: dict[str, Any],
        audit_type: str,
        through_chapter: int,
        chapters: dict[int, str],
    ) -> None:
        if (
            report.get("audit_type") != audit_type
            or report.get("through_chapter") != through_chapter
        ):
            raise ArtifactValidationError("audit 类型或截止章号不匹配")
        if not isinstance(report.get("findings"), list) or not isinstance(
            report.get("debts"), list
        ):
            raise ArtifactValidationError("audit findings/debts 必须是数组")
        for finding in report["findings"]:
            for evidence in finding.get("evidence", []):
                chapter = evidence.get("chapter")
                quote = evidence.get("quote")
                if chapter not in chapters or not quote or quote not in chapters[chapter]:
                    raise ArtifactValidationError("audit 引文无法在正式章节定位")
        for debt in report["debts"]:
            if not debt.get("id") or debt.get("scope") != "future":
                raise ArtifactValidationError("审计债务只能作用于未来章节")
