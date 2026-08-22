from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .cli_capabilities import build_exec_argv
from .errors import ProviderError


class ModelProvider(Protocol):
    def generate(self, prompt: str, output_schema: Path | None = None) -> str: ...


PLACEHOLDER_OUTPUTS = {
    "[PLACEHOLDER]",
    "PLACEHOLDER",
    "TO" + "DO",
    "待生成",
    "请手动执行",
}


def clean_schema_for_cli(schema_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove $id and $schema keywords which may cause strict validator rejections."""
    clean: dict[str, Any] = {}
    for k, v in schema_dict.items():
        if k in ("$id", "$schema"):
            continue
        if isinstance(v, dict):
            clean[k] = clean_schema_for_cli(v)
        elif isinstance(v, list):
            clean[k] = [
                clean_schema_for_cli(x) if isinstance(x, dict) else x for x in v
            ]
        else:
            clean[k] = v
    return clean


def strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


@dataclass(frozen=True)
class CodexCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        command = list(self.command)
        if self.project_root is not None:
            command.extend(["-C", str(self.project_root)])
        if output_schema is not None:
            command.extend(["--output-schema", str(output_schema)])
        command.append("-")
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"AI 调用超时（{self.timeout_seconds}秒）") from exc
        except OSError as exc:
            raise ProviderError(f"AI 命令无法启动: {exc}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip()[-1000:] or "无错误详情"
            raise ProviderError(f"AI 返回错误({result.returncode}): {detail}")
        output = result.stdout.strip()
        if not output or output in PLACEHOLDER_OUTPUTS:
            raise ProviderError("AI 返回了空内容或占位内容")
        return output


@dataclass(frozen=True)
class AgyCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        cmd = list(self.command)
        if "--print-timeout" not in cmd:
            cmd.extend(["--print-timeout", f"{self.timeout_seconds}s"])
        if self.model:
            cmd.extend(["--model", self.model])
        if self.reasoning_effort and self.reasoning_effort not in ("none", ""):
            model_slug = (self.model or "").lower()
            if not any(kw in model_slug for kw in ("claude", "gpt-oss")):
                cmd.extend(["--effort", self.reasoning_effort])

        schema_temp_file: Path | None = None
        try:
            if output_schema is not None:
                raw_schema = json.loads(output_schema.read_text(encoding="utf-8"))
                cleaned = clean_schema_for_cli(raw_schema)
                with tempfile.NamedTemporaryFile(
                    "w", suffix=".json", encoding="utf-8", delete=False
                ) as f:
                    json.dump(cleaned, f, ensure_ascii=False)
                    schema_temp_file = Path(f.name)
                cmd.extend(["--output-format", "json", "--json-schema", str(schema_temp_file)])
            else:
                cmd.extend(["--output-format", "json"])

            cmd.extend(["--print", prompt])

            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(self.project_root) if self.project_root else None,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(f"AGY 调用超时（{self.timeout_seconds}秒）") from exc
            except OSError as exc:
                raise ProviderError(f"AGY 命令无法启动: {exc}") from exc

            stdout_str = result.stdout.strip()
            if result.returncode != 0 and not stdout_str:
                detail = result.stderr.strip()[-1000:] or "无错误详情"
                raise ProviderError(f"AGY 返回错误({result.returncode}): {detail}")

            if not stdout_str:
                raise ProviderError("AGY 返回了空内容")

            payload = None
            try:
                start = stdout_str.find("{")
                end = stdout_str.rfind("}")
                if start != -1 and end != -1:
                    payload = json.loads(stdout_str[start : end + 1])
                else:
                    payload = json.loads(stdout_str)
            except json.JSONDecodeError:
                payload = None

            if isinstance(payload, dict):
                if (
                    output_schema is not None
                    and "structured_output" in payload
                    and payload["structured_output"] is not None
                ):
                    output = json.dumps(payload["structured_output"], ensure_ascii=False)
                elif "response" in payload and isinstance(payload["response"], str):
                    output = payload["response"].strip()
                else:
                    output = stdout_str
            else:
                output = stdout_str

            if output_schema is not None:
                output = strip_markdown_fence(output)

            if not output or output in PLACEHOLDER_OUTPUTS:
                raise ProviderError("AGY 返回了空内容或占位内容")
            return output
        finally:
            if schema_temp_file and schema_temp_file.exists():
                schema_temp_file.unlink(missing_ok=True)


@dataclass(frozen=True)
class OpenCodeCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        cmd = list(self.command)
        if self.model:
            cmd.extend(["-m", self.model])

        final_prompt = prompt
        if output_schema is not None:
            raw_schema = json.loads(output_schema.read_text(encoding="utf-8"))
            cleaned = clean_schema_for_cli(raw_schema)
            final_prompt = (
                prompt
                + "\n\n【重要输出格式要求】请务必且仅输出符合以下 JSON Schema 的纯 JSON 字符串，不要包含任何前缀、解释说明或 markdown 代码块：\n"
                + json.dumps(cleaned, ensure_ascii=False, indent=2)
            )

        cmd.append(final_prompt)

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.project_root) if self.project_root else None,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(f"OpenCode 调用超时（{self.timeout_seconds}秒）") from exc
        except OSError as exc:
            raise ProviderError(f"OpenCode 命令无法启动: {exc}") from exc

        if result.returncode != 0:
            detail = result.stderr.strip()[-1000:] or result.stdout.strip()[-1000:] or "无错误详情"
            raise ProviderError(f"OpenCode 返回错误({result.returncode}): {detail}")

        lines = result.stdout.splitlines()
        text_parts = []
        is_json_stream = False
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                event = json.loads(line_str)
                if isinstance(event, dict) and event.get("type") == "text":
                    is_json_stream = True
                    part = event.get("part")
                    if isinstance(part, dict) and "text" in part:
                        text_parts.append(part["text"])
            except json.JSONDecodeError:
                pass

        if is_json_stream:
            output = "".join(text_parts).strip()
        else:
            output = result.stdout.strip()

        if output_schema is not None:
            cleaned_out = strip_markdown_fence(output)
            try:
                json_obj = json.loads(cleaned_out)
                output = json.dumps(json_obj, ensure_ascii=False)
            except json.JSONDecodeError:
                output = cleaned_out

        if not output or output in PLACEHOLDER_OUTPUTS:
            raise ProviderError("OpenCode 返回了空内容或占位内容")
        return output


@dataclass(frozen=True)
class GrokCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    def generate(self, prompt: str, output_schema: Path | None = None) -> str:
        cmd = list(self.command)
        if self.model:
            cmd.extend(["-m", self.model])
        if self.reasoning_effort:
            cmd.extend(["--reasoning-effort", self.reasoning_effort])

        prompt_temp_file: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
                f.write(prompt)
                prompt_temp_file = Path(f.name)
            cmd.extend(["--prompt-file", str(prompt_temp_file)])

            if output_schema is not None:
                raw_schema = json.loads(output_schema.read_text(encoding="utf-8"))
                cleaned = clean_schema_for_cli(raw_schema)
                cmd.extend(["--json-schema", json.dumps(cleaned, ensure_ascii=False)])

            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(self.project_root) if self.project_root else None,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ProviderError(f"Grok 调用超时（{self.timeout_seconds}秒）") from exc
            except OSError as exc:
                raise ProviderError(f"Grok 命令无法启动: {exc}") from exc

            stdout_str = result.stdout.strip()
            if result.returncode != 0:
                detail = result.stderr.strip()[-1000:] or stdout_str[-1000:] or "无错误详情"
                raise ProviderError(f"Grok 返回错误({result.returncode}): {detail}")

            if output_schema is not None:
                start = stdout_str.find("{")
                end = stdout_str.rfind("}")
                if start != -1 and end != -1:
                    try:
                        wrapper = json.loads(stdout_str[start : end + 1])
                        if isinstance(wrapper, dict):
                            if (
                                "structuredOutput" in wrapper
                                and wrapper["structuredOutput"] is not None
                            ):
                                output = json.dumps(
                                    wrapper["structuredOutput"], ensure_ascii=False
                                )
                            elif "text" in wrapper and isinstance(wrapper["text"], str):
                                output = wrapper["text"].strip()
                            else:
                                output = stdout_str[start : end + 1]
                        else:
                            output = stdout_str
                    except json.JSONDecodeError:
                        output = stdout_str
                else:
                    output = stdout_str
            else:
                output = stdout_str

            if output_schema is not None:
                output = strip_markdown_fence(output)

            if not output or output in PLACEHOLDER_OUTPUTS:
                raise ProviderError("Grok 返回了空内容或占位内容")
            return output
        finally:
            if prompt_temp_file and prompt_temp_file.exists():
                prompt_temp_file.unlink(missing_ok=True)


def provider_from_spec(
    spec: dict[str, Any], project_root: Path
) -> ModelProvider:
    provider_type = spec.get("type", "codex_cli")
    timeout = spec.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ProviderError("provider.timeout_seconds 必须是正整数")

    model = spec.get("model")
    effort = spec.get("reasoning_effort")

    if provider_type in ("codex_cli", "codex"):
        command = spec.get("command") or [
            "codex",
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--skip-git-repo-check",
            "--config",
            'sqlite_home="novel/work/.codex-runtime"',
        ]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) for x in command)
        ):
            raise ProviderError("provider.command 必须是非空字符串数组")
        if "--dangerously-bypass-approvals-and-sandbox" in command:
            raise ProviderError("provider.command 禁止绕过沙箱")
        if not isinstance(model, str) or not isinstance(effort, str):
            raise ProviderError("Codex模型和思考强度必须显式配置")
        command = build_exec_argv(command, model, effort)
        return CodexCliProvider(command, timeout, project_root, model, effort)

    elif provider_type in ("agy_cli", "agy", "antigravity"):
        command = spec.get("command") or [
            "agy",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) for x in command)
        ):
            raise ProviderError("provider.command 必须是非空字符串数组")
        return AgyCliProvider(command, timeout, project_root, model, effort)

    elif provider_type in ("opencode_cli", "opencode"):
        command = spec.get("command") or [
            "opencode",
            "run",
            "--pure",
            "--format",
            "json",
        ]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) for x in command)
        ):
            raise ProviderError("provider.command 必须是非空字符串数组")
        return OpenCodeCliProvider(command, timeout, project_root, model, effort)

    elif provider_type in ("grok_cli", "grok"):
        command = spec.get("command") or ["grok"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) for x in command)
        ):
            raise ProviderError("provider.command 必须是非空字符串数组")
        return GrokCliProvider(command, timeout, project_root, model, effort)

    else:
        raise ProviderError(
            f"不支持的 provider.type: {provider_type} (支持 codex_cli, agy_cli, opencode_cli, grok_cli)"
        )


def provider_from_config(config: dict[str, Any], project_root: Path) -> ModelProvider:
    return provider_from_spec(config.get("provider", {}), project_root)
