import unittest

from scripts.prequel.evaluation import (
    build_scorecard,
    canonicalize_artifact_quotes,
    classify_candidate,
    eligible,
    merge_specialist_review,
    promotion_decision,
    revision_improved,
    scorecard_from_integrated,
    selection_policy,
    tally_ballots,
    validate_integrated_review,
    validate_specialist_review,
)


def review(dimension, score, hard=False):
    return {
        "chapter_number": 3,
        "dimension": dimension,
        "score": score,
        "hard_failures": [
            {"code": "FACT", "quote": "门内", "explanation": "冲突"}
        ] if hard else [],
        "warnings": [],
        "evidence": [
            {"quote": "门内", "finding": "证据"},
            {"quote": "铁栓", "finding": "证据"},
            {"quote": "纸灰", "finding": "证据"},
        ],
        "required_revisions": [],
        "summary": "完成审查",
    }


def integrated(scores=None, hard=False):
    scores = scores or {
        "continuity": 90,
        "character": 80,
        "craft": 82,
        "anti_slop": 84,
    }
    evidence = {
        dimension: [
            {"quote": "门内", "finding": "证据"},
            {"quote": "铁栓", "finding": "证据"},
        ]
        for dimension in ("continuity", "character", "craft", "anti_slop")
    }
    return {
        "chapter_number": 3,
        "scores": scores,
        "confidences": {name: 0.9 for name in scores},
        "hard_failures": (
            [{"dimension": "continuity", "code": "FACT", "quote": "门内", "explanation": "冲突"}]
            if hard
            else []
        ),
        "warnings": [],
        "evidence": evidence,
        "required_revisions": [],
        "specialist_requests": [],
        "fact_findings": [
            {"fact_id": "CANON-RULE-001", "value": "成立", "quote": "门内"}
        ],
        "summaries": {name: "通过" for name in scores},
    }


class EvaluationTests(unittest.TestCase):
    def test_quote_canonicalization_repairs_only_source_mappable_boundaries(self):
        draft = (
            "母亲说：“先收好。还要查账。”\n\n"
            "灰沿着竹篾裂开。\n\n蒸屉的缝里是干净的。"
            "\n母亲从腰间解下布带，把钥匙系在内侧，又把外衣压好。"
        )
        artifact = {
            "evidence": [
                {"quote": "母亲说：“先收好。”", "finding": "边界"},
                {
                    "quote": "灰沿着竹篾裂开。蒸屉的缝里是干净的。",
                    "finding": "空行",
                },
                {"quote": "“灰沿着竹篾裂开。”", "finding": "叙述句误加引号"},
                {
                    "quote": "母亲从腰间解下布带，把钥匙系在内侧。",
                    "finding": "句末标点边界",
                },
                {"quote": "正文并不存在", "finding": "假证据"},
            ]
        }
        repaired = canonicalize_artifact_quotes(artifact, draft)
        self.assertEqual(repaired, 4)
        self.assertEqual(artifact["evidence"][0]["quote"], "母亲说：“先收好。")
        self.assertEqual(
            artifact["evidence"][1]["quote"],
            "灰沿着竹篾裂开。\n\n蒸屉的缝里是干净的。",
        )
        self.assertEqual(artifact["evidence"][2]["quote"], "灰沿着竹篾裂开。")
        self.assertEqual(
            artifact["evidence"][3]["quote"],
            "母亲从腰间解下布带，把钥匙系在内侧",
        )
        self.assertEqual(artifact["evidence"][4]["quote"], "正文并不存在")

    def test_integrated_false_quote_is_rejected(self):
        value = integrated()
        value["evidence"]["craft"][0]["quote"] = "不存在"
        issues = validate_integrated_review(
            value, "门内有铁栓", 3, {"CANON-RULE-001"}
        )
        self.assertIn("REVIEW_FALSE_EVIDENCE", {issue.code for issue in issues})

    def test_candidate_classes_are_deterministic(self):
        hard = scorecard_from_integrated(integrated(hard=True))
        self.assertEqual(classify_candidate(hard), "HARD_FAIL")
        eligible_card = scorecard_from_integrated(integrated())
        self.assertEqual(classify_candidate(eligible_card), "ELIGIBLE")
        near = scorecard_from_integrated(
            integrated({"continuity": 84, "character": 80, "craft": 84, "anti_slop": 84})
        )
        self.assertEqual(classify_candidate(near), "NEAR_MISS")
        low = scorecard_from_integrated(
            integrated({"continuity": 70, "character": 70, "craft": 70, "anti_slop": 70})
        )
        self.assertEqual(classify_candidate(low), "LOW_SCORE")

    def test_selection_policy_skips_selector_for_large_gap(self):
        candidates = [
            {"identifier": "a", "classification": "ELIGIBLE", "scorecard": {"weighted_score": 91}},
            {"identifier": "b", "classification": "ELIGIBLE", "scorecard": {"weighted_score": 85}},
        ]
        action = selection_policy(candidates, 4)
        self.assertEqual((action.kind, action.selected_id), ("DIRECT_SELECT", "a"))

    def test_single_eligible_never_replans(self):
        candidates = [
            {"identifier": "a", "classification": "ELIGIBLE", "scorecard": {"weighted_score": 90}},
            {"identifier": "b", "classification": "LOW_SCORE", "scorecard": {"weighted_score": 70}},
        ]
        action = selection_policy(candidates)
        self.assertEqual(action.kind, "DIRECT_SELECT_LOW_CONFIDENCE")
        self.assertEqual(action.selected_id, "a")

    def test_single_eligible_cannot_auto_promote_without_guard(self):
        card = scorecard_from_integrated(
            integrated({"continuity": 92, "character": 84, "craft": 85, "anti_slop": 83})
        )
        outcome = promotion_decision(
            card,
            selection_confident=True,
            selection_mode="SINGLE_ELIGIBLE",
            continuity_guard_passed=False,
        )
        self.assertEqual(outcome["status"], "WAITING_USER")

    def test_specialist_replaces_only_its_dimension(self):
        card = scorecard_from_integrated(integrated())
        updated = merge_specialist_review(card, review("continuity", 95))
        self.assertEqual(updated["scores"]["continuity"], 95)
        self.assertEqual(updated["scores"]["character"], card["scores"]["character"])
    def test_false_quote_invalidates_specialist_review(self):
        issues = validate_specialist_review(
            review("continuity", 90), "只有铁栓和纸灰", 3, "continuity"
        )
        self.assertIn("REVIEW_FALSE_EVIDENCE", {issue.code for issue in issues})

    def test_dimension_floor_cannot_be_hidden_by_weighted_score(self):
        reviews = {
            "continuity": review("continuity", 95),
            "character": review("character", 74),
            "craft": review("craft", 95),
            "anti_slop": review("anti_slop", 95),
        }
        self.assertFalse(eligible(build_scorecard(reviews)))

    def test_ballot_requires_two_votes(self):
        self.assertEqual(
            tally_ballots(["candidate_01", "candidate_01", "candidate_02"]),
            ("candidate_01", 2),
        )
        self.assertEqual(
            tally_ballots(["candidate_01", "candidate_02", None]),
            (None, 1),
        )

    def test_revision_regression_is_rejected(self):
        previous = {"weighted_score": 86, "scores": {"continuity": 92, "character": 84, "craft": 84, "anti_slop": 84}}
        current = {"weighted_score": 87, "scores": {"continuity": 88, "character": 90, "craft": 86, "anti_slop": 86}}
        self.assertFalse(revision_improved(previous, current, 2))

    def test_high_confidence_result_auto_promotes(self):
        card = {"weighted_score": 86, "scores": {"continuity": 92, "character": 84, "craft": 85, "anti_slop": 83}, "hard_failures": [], "required_revisions": []}
        self.assertEqual(
            promotion_decision(card, "candidate_01", "candidate_01", 2)["status"],
            "AUTO_PROMOTE",
        )


if __name__ == "__main__":
    unittest.main()
