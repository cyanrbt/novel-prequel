import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prequel.errors import ArtifactValidationError, QualityGateError
from scripts.prequel.pipeline import run_preflight
from scripts.prequel.prompt_native import (
    validate_agent_result,
    validate_prompt_native_project,
    validate_task_envelope,
)


ROOT = Path(__file__).resolve().parents[1]


class PromptNativeWorkflowTests(unittest.TestCase):
    def fixture(self, name: str) -> dict:
        return json.loads(
            (ROOT / "tests/fixtures" / name).read_text(encoding="utf-8")
        )

    def test_repository_protocol_smoke_test_passes_without_agent_cli(self):
        checks = validate_prompt_native_project(ROOT)
        self.assertIn("core story config is execution-backend agnostic", checks)
        self.assertIn("Agent CLI backends and launchers are absent", checks)
        self.assertTrue(any(item.startswith("task/result binding") for item in checks))

    def test_task_envelope_is_bound_to_canonical_inputs(self):
        task = self.fixture("prompt_native_task.json")
        checks = validate_task_envelope(ROOT, task)
        self.assertTrue(any(item.startswith("input fingerprint") for item in checks))
        changed = copy.deepcopy(task)
        changed["inputs"]["sample"] = "被修改的输入"
        with self.assertRaisesRegex(ArtifactValidationError, "fingerprint"):
            validate_task_envelope(ROOT, changed)

    def test_task_envelope_rejects_schema_violations_and_path_escape(self):
        task = self.fixture("prompt_native_task.json")
        changed = copy.deepcopy(task)
        changed["capabilities"]["filesystem"] = "root"
        with self.assertRaisesRegex(ArtifactValidationError, "filesystem"):
            validate_task_envelope(ROOT, changed)

        changed = copy.deepcopy(task)
        changed["capabilities"]["extra"] = True
        with self.assertRaisesRegex(ArtifactValidationError, "未声明字段"):
            validate_task_envelope(ROOT, changed)

        changed = copy.deepcopy(task)
        changed["role_file"] = "../outside.md"
        with self.assertRaisesRegex(ArtifactValidationError, "必须位于"):
            validate_task_envelope(ROOT, changed)

    def test_result_cannot_be_reused_for_another_task(self):
        task = self.fixture("prompt_native_task.json")
        result = self.fixture("prompt_native_result.json")
        validate_agent_result(task, result, ROOT)
        changed = copy.deepcopy(result)
        changed["task_id"] = "another-task"
        with self.assertRaisesRegex(ArtifactValidationError, "task_id"):
            validate_agent_result(task, changed, ROOT)

    def test_legacy_protocol_pair_remains_readable(self):
        task = self.fixture("prompt_native_task.json")
        result = self.fixture("prompt_native_result.json")
        task["protocol"] = "prequel-task/1"
        result["protocol"] = "prequel-result/1"
        validate_agent_result(task, result, ROOT)

    def test_completed_result_must_match_declared_artifact_schema(self):
        task = self.fixture("prompt_native_task.json")
        result = self.fixture("prompt_native_result.json")
        changed = copy.deepcopy(result)
        changed["artifact"].pop("message")
        with self.assertRaisesRegex(ArtifactValidationError, "Agent artifact"):
            validate_agent_result(task, changed, ROOT)

        changed = copy.deepcopy(result)
        changed["unexpected"] = True
        with self.assertRaisesRegex(ArtifactValidationError, "未声明字段"):
            validate_agent_result(task, changed, ROOT)

    def test_core_preflight_does_not_construct_an_execution_backend(self):
        with (
            patch(
                "scripts.prequel.pipeline.formal_review_binding_status",
                return_value={"status": "VALID"},
            ),
            patch(
                "scripts.prequel.pipeline.load_voice_profile_status",
                return_value="READY",
            ),
        ):
            checks = run_preflight(ROOT)
        self.assertIn("agent-agnostic story config loaded", checks)

    def test_preflight_blocks_next_chapter_while_voice_is_calibrating(self):
        with (
            patch(
                "scripts.prequel.pipeline.formal_review_binding_status",
                return_value={"status": "VALID"},
            ),
            self.assertRaisesRegex(QualityGateError, "文风画像仍在校准"),
        ):
            run_preflight(ROOT)

    def test_maintenance_preflight_allows_calibrating_voice_profile(self):
        with patch(
            "scripts.prequel.pipeline.formal_review_binding_status",
            return_value={"status": "VALID"},
        ):
            checks = run_preflight(
                ROOT,
                require_voice_ready=False,
            )
        self.assertIn("positive voice profile status validated: CALIBRATING", checks)


if __name__ == "__main__":
    unittest.main()
