import hashlib
import unittest
from pathlib import Path

from scripts.prequel.reader_review import (
    build_blind_reader_packet,
    validate_blind_reader_review,
)


class BlindReaderReviewTests(unittest.TestCase):
    def setUp(self):
        self.draft = "张洞要上船。\n舱门没有开。\n纸从门缝里出来。"
        self.report = {
            "chapter_number": 1,
            "draft_sha256": hashlib.sha256(self.draft.encode("utf-8")).hexdigest(),
            "verdict": "PASS",
            "reader_recap": {
                "current_goal": "张洞要上船。",
                "character_positions": "张洞在船边。",
                "spatial_map": "舱门在船内。",
                "causal_chain": "门未开，纸仍从门缝出来。",
                "next_question": "纸从哪里来？",
            },
            "adversarial_checks": {
                "ordinary_explanations": [],
                "missing_preconditions": [],
                "knowledge_or_behavior_gaps": [],
                "physical_or_spatial_gaps": [],
                "unsupported_recap_claims": [],
            },
            "reading_experience": {
                "prose_accessibility": 5,
                "character_believability": 4,
                "target_emotion_effect": 4,
                "narrative_momentum": 4,
                "opening_pull": 4,
                "protagonist_ownership": 4,
                "question_progression": 4,
                "ending_compulsion": 4,
                "competitive_readiness": "MATCH",
                "next_click_reason": "张洞必须确认纸灰如何越过关闭的门。",
                "continue_reading": True,
                "first_drop_point": None,
                "friction_reasons": [],
                "friction_severity": "NONE",
            },
            "benchmark_comparison": {
                "character_attachment": {
                    "score": 4, "quote": "张洞要上船。", "assessment": "具体愿望已受威胁。",
                },
                "active_threat": {
                    "score": 4, "quote": "纸从门缝里出来。", "assessment": "异常正在越过边界。",
                },
                "protagonist_specificity": {
                    "score": 4, "quote": "张洞要上船。", "assessment": "行动连接个人去向。",
                },
                "revelation_transformation": {
                    "score": 4, "quote": "舱门没有开。", "assessment": "关闭的门失去安全意义。",
                },
                "emotional_aftereffect": {
                    "score": 4, "quote": "张洞要上船。", "assessment": "读者担心愿望被异常夺走。",
                },
                "evidence_payoff_mode": "MIXED",
                "would_choose_over_competent_peer": True,
                "major_gaps": [],
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
        packet = build_blind_reader_packet(state, 2, self.draft, Path.cwd())
        self.assertEqual(packet["prior_reader_facts"], [{"chapter": 1, "title": "旧章", "reader_visible_summary": "已发生的事"}])
        self.assertNotIn("world_lore", packet)
        self.assertNotIn("protagonist", packet)
        self.assertEqual(packet["draft_sha256"], self.report["draft_sha256"])
        self.assertIn("五项硬校准", packet["benchmark_calibration"])

    def test_valid_report_requires_reader_visible_evidence(self):
        self.assertEqual(validate_blind_reader_review(self.report, self.draft, 1), [])

    def test_report_rejects_quote_not_present_in_draft(self):
        self.report["evidence"][0]["quote"] = "作者的解释"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_FALSE_EVIDENCE", codes)

    def test_report_rejects_stale_draft_hash(self):
        self.report["draft_sha256"] = "0" * 64
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_DRAFT_MISMATCH", codes)

    def test_pass_allows_explicitly_preserved_ordinary_explanation(self):
        self.report["adversarial_checks"]["ordinary_explanations"] = ["纸也可能由门内的人塞出。"]
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertNotIn("READER_PASS_WITH_GAPS", codes)

    def test_pass_rejects_nonempty_spatial_gap(self):
        self.report["adversarial_checks"]["physical_or_spatial_gaps"] = [
            "没有交代人物如何越过舱门。"
        ]
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("READER_PASS_WITH_GAPS", codes)

    def test_pass_requires_actual_continuation_pull(self):
        self.report["reading_experience"]["narrative_momentum"] = 2
        self.report["reading_experience"]["continue_reading"] = False
        self.report["reading_experience"]["first_drop_point"] = {
            "quote": "舱门没有开。",
            "explanation": "场面没有继续变化。",
        }
        self.report["reading_experience"]["friction_reasons"] = ["推进停滞"]
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_PASS_WITHOUT_PULL", codes)

    def test_pass_rejects_merely_competent_chapter(self):
        self.report["reading_experience"]["opening_pull"] = 3
        self.report["reading_experience"]["competitive_readiness"] = "BELOW"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_PASS_WITHOUT_PULL", codes)

    def test_pass_requires_match_not_near(self):
        self.report["reading_experience"]["competitive_readiness"] = "NEAR"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_PASS_WITHOUT_PULL", codes)

    def test_pass_rejects_evidence_only_payoff(self):
        self.report["benchmark_comparison"]["evidence_payoff_mode"] = "EVIDENCE_ONLY"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_PASS_BELOW_BENCHMARK", codes)

    def test_pass_rejects_low_attachment_despite_high_legacy_scores(self):
        self.report["benchmark_comparison"]["character_attachment"]["score"] = 3
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_PASS_BELOW_BENCHMARK", codes)

    def test_pass_allows_minor_friction_when_benchmark_is_met(self):
        self.report["reading_experience"]["friction_reasons"] = ["一处空间说明稍密"]
        self.report["reading_experience"]["friction_severity"] = "MINOR"
        self.assertEqual(validate_blind_reader_review(self.report, self.draft, 1), [])

    def test_drop_point_must_quote_current_draft(self):
        self.report["verdict"] = "REVISE"
        self.report["blocking_issues"] = [{
            "code": "DROP", "quote": "舱门没有开。",
            "reader_question": "接下来发生什么？", "explanation": "推进停滞。",
        }]
        self.report["revision_instructions"] = ["让场面产生新的选择。"]
        self.report["reading_experience"]["first_drop_point"] = {
            "quote": "正文中不存在的句子", "explanation": "失去兴趣",
        }
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_FALSE_DROP_POINT", codes)


if __name__ == "__main__":
    unittest.main()
