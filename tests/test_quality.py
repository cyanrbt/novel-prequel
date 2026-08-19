import json
import unittest
from pathlib import Path

from scripts.prequel.quality import scan_draft, validate_plan, validate_review


def _valid_plan():
    return {
        "chapter_number": 1,
        "title": "两种冷意",
        "event_id": "event_1",
        "phase": "双暴露",
        "chapter_purpose": "让身份重演与地下路径在同一场现实危机中出现",
        "serial_continuity": {
            "prior_human_wound": "首章没有上一章人物伤口。",
            "opening_consequence": "张洞刚凭手艺挣到第一笔工钱。",
            "carried_object_state": "工钱已经登记在张洞的随身资源中。",
            "pressure_novelty": "首章同时建立边界身份与地下路径两种压力。",
        },
        "reader_investment": {
            "attachment_anchor": {
                "focus": "张洞靠手艺取得的第一份报酬和父亲对他的认可。",
                "on_page_moment": "雇主验过修好的货箱，当众把工钱交给张洞。",
                "private_meaning": "这份报酬证明他能凭自己本事进入更大的城市。",
                "lived_value": "父子可以靠同一门手艺合作，而不必接受家族替他们安排去留。",
                "threatened_loss": "异常若逼停货栈，刚建立的合作会立即中断。",
                "loss_carrier": "工钱、雇主认可和后续工作介绍共同承载这条普通生活。",
            },
            "protagonist_contradiction": "他想证明能独立作主，却会在危险时替父亲决定风险。",
            "threat_in_motion": "门外来客沿重复身份逼近边界，地下刮擦同时使旧木变冷。",
            "core_threat_continuation": {
                "prior_hook": "首章无前置钩子，当场建立两类现象为何同时出现的问题。",
                "current_effect": "来客要求熟人放行，地下冷意迫使货栈工人移动旧木。",
                "local_answer": "关门只影响来客，搬动旧木却会让地下刮擦改变方向。",
                "old_defense": "关门后把可疑旧木搬出工作区。",
                "defense_failure": "门外来客停下时，旧木下的刮擦反而逼近搬运者。",
                "replacement_rule": "先分开有权放行的人和接触旧木的人，再组织撤出。",
                "forced_change": "张洞必须放弃独自控制现场，让父亲选择自己的撤离位置。",
                "human_pressure_link": "雇主想保货物，父亲想保工人，两类异常迫使他们公开冲突。",
            },
            "revelation_shift": {
                "from": "门外来客是不是地下异常的另一种样子？",
                "on_page_answer": "来客停下时，地下刮擦仍沿旧木继续移动。",
                "to": "两类作用同时发生时，活人该分别避开什么？",
                "changes": "ACTION_RULE",
                "old_response": "关门并把所有可疑物件一起搬走。",
                "counterexample": "关闭边界没有停止地下路径，搬运反而让它靠近工人。",
                "new_response": "让不同接触者分开撤离并记录两种现象各自变化。",
                "executed_change": "张洞接受父亲分配位置，自己只负责记录和示警。",
            },
            "emotional_afterimage": {
                "person": "张洞与父亲。",
                "immediate_wound": "张洞越过父亲替他安排风险，父亲撤回了刚给出的信任。",
                "material_aftereffect": "第一笔工钱仍在，但后续介绍暂时搁置。",
                "relationship_aftereffect": "父亲要求张洞下一次先说明观察，再讨论由谁承担。",
                "unresolved_choice": "张洞要在保护家人与尊重家人选择之间找到新的做法。",
                "mystery_subordinate_to": "两类异常的关系只有在继续逼迫父子选择时才重要。",
            },
            "clue_delivery": {
                "method": "张洞在同一救援中对照两类现象的变化",
                "resistance": "工人必须搬货，熟人又必须决定是否放行",
                "coincidence_risk": "LOW",
            },
        },
        "dramatic_spine": {
            "opening_pressure": "张洞刚拿到工钱，货栈便要求所有人立即搬货。",
            "opening_genre_signal": "边界外的熟悉身份与地下刮擦同时出现。",
            "protagonist_immediate_want": "保住工钱和后续工作介绍。",
            "personal_stake": "证明自己能凭手艺承担家庭责任。",
            "destabilizing_event": "两类异常在同一批货物周围表现出不同路径。",
            "protagonist_choice": "张洞组织人员分开撤出，而不是独自查清所有现象。",
            "choice_cost": "他把路线决定权还给父亲，并公开承认自己的判断不完整。",
            "cost_realization": "父亲当场否决张洞安排的位置，改走自己选择的出口。",
            "relationship_friction": "张洞想先保父亲，父亲拒绝被当成需要搬走的物件。",
            "question_progression": [
                "门外身份为何重复出现？",
                "地下刮擦为何跟随旧木？",
                "两类现象是否会互相改变路径？",
            ],
            "emotional_turn": "获得报酬的自豪变成承认判断有限的难堪。",
            "serial_promise": "下一次选择必须由承担风险的人共同决定。",
            "ending_leverage": "张洞保住工钱并记录了两类现象的第一组差异。",
        },
        "scenes": [{
            "location": "双桥镇货栈",
            "characters": ["张洞", "父亲", "货栈雇主"],
            "goal": "把工人和货物撤出受影响区域",
            "conflict": "门外身份与地下路径要求相反的应对",
            "function": "建立两类异常的差异",
            "initial_state": "货栈仍在营业，张洞刚领到工钱。",
            "discovery_path": "张洞先看见来客停步，随后从未移动的旧木下听见刮擦继续靠近。",
            "knowledge_limits": "他只知道两类现象没有同步停止，不知道来源与完整规律。",
            "ordinary_explanations": {
                "considered": ["有人冒名顶替", "地下有动物"],
                "excluded": ["同一人在两个位置同时行动"],
                "remaining": ["两种普通原因恰好同时发生"],
            },
            "choice_reason": "继续留人核验会让工人同时暴露在两条路径上。",
            "end_state": "工人分批撤出，张洞保住报酬但失去独自安排父亲的权力。",
            "pressure_change": "一个未知被拆成两类仍未解决的行动危险",
            "irreversible_change": "父亲公开拒绝张洞替自己选择位置",
            "threat_action": "来客要求放行的同时，地下刮擦沿搬动的旧木转向工人。",
            "human_turn": "父亲自行选择出口，并要求张洞只报告自己真正看见的差异。",
            "payoff_type": "MIXED",
        }],
        "new_information": ["两类现象不会同步停止"],
        "state_changes": {
            "protagonist_known_info_add": ["两类现象不会同步停止"],
            "protagonist_inventory_add": ["第一笔工钱"],
            "protagonist_inventory_remove": [],
            "protagonist_location": "双桥镇货栈",
            "protagonist_body_updates": [],
            "ability_updates": [],
            "timeline_year": 1911,
            "timeline_elapsed_days": 1,
            "character_updates": ["父亲拒绝由张洞替自己选择风险位置"],
            "world_confirmed_add": [],
            "world_hypotheses_add": ["门外身份与地下路径可能是两类作用"],
        },
        "rule_hypotheses": ["门外身份与地下路径可能是两类作用"],
        "canon_evidence_ids": ["CANON-RULE-001"],
        "foreshadow_operations": {"plant": [], "recover": []},
        "milestone_operations": {"complete": []},
        "hook": {"type": "路径分裂", "content": "来客停下后，地下刮擦仍在移动"},
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


def _state():
    return json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))


class QualityGateTests(unittest.TestCase):
    def test_plan_rejects_unregistered_canon_evidence(self):
        state = _state()
        plan = _valid_plan()
        plan["canon_evidence_ids"] = ["MADE-UP-FACT"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("UNKNOWN_CANON_EVIDENCE", {item.code for item in issues})

    def test_plan_rejects_unregistered_design_identifiers(self):
        state = _state()
        plan = _valid_plan()
        plan["foreshadow_operations"]["plant"] = ["F-Z99"]
        plan["milestone_operations"]["complete"] = ["M9-NOT-REAL"]
        issues = validate_plan(
            plan,
            state,
            {"CANON-RULE-001"},
            {"F-X01"},
            {"M1-TEST"},
        )
        self.assertEqual(
            {"UNKNOWN_FORESHADOW", "UNKNOWN_MILESTONE"},
            {item.code for item in issues if item.code.startswith("UNKNOWN_")}
            - {"UNKNOWN_CANON_EVIDENCE"},
        )

    def test_plan_requires_reconstructable_scene_model(self):
        state = _state()
        plan = _valid_plan()
        del plan["scenes"][0]["initial_state"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("SCENE_MODEL_MISSING", {item.code for item in issues})

    def test_plan_requires_distinct_question_progression(self):
        state = _state()
        plan = _valid_plan()
        plan["dramatic_spine"]["question_progression"] = ["现象从哪里来？"] * 3
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("BAD_QUESTION_PROGRESSION", {item.code for item in issues})

    def test_plan_rejects_removal_of_untracked_inventory(self):
        state = _state()
        plan = _valid_plan()
        plan["state_changes"]["protagonist_inventory_remove"] = ["未登记资源"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("INVENTORY_REMOVE_MISSING", {item.code for item in issues})

    def test_plan_rejects_known_information_as_new_progress(self):
        state = _state()
        plan = _valid_plan()
        repeated = "两类现象不会同步停止"
        state["protagonist"]["known_info"].append(repeated)
        plan["state_changes"]["protagonist_known_info_add"] = [repeated]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("KNOWN_INFO_ALREADY_PRESENT", {item.code for item in issues})

    def test_plan_rejects_malformed_ordinary_explanations(self):
        state = _state()
        plan = _valid_plan()
        plan["scenes"][0]["ordinary_explanations"] = {"considered": "普通回声"}
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("SCENE_BAD_ALTERNATIVES", {item.code for item in issues})

    def test_plan_rejects_evidence_dominated_scenes(self):
        state = _state()
        plan = _valid_plan()
        plan["scenes"][0]["payoff_type"] = "EVIDENCE_ONLY"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        codes = {item.code for item in issues}
        self.assertIn("EVIDENCE_DOMINATED_PLAN", codes)
        self.assertIn("EVIDENCE_ONLY_ENDING", codes)

    def test_plan_rejects_high_coincidence_clue_delivery(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["clue_delivery"]["coincidence_risk"] = "HIGH"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("HIGH_COINCIDENCE_CLUE", {item.code for item in issues})

    def test_plan_rejects_threat_field_that_admits_no_current_action(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["threat_in_motion"] = (
            "异常尚未再现，家人只是在讨论上次事故。"
        )
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("INACTIVE_THREAT_ADMITTED", {item.code for item in issues})

    def test_plan_requires_cross_chapter_core_threat_contract(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["core_threat_continuation"] = {}
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("BAD_CORE_THREAT_CONTINUATION", {item.code for item in issues})

    def test_plan_rejects_conflicting_testimony_as_the_only_core_answer(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["core_threat_continuation"]["local_answer"] = (
            "在场者只是说法不一，仍不清楚谁先移动。"
        )
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("STALLED_CORE_REVELATION", {item.code for item in issues})

    def test_plan_rejects_repeating_the_old_defense_as_a_new_rule(self):
        state = _state()
        plan = _valid_plan()
        thread = plan["reader_investment"]["core_threat_continuation"]
        thread["old_defense"] = "所有人留在原地等待。"
        thread["replacement_rule"] = "所有人留在原地等待。"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("STALLED_ACTION_RULE", {item.code for item in issues})

    def test_plan_rejects_superficial_attachment_label(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["attachment_anchor"] = "父亲很重要"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("BAD_ATTACHMENT_ANCHOR", {item.code for item in issues})

    def test_plan_requires_human_afterimage_beyond_mystery(self):
        state = _state()
        plan = _valid_plan()
        plan["reader_investment"]["emotional_afterimage"] = "只想知道来客是谁"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("BAD_EMOTIONAL_AFTERIMAGE", {item.code for item in issues})

    def test_plan_requires_question_to_change_kind(self):
        state = _state()
        plan = _valid_plan()
        shift = plan["reader_investment"]["revelation_shift"]
        shift["to"] = shift["from"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("BAD_REVELATION_SHIFT", {item.code for item in issues})

    def test_plan_rejects_an_unexecuted_revelation_response(self):
        state = _state()
        plan = _valid_plan()
        shift = plan["reader_investment"]["revelation_shift"]
        shift["new_response"] = shift["old_response"]
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("STALLED_REVELATION_RESPONSE", {item.code for item in issues})

    def test_next_chapter_cannot_repeat_exit_loss_in_another_form(self):
        state = _state()
        state["chapter"].update({"last_chapter": 1, "next_chapter": 2})
        state["recent_hooks"] = [{
            "chapter": 1,
            "type": "代价展示",
            "content": "道路封闭使张洞错过离开本地的机会。",
        }]
        plan = _valid_plan()
        plan["chapter_number"] = 2
        plan["dramatic_spine"]["choice_cost"] = "新的停运让他再次无法撤离。"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("REPEATED_EXIT_LOSS", {item.code for item in issues})

    def test_next_chapter_can_compare_new_wound_with_prior_exit_loss(self):
        state = _state()
        state["chapter"].update({"last_chapter": 1, "next_chapter": 2})
        state["recent_hooks"] = [{
            "chapter": 1,
            "type": "代价展示",
            "content": "道路封闭使张洞错过离开本地的机会。",
        }]
        plan = _valid_plan()
        plan["chapter_number"] = 2
        plan["reader_investment"]["attachment_anchor"]["private_meaning"] = (
            "相比上一章失去离开本地的机会，这次父亲撤回信任才是新的伤口。"
        )
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertNotIn("REPEATED_EXIT_LOSS", {item.code for item in issues})

    def test_next_chapter_cannot_repeat_livelihood_loss(self):
        state = _state()
        state["chapter"].update({"last_chapter": 1, "next_chapter": 2})
        state["recent_hooks"] = [{
            "chapter": 1,
            "type": "代价展示",
            "content": "雇主终止工作，张洞失去唯一收入。",
        }]
        plan = _valid_plan()
        plan["chapter_number"] = 2
        plan["dramatic_spine"]["choice_cost"] = "新的雇用也被终止，他再次失去谋生收入。"
        issues = validate_plan(plan, state, {"CANON-RULE-001"})
        self.assertIn("REPEATED_CAREER_LOSS", {item.code for item in issues})

    def test_foreshadow_must_be_planted_in_an_earlier_chapter(self):
        state = _state()
        plan = _valid_plan()
        plan["foreshadow_operations"]["recover"] = ["F-X01"]
        issues = validate_plan(
            plan,
            state,
            {"CANON-RULE-001"},
            {"F-X01"},
            set(),
            {"entries": {"F-X01": {}}},
            {"milestones": {}},
        )
        self.assertIn("FORESHADOW_NOT_PLANTED", {item.code for item in issues})

    def test_milestone_requires_prior_milestone_and_current_volume(self):
        state = _state()
        plan = _valid_plan()
        plan["milestone_operations"]["complete"] = ["M2-ACTIVE-PRICE"]
        registry = {
            "milestones": {
                "M2-ACTIVE-PRICE": {"volume": 2, "after": ["M1-CITY-EXIT"]}
            }
        }
        issues = validate_plan(
            plan,
            state,
            {"CANON-RULE-001"},
            set(),
            {"M2-ACTIVE-PRICE"},
            {"entries": {}},
            registry,
        )
        codes = {item.code for item in issues}
        self.assertIn("MILESTONE_PREREQUISITE_MISSING", codes)
        self.assertIn("MILESTONE_WRONG_VOLUME", codes)

    def test_due_foreshadow_blocks_exit_milestone(self):
        state = _state()
        state["active_foreshadows"] = {
            "F-X01": {"status": "已播种", "plant_chapter": 1}
        }
        state["chapter"]["next_chapter"] = 2
        plan = _valid_plan()
        plan["chapter_number"] = 2
        plan["milestone_operations"]["complete"] = ["M1-CITY-EXIT"]
        issues = validate_plan(
            plan,
            state,
            {"CANON-RULE-001"},
            {"F-X01"},
            {"M1-CITY-EXIT"},
            {"entries": {"F-X01": {"recover_by": "M1-CITY-EXIT"}}},
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
            length_policy={
                "safe_min": 2500,
                "target_min": 3200,
                "target_max": 5000,
                "safe_max": 8000,
            },
        )
        self.assertFalse(result["passed"])
        self.assertIn("WORD_COUNT_HARD_FAIL", {item["code"] for item in result["issues"]})

    def test_taste_contract_remains_active(self):
        result = scan_draft(
            "第1章\n\n这段文字包含禁用表达。",
            [],
            {"characters": [], "terms": []},
            {"chapter_number": 1, "prohibited_elements": []},
            taste_contract={
                "deterministic_checks": {
                    "forbidden_tokens": ["禁用表达"],
                    "warn_staccato_run": 99,
                }
            },
        )
        self.assertIn(
            "USER_TASTE_FORBIDDEN_TOKEN",
            {item["code"] for item in result["issues"]},
        )

    def test_plan_must_match_next_chapter_and_change_state(self):
        state = _state()
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
