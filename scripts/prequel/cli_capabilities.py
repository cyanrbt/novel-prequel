from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable

from .errors import ProviderError


APPROVED_CODEX_MODELS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
}
APPROVED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


@dataclass(frozen=True)
class DiscoveredModel:
    slug: str
    display_name: str
    supported_efforts: list[str] = field(default_factory=list)
    is_default: bool = False


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_type: str
    cli_command: str
    version: str
    models: list[DiscoveredModel] = field(default_factory=list)

    def model_map(self) -> dict[str, DiscoveredModel]:
        return {m.slug: m for m in self.models}

    def model_slugs(self) -> list[str]:
        return [m.slug for m in self.models]

    def efforts_for(self, model_slug: str) -> list[str]:
        model = self.model_map().get(model_slug)
        return list(model.supported_efforts) if model else []


def _to_argv(command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        return list(command)
    return shlex.split(command)


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


def bundled_model_catalog(codex_command: str | list[str] = "codex") -> dict[str, Any]:
    cmd = [*_to_argv(codex_command), "debug", "models", "--bundled"]
    try:
        result = subprocess.run(
            cmd,
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


def codex_version(codex_command: str | list[str] = "codex") -> str:
    cmd = [*_to_argv(codex_command), "--version"]
    try:
        result = subprocess.run(
            cmd,
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


def agy_version(agy_command: str | list[str] = "agy") -> str:
    argv = _to_argv(agy_command)
    cmd = [*argv, "changelog"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法启动AGY CLI: {exc}") from exc
    if result.returncode != 0:
        try:
            help_res = subprocess.run(
                [*argv, "--help"],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            if help_res.returncode == 0:
                return "agy-cli"
        except Exception:
            pass
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"AGY版本命令失败: {detail}")
    line = (
        result.stdout.strip().splitlines()[0].split(":")[0].strip()
        if result.stdout.strip()
        else "active"
    )
    return f"agy {line}"


def opencode_version(opencode_command: str | list[str] = "opencode") -> str:
    cmd = [*_to_argv(opencode_command), "--version"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取OpenCode版本: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ProviderError("OpenCode版本命令失败")
    return f"opencode {result.stdout.strip()}"


def grok_version(grok_command: str | list[str] = "grok") -> str:
    cmd = [*_to_argv(grok_command), "--version"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取Grok版本: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ProviderError("Grok版本命令失败")
    return result.stdout.strip()


def discover_codex_capabilities(
    codex_command: str | list[str] = "codex",
) -> ProviderCapabilities:
    ver = codex_version(codex_command)
    cat = bundled_model_catalog(codex_command)
    models: list[DiscoveredModel] = []
    for item in cat.get("models", []):
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        slug = item["slug"]
        efforts = [
            level.get("effort")
            for level in item.get("supported_reasoning_levels", [])
            if isinstance(level, dict) and isinstance(level.get("effort"), str)
        ]
        models.append(
            DiscoveredModel(
                slug=slug,
                display_name=item.get("name") or slug,
                supported_efforts=efforts,
                is_default=bool(item.get("is_default", False)),
            )
        )
    return ProviderCapabilities(
        provider_type="codex_cli",
        cli_command=" ".join(_to_argv(codex_command)),
        version=ver,
        models=models,
    )


def discover_agy_capabilities(
    agy_command: str | list[str] = "agy",
) -> ProviderCapabilities:
    ver = agy_version(agy_command)
    cmd = [*_to_argv(agy_command), "models"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取AGY模型列表: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"AGY模型列表命令失败: {detail}")

    models: list[DiscoveredModel] = []
    gemini_efforts = ["low", "medium", "high"]
    none_efforts = ["none"]
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or "Fetching" in line:
            continue
        parts = line.split(maxsplit=1)
        slug = parts[0]
        name = parts[1] if len(parts) > 1 else slug
        if any(kw in slug.lower() for kw in ("claude", "gpt-oss")):
            efforts = none_efforts
        else:
            efforts = gemini_efforts
        models.append(
            DiscoveredModel(
                slug=slug,
                display_name=name,
                supported_efforts=efforts,
            )
        )
    return ProviderCapabilities(
        provider_type="agy_cli",
        cli_command=" ".join(_to_argv(agy_command)),
        version=ver,
        models=models,
    )


def discover_opencode_capabilities(
    opencode_command: str | list[str] = "opencode",
) -> ProviderCapabilities:
    ver = opencode_version(opencode_command)
    cmd = [*_to_argv(opencode_command), "models"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取OpenCode模型列表: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"OpenCode模型列表命令失败: {detail}")

    models: list[DiscoveredModel] = []
    for line in result.stdout.splitlines():
        slug = line.strip()
        if not slug:
            continue
        models.append(
            DiscoveredModel(
                slug=slug,
                display_name=slug,
                supported_efforts=["none"],
            )
        )
    return ProviderCapabilities(
        provider_type="opencode_cli",
        cli_command=" ".join(_to_argv(opencode_command)),
        version=ver,
        models=models,
    )


def discover_grok_capabilities(
    grok_command: str | list[str] = "grok",
) -> ProviderCapabilities:
    ver = grok_version(grok_command)
    cmd = [*_to_argv(grok_command), "models"]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(f"无法读取Grok模型列表: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:] or "无错误详情"
        raise ProviderError(f"Grok模型列表命令失败: {detail}")

    models: list[DiscoveredModel] = []
    standard_efforts = ["low", "medium", "high", "xhigh"]
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or any(
            kw in line
            for kw in ("Available", "Default", "logged in", "Fetching")
        ):
            continue
        is_default = False
        if line.startswith("*"):
            is_default = True
            line = line.lstrip("*").strip()
        elif line.startswith("-"):
            line = line.lstrip("-").strip()
        slug = line.split()[0]
        models.append(
            DiscoveredModel(
                slug=slug,
                display_name=slug,
                supported_efforts=standard_efforts,
                is_default=is_default,
            )
        )
    return ProviderCapabilities(
        provider_type="grok_cli",
        cli_command=" ".join(_to_argv(grok_command)),
        version=ver,
        models=models,
    )


def discover_capabilities(
    provider_type: str,
    command: str | list[str] | None = None,
) -> ProviderCapabilities:
    if provider_type in ("codex_cli", "codex"):
        return discover_codex_capabilities(command or "codex")
    elif provider_type in ("agy_cli", "agy", "antigravity"):
        return discover_agy_capabilities(command or "agy")
    elif provider_type in ("opencode_cli", "opencode"):
        return discover_opencode_capabilities(command or "opencode")
    elif provider_type in ("grok_cli", "grok"):
        return discover_grok_capabilities(command or "grok")
    else:
        raise ProviderError(f"不支持的 Provider 类型: {provider_type}")


def discover_all_capabilities() -> dict[str, ProviderCapabilities]:
    results: dict[str, ProviderCapabilities] = {}
    for ptype in ("codex_cli", "agy_cli", "opencode_cli", "grok_cli"):
        try:
            results[ptype] = discover_capabilities(ptype)
        except Exception:
            pass
    return results


def validate_requested_routes(
    catalog: dict[str, Any] | ProviderCapabilities,
    routes: dict[str, tuple[str, str]],
) -> list[str]:
    supported: dict[str, set[str]] = {}
    if isinstance(catalog, ProviderCapabilities):
        for m in catalog.models:
            supported[m.slug] = set(m.supported_efforts)
    else:
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
        if not model:
            continue
        if model not in supported:
            errors.append(f"{stage}: 模型目录不存在 {model}")
        elif (
            effort
            and effort not in ("none", "")
            and supported[model]
            and supported[model] != {"none"}
            and effort not in supported[model]
        ):
            errors.append(f"{stage}: 模型目录不支持 {model}/{effort}")
    return errors
