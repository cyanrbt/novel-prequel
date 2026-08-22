import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError, ProviderError
from scripts.prequel.provider import (
    AgyCliProvider,
    CodexCliProvider,
    GrokCliProvider,
    OpenCodeCliProvider,
    clean_schema_for_cli,
    provider_from_spec,
    strip_markdown_fence,
)


class ProviderTests(unittest.TestCase):
    def test_provider_spec_adds_explicit_model_and_effort(self):
        provider = provider_from_spec(
            {
                "type": "codex_cli",
                "command": ["codex", "exec"],
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
            },
            Path.cwd(),
        )
        self.assertIsInstance(provider, CodexCliProvider)
        self.assertEqual(provider.model, "gpt-5.6-terra")
        self.assertEqual(provider.reasoning_effort, "medium")
        self.assertIn("--model", provider.command)
        self.assertIn('model_reasoning_effort="medium"', provider.command)

    def test_provider_spec_rejects_ultra(self):
        with self.assertRaises(ProviderError):
            provider_from_spec(
                {
                    "type": "codex_cli",
                    "command": ["codex", "exec"],
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "ultra",
                },
                Path.cwd(),
            )

    def test_provider_spec_creates_agy_provider(self):
        provider = provider_from_spec(
            {
                "type": "agy_cli",
                "model": "gemini-3.7-flash-high",
                "reasoning_effort": "high",
            },
            Path.cwd(),
        )
        self.assertIsInstance(provider, AgyCliProvider)
        self.assertEqual(provider.model, "gemini-3.7-flash-high")
        self.assertEqual(provider.reasoning_effort, "high")

    def test_provider_spec_creates_opencode_provider(self):
        provider = provider_from_spec(
            {
                "type": "opencode_cli",
                "model": "deepseek/deepseek-chat",
            },
            Path.cwd(),
        )
        self.assertIsInstance(provider, OpenCodeCliProvider)
        self.assertEqual(provider.model, "deepseek/deepseek-chat")

    def test_provider_spec_creates_grok_provider(self):
        provider = provider_from_spec(
            {
                "type": "grok_cli",
                "model": "grok-4.6",
                "reasoning_effort": "high",
            },
            Path.cwd(),
        )
        self.assertIsInstance(provider, GrokCliProvider)
        self.assertEqual(provider.model, "grok-4.6")
        self.assertEqual(provider.reasoning_effort, "high")

    def test_clean_schema_removes_id_and_schema(self):
        raw = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": "test.schema.json",
            "type": "object",
            "properties": {
                "sub": {
                    "$id": "sub.json",
                    "type": "string",
                }
            },
        }
        cleaned = clean_schema_for_cli(raw)
        self.assertNotIn("$schema", cleaned)
        self.assertNotIn("$id", cleaned)
        self.assertNotIn("$id", cleaned["properties"]["sub"])
        self.assertEqual(cleaned["type"], "object")

    def test_strip_markdown_fence(self):
        self.assertEqual(strip_markdown_fence("```json\n{\"a\": 1}\n```"), "{\"a\": 1}")
        self.assertEqual(strip_markdown_fence("```\nhello\n```"), "hello")
        self.assertEqual(strip_markdown_fence("normal text"), "normal text")

    def test_output_schemas_use_openai_supported_object_contract(self):
        unsupported = {"minProperties", "maxProperties", "minLength", "maxLength"}

        def inspect(node, path="root"):
            if isinstance(node, dict):
                self.assertFalse(unsupported & node.keys(), f"unsupported keyword at {path}")
                if node.get("type") == "object":
                    self.assertEqual(node.get("additionalProperties"), False, path)
                    self.assertEqual(set(node.get("required", [])), set(node.get("properties", {})), path)
                for key, value in node.items():
                    inspect(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    inspect(value, f"{path}[{index}]")

        for name in (
            "plan",
            "review",
            "integrated_review",
            "specialist_review",
            "revision_verification",
            "ballot",
            "audit",
            "reader_review",
        ):
            schema = json.loads(Path(f"schemas/{name}.schema.json").read_text(encoding="utf-8"))
            inspect(schema, name)

    def provider_for(self, source: str, timeout: int = 2) -> CodexCliProvider:
        return CodexCliProvider([sys.executable, "-c", source], timeout)

    def test_returns_model_stdout(self):
        provider = self.provider_for("print('有效输出')")
        self.assertEqual(provider.generate("测试"), "有效输出")

    def test_rejects_empty_output(self):
        with self.assertRaises(ProviderError):
            self.provider_for("print('')").generate("测试")

    def test_rejects_placeholder_output(self):
        with self.assertRaises(ProviderError):
            self.provider_for("print('[PLACEHOLDER]')").generate("测试")

    def test_timeout_is_a_failure(self):
        source = "import time; time.sleep(2); print('late')"
        with self.assertRaises(ProviderError):
            self.provider_for(source, timeout=1).generate("测试")

    def test_agy_provider_parses_json_output(self):
        source = "import json; print(json.dumps({'status': 'SUCCESS', 'response': 'agy response'}))"
        provider = AgyCliProvider([sys.executable, "-c", source], timeout_seconds=2)
        self.assertEqual(provider.generate("prompt"), "agy response")

    def test_opencode_provider_parses_stream_json(self):
        source = "import json; print(json.dumps({'type': 'text', 'part': {'text': 'opencode output'}}))"
        provider = OpenCodeCliProvider([sys.executable, "-c", source], timeout_seconds=2)
        self.assertEqual(provider.generate("prompt"), "opencode output")

    def test_grok_provider_parses_plain_and_json(self):
        source = "print('grok plain output')"
        provider = GrokCliProvider([sys.executable, "-c", source], timeout_seconds=2)
        self.assertEqual(provider.generate("prompt"), "grok plain output")

    def test_workspace_rejects_unknown_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 1)
            with self.assertRaises(ArtifactValidationError):
                workspace.write_text("formal_chapter.txt", "不能直接保存")


if __name__ == "__main__":
    unittest.main()
