import sys
import unittest

from scripts.prequel.cli_capabilities import (
    DiscoveredModel,
    ProviderCapabilities,
    agy_version,
    build_exec_argv,
    codex_version,
    discover_all_capabilities,
    discover_capabilities,
    grok_version,
    opencode_version,
    validate_requested_routes,
)
from scripts.prequel.errors import ProviderError


def catalog(luna_efforts=None):
    efforts = lambda values: [{"effort": value} for value in values]
    common = ["low", "medium", "high", "xhigh"]
    return {
        "models": [
            {"slug": "gpt-5.6-sol", "supported_reasoning_levels": efforts(common)},
            {"slug": "gpt-5.6-terra", "supported_reasoning_levels": efforts(common)},
            {
                "slug": "gpt-5.6-luna",
                "supported_reasoning_levels": efforts(luna_efforts or common),
            },
        ]
    }


class CliCapabilitiesTests(unittest.TestCase):
    def test_approved_model_effort_pairs_exist_in_catalog(self):
        errors = validate_requested_routes(
            catalog(),
            {
                "writer": ("gpt-5.6-sol", "medium"),
                "planner": ("gpt-5.6-terra", "medium"),
                "specialist": ("gpt-5.6-terra", "high"),
                "verifier": ("gpt-5.6-luna", "high"),
            },
        )
        self.assertEqual(errors, [])

    def test_missing_effort_fails_capability_check(self):
        errors = validate_requested_routes(
            catalog(luna_efforts=["low", "medium"]),
            {"verifier": ("gpt-5.6-luna", "high")},
        )
        self.assertIn("gpt-5.6-luna/high", errors[0])

    def test_provider_capabilities_validates_routes(self):
        caps = ProviderCapabilities(
            provider_type="grok_cli",
            cli_command="grok",
            version="grok 1.0.5",
            models=[
                DiscoveredModel(
                    slug="grok-4.6",
                    display_name="grok-4.6",
                    supported_efforts=["low", "medium", "high", "xhigh"],
                    is_default=True,
                )
            ],
        )
        errors = validate_requested_routes(
            caps,
            {"writer": ("grok-4.6", "high")},
        )
        self.assertEqual(errors, [])

        invalid_errors = validate_requested_routes(
            caps,
            {"writer": ("non-existent-model", "high")},
        )
        self.assertEqual(len(invalid_errors), 1)
        self.assertIn("non-existent-model", invalid_errors[0])

    def test_exec_argv_preserves_toml_value_as_one_argument(self):
        argv = build_exec_argv(
            ["codex", "exec"], "gpt-5.6-terra", "medium"
        )
        self.assertEqual(
            argv[-4:],
            [
                "--model",
                "gpt-5.6-terra",
                "--config",
                'model_reasoning_effort="medium"',
            ],
        )

    def test_ultra_is_rejected_for_budgeted_pipeline(self):
        with self.assertRaises(ProviderError):
            build_exec_argv(["codex", "exec"], "gpt-5.6-sol", "ultra")

    def test_agy_version_parser(self):
        cmd = f"{sys.executable} -c \"print('1.2.3: release notes')\""
        ver = agy_version(cmd)
        self.assertEqual(ver, "agy 1.2.3")

    def test_opencode_version_parser(self):
        cmd = f"{sys.executable} -c \"print('2.0.0')\""
        ver = opencode_version(cmd)
        self.assertEqual(ver, "opencode 2.0.0")

    def test_grok_version_parser(self):
        cmd = f"{sys.executable} -c \"print('grok 1.0.5')\""
        ver = grok_version(cmd)
        self.assertEqual(ver, "grok 1.0.5")

    def test_discover_capabilities_dispatch_error(self):
        with self.assertRaises(ProviderError):
            discover_capabilities("invalid_provider")


if __name__ == "__main__":
    unittest.main()
