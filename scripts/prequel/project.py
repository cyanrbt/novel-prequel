from __future__ import annotations

import json
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ArtifactValidationError


PROJECT_SCHEMA = "creative-project/1"
PROJECT_POINTER_SCHEMA = "creative-project-pointer/1"


REQUIRED_PROJECT_PATHS = frozenset(
    {
        "state",
        "canon_registry",
        "arc_registry",
        "foreshadow_registry",
        "creative_debts",
        "quality_lessons",
        "memory_index",
        "knowledge_dir",
        "plots_dir",
        "series_architecture",
        "opening_blueprint",
        "characters_dir",
        "protagonist_card",
        "character_voice_fallbacks",
        "chapters_dir",
        "chapter_meta_dir",
        "work_dir",
        "full_novel",
        "reviews_dir",
        "rulebook",
        "setting_whitelist",
        "setting_blacklist",
        "compact_style",
        "reference_voice_profile",
        "user_taste_contract",
        "style_anchors",
        "opening_benchmarks",
    }
)


KEY_FILE_ALIASES: dict[str, str] = {
    "state": "state",
    "rulebook": "rulebook",
    "compact_style": "compact_style",
    "reference_voice_profile": "reference_voice_profile",
    "user_taste_contract": "user_taste_contract",
    "style_anchors": "style_anchors",
    "series_architecture": "series_architecture",
    "canon_registry": "canon_registry",
    "arc_registry": "arc_registry",
    "foreshadow_registry": "foreshadow_registry",
    "setting_whitelist": "setting_whitelist",
    "setting_blacklist": "setting_blacklist",
    "opening_benchmarks": "opening_benchmarks",
}


_ACTIVE_PROJECT: ContextVar[ProjectSpec | None] = ContextVar(
    "creative_active_project", default=None
)


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"{label}无效 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label}根节点必须是object: {path}")
    return value


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _manifest_path(repository_root: Path, project: str | Path | None) -> Path | None:
    if project is not None:
        candidate = Path(project)
        if not candidate.is_absolute():
            candidate = repository_root / candidate
        if candidate.is_dir():
            candidate = candidate / "project.json"
        return candidate.resolve()

    pointer = repository_root / "project.json"
    if not pointer.is_file():
        return None
    value = _read_object(pointer, "默认项目指针")
    if value.get("schema") == PROJECT_SCHEMA:
        return pointer.resolve()
    if value.get("schema") != PROJECT_POINTER_SCHEMA:
        raise ArtifactValidationError("project.json 既不是项目清单也不是项目指针")
    target = value.get("project")
    if not isinstance(target, str) or not target.strip():
        raise ArtifactValidationError("默认项目指针缺少 project")
    return (pointer.parent / target).resolve()


@dataclass(frozen=True)
class ProjectSpec:
    repository_root: Path
    project_id: str
    title: str
    manifest_path: Path | None
    declared_paths: Mapping[str, Path]
    engine_config_path: Path
    story_config_path: Path
    profiles: tuple[str, ...] = ()

    def path(self, key: str) -> Path:
        try:
            return self.declared_paths[key]
        except KeyError as exc:
            raise ArtifactValidationError(
                f"项目 {self.project_id} 未声明路径: {key}"
            ) from exc

    def relative_path(self, key: str) -> str:
        path = self.path(key)
        try:
            return str(path.relative_to(self.repository_root))
        except ValueError:
            return str(path)

    def load_config(self) -> dict[str, Any]:
        engine = _read_object(self.engine_config_path, "引擎配置")
        if self.story_config_path == self.engine_config_path:
            merged = engine
        else:
            story = _read_object(self.story_config_path, "故事配置")
            merged = _deep_merge(engine, story)
        merged["project_id"] = self.project_id
        merged["project_title"] = self.title
        merged["project_profiles"] = list(self.profiles)
        merged["key_files"] = {
            alias: self.relative_path(path_key)
            for alias, path_key in KEY_FILE_ALIASES.items()
            if path_key in self.declared_paths
        }
        return merged


def load_project_spec(
    repository_root: Path,
    project: str | Path | ProjectSpec | None = None,
) -> ProjectSpec:
    if isinstance(project, ProjectSpec):
        return project
    root = repository_root.resolve()
    if project is None:
        active = _ACTIVE_PROJECT.get()
        if active is not None and active.repository_root == root:
            return active
    manifest_path = _manifest_path(root, project)
    if manifest_path is None:
        raise ArtifactValidationError(
            "仓库缺少 project.json；所有故事必须通过 creative-project/1 清单选择"
        )

    if not manifest_path.is_relative_to(root):
        raise ArtifactValidationError("创作项目清单必须位于仓库内")
    manifest = _read_object(manifest_path, "创作项目清单")
    if manifest.get("schema") != PROJECT_SCHEMA:
        raise ArtifactValidationError(
            f"创作项目清单 schema 必须是 {PROJECT_SCHEMA}: {manifest_path}"
        )
    project_id = manifest.get("project_id")
    title = manifest.get("title")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ArtifactValidationError("创作项目清单缺少 project_id")
    if not isinstance(title, str) or not title.strip():
        raise ArtifactValidationError("创作项目清单缺少 title")

    def declared_file(field: str) -> Path:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ArtifactValidationError(f"创作项目清单缺少 {field}")
        resolved = (manifest_path.parent / value).resolve()
        if not resolved.is_relative_to(root):
            raise ArtifactValidationError(f"创作项目清单 {field} 必须位于仓库内")
        return resolved

    raw_paths = manifest.get("paths")
    if not isinstance(raw_paths, dict):
        raise ArtifactValidationError("创作项目清单 paths 必须是object")
    declared_paths: dict[str, Path] = {}
    for key, value in raw_paths.items():
        if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
            raise ArtifactValidationError("创作项目清单 paths 只能包含非空字符串")
        resolved = (manifest_path.parent / value).resolve()
        if not resolved.is_relative_to(root):
            raise ArtifactValidationError(f"创作项目路径必须位于仓库内: {key}")
        declared_paths[key] = resolved
    missing = sorted(REQUIRED_PROJECT_PATHS - set(declared_paths))
    if missing:
        raise ArtifactValidationError("创作项目清单缺少路径: " + ", ".join(missing))

    raw_profiles = manifest.get("profiles", [])
    if not isinstance(raw_profiles, list) or not all(
        isinstance(item, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", item)
        for item in raw_profiles
    ):
        raise ArtifactValidationError(
            "创作项目清单 profiles 必须是安全的小写标识符数组"
        )
    return ProjectSpec(
        repository_root=root,
        project_id=project_id,
        title=title,
        manifest_path=manifest_path,
        declared_paths=declared_paths,
        engine_config_path=declared_file("engine_config"),
        story_config_path=declared_file("story_config"),
        profiles=tuple(raw_profiles),
    )


def activate_project(spec: ProjectSpec) -> Token[ProjectSpec | None]:
    """Select one project for the current execution context."""
    return _ACTIVE_PROJECT.set(spec)


def reset_active_project(token: Token[ProjectSpec | None]) -> None:
    _ACTIVE_PROJECT.reset(token)


def project_path(repository_root: Path, key: str) -> Path:
    return load_project_spec(repository_root).path(key)


def role_paths(repository_root: Path, role: str) -> list[Path]:
    spec = load_project_spec(repository_root)
    config = spec.load_config()
    roles = config.get("agents", {})
    relative = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(relative, str) or not relative.strip():
        relative = f"agents/{role}.md"
    declared = [relative]
    overlays = config.get("role_overlays", {})
    if isinstance(overlays, dict):
        raw = overlays.get(role, [])
        if isinstance(raw, str):
            declared.append(raw)
        elif isinstance(raw, list):
            declared.extend(item for item in raw if isinstance(item, str))
    paths: list[Path] = []
    for item in declared:
        path = Path(item)
        if not path.is_absolute():
            path = spec.repository_root / path
        path = path.resolve()
        if not path.is_relative_to(spec.repository_root) or path.suffix != ".md":
            raise ArtifactValidationError(f"角色指令必须是仓库内Markdown: {item}")
        if not path.is_file():
            raise ArtifactValidationError(f"角色指令不存在: {item}")
        paths.append(path)
    return paths


def load_role_text(repository_root: Path, role: str) -> str:
    parts = [
        path.read_text(encoding="utf-8").strip()
        for path in role_paths(repository_root, role)
    ]
    return "\n\n".join(part for part in parts if part)
