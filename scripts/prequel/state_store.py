from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .errors import AtomicWriteError, StateValidationError


VALID_MACHINE_STATES = {
    "IDLE",
    "INIT",
    "OUTLINE",
    "WRITE",
    "REVIEW",
    "RECOVERY",
    "ERROR",
    "WAITING_USER",
}

REQUIRED_ROOT = {
    "schema",
    "machine_state",
    "chapter",
    "timeline",
    "protagonist",
    "characters",
    "world_lore",
    "active_foreshadows",
    "revealed_rules",
    "recent_hooks",
    "chapter_summaries",
}

STATE_SCHEMA = "novel-prequel-state"

REQUIRED_CHAPTER = {
    "last_chapter",
    "next_chapter",
    "current_volume",
    "current_volume_name",
    "current_event",
    "current_event_name",
    "current_phase",
}


def _require_type(
    container: dict[str, Any], key: str, expected: type | tuple[type, ...], errors: list[str]
) -> None:
    if key in container and not isinstance(container[key], expected):
        errors.append(f"{key} 类型错误")


def validate_state(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state 必须是 JSON object"]

    for key in sorted(REQUIRED_ROOT - state.keys()):
        errors.append(f"缺失根字段: {key}")

    if state.get("schema") != STATE_SCHEMA:
        errors.append(f"schema 必须为 {STATE_SCHEMA}")
    if state.get("machine_state") not in VALID_MACHINE_STATES:
        errors.append("machine_state 无效")

    chapter = state.get("chapter")
    if not isinstance(chapter, dict):
        errors.append("chapter 必须是 object")
        return errors
    for key in sorted(REQUIRED_CHAPTER - chapter.keys()):
        errors.append(f"缺失 chapter 字段: {key}")

    _require_type(chapter, "last_chapter", int, errors)
    _require_type(chapter, "next_chapter", int, errors)
    _require_type(chapter, "current_volume", int, errors)
    last_chapter = chapter.get("last_chapter")
    next_chapter = chapter.get("next_chapter")
    if isinstance(last_chapter, int) and isinstance(next_chapter, int):
        if last_chapter < 0:
            errors.append("last_chapter 不能小于 0")
        if next_chapter != last_chapter + 1:
            errors.append("next_chapter 必须等于 last_chapter + 1")

    timeline = state.get("timeline")
    if not isinstance(timeline, dict):
        errors.append("timeline 必须是 object")
    elif not isinstance(timeline.get("current_year"), int):
        errors.append("timeline.current_year 必须是整数")

    protagonist = state.get("protagonist")
    if not isinstance(protagonist, dict):
        errors.append("protagonist 必须是 object")
    else:
        for key in ("name", "age", "location", "abilities", "known_info", "body", "inventory"):
            if key not in protagonist:
                errors.append(f"缺失 protagonist 字段: {key}")

    for key in ("characters", "world_lore", "active_foreshadows", "chapter_summaries"):
        if key in state and not isinstance(state[key], dict):
            errors.append(f"{key} 必须是 object")
    for key in ("revealed_rules", "recent_hooks"):
        if key in state and not isinstance(state[key], list):
            errors.append(f"{key} 必须是 array")
    if "completed_milestones" in state and not isinstance(state["completed_milestones"], list):
        errors.append("completed_milestones 必须是 array")

    summaries = state.get("chapter_summaries", {})
    if isinstance(summaries, dict):
        if not isinstance(summaries.get("compression_level"), int):
            errors.append("chapter_summaries.compression_level 必须是整数")
        if not isinstance(summaries.get("summaries"), dict):
            errors.append("chapter_summaries.summaries 必须是 object")

    return errors


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateValidationError(f"状态文件不存在: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StateValidationError(f"状态文件无法读取: {exc}") from exc
    errors = validate_state(state)
    if errors:
        raise StateValidationError("；".join(errors))
    return state


def atomic_save_json(path: Path, value: Any, *, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        if backup and path.exists():
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise AtomicWriteError(f"文件写入失败 {path}: {exc}") from exc


def atomic_save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise AtomicWriteError(f"文件写入失败 {path}: {exc}") from exc


def atomic_save_state(path: Path, state: dict[str, Any]) -> None:
    errors = validate_state(state)
    if errors:
        raise StateValidationError("；".join(errors))
    atomic_save_json(path, copy.deepcopy(state), backup=True)
