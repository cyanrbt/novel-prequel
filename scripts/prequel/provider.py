from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .backends import (
    PLACEHOLDER_OUTPUTS,
    AntigravityBackend,
    CliBackend,
    CodexBackend,
    OpenCodeBackend,
    backend_from_spec,
    backend_model_argv,
    build_invocation,
    repair_json_text,
)
from .errors import ProviderError


class ModelProvider(Protocol):
    def generate(self, prompt: str, output_schema: Path | None = None) -> str: ...


def _execute(
    backend: CliBackend,
    command: list[str],
    project_root: Path | None,
    output_schema: Path | None,
    prompt: str,
    timeout_seconds: int,
) -> str:
    argv, stdin_text = build_invocation(
        backend, command, project_root, output_schema, prompt
    )
    try:
        result = subprocess.run(
            argv,
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"AI 调用超时（{timeout_seconds}秒）") from exc
    except OSError as exc:
        raise ProviderError(f"AI 命令无法启动: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"AI 返回错误({result.returncode}): {detail}")
    output = backend.parse_output(result.stdout)
    if output_schema is not None and not backend.schema_native:
        output = repair_json_text(output)
    if not output or output in PLACEHOLDER_OUTPUTS:
        raise ProviderError("AI 返回了空内容或占位内容")
    return output


@dataclass(frozen=True)
class CodexCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        return _execute(
            CodexBackend(),
            self.command,
            self.project_root,
            output_schema,
            prompt,
            self.timeout_seconds,
        )


@dataclass(frozen=True)
class OpenCodeCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        return _execute(
            OpenCodeBackend(),
            self.command,
            self.project_root,
            output_schema,
            prompt,
            self.timeout_seconds,
        )


@dataclass(frozen=True)
class AntigravityCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        return _execute(
            AntigravityBackend(),
            self.command,
            self.project_root,
            output_schema,
            prompt,
            self.timeout_seconds,
        )


_PROVIDER_CLASSES = {
    "codex_cli": CodexCliProvider,
    "opencode_cli": OpenCodeCliProvider,
    "antigravity_cli": AntigravityCliProvider,
}


def provider_from_spec(spec: dict, project_root: Path) -> ModelProvider:
    backend = backend_from_spec(spec)
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(value, str) for value in command
    ):
        raise ProviderError("provider.command 必须是非空字符串数组")
    if "--dangerously-bypass-approvals-and-sandbox" in command:
        raise ProviderError("provider.command 禁止绕过沙箱")
    timeout = spec.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ProviderError("provider.timeout_seconds 必须是正整数")
    model = spec.get("model")
    effort = spec.get("reasoning_effort")
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ProviderError(f"{backend.label}模型和思考强度必须显式配置")
    command = backend_model_argv(command, backend, model, effort)
    return _PROVIDER_CLASSES[backend.type](
        command, timeout, project_root, model, effort
    )


def provider_from_config(config: dict, project_root: Path) -> ModelProvider:
    return provider_from_spec(config.get("provider", {}), project_root)
