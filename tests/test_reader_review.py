import unittest

from scripts.prequel.reader_review import (
    build_blind_reader_packet,
    validate_blind_reader_review,
)


class BlindReaderReviewTests(unittest.TestCase):
    def setUp(self):
        self.draft = "张洞要上船。\n舱门没有开。\n纸从门缝里出来。"
        self.report = {
            "chapter_number": 1,
            "verdict": "PASS",
            "reader_recap": {
                "current_goal": "张洞要上船。",
                "character_positions": "张洞在船边。",
                "spatial_map": "舱门在船内。",
                "causal_chain": "门未开，纸仍从门缝出来。",
                "next_question": "纸从哪里来？",
            },
            "blocking_issues": [],
            "warnings": [],
            "evidence": [
                {"quote": "张洞要上船。", "finding": "目标明确。"},
                {"quote": "舱门没有开。", "finding": "门的状态明确。"},
                {"quote": "纸从门缝里出来。", "finding": "异常结果明确。"},
            ],
            "revision_instructions": [],
        }

    def test_blind_packet_excludes_hidden_outline_and_current_state(self):
        state = {
            "chapter_summaries": {
                "summaries": {
                    "1": {"title": "旧章", "core": "已发生的事"},
                    "2": {"title": "当前", "core": "不能提前给读者"},
                }
            },
            "world_lore": {"hidden": "幕后规则"},
            "protagonist": {"known_info": ["作者知道的事"]},
        }
        packet = build_blind_reader_packet(state, 2, self.draft)
        self.assertEqual(packet["prior_reader_facts"], [{"chapter": 1, "title": "旧章", "reader_visible_summary": "已发生的事"}])
        self.assertNotIn("world_lore", packet)
        self.assertNotIn("protagonist", packet)

    def test_valid_report_requires_reader_visible_evidence(self):
        self.assertEqual(validate_blind_reader_review(self.report, self.draft, 1), [])

    def test_report_rejects_quote_not_present_in_draft(self):
        self.report["evidence"][0]["quote"] = "作者的解释"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_FALSE_EVIDENCE", codes)


if __name__ == "__main__":
    unittest.main()
