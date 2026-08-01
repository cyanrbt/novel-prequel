import json
import unittest
from pathlib import Path

from scripts.prequel.context_builder import build_planner_context, build_writer_packet


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads(
            Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8")
        )

    def test_writer_packet_does_not_contain_raw_excerpt_paths(self):
        plan = {"chapter_number": 1, "canon_evidence_ids": ["CANON-RULE-001"]}
        context = {
            "canon_facts": [{
                "id": "CANON-RULE-001", "level": "A", "claim": "规则",
                "allowed_use": "允许", "forbidden_overclaim": "禁止",
                "evidence_files": ["raw_excerpts/secret.md"],
            }],
            "era_bans": {"characters": [], "terms": []},
        }
        packet = build_writer_packet(self.state, plan, [], context)
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("raw_excerpts", rendered)
        self.assertNotIn("evidence_files", rendered)

    def test_planner_receives_era_bans(self):
        context = build_planner_context(Path.cwd(), self.state)
        self.assertIn("周正", context["era_bans"]["characters"])
        self.assertIn("负责人", context["era_bans"]["terms"])


if __name__ == "__main__":
    unittest.main()
