import hashlib
import json
import unittest
from pathlib import Path

from scripts.prequel.state_settlement import (
    build_state_settlement_packet,
    canonicalize_missing_change_paths,
    expected_state_changes,
    validate_state_settlement,
)
from tests.test_pipeline import valid_plan_json


class StateSettlementTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        self.plan = json.loads(valid_plan_json())
        self.draft = "第1章：门上的灰\n张洞把纸灰分进碗里，记下纸灰会离开祠堂。\n一天过去，他猜纸灰可能标记被敲门者。\n天黑前，那层灰到了门内。"
        expected = expected_state_changes(self.state, self.plan)
        quote_by_path = {
            "state_changes.protagonist_known_info_add[0]": "纸灰会离开祠堂",
            "state_changes.timeline_elapsed_days": "一天过去",
            "state_changes.world_hypotheses_add[0]": "纸灰可能标记被敲门者",
            "foreshadow_operations.plant[0]": "那层灰到了门内",
        }
        self.report = {
            "chapter_number": 1,
            "draft_sha256": hashlib.sha256(self.draft.encode("utf-8")).hexdigest(),
            "verdict": "PASS",
            "reader_visible_summary": {
                "core": "张洞留下纸灰样本，并发现纸灰已经越过门板。",
                "evidence": ["张洞把纸灰分进碗里", "那层灰到了门内"],
            },
            "hook": {"type": "安全区崩坏", "content": "纸灰越过门板", "quote": "那层灰到了门内"},
            "change_evidence": [
                {
                    "path": item["path"], "value": item["value"],
                    "quote": quote_by_path[item["path"]], "finding": item["meaning"],
                }
                for item in expected
            ],
            "missing_changes": [],
        }

    def test_packet_marks_plan_changes_as_candidates_not_facts(self):
        packet = build_state_settlement_packet(self.state, self.plan, self.draft)
        self.assertIn("planned_change_candidates", packet)
        self.assertNotIn("state_changes", packet)
        self.assertIn("没有逐字证据的变化不得结算", packet["instruction_boundary"])

    def test_complete_text_grounded_settlement_passes(self):
        self.assertEqual(
            validate_state_settlement(self.report, self.state, self.plan, self.draft),
            [],
        )

    def test_missing_planned_change_blocks_pass(self):
        missing = self.report["change_evidence"].pop()
        self.report["missing_changes"] = [missing["path"]]
        codes = {
            item.code
            for item in validate_state_settlement(self.report, self.state, self.plan, self.draft)
        }
        self.assertIn("SETTLEMENT_PASS_WITH_GAPS", codes)

    def test_optional_missing_change_can_still_pass(self):
        path = "state_changes.world_hypotheses_add[0]"
        self.report["change_evidence"] = [
            item for item in self.report["change_evidence"] if item["path"] != path
        ]
        self.report["missing_changes"] = [path]
        self.assertEqual(
            validate_state_settlement(
                self.report, self.state, self.plan, self.draft
            ),
            [],
        )

    def test_annotated_missing_path_is_canonicalized(self):
        expected = expected_state_changes(self.state, self.plan)
        self.report["missing_changes"] = [
            "state_changes.timeline_elapsed_days: 1（正文没有精确日期）"
        ]
        repaired = canonicalize_missing_change_paths(self.report, expected)
        self.assertEqual(repaired, 1)
        self.assertEqual(
            self.report["missing_changes"],
            ["state_changes.timeline_elapsed_days"],
        )

    def test_registry_semantics_are_included_for_required_foreshadow(self):
        packet = build_state_settlement_packet(
            self.state,
            self.plan,
            self.draft,
            {
                "entries": {
                    "F-A01": {
                        "plant": "不该出现的纸灰",
                        "meaning": "纸灰指向封存泄漏",
                    }
                }
            },
        )
        foreshadow = next(
            item
            for item in packet["planned_change_candidates"]
            if item["path"] == "foreshadow_operations.plant[0]"
        )
        self.assertTrue(foreshadow["required_for_promotion"])
        self.assertIn("不该出现的纸灰", foreshadow["meaning"])

    def test_false_quote_is_rejected(self):
        self.report["change_evidence"][0]["quote"] = "正文不存在"
        codes = {
            item.code
            for item in validate_state_settlement(self.report, self.state, self.plan, self.draft)
        }
        self.assertIn("SETTLEMENT_FALSE_EVIDENCE", codes)

    def test_duplicate_change_path_is_rejected(self):
        self.report["change_evidence"].append(dict(self.report["change_evidence"][0]))
        codes = {
            item.code
            for item in validate_state_settlement(self.report, self.state, self.plan, self.draft)
        }
        self.assertIn("SETTLEMENT_DUPLICATE_PATH", codes)


if __name__ == "__main__":
    unittest.main()
