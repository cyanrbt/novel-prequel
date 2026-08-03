import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError, ProviderError
from scripts.prequel.evolution import QualityEvolutionEngine
from scripts.prequel.evaluation import selection_policy, validate_revision_verification
from scripts.prequel.model_calls import ModelCallExecutor
from scripts.prequel.model_router import StageModelRouter
from scripts.prequel.run_manifest import RunManifest, fingerprint
from tests.test_pipeline import make_project_fixture, valid_draft, valid_plan_json


class QueueProvider:
    def __init__(self, outputs, model, effort, barrier_size=0):
        self.outputs = list(outputs)
        self.model = model
        self.reasoning_effort = effort
        self.lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.barrier = threading.Barrier(barrier_size) if barrier_size else None

    def generate(self, prompt, output_schema=None):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if not self.outputs:
                raise AssertionError("没有脚本化输出")
            output = self.outputs.pop(0)
        try:
            if self.barrier is not None and self.calls <= self.barrier.parties:
                self.barrier.wait(timeout=2)
            if isinstance(output, BaseException):
                raise output
            return output
        finally:
            with self.lock:
                self.active -= 1


def integrated(scores, *, revisions=None, requests=None):
    dimensions = ("continuity", "character", "craft", "anti_slop")
    return json.dumps(
        {
            "chapter_number": 1,
            "scores": scores,
            "confidences": {name: 0.92 for name in dimensions},
            "hard_failures": [],
            "warnings": [],
            "evidence": {
                name: [
                    {"quote": "门板上的灰", "finding": "物证明确"},
                    {"quote": "到了门内", "finding": "变化明确"},
                ]
                for name in dimensions
            },
            "required_revisions": revisions or [],
            "specialist_requests": requests or [],
            "fact_findings": [],
            "summaries": {name: "完成初筛" for name in dimensions},
        },
        ensure_ascii=False,
    )


def specialist(dimension, score):
    return json.dumps(
        {
            "chapter_number": 1,
            "dimension": dimension,
            "score": score,
            "hard_failures": [],
            "warnings": [],
            "evidence": [
                {"quote": "门板上的灰", "finding": "物证明确"},
                {"quote": "没有落到底", "finding": "观察成立"},
                {"quote": "到了门内", "finding": "变化明确"},
            ],
            "required_revisions": [],
            "summary": "专项通过",
        },
        ensure_ascii=False,
    )


def ballot(winner="A"):
    return json.dumps(
        {
            "winner": winner,
            "criteria": {
                "plan_fulfillment": winner,
                "character": winner,
                "pacing": winner,
                "anti_slop": winner,
            },
            "evidence": [
                {"candidate": "A", "quote": "门板上的灰", "finding": "明确"},
                {"candidate": "A", "quote": "到了门内", "finding": "明确"},
                {"candidate": "B", "quote": "门板上的灰", "finding": "明确"},
                {"candidate": "B", "quote": "到了门内", "finding": "明确"},
            ],
            "rationale": "A更稳",
        },
        ensure_ascii=False,
    )


def verification():
    return json.dumps(
        {
            "chapter_number": 1,
            "passed": True,
            "resolved": [
                {"code": "FIX_CAUSAL", "resolved": True, "explanation": "已修复"}
            ],
            "regressions": [],
            "evidence": [
                {"code": "FIX_CAUSAL", "quote": "门板上的灰", "finding": "已落实"}
            ],
            "updated_scores": [{"dimension": "continuity", "score": 93}],
            "summary": "验证通过",
        },
        ensure_ascii=False,
    )


class EvolutionTests(unittest.TestCase):
    def test_single_high_score_local_hard_failure_enters_verified_revision(self):
        action = selection_policy(
            [
                {
                    "identifier": "candidate_01",
                    "classification": "HARD_FAIL",
                    "scorecard": {
                        "weighted_score": 90,
                        "hard_failures": [{"code": "COUNT"}],
                        "required_revisions": [{"code": "COUNT"}],
                    },
                }
            ]
        )
        self.assertEqual((action.kind, action.selected_id), ("REVISE", "candidate_01"))

    def test_passing_verification_cannot_hide_a_large_score_regression(self):
        issues = validate_revision_verification(
            {
                "chapter_number": 1,
                "passed": True,
                "resolved": [],
                "regressions": [],
                "evidence": [],
                "updated_scores": [{"dimension": "continuity", "score": 9}],
                "summary": "错误地按十分制给分",
            },
            "修订稿正文",
            1,
            baseline_scores={"continuity": 86},
            max_dimension_regression=3,
        )
        self.assertTrue(
            any(issue.code == "VERIFY_UNDECLARED_SCORE_REGRESSION" for issue in issues)
        )

    def test_review_diagnostic_paths_are_narrowly_whitelisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 1, 1)
            workspace.write_text(
                "candidates/candidate_01/diagnostics/integrated_review.invalid.txt",
                "raw integrated output",
            )
            workspace.write_text(
                "candidates/candidate_01/diagnostics/continuity_review.invalid.txt",
                "raw specialist output",
            )
            with self.assertRaises(ArtifactValidationError):
                workspace.write_text(
                    "candidates/candidate_01/diagnostics/arbitrary.txt", "x"
                )
            with self.assertRaises(ArtifactValidationError):
                workspace.write_text("../escaped.invalid.txt", "x")

    def setup_run(
        self,
        root,
        *,
        writer_outputs,
        triage_outputs,
        specialist_outputs=None,
        selector_outputs=None,
        reviser_outputs=None,
        verifier_outputs=None,
        mode="balanced",
        shadow=None,
        events=None,
    ):
        for name in (
            "reviewer_integrated",
            "reviewer_verifier",
            "reviewer_continuity",
            "reviewer_character",
            "reviewer_craft",
            "reviewer_anti_slop",
            "selector",
        ):
            source = Path.cwd() / f"agents/{name}.md"
            (root / f"agents/{name}.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        for name in ("integrated_review", "revision_verification", "specialist_review", "ballot"):
            source = Path.cwd() / f"schemas/{name}.schema.json"
            (root / f"schemas/{name}.schema.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        prose = QueueProvider(
            [*writer_outputs, *(reviser_outputs or [])],
            "gpt-5.6-sol",
            "medium",
            barrier_size=len(writer_outputs) if len(writer_outputs) == 2 else 0,
        )
        triage = QueueProvider(triage_outputs, "gpt-5.6-terra", "medium")
        specialist_provider = QueueProvider(
            specialist_outputs or [], "gpt-5.6-terra", "high"
        )
        selector_provider = QueueProvider(
            selector_outputs or [], "gpt-5.6-sol", "medium"
        )
        verifier_provider = QueueProvider(
            verifier_outputs or [], "gpt-5.6-terra", "high"
        )
        planner = QueueProvider(["{}"], "gpt-5.6-terra", "medium")
        providers = {
            "planner": planner,
            "prose": prose,
            "triage": triage,
            "specialist": specialist_provider,
            "selector": selector_provider,
            "verifier": verifier_provider,
        }
        routes = {
            "planner": "planner",
            "candidate_writer": "prose",
            "integrated_reviewer": "triage",
            "continuity_reviewer": "specialist",
            "character_reviewer": "specialist",
            "craft_reviewer": "specialist",
            "anti_slop_reviewer": "specialist",
            "selector": "selector",
            "reviser": "prose",
            "verifier": "verifier",
            "verifier_complex": "verifier",
        }
        router = StageModelRouter(providers, routes)
        state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
        plan = json.loads(valid_plan_json())
        context = {"canon_facts": [], "era_bans": {"characters": ["周正"], "terms": ["负责人"]}}
        workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
        manifest = RunManifest.create(workspace, 1, fingerprint(state), call_limit=10, mode=mode)
        caller = ModelCallExecutor(
            router, manifest, None if events is None else events.append
        )
        caller.call("planner", "规划", None, "PLAN")
        config = {
            "quality_evolution": {
                "weights": {"continuity": 0.3, "character": 0.25, "craft": 0.3, "anti_slop": 0.15},
                "candidate_floors": {"continuity": 85, "character": 75, "craft": 75, "anti_slop": 80},
                "auto_promote": {"weighted_score": 85, "continuity": 90, "character": 82, "craft": 82, "anti_slop": 82, "ballot_votes": 1},
                "manual_floor": 78,
                "selector_score_gap": 4,
            }
        }
        engine = QualityEvolutionEngine(
            root, router, config, caller, mode=mode, shadow_dimension=shadow
        )
        result = engine.run(
            state=state,
            plan=plan,
            recent=[],
            planner_context=context,
            workspace=workspace,
            manifest=manifest,
        )
        return result, manifest, workspace, providers

    def test_balanced_happy_path_uses_five_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, manifest, _, providers = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    integrated({"continuity": 95, "character": 90, "craft": 92, "anti_slop": 90}),
                    integrated({"continuity": 91, "character": 81, "craft": 81, "anti_slop": 86}),
                ],
            )
            self.assertEqual(manifest.data["budget"]["spent"], 5)
            self.assertEqual(result.scorecard["scores"]["continuity"], 95)
            self.assertEqual(providers["prose"].max_active, 2)
            self.assertEqual(providers["selector"].calls, 0)

    def test_failed_candidate_is_not_retried_and_degradation_is_explained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, _, _, providers = self.setup_run(
                root,
                writer_outputs=[ProviderError("boom"), valid_draft()],
                triage_outputs=[integrated({"continuity": 91, "character": 82, "craft": 82, "anti_slop": 86})],
                specialist_outputs=[specialist("continuity", 92)],
            )
            self.assertEqual(providers["prose"].calls, 2)
            self.assertTrue(result.decision["degraded"])
            self.assertTrue(result.decision["generation_degraded"])
            self.assertFalse(result.decision["evaluation_degraded"])
            self.assertTrue(result.decision["automatic_retry_skipped_reason"])
            self.assertTrue(result.decision["recommended_actions"])

    def test_static_hard_fail_is_content_degraded_not_review_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            hard_fail_draft = valid_draft() + "\n周正站在门外。"
            result, _, _, _ = self.setup_run(
                root,
                writer_outputs=[hard_fail_draft, valid_draft()],
                triage_outputs=[
                    integrated(
                        {
                            "continuity": 91,
                            "character": 82,
                            "craft": 82,
                            "anti_slop": 86,
                        }
                    )
                ],
                specialist_outputs=[specialist("continuity", 92)],
            )
            candidate = next(
                item
                for item in result.decision["candidates"].values()
                if item["classification"] == "HARD_FAIL"
            )
            self.assertEqual(candidate["content_status"], "HARD_FAIL")
            self.assertEqual(candidate["review_status"], "SKIPPED")
            self.assertTrue(result.decision["content_degraded"])
            self.assertFalse(result.decision["evaluation_degraded"])
            failure = next(
                item
                for item in result.decision["failures"]
                if item["failure_kind"] == "CONTENT_HARD_FAIL"
            )
            self.assertIsNone(failure["diagnostic_artifact"])

    def test_invalid_integrated_evidence_preserves_raw_and_is_not_content_hard_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            events = []
            bad = integrated(
                {
                    "continuity": 95,
                    "character": 90,
                    "craft": 92,
                    "anti_slop": 90,
                }
            ).replace("门板上的灰", "正文里不存在的句子")
            result, _, workspace, _ = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    bad,
                    integrated(
                        {
                            "continuity": 91,
                            "character": 82,
                            "craft": 82,
                            "anti_slop": 86,
                        }
                    ),
                ],
                specialist_outputs=[specialist("continuity", 92)],
                events=events,
            )
            candidate = next(
                item
                for item in result.decision["candidates"].values()
                if item["classification"] == "REVIEW_INVALID"
            )
            self.assertEqual(candidate["content_status"], "VALID")
            self.assertEqual(candidate["review_status"], "INVALID")
            self.assertEqual(candidate["failure_kind"], "EVIDENCE_VALIDATION")
            self.assertTrue(workspace.exists(candidate["diagnostic_artifact"]))
            self.assertIn(
                "正文里不存在的句子",
                workspace.read_text(candidate["diagnostic_artifact"]),
            )
            self.assertFalse(result.decision["generation_degraded"])
            self.assertTrue(result.decision["evaluation_degraded"])
            self.assertTrue(result.decision["degraded"])
            self.assertIn(
                "无效审查", result.decision["automatic_retry_skipped_reason"]
            )
            kinds = [event["kind"] for event in events]
            self.assertIn("CALL_COMPLETED", kinds)
            self.assertIn("ARTIFACT_INVALID", kinds)
            self.assertLess(
                kinds.index("CALL_COMPLETED"), kinds.index("ARTIFACT_INVALID")
            )

    def test_invalid_integrated_json_is_classified_as_parse_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, _, workspace, _ = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    "not-json \n\n",
                    integrated(
                        {
                            "continuity": 91,
                            "character": 82,
                            "craft": 82,
                            "anti_slop": 86,
                        }
                    ),
                ],
                specialist_outputs=[specialist("continuity", 92)],
            )
            candidate = next(
                item
                for item in result.decision["candidates"].values()
                if item["classification"] == "REVIEW_INVALID"
            )
            self.assertEqual(candidate["failure_kind"], "PARSE_ERROR")
            self.assertEqual(
                workspace.read_text(candidate["diagnostic_artifact"]),
                "not-json \n\n",
            )

    def test_invalid_specialist_evidence_degrades_without_erasing_integrated_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            bad_triage = integrated(
                {
                    "continuity": 95,
                    "character": 90,
                    "craft": 92,
                    "anti_slop": 90,
                }
            ).replace("门板上的灰", "初筛不存在的句子")
            bad_specialist = specialist("continuity", 92).replace(
                "门板上的灰", "专项不存在的句子"
            )
            result, _, workspace, _ = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    bad_triage,
                    integrated(
                        {
                            "continuity": 91,
                            "character": 82,
                            "craft": 82,
                            "anti_slop": 86,
                        }
                    ),
                ],
                specialist_outputs=[bad_specialist],
            )
            self.assertTrue(result.decision["evaluation_degraded"])
            failure = next(
                item
                for item in result.decision["failures"]
                if item["stage"].startswith("specialist_")
            )
            self.assertEqual(failure["failure_kind"], "EVIDENCE_VALIDATION")
            self.assertTrue(workspace.exists(failure["diagnostic_artifact"]))
            selected = result.decision["candidates"][result.selected_id]
            self.assertEqual(selected["scorecard"]["scores"]["continuity"], 91)
            self.assertEqual(result.status, "WAITING_USER")

    def test_provider_failure_without_raw_has_null_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, _, _, _ = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    ProviderError("reviewer unavailable"),
                    integrated(
                        {
                            "continuity": 91,
                            "character": 82,
                            "craft": 82,
                            "anti_slop": 86,
                        }
                    ),
                ],
                specialist_outputs=[specialist("continuity", 92)],
            )
            candidate = next(
                item
                for item in result.decision["candidates"].values()
                if item["classification"] == "REVIEW_INVALID"
            )
            self.assertEqual(candidate["failure_kind"], "PROVIDER_ERROR")
            self.assertIsNone(candidate["diagnostic_artifact"])

    def test_diagnostic_write_failure_preserves_primary_review_error(self):
        from scripts.prequel.evolution import _review_failure

        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 1, 1)
            with patch(
                "scripts.prequel.evolution.ChapterWorkspace.write_raw_text",
                side_effect=ArtifactValidationError("disk full"),
            ):
                failure = _review_failure(
                    workspace,
                    stage="triage_candidate_01",
                    failure_kind="EVIDENCE_VALIDATION",
                    message="原始证据错误",
                    diagnostic_path=(
                        "candidates/candidate_01/diagnostics/"
                        "integrated_review.invalid.txt"
                    ),
                    raw="raw",
                )
            self.assertTrue(failure.message.startswith("原始证据错误"))
            self.assertIn("disk full", failure.message)
            self.assertIsNone(failure.diagnostic_artifact)

    def test_close_scores_use_exactly_one_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, manifest, _, providers = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    integrated({"continuity": 94, "character": 88, "craft": 89, "anti_slop": 88}),
                    integrated({"continuity": 92, "character": 86, "craft": 87, "anti_slop": 87}),
                ],
                selector_outputs=[ballot("A")],
            )
            self.assertEqual(providers["selector"].calls, 1)
            self.assertEqual(manifest.data["budget"]["spent"], 6)
            self.assertEqual(result.selected_id, "candidate_01")

    def test_fast_mode_uses_three_calls_and_waits_for_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            result, manifest, _, _ = self.setup_run(
                root,
                writer_outputs=[valid_draft()],
                triage_outputs=[integrated({"continuity": 95, "character": 90, "craft": 92, "anti_slop": 90})],
                mode="fast",
            )
            self.assertEqual(manifest.data["budget"]["spent"], 3)
            self.assertEqual(result.status, "WAITING_USER")

    def test_full_path_stops_at_ten_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            issue = {
                "dimension": "craft",
                "code": "FIX_PACING",
                "quote": "门板上的灰",
                "instruction": "收紧节奏",
                "acceptance": "场景推进更利落",
            }
            requests = [{"dimension": "continuity", "reason": "复核", "decision_impact": "选择"}]
            result, manifest, workspace, providers = self.setup_run(
                root,
                writer_outputs=[valid_draft(), valid_draft()],
                triage_outputs=[
                    integrated({"continuity": 92, "character": 86, "craft": 87, "anti_slop": 87}, revisions=[issue], requests=requests),
                    integrated({"continuity": 91, "character": 85, "craft": 86, "anti_slop": 86}, revisions=[issue], requests=requests),
                ],
                specialist_outputs=[specialist("continuity", 93), specialist("continuity", 92)],
                selector_outputs=[ballot("A")],
                reviser_outputs=[valid_draft()],
                verifier_outputs=[verification()],
            )
            self.assertEqual(manifest.data["budget"]["spent"], 10)
            self.assertEqual(providers["selector"].calls, 1)
            self.assertTrue(workspace.exists("revisions/round_01/verification.json"))
            self.assertNotEqual(result.status, "BUDGET_EXHAUSTED")


if __name__ == "__main__":
    unittest.main()
