import json
import unittest
from pathlib import Path

from scripts.prequel.context_builder import (
    CANDIDATE_FOCUSES,
    build_ballot_packet,
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
            "event_outline": "只能借用死者声音",
        }
        packet = build_writer_packet(self.state, plan, [], context)
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("raw_excerpts", rendered)
        self.assertNotIn("evidence_files", rendered)
        self.assertEqual(packet["event_guardrails"], "只能借用死者声音")

    def test_writer_receives_story_brief_not_auditor_scene_ledger(self):
        plan = {
            "chapter_number": 1,
            "title": "门",
            "event_id": "event_1",
            "phase": "setup",
            "chapter_purpose": "张洞被迫做选择",
            "reader_investment": {"attachment_anchor": {
                "focus": "母子关系",
                "on_page_moment": "母亲替他装好行李",
                "private_meaning": "两人都不肯说舍不得",
                "lived_value": "母亲靠针线活养家并自己选择客户",
                "threatened_loss": "离镇或留下都会伤害信任",
                "loss_carrier": "行囊与针线凳承载两人的去留生活",
            }},
            "dramatic_spine": {
                "protagonist_choice": "张洞留下",
                "cost_realization": "张洞当场交出船钱",
                "ending_leverage": "张洞拿着唯一钥匙守住正门",
            },
            "new_information": ["门后有声音"],
            "rule_hypotheses": ["声音会借名"],
            "hook": {"type": "choice", "content": "他没有回家"},
            "prohibited_elements": ["全知解释"],
            "canon_evidence_ids": [],
            "scenes": [{
                "location": "木铺", "characters": ["张洞"], "goal": "取账本",
                "conflict": "门不开", "function": "制造选择",
                "initial_state": "门闩落着", "discovery_path": "先问再量",
                "knowledge_limits": "不知道声音来源",
                "ordinary_explanations": {"considered": ["有人"], "excluded": [], "remaining": ["有人"]},
                "choice_reason": "为了父亲", "end_state": "门仍关闭",
                "pressure_change": "退路减少", "irreversible_change": "账本被毁",
                "threat_action": "门后的人继续烧账本",
                "human_turn": "父亲不再信任张洞",
                "payoff_type": "MIXED",
            }],
        }
        packet = build_writer_packet(self.state, plan, [], {})
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn('"plan"', rendered)
        self.assertNotIn("ordinary_explanations", rendered)
        self.assertNotIn("discovery_path", rendered)
        self.assertNotIn("choice_reason", rendered)
        self.assertNotIn("end_state", rendered)
        self.assertNotIn('"dramatic_spine"', rendered)
        self.assertEqual(
            packet["story_brief"]["dramatic_contract"]["cost_realization"],
            "张洞当场交出船钱",
        )
        self.assertEqual(
            packet["story_brief"]["dramatic_contract"]["ending_leverage"],
            "张洞拿着唯一钥匙守住正门",
        )
        self.assertEqual(
            packet["story_brief"]["narrative_engine"]["attachment_anchor"]["on_page_moment"],
            "母亲替他装好行李",
        )
        self.assertEqual(
            packet["story_brief"]["scenes"][0]["human_turn"],
            "父亲不再信任张洞",
        )
        self.assertEqual(packet["story_brief"]["scenes"][0]["goal"], "取账本")
        self.assertNotIn("pressure_change", packet["story_brief"]["scenes"][0])
        self.assertNotIn("threat_action", packet["story_brief"]["scenes"][0])
        self.assertNotIn("payoff_type", packet["story_brief"]["scenes"][0])
        self.assertEqual(
            packet["hard_constraints"]["knowledge_boundaries"][0]["constraint"],
            "不知道声音来源",
        )

    def test_writer_context_uses_authoritative_sources_and_recent_prose(self):
        plan = {
            "chapter_number": 1,
            "canon_evidence_ids": [],
            "scenes": [{"characters": ["张洞"], "location": "双桥"}],
        }
        packet = build_writer_packet(
            self.state,
            plan,
            ["旧章开头" + "甲" * 2600 + "旧章结尾"],
            {},
            project_root=Path.cwd(),
        )
        sources = packet["authoritative_context"]
        self.assertIn("schema: novel-prequel-style", sources["style"])
        self.assertEqual(sources["protagonist_runtime_profile"], self.state["protagonist"])
        self.assertIn("张洞", sources["character_voice_fallbacks"])
        self.assertEqual(
            packet["user_taste_contract"]["schema"],
            "novel-user-taste-contract",
        )
        self.assertIn("TASTE-POV-001", sources["user_taste_contract"])
        self.assertTrue(sources["recent_prose"][0].endswith("旧章结尾"))
        self.assertNotIn("style_anchors", sources)
        anchor_trace = next(
            item for item in packet["context_trace"] if item["label"] == "style_anchors"
        )
        self.assertFalse(anchor_trace["included"])

    def test_planner_receives_era_bans(self):
        context = build_planner_context(Path.cwd(), self.state)
        self.assertIn("周正", context["era_bans"]["characters"])
        self.assertIn("负责人", context["era_bans"]["terms"])

    def test_focus_library_always_pairs_structure_with_lived_voice(self):
        self.assertEqual(
            {item["name"] for item in CANDIDATE_FOCUSES},
            {
                "causal_tension",
                "character_pressure",
                "atmospheric_precision",
                "serial_compulsion",
                "lived_voice",
            },
        )
        plan = {
            "chapter_number": 3,
            "chapter_purpose": "调查异常规则和门后的证据",
            "dramatic_spine": {"protagonist_choice": "张洞选择承担代价"},
            "scenes": [],
            "rule_hypotheses": ["规则需要试错"],
        }
        selected = select_candidate_focuses(plan, 3)
        self.assertEqual(len(selected), 2)
        self.assertIn("causal_tension", {item["name"] for item in selected})
        self.assertIn("lived_voice", {item["name"] for item in selected})
        lived_voice = next(
            item for item in selected if item["name"] == "lived_voice"
        )
        self.assertIn("转述清单", lived_voice["instruction"])
        self.assertIn("关键声音直接呈现", lived_voice["instruction"])

    def test_ambiguous_plan_rotates_focus_pair_by_chapter(self):
        plan = {"chapter_purpose": "推进", "scenes": []}
        pairs = [
            tuple(item["name"] for item in select_candidate_focuses(plan, number))
            for number in (1, 2, 3)
        ]
        self.assertEqual(len(set(pairs)), 3)
        self.assertTrue(all("lived_voice" in pair for pair in pairs))

    def test_integrated_reviewer_gets_full_draft(self):
        plan = {
            "chapter_number": 1,
            "chapter_purpose": "逼主角做选择",
            "scenes": [{
                "location": "木铺",
                "initial_state": "前门关闭",
                "discovery_path": "从前门进入",
                "ordinary_explanations": {"remaining": ["有人恶作剧"]},
                "choice_reason": "为了父亲",
                "end_state": "前门仍关闭",
            }],
        }
        context = {
            "canon_facts": [{"id": "CANON-RULE-001"}],
            "era_bans": {},
            "event_outline": "事件权威规则",
        }
        packet = build_integrated_review_packet(
            self.state, plan, "完整正文", {"passed": True}, context
        )
        self.assertEqual(packet["draft"], "完整正文")
        ledger = packet["constraint_ledger"]
        self.assertEqual(ledger["hard_constraints"]["chapter_number"], 1)
        self.assertEqual(
            ledger["diagnostic_scene_model"][0]["discovery_path"],
            "从前门进入",
        )
        self.assertFalse(
            ledger["audit_policy"]["diagnostic_scene_model_is_binding"]
        )
        self.assertTrue(ledger["audit_policy"]["coherent_alternatives_are_allowed"])
        self.assertEqual(ledger["audit_policy"]["quote_source"], "draft_only")
        self.assertEqual(packet["event_outline"], "事件权威规则")
        self.assertNotIn("all_chapters", json.dumps(packet, ensure_ascii=False))

    def test_ballot_receives_story_brief_not_hidden_scene_diagnostics(self):
        plan = {
            "chapter_number": 1,
            "chapter_purpose": "逼主角做选择",
            "scenes": [{
                "location": "木铺",
                "goal": "取账本",
                "discovery_path": "从前门进入",
                "choice_reason": "为了父亲",
            }],
        }
        packet = build_ballot_packet(plan, "甲稿", "乙稿")
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn('"plan"', rendered)
        self.assertNotIn("discovery_path", rendered)
        self.assertNotIn("choice_reason", rendered)
        self.assertEqual(packet["story_brief"]["scenes"][0]["goal"], "取账本")

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
        self.assertIn("constraint_ledger", packet)
        self.assertNotIn("plan", packet)
        self.assertEqual(packet["target_issues"], [{"code": "X"}])
        self.assertNotIn("all_reviews", packet)


if __name__ == "__main__":
    unittest.main()
