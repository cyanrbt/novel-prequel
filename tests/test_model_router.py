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


if __name__ == "__main__":
    unittest.main()
