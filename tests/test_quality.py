import json
import unittest
from pathlib import Path

from scripts.prequel.quality import scan_draft, validate_plan, validate_review


def _valid_plan():
    return {
        "chapter_number": 1,
        "title": "门上的灰",
        "event_id": "event_1",
        "phase": "征兆",
        "chapter_purpose": "建立第一次异常",
        "scenes": [{"irreversible_change": "纸灰进入门内"}],
        "new_information": ["纸灰会移动"],
        "state_changes": {
            "protagonist_known_info_add": ["纸灰会移动"],
            "protagonist_inventory_add": [],
            "protagonist_inventory_remove": [],
            "protagonist_location": None,
            "protagonist_body_updates": [],
            "ability_updates": [],
            "timeline_year": 1908,
            "timeline_elapsed_days": 1,
            "character_updates": [],
            "world_confirmed_add": [],
            "world_hypotheses_add": ["纸灰会移动"],
        },
        "rule_hypotheses": ["纸灰会移动"],
        "canon_evidence_ids": ["CANON-RULE-001"],
        "foreshadow_operations": {"plant": [], "recover": []},
        "hook": {"type": "安全区崩坏", "content": "灰在门内"},
        "prohibited_elements": [],
    }


def _valid_review():
    return {
        "chapter_number": 1,
        "verdict": "PASS",
        "grade": "A",
        "p1_failures": [],
        "p2_warnings": [],
        "evidence": [
            {"quote": "第一处", "finding": "发现一"},
            {"quote": "第二处", "finding": "发现二"},
            {"quote": "第三处", "finding": "发现三"},
        ],
        "character_assessment": "一致",
        "canon_assessment": "一致",
        "style_assessment": "一致",
        "revision_instructions": [],
    }


class QualityGateTests(unittest.TestCase):
    def test_plan_rejects_unregistered_canon_evidence(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        plan["canon_evidence_ids"] = ["MADE-UP-FACT"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("UNKNOWN_CANON_EVIDENCE", {item.code for item in issues})

    def test_review_evidence_must_exist_in_draft(self):
        review = _valid_review()
        review["evidence"][0]["quote"] = "正文里不存在的句子"
        issues = validate_review(
            review,
            {"passed": True},
            expected_chapter=1,
            draft="第1章\n这里只存在另一句话。",
        )
        self.assertIn("REVIEW_FALSE_EVIDENCE", {item.code for item in issues})

    def test_pass_review_cannot_have_low_grade_or_revision_request(self):
        review = _valid_review()
        review["grade"] = "C"
        review["revision_instructions"] = ["仍需修改"]
        issues = validate_review(review, {"passed": True})
        codes = {item.code for item in issues}
        self.assertIn("REVIEW_PASS_LOW_GRADE", codes)
        self.assertIn("REVIEW_PASS_WITH_REVISIONS", codes)

    def test_blocks_modern_canon_leak(self):
        text = Path("tests/fixtures/chapter_with_zhou_zheng.txt").read_text(encoding="utf-8")
        result = scan_draft(
            text,
            [],
            {"characters": ["周正"], "terms": ["负责人", "总部"]},
            {"chapter_number": 14, "prohibited_elements": ["周正", "负责人"]},
        )
        self.assertFalse(result["passed"])
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("ERA_BANNED_CHARACTER", codes)
        self.assertIn("ERA_BANNED_TERM", codes)

    def test_blocks_repeated_template_paragraph(self):
        text = Path("tests/fixtures/chapter_with_repetition.txt").read_text(encoding="utf-8")
        result = scan_draft(
            text,
            [text],
            {"characters": [], "terms": []},
            {"chapter_number": 2, "prohibited_elements": []},
        )
        self.assertFalse(result["passed"])
        self.assertIn("EXACT_PARAGRAPH_REUSE", {item["code"] for item in result["issues"]})

    def test_accepts_clean_fixture_without_p1(self):
        text = Path("tests/fixtures/clean_chapter.txt").read_text(encoding="utf-8")
        result = scan_draft(
            text,
            [],
            {"characters": [], "terms": []},
            {"chapter_number": 1, "prohibited_elements": []},
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_plan_must_match_next_chapter_and_change_state(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        issues = validate_plan({"chapter_number": 2, "event_id": "event_1"}, state)
        codes = {issue.code for issue in issues}
        self.assertIn("PLAN_CHAPTER_MISMATCH", codes)
        self.assertIn("NO_STATE_CHANGE", codes)

    def test_pass_review_cannot_override_failed_static_gate(self):
        review = {
            "chapter_number": 1,
            "verdict": "PASS",
            "grade": "A",
            "p1_failures": [],
            "p2_warnings": [],
            "evidence": [
                {"quote": "甲", "finding": "甲"},
                {"quote": "乙", "finding": "乙"},
                {"quote": "丙", "finding": "丙"},
            ],
            "character_assessment": "一致",
            "canon_assessment": "一致",
            "style_assessment": "一致",
            "revision_instructions": [],
        }
        issues = validate_review(review, {"passed": False})
        self.assertIn("REVIEW_CONTRADICTS_STATIC", {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()
