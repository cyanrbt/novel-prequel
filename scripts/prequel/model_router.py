from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProviderError
from .provider import CodexCliProvider, ModelProvider, provider_from_spec


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
        legacy = config.get("provider", {})
        profiles = config.get("model_profiles") or {"default": {}}
        if not isinstance(profiles, dict):
            raise ProviderError("model_profiles 必须是object")
        if "default" not in profiles:
            profiles = {"default": {}, **profiles}
        providers: dict[str, ModelProvider] = {}
        settings: dict[str, ResolvedModelSettings] = {}
        for name, spec in profiles.items():
            if not isinstance(name, str) or not isinstance(spec, dict):
                raise ProviderError("model_profiles 的名称和配置必须有效")
            provider = provider_from_spec({**legacy, **spec}, project_root)
            providers[name] = provider
            settings[name] = ResolvedModelSettings(
                name, provider.model or "", provider.reasoning_effort or ""
            )
        routes = config.get("stage_routes", {})
        if not isinstance(routes, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in routes.items()
        ):
            raise ProviderError("stage_routes 必须是字符串映射")
        missing = sorted(set(routes.values()) - set(providers))
        if missing:
            raise ProviderError("阶段路由引用未知模型档案: " + ", ".join(missing))
        return cls(providers, routes, settings)

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
