from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

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


@dataclass(frozen=True)
class CodexCliProvider:
    command: list[str]
    timeout_seconds: int = 900
    project_root: Path | None = None

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


def provider_from_config(config: dict, project_root: Path) -> CodexCliProvider:
    provider = config.get("provider", {})
    if provider.get("type") != "codex_cli":
        raise ProviderError("当前只支持 provider.type=codex_cli")
    command = provider.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ProviderError("provider.command 必须是非空字符串数组")
    if "--dangerously-bypass-approvals-and-sandbox" in command:
        raise ProviderError("provider.command 禁止绕过沙箱")
    timeout = provider.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ProviderError("provider.timeout_seconds 必须是正整数")
    return CodexCliProvider(command, timeout, project_root)
