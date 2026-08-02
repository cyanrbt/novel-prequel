import json
import unittest
from pathlib import Path

from scripts.prequel.context_builder import (
    CANDIDATE_FOCUSES,
    build_chapter_context_pack,
    build_integrated_review_packet,
    build_planner_context,
    build_verification_packet,
    build_writer_packet,
    select_candidate_focuses,
)


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

    def test_three_focus_library_is_retained_but_chapter_selects_two(self):
        self.assertEqual(
            {item["name"] for item in CANDIDATE_FOCUSES},
            {"causal_tension", "character_pressure", "atmospheric_precision"},
        )
        plan = {
            "chapter_number": 3,
            "chapter_purpose": "调查异常规则和门后的证据",
            "scenes": [],
            "rule_hypotheses": ["规则需要试错"],
        }
        selected = select_candidate_focuses(plan, 3)
        self.assertEqual(len(selected), 2)
        self.assertIn("causal_tension", {item["name"] for item in selected})

    def test_ambiguous_plan_rotates_focus_pair_by_chapter(self):
        plan = {"chapter_purpose": "推进", "scenes": []}
        pairs = [
            tuple(item["name"] for item in select_candidate_focuses(plan, number))
            for number in (1, 2, 3)
        ]
        self.assertEqual(len(set(pairs)), 3)

    def test_integrated_reviewer_gets_full_draft(self):
        plan = {"chapter_number": 1}
        context = {
            "canon_facts": [{"id": "CANON-RULE-001"}],
            "era_bans": {},
        }
        packet = build_integrated_review_packet(
            self.state, plan, "完整正文", {"passed": True}, context
        )
        self.assertEqual(packet["draft"], "完整正文")
        self.assertNotIn("all_chapters", json.dumps(packet, ensure_ascii=False))

    def test_context_pack_keeps_core_facts(self):
        context = build_planner_context(Path.cwd(), self.state)
        packet = build_chapter_context_pack(self.state, context, ["近期正文"])
        self.assertIn("era_bans", packet["core"])
        self.assertIn("active_characters", packet["core"])
        self.assertIn("metrics", packet)

    def test_verifier_receives_diff_and_targeted_issues(self):
        packet = build_verification_packet(
            self.state,
            {"chapter_number": 1},
            {"canon_facts": [], "era_bans": {}},
            "旧稿",
            "新稿",
            [{"code": "X"}],
        )
        self.assertIn("diff", packet)
        self.assertEqual(packet["target_issues"], [{"code": "X"}])
        self.assertNotIn("all_reviews", packet)


if __name__ == "__main__":
    unittest.main()
