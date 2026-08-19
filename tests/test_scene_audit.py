import copy
import hashlib
import unittest
from pathlib import Path

from scripts.prequel.quality import scan_draft
from scripts.prequel.scene_audit import (
    build_scene_audit_packet,
    canonicalize_scene_audit_anchor_quotes,
    extract_scene_audit_anchors,
    validate_scene_mechanism_audit,
)
from scripts.prequel.taste_contract import load_taste_contract, validate_taste_contract


def passing_audit(draft: str) -> dict:
    anchors = extract_scene_audit_anchors(draft)
    return {
        "artifact_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "verdict": "PASS",
        "pov_source_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "claim_quote": item["quote"],
                "information_source": "当场可见",
                "source_quote": item["quote"],
                "verdict": "SUPPORTED",
                "explanation": "来源在结论之前。",
            }
            for item in anchors["pov_claims"]
        ],
        "boundary_action_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "action_quote": item["quote"],
                "before_quote": item["quote"],
                "after_quote": item["quote"],
                "visible_to_pov": True,
                "verdict": "COHERENT",
                "explanation": "边界动作连续。",
            }
            for item in anchors["boundary_actions"]
        ],
        "shock_response_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "trigger_quote": item["quote"],
                "response_quote": None,
                "response_window": "人物早已知道。",
                "verdict": "NOT_NEW_INFORMATION",
                "explanation": "测试基线没有新增冲击。",
            }
            for item in anchors["shock_triggers"]
        ],
        "dialogue_register_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "dialogue_quote": item["quote"],
                "speaker": "人物",
                "goal": "阻止开门",
                "verdict": "NATURAL",
                "explanation": "口语自然。",
            }
            for item in anchors["dialogue_samples"]
        ],
        "first_read_reconstruction": {
            "reader_can_reconstruct": True,
            "required_rereads": 0,
            "character_positions": "张洞在门内，来人在门外。",
            "visibility_limits": "门缝限制视野。",
            "action_chain": "门开缝，人物观察，随后关门。",
            "confusing_quotes": [],
        },
        "blocking_issues": [],
        "revision_instructions": [],
    }


class SceneAuditTests(unittest.TestCase):
    def setUp(self):
        self.contract = load_taste_contract(Path.cwd())

    def test_project_taste_contract_is_structurally_valid(self):
        self.assertEqual(validate_taste_contract(self.contract), [])
        ids = {item["id"] for item in self.contract["hard_constraints"]}
        self.assertIn("TASTE-POV-001", ids)
        self.assertIn("TASTE-REACTION-001", ids)

    def test_anchor_extraction_covers_perception_boundary_shock_and_dialogue(self):
        draft = "门只开了一道缝，张洞认出来人是六伢。周秀兰已经死了。李二说：“你找谁？”"
        anchors = extract_scene_audit_anchors(draft)
        self.assertTrue(anchors["pov_claims"])
        self.assertTrue(anchors["boundary_actions"])
        self.assertTrue(anchors["shock_triggers"])
        self.assertTrue(anchors["dialogue_samples"])

    def test_validator_rejects_uncovered_high_risk_anchor(self):
        draft = "张洞看清门外站着一个人。"
        audit = passing_audit(draft)
        audit["pov_source_ledger"] = []
        codes = {
            item.code
            for item in validate_scene_mechanism_audit(audit, draft)
        }
        self.assertIn("SCENE_ANCHOR_COVERAGE_MISSING", codes)

    def test_pov_source_quote_must_end_no_later_than_claim(self):
        draft = "张洞看见门板上的灰。灰正落在张洞手上。"
        audit = passing_audit(draft)
        audit["pov_source_ledger"][0]["source_quote"] = draft
        codes = {
            item.code
            for item in validate_scene_mechanism_audit(audit, draft)
        }
        self.assertIn("SCENE_RETROACTIVE_POV_SOURCE", codes)

    def test_validator_rejects_pass_with_underreaction(self):
        draft = "周秀兰已经下葬。李二问：“你来还什么？”"
        audit = passing_audit(draft)
        audit["shock_response_ledger"][0].update(
            {
                "response_quote": "李二问：",
                "response_window": "紧接着问账。",
                "verdict": "UNDERREACTION",
                "explanation": "没有受惊过渡。",
            }
        )
        codes = {
            item.code
            for item in validate_scene_mechanism_audit(audit, draft)
        }
        self.assertIn("SCENE_PASS_CONFLICT", codes)

    def test_validator_rejects_pass_with_archaic_dialogue(self):
        draft = "李二说：“周氏婶，此事容后再议。”"
        audit = passing_audit(draft)
        audit["dialogue_register_ledger"][0]["verdict"] = "ARCHAIC"
        codes = {
            item.code
            for item in validate_scene_mechanism_audit(
                audit, draft, taste_contract=self.contract
            )
        }
        self.assertIn("SCENE_PASS_CONFLICT", codes)

    def test_validator_allows_explicit_supernatural_visibility_gap(self):
        draft = "张洞看清她站在门外。他关上门，转身后看见她站在屋里。"
        audit = passing_audit(draft)
        for row in audit["boundary_action_ledger"]:
            row["visible_to_pov"] = False
            row["explanation"] = "前后状态可见，中间灵异转移发生在明确的视线中断中。"
        self.assertEqual(validate_scene_mechanism_audit(audit, draft), [])

    def test_scene_packet_can_carry_published_prior_facts(self):
        draft = "前门还栏着，可周秀兰已经在屋里。"
        prior = [
            {
                "chapter": 1,
                "title": "第五升",
                "reader_visible_summary": "张洞背对院子栏门后，发现周秀兰已从门外出现在院内。",
            }
        ]
        packet = build_scene_audit_packet(
            draft,
            self.contract,
            artifact_label="第二章",
            prior_reader_facts=prior,
        )
        self.assertEqual(packet["prior_reader_facts"], prior)

    def test_anchor_quote_canonicalization_uses_deterministic_id(self):
        draft = "张洞看清门外站着一个人。"
        audit = passing_audit(draft)
        audit["pov_source_ledger"][0]["claim_quote"] = "张洞看清门外。"
        canonicalize_scene_audit_anchor_quotes(audit, draft)
        self.assertEqual(
            audit["pov_source_ledger"][0]["claim_quote"],
            extract_scene_audit_anchors(draft)["pov_claims"][0]["quote"],
        )

    def test_auxiliary_quote_canonicalization_repairs_only_unique_stem(self):
        draft = "门关上了，簸箕放在院子中间，母亲正在走回来。"
        audit = passing_audit(draft)
        audit["boundary_action_ledger"][0]["before_quote"] = "簸箕放在院子中间。"
        canonicalize_scene_audit_anchor_quotes(audit, draft)
        self.assertEqual(
            audit["boundary_action_ledger"][0]["before_quote"],
            "簸箕放在院子中间",
        )

    def test_auxiliary_quote_can_drop_short_wrong_subject_prefix(self):
        draft = "他的手已经碰到木栓，脚跟也退出鞋帮半寸。"
        audit = {
            "boundary_action_ledger": [
                {
                    "anchor_id": "BOUNDARY-TEST",
                    "after_quote": "张洞的手已经碰到木栓。",
                }
            ]
        }

        canonicalize_scene_audit_anchor_quotes(audit, draft)

        self.assertEqual(
            audit["boundary_action_ledger"][0]["after_quote"],
            "手已经碰到木栓",
        )

    def test_static_gate_blocks_obstructed_identification(self):
        draft = "第1章：门\n\n门只开了一道缝，张洞一眼认出跑来的是六伢。"
        result = scan_draft(
            draft,
            [],
            {"characters": [], "terms": []},
            {"chapter_number": 1, "prohibited_elements": []},
            length_policy={"safe_min": 1, "target_min": 1},
            taste_contract=self.contract,
        )
        self.assertIn(
            "OBSTRUCTED_IDENTIFICATION",
            {item["code"] for item in result["issues"]},
        )

    def test_static_gate_blocks_rejected_name_and_address(self):
        draft = "第1章：门\n\n孙周氏站着。李二说：“婶，你怎么来了？”"
        contract = copy.deepcopy(self.contract)
        contract["deterministic_checks"]["forbidden_tokens"] = ["孙周氏"]
        contract["deterministic_checks"]["forbidden_address_tokens"] = ["婶"]
        result = scan_draft(
            draft,
            [],
            {"characters": [], "terms": []},
            {"chapter_number": 1, "prohibited_elements": []},
            length_policy={"safe_min": 1, "target_min": 1},
            taste_contract=contract,
        )
        codes = {item["code"] for item in result["issues"]}
        self.assertIn("USER_TASTE_FORBIDDEN_TOKEN", codes)
        self.assertIn("USER_TASTE_FORBIDDEN_ADDRESS", codes)


if __name__ == "__main__":
    unittest.main()
