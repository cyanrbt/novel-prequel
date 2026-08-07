from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .errors import ProviderError

PLACEHOLDER_OUTPUTS = {
    "[PLACEHOLDER]",
    "PLACEHOLDER",
    "TO" + "DO",
    "待生成",
    "请手动执行",
}

APPROVED_CODEX_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
}
CODEX_EFFORTS = {"low", "medium", "high", "xhigh"}
OPENCODE_EFFORTS = {"low", "medium", "high", "minimal", "max", "none", "xhigh"}
ANTIGRAVITY_EFFORTS = {"low", "medium", "high"}

_SCHEMA_INSTRUCTION = (
    "\n\n# 输出契约\n"
    "你的回复必须且只能是满足以下 JSON Schema 的一个 JSON object。"
    "不要附加解释，也不要输出代码围栏之外的文字。\n"
    "```json\n{}\n```"
)

_JSON_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def repair_json_text(text: str) -> str:
    """Remove trailing commas introduced by models lacking server-side schema enforcement."""
    previous = None
    while previous != text:
        previous = text
        text = _JSON_TRAILING_COMMA.sub(r"\1", text)
    return text


class CliBackend:
    """Describe one headless agent CLI so the engine can build invocations."""

    type: str
    name: str
    label: str
    prompt_delivery: str = "stdin"  # "stdin" 通过 "-" 传 stdin；"positional" 作为尾随参数
    schema_native: bool = False
    schema_flag: str | None = None
    workdir_flag: str | None = None

    def model_argv(self, model: str, effort: str) -> list[str]:
        raise NotImplementedError

    def catalog_argv(self) -> tuple[str, ...]:
        return ()

    def parse_catalog(self, stdout: str) -> dict[str, Any] | None:
        return None

    def parse_output(self, stdout: str) -> str:
        return stdout.strip()

    def parse_schema(self, output_schema: Path) -> str:
        try:
            return output_schema.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderError(f"无法读取输出schema: {exc}") from exc


class CodexBackend(CliBackend):
    type = "codex_cli"
    name = "codex"
    label = "Codex"
    prompt_delivery = "stdin"
    schema_native = True
    schema_flag = "--output-schema"
    workdir_flag = "-C"

    def model_argv(self, model: str, effort: str) -> list[str]:
        if model not in APPROVED_CODEX_MODELS:
            raise ProviderError(f"未知或未批准的Codex模型: {model}")
        if effort not in CODEX_EFFORTS:
            raise ProviderError(f"不允许的Codex思考强度: {effort}")
        return ["--model", model, "--config", f'model_reasoning_effort="{effort}"']

    def catalog_argv(self) -> tuple[str, ...]:
        return ("debug", "models", "--bundled")

    def parse_catalog(self, stdout: str) -> dict[str, Any]:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Codex模型目录不是合法JSON: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            raise ProviderError("Codex模型目录缺少models数组")
        return value


class OpenCodeBackend(CliBackend):
    type = "opencode_cli"
    name = "opencode"
    label = "OpenCode"
    prompt_delivery = "stdin"
    schema_native = False
    schema_flag = None
    workdir_flag = "--dir"

    def model_argv(self, model: str, effort: str) -> list[str]:
        if not isinstance(model, str) or "/" not in model:
            raise ProviderError(f"OpenCode模型必须是 provider/model 格式: {model}")
        if effort not in OPENCODE_EFFORTS:
            raise ProviderError(f"不允许的OpenCode思考强度: {effort}")
        return ["--model", model, "--variant", effort]

    def catalog_argv(self) -> tuple[str, ...]:
        return ("models",)

    def parse_catalog(self, stdout: str) -> dict[str, Any]:
        models = []
        for line in stdout.splitlines():
            slug = line.strip()
            if slug and not slug.startswith("#"):
                models.append({"slug": slug, "supported_reasoning_levels": []})
        return {"models": models}

    def parse_output(self, stdout: str) -> str:
        parts: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "text":
                continue
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        if not parts:
            raise ProviderError("OpenCode未产生文本输出")
        return "\n".join(parts).strip()


class AntigravityBackend(CliBackend):
    """Antigravity（agy）CLI。

    注意：headless（--print）模式会拒绝需要 command 权限的工具，导致无输出。
    使用本后端前需在 ~/.gemini/config/ 的 settings.json 中配置 permissions.allow，
    或选择不带工具的最小 agent。
    """

    type = "antigravity_cli"
    name = "antigravity"
    label = "Antigravity"
    prompt_delivery = "positional"
    schema_native = True
    schema_flag = "--json-schema"
    workdir_flag = None

    def model_argv(self, model: str, effort: str) -> list[str]:
        if not isinstance(model, str) or not model.strip():
            raise ProviderError("Antigravity模型必须显式配置")
        if effort not in ANTIGRAVITY_EFFORTS:
            raise ProviderError(f"不允许的Antigravity思考强度: {effort}")
        return ["--print", "--output-format", "text", "--model", model]

    def catalog_argv(self) -> tuple[str, ...]:
        return ("models",)

    def parse_catalog(self, stdout: str) -> dict[str, Any]:
        models = []
        for line in stdout.splitlines():
            slug = line.split("\t", 1)[0].strip()
            if slug and not slug.startswith("#"):
                models.append({"slug": slug, "supported_reasoning_levels": []})
        return {"models": models}

    def parse_output(self, stdout: str) -> str:
        if "no output produced" in stdout:
            raise ProviderError(f"Antigravity headless无输出: {stdout.strip()[:400]}")
        return stdout.strip()


BACKENDS: dict[str, CliBackend] = {
    backend.type: backend
    for backend in (CodexBackend(), OpenCodeBackend(), AntigravityBackend())
}


def backend_from_spec(spec: dict[str, Any]) -> CliBackend:
    backend_type = spec.get("type") if isinstance(spec, dict) else None
    if backend_type not in BACKENDS:
        supported = ", ".join(sorted(BACKENDS))
        raise ProviderError(f"不支持的 provider.type={backend_type!r}；支持: {supported}")
    return BACKENDS[backend_type]


def backend_model_argv(
    base_command: Iterable[str],
    backend: CliBackend,
    model: str,
    effort: str,
) -> list[str]:
    command = list(base_command)
    if any(arg in {"-m", "--model"} for arg in command):
        raise ProviderError("provider.command 不得内嵌模型参数")
    if any("model_reasoning_effort" in arg for arg in command):
        raise ProviderError("provider.command 不得内嵌思考强度")
    return [*command, *backend.model_argv(model, effort)]


def build_invocation(
    backend: CliBackend,
    command: list[str],
    project_root: Path | None,
    output_schema: Path | None,
    prompt: str,
) -> tuple[list[str], str | None]:
    """Return (argv, stdin_text). 位置参数后端的 prompt 会进入 argv 而非 stdin。

    模型与思考强度参数由 ``provider_from_spec`` 在构造时写入 command，
    这里只负责工作目录、输出契约和 prompt 投递，避免重复追加模型参数。
    """
    argv = list(command)
    if project_root is not None and backend.workdir_flag:
        argv.extend([backend.workdir_flag, str(project_root)])
    delivered = prompt
    if output_schema is not None:
        if backend.schema_native and backend.schema_flag:
            argv.extend([backend.schema_flag, str(output_schema)])
        else:
            delivered = prompt + _SCHEMA_INSTRUCTION.format(
                backend.parse_schema(output_schema)
            )
    if backend.prompt_delivery == "stdin":
        argv.append("-")
        return argv, delivered
    argv.extend(["--", delivered])
    return argv, None
