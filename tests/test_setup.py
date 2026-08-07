import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.pipeline import (
    LOCAL_CONFIG_PATH,
    resolve_config_path,
    config_selection_origin,
)
from scripts.prequel.setup import (
    antigravity_effort_from_slug,
    apply_model_config,
    available_efforts,
    model_options,
    write_local_config,
)


def _template_fixture(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    template = {
        "provider": {
            "type": "codex_cli",
            "command": ["codex", "exec"],
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
        },
        "model_profiles": {
            "default": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
            "terra_high": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
        },
        "stage_routes": {"planner": "default", "continuity_reviewer": "terra_high"},
    }
    (root / "config/prequel_config.json").write_text(
        json.dumps(template, ensure_ascii=False), encoding="utf-8"
    )


class ConfigResolutionTests(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.get("PREQUEL_CONFIG")
        if "PREQUEL_CONFIG" in os.environ:
            del os.environ["PREQUEL_CONFIG"]

    def tearDown(self):
        if self._env is None:
            os.environ.pop("PREQUEL_CONFIG", None)
        else:
            os.environ["PREQUEL_CONFIG"] = self._env

    def test_default_falls_back_to_prequel_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _template_fixture(root)
            self.assertEqual(
                resolve_config_path(root), root / "config/prequel_config.json"
            )
            self.assertEqual(config_selection_origin(root), "default")

    def test_local_config_wins_over_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _template_fixture(root)
            local = root / LOCAL_CONFIG_PATH
            local.write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_config_path(root), local)
            self.assertEqual(config_selection_origin(root), "local")

    def test_env_overrides_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _template_fixture(root)
            (root / LOCAL_CONFIG_PATH).write_text("{}", encoding="utf-8")
            os.environ["PREQUEL_CONFIG"] = "config/prequel_config.opencode.json"
            self.assertEqual(
                resolve_config_path(root),
                root / "config/prequel_config.opencode.json",
            )
            self.assertEqual(config_selection_origin(root), "env")


class SetupLogicTests(unittest.TestCase):
    def test_apply_model_config_rewrites_all_profiles(self):
        config = {
            "provider": {"type": "codex_cli"},
            "model_profiles": {
                "default": {"model": "a", "reasoning_effort": "x"},
                "terra_high": {"model": "b", "reasoning_effort": "y"},
            },
        }
        result = apply_model_config(config, "gpt-5.6-terra", "high")
        for profile in result["model_profiles"].values():
            self.assertEqual(profile["model"], "gpt-5.6-terra")
            self.assertEqual(profile["reasoning_effort"], "high")
        self.assertEqual(result["provider"]["model"], "gpt-5.6-terra")
        self.assertEqual(result["provider"]["reasoning_effort"], "high")

    def test_model_options_drop_unapproved_codex_models(self):
        catalog = {
            "models": [
                {"slug": "gpt-5.6-terra"},
                {"slug": "gpt-5.5"},
                {"slug": "gpt-5.6-sol", "name": "Sol"},
            ]
        }
        options = model_options("codex_cli", catalog)
        slugs = [slug for slug, _ in options]
        self.assertIn("gpt-5.6-terra", slugs)
        self.assertIn("gpt-5.6-sol", slugs)
        self.assertNotIn("gpt-5.5", slugs)
        self.assertIn("Sol", options[1][1])

    def test_available_efforts_codex_from_catalog(self):
        catalog = {
            "models": [
                {
                    "slug": "gpt-5.6-terra",
                    "supported_reasoning_levels": [
                        {"effort": "low"},
                        {"effort": "medium"},
                        {"effort": "high"},
                    ],
                }
            ]
        }
        self.assertEqual(
            available_efforts("codex_cli", catalog, "gpt-5.6-terra"),
            ["low", "medium", "high"],
        )

    def test_available_efforts_opencode_is_common(self):
        self.assertEqual(
            available_efforts("opencode_cli", {"models": []}, "deepseek/deepseek-chat"),
            ["low", "medium", "high"],
        )

    def test_antigravity_effort_derived_from_slug(self):
        self.assertEqual(
            antigravity_effort_from_slug("gemini-3.1-pro-low"), "low"
        )
        self.assertEqual(
            antigravity_effort_from_slug("gemini-3.6-flash-medium"), "medium"
        )
        self.assertIsNone(antigravity_effort_from_slug("claude-sonnet-4-6"))

    def test_antigravity_effort_falls_back_to_all_levels(self):
        self.assertEqual(
            available_efforts("antigravity_cli", {"models": []}, "claude-sonnet-4-6"),
            ["high", "low", "medium"],
        )

    def test_write_local_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = write_local_config(root, {"key": "value"})
            self.assertEqual(target, root / LOCAL_CONFIG_PATH)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")), {"key": "value"}
            )


if __name__ == "__main__":
    unittest.main()
