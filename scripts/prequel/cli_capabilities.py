from __future__ import annotations

import subprocess
from typing import Any, Iterable

from .backends import BACKENDS, backend_model_argv
from .errors import ProviderError


def build_exec_argv(
    base_command: Iterable[str],
    model: str,
    reasoning_effort: str,
    backend_type: str = "codex_cli",
) -> list[str]:
    try:
        backend = BACKENDS[backend_type]
    except KeyError as exc:
        raise ProviderError(f"不支持的 provider.type={backend_type}") from exc
    return backend_model_argv(base_command, backend, model, reasoning_effort)


def bundled_model_catalog(
    executable: str, backend_type: str = "codex_cli"
) -> dict[str, Any] | None:
    try:
        backend = BACKENDS[backend_type]
    except KeyError as exc:
        raise ProviderError(f"不支持的 provider.type={backend_type}") from exc
    argv = [executable, *backend.catalog_argv()]
    try:
        result = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取{backend.label}模型目录: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"{backend.label}模型目录命令失败: {detail}")
    return backend.parse_catalog(result.stdout)


def validate_requested_routes(
    catalog: dict[str, Any],
    routes: dict[str, tuple[str, str]],
) -> list[str]:
    supported: dict[str, set[str]] = {}
    for item in catalog.get("models", []):
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        supported[item["slug"]] = {
            level.get("effort")
            for level in item.get("supported_reasoning_levels", [])
            if isinstance(level, dict) and isinstance(level.get("effort"), str)
        }
    errors: list[str] = []
    for stage, (model, effort) in sorted(routes.items()):
        if model not in supported:
            errors.append(f"{stage}: 模型目录不存在 {model}")
        elif supported[model] and effort not in supported[model]:
            errors.append(f"{stage}: 模型目录不支持 {model}/{effort}")
    return errors


def tool_version(executable: str, backend_type: str = "codex_cli") -> str:
    try:
        backend = BACKENDS[backend_type]
    except KeyError as exc:
        raise ProviderError(f"不支持的 provider.type={backend_type}") from exc
    try:
        result = subprocess.run(
            [executable, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取{backend.label}版本: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ProviderError(f"{backend.label}版本命令失败")
    return result.stdout.strip()


def codex_version(codex_command: str = "codex") -> str:
    return tool_version(codex_command, "codex_cli")
