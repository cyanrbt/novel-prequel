import unittest

from scripts.orchestrator import format_progress_event


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


if __name__ == "__main__":
    unittest.main()
