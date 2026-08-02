from __future__ import annotations

import json
import subprocess
from typing import Any, Iterable

from .errors import ProviderError


APPROVED_CODEX_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
}
APPROVED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def build_exec_argv(
    base_command: Iterable[str], model: str, reasoning_effort: str
) -> list[str]:
    command = list(base_command)
    if model not in APPROVED_CODEX_MODELS:
        raise ProviderError(f"未知或未批准的Codex模型: {model}")
    if reasoning_effort not in APPROVED_REASONING_EFFORTS:
        raise ProviderError(f"不允许的思考强度: {reasoning_effort}")
    if any(arg in {"-m", "--model"} for arg in command):
        raise ProviderError("provider.command 不得内嵌模型参数")
    if any("model_reasoning_effort" in arg for arg in command):
        raise ProviderError("provider.command 不得内嵌思考强度")
    return [
        *command,
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]


def bundled_model_catalog(codex_command: str = "codex") -> dict[str, Any]:
    try:
        result = subprocess.run(
            [codex_command, "debug", "models", "--bundled"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取Codex内置模型目录: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"Codex模型目录命令失败: {detail}")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"Codex模型目录不是合法JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("models"), list):
        raise ProviderError("Codex模型目录缺少models数组")
    return value


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
        elif effort not in supported[model]:
            errors.append(f"{stage}: 模型目录不支持 {model}/{effort}")
    return errors


def codex_version(codex_command: str = "codex") -> str:
    try:
        result = subprocess.run(
            [codex_command, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取Codex版本: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ProviderError("Codex版本命令失败")
    return result.stdout.strip()
