import unittest
from pathlib import Path

from scripts.prequel.errors import ProviderError
from scripts.prequel.model_router import StageModelRouter


class StubProvider:
    def generate(self, prompt, output_schema=None):
        return prompt


class ModelRouterTests(unittest.TestCase):
    def test_single_provider_serves_every_stage(self):
        provider = StubProvider()
        router = StageModelRouter.single(provider)
        self.assertIs(router.provider_for("planner"), provider)
        self.assertIs(router.provider_for("selector"), provider)

    def test_profile_inherits_legacy_command_and_overrides_timeout(self):
        config = {
            "provider": {
                "type": "codex_cli",
                "command": ["codex", "exec"],
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "timeout_seconds": 900,
            },
            "model_profiles": {"default": {}, "judge": {"timeout_seconds": 1200}},
            "stage_routes": {"planner": "default", "selector": "judge"},
        }
        router = StageModelRouter.from_config(config, Path.cwd())
        self.assertEqual(router.provider_for("planner").timeout_seconds, 900)
        self.assertEqual(router.provider_for("selector").timeout_seconds, 1200)

    def test_resolved_route_exposes_model_effort_and_profile(self):
        config = {
            "provider": {
                "type": "codex_cli",
                "command": ["codex", "exec"],
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
            },
            "model_profiles": {
                "default": {},
                "terra_high": {
                    "model": "gpt-5.6-terra",
                    "reasoning_effort": "high",
                },
            },
            "stage_routes": {"continuity_reviewer": "terra_high"},
        }
        route = StageModelRouter.from_config(config, Path.cwd()).settings_for(
            "continuity_reviewer"
        )
        self.assertEqual(
            (route.profile, route.model, route.reasoning_effort),
            ("terra_high", "gpt-5.6-terra", "high"),
        )

    def test_unknown_profile_fails_preflight(self):
        config = {
            "provider": {
                "type": "codex_cli",
                "command": ["codex", "exec"],
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
            },
            "model_profiles": {"default": {}},
            "stage_routes": {"selector": "missing"},
        }
        with self.assertRaises(ProviderError):
            StageModelRouter.from_config(config, Path.cwd())

    def test_multi_provider_router_with_agy_opencode_grok(self):
        config = {
            "provider": {
                "type": "agy_cli",
                "model": "gemini-3.7-flash-high",
                "reasoning_effort": "high",
            },
            "model_profiles": {
                "default": {},
                "opencode_stage": {
                    "type": "opencode_cli",
                    "model": "deepseek/deepseek-chat",
                },
                "grok_stage": {
                    "type": "grok_cli",
                    "model": "grok-4.6",
                    "reasoning_effort": "high",
                },
            },
            "stage_routes": {
                "planner": "default",
                "candidate_writer": "opencode_stage",
                "verifier": "grok_stage",
            },
        }
        router = StageModelRouter.from_config(config, Path.cwd())
        from scripts.prequel.provider import (
            AgyCliProvider,
            GrokCliProvider,
            OpenCodeCliProvider,
        )

        self.assertIsInstance(router.provider_for("planner"), AgyCliProvider)
        self.assertIsInstance(
            router.provider_for("candidate_writer"), OpenCodeCliProvider
        )
        self.assertIsInstance(router.provider_for("verifier"), GrokCliProvider)


if __name__ == "__main__":
    unittest.main()
