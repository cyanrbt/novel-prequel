from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProviderError
from .provider import ModelProvider


@dataclass(frozen=True)
class ResolvedModelSettings:
    profile: str
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class StageModelRouter:
    providers: dict[str, ModelProvider]
    routes: dict[str, str]
    settings: dict[str, ResolvedModelSettings] = field(default_factory=dict)

    @classmethod
    def single(cls, provider: ModelProvider) -> "StageModelRouter":
        model = getattr(provider, "model", None) or "injected-test-provider"
        effort = getattr(provider, "reasoning_effort", None) or "none"
        return cls(
            {"default": provider},
            {},
            {"default": ResolvedModelSettings("default", model, effort)},
        )

    @classmethod
    def from_config(cls, config: dict, project_root: Path) -> "StageModelRouter":
        """Fail closed: repository-managed Agent execution backends are retired."""
        raise ProviderError(
            "不再从仓库配置构造 Agent 后端；由当前 Agent 执行 WORKFLOW.md"
        )

    def profile_for(self, stage: str) -> str:
        return self.routes.get(stage, "default")

    def provider_for(self, stage: str) -> ModelProvider:
        profile = self.profile_for(stage)
        try:
            return self.providers[profile]
        except KeyError as exc:
            raise ProviderError(f"阶段 {stage} 没有可用模型档案") from exc

    def settings_for(self, stage: str) -> ResolvedModelSettings:
        profile = self.profile_for(stage)
        if profile in self.settings:
            return self.settings[profile]
        provider = self.provider_for(stage)
        model = getattr(provider, "model", None) or "injected-test-provider"
        effort = getattr(provider, "reasoning_effort", None) or "none"
        return ResolvedModelSettings(profile, model, effort)
