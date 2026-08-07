from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backends import ANTIGRAVITY_EFFORTS, BACKENDS
from .cli_capabilities import bundled_model_catalog
from .errors import ProviderError
from .pipeline import LOCAL_CONFIG_PATH, TEMPLATE_CONFIGS
from .state_store import atomic_save_json

AGENT_CHOICES = (
    ("codex_cli", "Codex CLI"),
    ("opencode_cli", "OpenCode"),
    ("antigravity_cli", "Antigravity (agy)"),
)


def agent_labels() -> list[tuple[str, str]]:
    return list(AGENT_CHOICES)


def load_template(project_root: Path, backend_type: str) -> dict[str, Any]:
    if backend_type not in TEMPLATE_CONFIGS:
        raise ProviderError(f"未知后端模板: {backend_type}")
    path = project_root / TEMPLATE_CONFIGS[backend_type]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"无法读取后端模板 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProviderError(f"后端模板根节点必须是object: {path}")
    return value


def backend_executable(project_root: Path, backend_type: str) -> str:
    command = load_template(project_root, backend_type).get("provider", {}).get(
        "command", []
    )
    backend = BACKENDS[backend_type]
    if isinstance(command, list) and command and isinstance(command[0], str):
        return command[0]
    return backend.name


def fetch_model_catalog(project_root: Path, backend_type: str) -> dict[str, Any]:
    executable = backend_executable(project_root, backend_type)
    return bundled_model_catalog(executable, backend_type)


def model_options(backend_type: str, catalog: dict[str, Any]) -> list[tuple[str, str]]:
    """List (slug, label) choices, dropping models the backend would reject."""
    backend = BACKENDS[backend_type]
    options: list[tuple[str, str]] = []
    for item in catalog.get("models", []):
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        slug = item["slug"]
        try:
            backend.model_argv(slug, "medium")
        except ProviderError:
            continue
        name = item.get("name") or item.get("label")
        label = slug if not name else f"{slug} ({name})"
        options.append((slug, label))
    return options


def antigravity_effort_from_slug(model: str) -> str | None:
    for effort in sorted(ANTIGRAVITY_EFFORTS, key=len, reverse=True):
        if model.endswith(f"-{effort}"):
            return effort
    return None


def available_efforts(
    backend_type: str, catalog: dict[str, Any], model: str
) -> list[str]:
    if backend_type == "antigravity_cli":
        derived = antigravity_effort_from_slug(model)
        return [derived] if derived else sorted(ANTIGRAVITY_EFFORTS)
    if backend_type == "codex_cli":
        levels = next(
            (
                item.get("supported_reasoning_levels", [])
                for item in catalog.get("models", [])
                if item.get("slug") == model
            ),
            [],
        )
        efforts = [
            level.get("effort")
            for level in levels
            if isinstance(level, dict) and isinstance(level.get("effort"), str)
        ]
        return efforts or ["low", "medium", "high"]
    return ["low", "medium", "high"]


def apply_model_config(
    config: dict[str, Any], model: str, effort: str
) -> dict[str, Any]:
    """Rewrite every model profile to the chosen model + reasoning effort."""
    result = json.loads(json.dumps(config))
    profiles = result.setdefault("model_profiles", {})
    for name in profiles:
        profiles[name] = {"model": model, "reasoning_effort": effort}
    provider = result.setdefault("provider", {})
    provider["model"] = model
    provider["reasoning_effort"] = effort
    return result


def build_backend_config(
    project_root: Path, backend_type: str, model: str, effort: str
) -> dict[str, Any]:
    config = load_template(project_root, backend_type)
    backend = BACKENDS[backend_type]
    backend.model_argv(model, effort)
    return apply_model_config(config, model, effort)


def write_local_config(project_root: Path, config: dict[str, Any]) -> Path:
    target = project_root / LOCAL_CONFIG_PATH
    atomic_save_json(target, config)
    return target
