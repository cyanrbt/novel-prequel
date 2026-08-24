import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.orchestrator import command_reader_review, format_progress_event


class OrchestratorFormattingTests(unittest.TestCase):
    def test_progress_formatter_never_includes_prompt_or_output(self):
        event = {
            "kind": "CALL_STARTED",
            "call_id": "call_004",
            "stage": "integrated_reviewer",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "prompt": "secret prompt",
            "output": "secret output",
        }
        line = format_progress_event(event)
        self.assertIn("call_004", line)
        self.assertIn("gpt-5.6-terra/medium", line)
        self.assertNotIn("secret", line)

    def test_invalid_artifact_formatter_includes_diagnostic_path(self):
        line = format_progress_event(
            {
                "kind": "ARTIFACT_INVALID",
                "stage": "triage_candidate_01",
                "failure_kind": "EVIDENCE_VALIDATION",
                "diagnostic_artifact": (
                    "candidates/candidate_01/diagnostics/"
                    "integrated_review.invalid.txt"
                ),
            }
        )
        self.assertIn("审查无效", line)
        self.assertIn("integrated_review.invalid.txt", line)

    def test_completed_formatter_distinguishes_model_from_artifact_validation(self):
        line = format_progress_event(
            {
                "kind": "CALL_COMPLETED",
                "call_id": "call_005",
                "stage": "integrated_reviewer",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "medium",
                "duration_ms": 84200,
            }
        )
        self.assertIn("模型调用完成", line)
        self.assertIn("正在校验工件", line)
        self.assertIn("84.2秒", line)

    def test_reader_review_builds_packet_with_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter_path = root / "chapter_002.txt"
            chapter_path.write_text("第2章\n\n正文。", encoding="utf-8")

            class Provider:
                def generate(self, prompt, output_schema=None):
                    return "{}"

            class Router:
                def provider_for(self, stage):
                    self.assertEqual(stage, "blind_reader_reviewer")
                    return Provider()

                def assertEqual(self, left, right):
                    if left != right:
                        raise AssertionError(f"{left!r} != {right!r}")

            with (
                patch("scripts.orchestrator.PROJECT_ROOT", root),
                patch("scripts.orchestrator.STATE_FILE", root / "current.json"),
                patch("scripts.orchestrator.load_state", return_value={}),
                patch("scripts.orchestrator.formal_chapter_paths", return_value=[chapter_path]),
                patch("scripts.orchestrator.load_config", return_value={}),
                patch("scripts.orchestrator.load_execution_config", return_value={}),
                patch("scripts.orchestrator.StageModelRouter.from_config", return_value=Router()),
                patch("scripts.orchestrator.build_blind_reader_packet", return_value={}) as build_packet,
                patch("scripts.orchestrator.build_blind_reader_prompt", return_value="prompt"),
                patch("scripts.orchestrator.parse_json_artifact", return_value={"verdict": "PASS"}),
                patch("scripts.orchestrator.validate_blind_reader_review", return_value=[]),
                patch("scripts.orchestrator.atomic_save_text"),
                patch("scripts.orchestrator.atomic_save_json"),
            ):
                result = command_reader_review(SimpleNamespace(chapter=2))

            self.assertEqual(result, 0)
            build_packet.assert_called_once_with(
                {}, 2, "第2章\n\n正文。", root
            )


if __name__ == "__main__":
    unittest.main()
