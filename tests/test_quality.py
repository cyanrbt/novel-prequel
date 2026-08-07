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
        "scenes": [{
            "location": "张家院",
            "characters": ["张洞"],
            "goal": "检查门板",
            "conflict": "纸灰进入门内",
            "function": "升级",
            "initial_state": "门从内上栓，张洞站在屋内。",
            "discovery_path": "张洞开灯后看见门板内侧的纸灰。",
            "knowledge_limits": "张洞只知道门栓未动，不知道纸灰何时出现。",
            "ordinary_explanations": {
                "considered": ["灶灰被风吹入"],
                "excluded": [],
                "remaining": ["纸灰早已留在门缝"]
            },
            "choice_reason": "张洞要先确认门况再决定是否叫醒家人。",
            "end_state": "门仍上栓，纸灰留在门内，张洞没有开门。",
            "pressure_change": "张洞不再相信院门安全",
            "irreversible_change": "纸灰进入门内"
        }],
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
        "milestone_operations": {"complete": []},
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

    def test_plan_rejects_unregistered_design_identifiers(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        plan["foreshadow_operations"]["plant"] = ["F-A99"]
        plan["milestone_operations"]["complete"] = ["M9-NOT-REAL"]
        issues = validate_plan(
            plan,
            state,
            {"CANON-RULE-001"},
            {"F-A01"},
            {"M1-TEST"},
        )
        self.assertEqual(
            {"UNKNOWN_FORESHADOW", "UNKNOWN_MILESTONE"},
            {item.code for item in issues if item.code.startswith("UNKNOWN_")} - {"UNKNOWN_CANON_EVIDENCE"},
        )

    def test_plan_requires_reconstructable_scene_model(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        del plan["scenes"][0]["initial_state"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("SCENE_MODEL_MISSING", {item.code for item in issues})

    def test_plan_rejects_malformed_ordinary_explanations(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        plan["scenes"][0]["ordinary_explanations"] = {"considered": "灶灰"}
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("SCENE_BAD_ALTERNATIVES", {item.code for item in issues})

    def test_foreshadow_must_be_planted_in_an_earlier_chapter(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        plan["foreshadow_operations"]["recover"] = ["F-A01"]
        issues = validate_plan(
            plan, state, {"CANON-RULE-001"}, {"F-A01"}, set(),
            {"entries": {"F-A01": {}}}, {"milestones": {}},
        )
        self.assertIn("FORESHADOW_NOT_PLANTED", {item.code for item in issues})

    def test_milestone_requires_prior_milestone_and_current_volume(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = _valid_plan()
        plan["milestone_operations"]["complete"] = ["M2-ACTIVE-PRICE"]
        registry = {
            "milestones": {
                "M2-ACTIVE-PRICE": {"volume": 2, "after": ["M1-CITY-EXIT"]}
            }
        }
        issues = validate_plan(
            plan, state, {"CANON-RULE-001"}, set(), {"M2-ACTIVE-PRICE"},
            {"entries": {}}, registry,
        )
        codes = {item.code for item in issues}
        self.assertIn("MILESTONE_PREREQUISITE_MISSING", codes)
        self.assertIn("MILESTONE_WRONG_VOLUME", codes)

    def test_due_foreshadow_blocks_exit_milestone(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        state["active_foreshadows"] = {
            "F-A01": {"status": "已播种", "plant_chapter": 1}
        }
        state["chapter"]["next_chapter"] = 2
        plan = _valid_plan()
        plan["chapter_number"] = 2
        plan["milestone_operations"]["complete"] = ["M1-CITY-EXIT"]
        issues = validate_plan(
            plan, state, {"CANON-RULE-001"}, {"F-A01"}, {"M1-CITY-EXIT"},
            {"entries": {"F-A01": {"recover_by": "M1-CITY-EXIT"}}},
            {"milestones": {"M1-CITY-EXIT": {"volume": 1}}},
        )
        self.assertIn("FORESHADOW_RECOVERY_OVERDUE", {item.code for item in issues})

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

    def test_custom_safe_min_blocks_short_draft(self):
        result = scan_draft(
            "第1章：短章\n\n" + "甲" * 120,
            [],
            {"characters": [], "terms": []},
            {"chapter_number": 1, "prohibited_elements": []},
            length_policy={"safe_min": 2500, "target_min": 3200, "target_max": 5000, "safe_max": 8000},
        )
        self.assertFalse(result["passed"])
        self.assertIn("WORD_COUNT_HARD_FAIL", {item["code"] for item in result["issues"]})

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
