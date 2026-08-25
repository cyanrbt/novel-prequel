import unittest

from scripts.orchestrator import build_parser


class OrchestratorTests(unittest.TestCase):
    def test_parser_exposes_only_deterministic_commands(self):
        choices = next(
            action.choices
            for action in build_parser()._actions
            if getattr(action, "choices", None)
        )
        for retired in (
            "next",
            "models",
            "discover",
            "manual-review",
            "audit",
            "reader-review",
            "demo-review",
        ):
            self.assertNotIn(retired, choices)
        for command in (
            "status",
            "preflight",
            "workflow-check",
            "scene-experiment",
            "manual-import",
            "lint",
            "review",
            "accept",
            "merge",
            "recover",
        ):
            self.assertIn(command, choices)


if __name__ == "__main__":
    unittest.main()
