import unittest

from scripts.prequel.cli_capabilities import (
    build_exec_argv,
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


if __name__ == "__main__":
    unittest.main()
