import json
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError, ProviderError
from scripts.prequel.provider import CodexCliProvider


class ProviderTests(unittest.TestCase):
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

        for name in ("plan", "review"):
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

    def test_workspace_rejects_unknown_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 1)
            with self.assertRaises(ArtifactValidationError):
                workspace.write_text("formal_chapter.txt", "不能直接保存")


if __name__ == "__main__":
    unittest.main()
