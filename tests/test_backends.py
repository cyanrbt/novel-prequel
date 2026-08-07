import json
import unittest
from pathlib import Path

from scripts.prequel.backends import (
    AntigravityBackend,
    CodexBackend,
    OpenCodeBackend,
    backend_from_spec,
    backend_model_argv,
    build_invocation,
    repair_json_text,
)
from scripts.prequel.errors import ProviderError
from scripts.prequel.provider import provider_from_spec


class JsonRepairTests(unittest.TestCase):
    def test_strips_trailing_commas(self):
        self.assertEqual(repair_json_text('{"a": 1,}'), '{"a": 1}')
        self.assertEqual(repair_json_text('{"a": [1, 2,],}'), '{"a": [1, 2]}')

    def test_preserves_valid_json(self):
        self.assertEqual(repair_json_text('{"a": 1, "b": [1, 2]}'), '{"a": 1, "b": [1, 2]}')


class BackendResolutionTests(unittest.TestCase):
    def test_spec_resolves_each_backend(self):
        for backend_type in ("codex_cli", "opencode_cli", "antigravity_cli"):
            backend = backend_from_spec({"type": backend_type})
            self.assertIn(backend.type, {backend_type})

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ProviderError):
            backend_from_spec({"type": "unknown_cli"})


class OpenCodeInvocationTests(unittest.TestCase):
    def _command(self):
        return backend_model_argv(
            ["opencode", "run", "--format", "json"],
            OpenCodeBackend(),
            "deepseek/deepseek-v4-flash",
            "medium",
        )

    def test_stdin_delivery_with_model_and_variant(self):
        argv, stdin = build_invocation(
            OpenCodeBackend(),
            self._command(),
            Path("/proj"),
            None,
            "提示词",
        )
        self.assertEqual(
            argv,
            [
                "opencode",
                "run",
                "--format",
                "json",
                "--model",
                "deepseek/deepseek-v4-flash",
                "--variant",
                "medium",
                "--dir",
                "/proj",
                "-",
            ],
        )
        self.assertEqual(stdin, "提示词")

    def test_model_argv_appears_once_in_final_invocation(self):
        argv, _ = build_invocation(
            OpenCodeBackend(), self._command(), None, None, "提示词"
        )
        self.assertEqual(argv.count("--model"), 1)
        self.assertEqual(argv.count("--variant"), 1)

    def test_schema_is_embedded_in_prompt(self):
        schema = Path("schemas/plan.schema.json")
        argv, stdin = build_invocation(
            OpenCodeBackend(),
            self._command(),
            None,
            schema,
            "正文",
        )
        self.assertNotIn("--json-schema", argv)
        self.assertIn("输出契约", stdin)
        self.assertIn("正文", stdin)
        self.assertIn(schema.read_text(encoding="utf-8")[:60], stdin)

    def test_model_requires_provider_slash(self):
        with self.assertRaises(ProviderError):
            backend_model_argv(
                ["opencode", "run"], OpenCodeBackend(), "no-slash-model", "medium"
            )

    def test_parse_ndjson_text_events(self):
        stream = "\n".join(
            [
                json.dumps({"type": "step_start"}),
                json.dumps({"type": "text", "part": {"text": "第一段"}}),
                json.dumps({"type": "text", "part": {"text": "第二段"}}),
                json.dumps({"type": "step_finish"}),
            ]
        )
        self.assertEqual(OpenCodeBackend().parse_output(stream), "第一段\n第二段")

    def test_parse_rejects_output_without_text(self):
        with self.assertRaises(ProviderError):
            OpenCodeBackend().parse_output('{"type":"step_start"}\n')


class AntigravityInvocationTests(unittest.TestCase):
    def _command(self):
        return backend_model_argv(
            ["agy"], AntigravityBackend(), "gemini-3.1-pro-low", "low"
        )

    def test_positional_prompt_with_print_mode(self):
        argv, stdin = build_invocation(
            AntigravityBackend(), self._command(), None, None, "提示词"
        )
        self.assertEqual(
            argv,
            ["agy", "--print", "--output-format", "text", "--model", "gemini-3.1-pro-low", "--", "提示词"],
        )
        self.assertIsNone(stdin)

    def test_native_schema_flag(self):
        schema = Path("schemas/plan.schema.json")
        argv, _ = build_invocation(
            AntigravityBackend(), self._command(), None, schema, "正文"
        )
        self.assertIn("--json-schema", argv)
        self.assertIn("正文", argv)

    def test_rejects_no_output_error_message(self):
        with self.assertRaises(ProviderError):
            AntigravityBackend().parse_output(
                'jetski: no output produced — a tool required the "command" permission'
            )


class CatalogParsingTests(unittest.TestCase):
    def test_opencode_catalog_from_plain_lines(self):
        catalog = OpenCodeBackend().parse_catalog(
            "deepseek/deepseek-chat\ndepseek/deepseek-reasoner\n"
        )
        slugs = {item["slug"] for item in catalog["models"]}
        self.assertIn("deepseek/deepseek-chat", slugs)
        self.assertTrue(all(item["supported_reasoning_levels"] == [] for item in catalog["models"]))

    def test_antigravity_catalog_from_tab_separated_lines(self):
        catalog = AntigravityBackend().parse_catalog(
            "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)\ngemini-3.6-flash-high\tGemini 3.6 Flash (High)\n"
        )
        slugs = {item["slug"] for item in catalog["models"]}
        self.assertEqual(slugs, {"gemini-3.1-pro-low", "gemini-3.6-flash-high"})

    def test_routes_with_empty_effort_sets_are_lenient(self):
        from scripts.prequel.cli_capabilities import validate_requested_routes

        catalog = {"models": [{"slug": "deepseek/deepseek-chat", "supported_reasoning_levels": []}]}
        errors = validate_requested_routes(catalog, {"planner": ("deepseek/deepseek-chat", "high")})
        self.assertEqual(errors, [])


class ProviderDispatchTests(unittest.TestCase):
    def test_opencode_provider_builds_exec_command(self):
        provider = provider_from_spec(
            {
                "type": "opencode_cli",
                "command": ["opencode", "run", "--format", "json"],
                "model": "deepseek/deepseek-v4-flash",
                "reasoning_effort": "medium",
            },
            Path.cwd(),
        )
        self.assertEqual(provider.model, "deepseek/deepseek-v4-flash")
        self.assertIn("--variant", provider.command)
        self.assertIn("deepseek/deepseek-v4-flash", provider.command)

    def test_antigravity_provider_builds_exec_command(self):
        provider = provider_from_spec(
            {
                "type": "antigravity_cli",
                "command": ["agy"],
                "model": "gemini-3.1-pro-low",
                "reasoning_effort": "low",
            },
            Path.cwd(),
        )
        self.assertEqual(provider.model, "gemini-3.1-pro-low")
        self.assertIn("--print", provider.command)
        self.assertIn("--output-format", provider.command)

    def test_codex_backend_is_unchanged_default(self):
        from scripts.prequel.cli_capabilities import build_exec_argv

        argv = build_exec_argv(["codex", "exec"], "gpt-5.6-terra", "medium")
        self.assertEqual(
            argv[-4:],
            ["--model", "gpt-5.6-terra", "--config", 'model_reasoning_effort="medium"'],
        )


if __name__ == "__main__":
    unittest.main()
