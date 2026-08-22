import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.orchestrator import build_parser
from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError, QualityGateError
from scripts.prequel.model_router import StageModelRouter
from scripts.prequel.pipeline import (
    accept_dry_run,
    import_manual_candidate,
    review_manual_candidate,
)
from scripts.prequel.state_settlement import expected_state_changes
from tests.test_pipeline import (
    FakeProvider,
    blind_reader_json,
    make_project_fixture,
    review_json,
    state_settlement_json,
    valid_draft,
    valid_plan_json,
)


def bound_review_json(draft: str, *, digest: str | None = None) -> str:
    review = json.loads(review_json("PASS"))
    review["draft_sha256"] = digest or hashlib.sha256(
        draft.encode("utf-8")
    ).hexdigest()
    return json.dumps(review, ensure_ascii=False)


def retryable_reader_draft() -> str:
    return valid_draft().replace(
        "灰正落在张洞手上。",
        "灰正落在张洞手上。门闩仍在原处。",
        1,
    )


def reader_report_with_omitted_middle_turn(draft: str) -> str:
    report = json.loads(blind_reader_json(draft))
    report["pacing_diagnostics"]["pressure_turns"] = [
        {"quote": "灰正落在张洞手上。", "effect": "异常进入目标。"},
        {"quote": "门闩仍在原处。", "effect": "既有防线暂时未变。"},
        {
            "quote": "天黑前，那层灰到了门内。",
            "effect": "家门防线失效。",
        },
    ]
    report["pacing_diagnostics"]["max_pressure_gap_chars"] = 650
    return json.dumps(report, ensure_ascii=False)


FALSE_BOUNDARY_QUOTE = '“我上船，钱照旧押着。”'
FALSE_SOURCE_QUOTE = "张洞看见门板上的灰。天黑前，那层灰到了门内。"
FALSE_READER_EVIDENCE_QUOTE = (
    "灰正落在张洞手上。天黑前，那层灰到了门内。"
)
FALSE_BENCHMARK_QUOTE = "灰已经到了门内"


def quote_only_reader_draft() -> str:
    return valid_draft().replace(
        "灰正落在张洞手上。",
        "张洞看见门板上的灰。灰正落在张洞手上。",
        1,
    )


def reader_report_with_quote_only_errors(draft: str) -> str:
    report = json.loads(blind_reader_json(draft))
    report["mechanism_audit"]["pov_source_ledger"][0][
        "source_quote"
    ] = FALSE_SOURCE_QUOTE
    report["evidence"][2]["quote"] = FALSE_READER_EVIDENCE_QUOTE
    return json.dumps(report, ensure_ascii=False)


def repaired_quote_only_reader_report(draft: str) -> str:
    report = json.loads(reader_report_with_quote_only_errors(draft))
    report["mechanism_audit"]["pov_source_ledger"][0][
        "source_quote"
    ] = "张洞看见门板上的灰。"
    report["evidence"][2]["quote"] = "天黑前，那层灰到了门内。"
    return json.dumps(report, ensure_ascii=False)


def reader_report_with_false_benchmark_quote(draft: str) -> str:
    report = json.loads(blind_reader_json(draft))
    report["benchmark_comparison"]["active_threat"][
        "quote"
    ] = FALSE_BENCHMARK_QUOTE
    return json.dumps(report, ensure_ascii=False)


def repaired_benchmark_quote_reader_report(draft: str) -> str:
    report = json.loads(reader_report_with_false_benchmark_quote(draft))
    report["benchmark_comparison"]["active_threat"]["quote"] = "到了门内"
    return json.dumps(report, ensure_ascii=False)


def reader_report_with_unique_prefix_copy_errors(draft: str) -> str:
    report = json.loads(blind_reader_json(draft))
    report["pacing_diagnostics"]["pressure_turns"][1]["quote"] = (
        "屋内张洞把簸箕移到门边。"
    )
    report["benchmark_comparison"]["active_threat"]["quote"] = (
        "门外天黑前，那层灰到了门内。"
    )
    return json.dumps(report, ensure_ascii=False)


def reader_report_with_gap_and_false_boundary_quote(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["mechanism_audit"]["boundary_action_ledger"][0][
        "after_quote"
    ] = FALSE_BOUNDARY_QUOTE
    return json.dumps(report, ensure_ascii=False)


def repaired_reader_report(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["pacing_diagnostics"]["pressure_turns"].insert(
        2,
        {
            "quote": "张洞把簸箕移到门边。",
            "effect": "张洞改变处置位置并接手现场。",
        },
    )
    return json.dumps(report, ensure_ascii=False)


def repaired_false_boundary_quote_report(draft: str) -> str:
    report = json.loads(reader_report_with_gap_and_false_boundary_quote(draft))
    valid = json.loads(blind_reader_json(draft))
    report["mechanism_audit"]["boundary_action_ledger"][0][
        "after_quote"
    ] = valid["mechanism_audit"]["boundary_action_ledger"][0]["after_quote"]
    return json.dumps(report, ensure_ascii=False)


def retroactive_source_gap_reader_draft() -> str:
    return retryable_reader_draft().replace(
        "灰正落在张洞手上。",
        "张洞看见门板上的灰。灰正落在张洞手上。",
        1,
    )


def reader_report_with_gap_and_retroactive_source(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["mechanism_audit"]["pov_source_ledger"][0][
        "source_quote"
    ] = "天黑前，那层灰到了门内。"
    return json.dumps(report, ensure_ascii=False)


def repaired_gap_and_retroactive_source_report(draft: str) -> str:
    report = json.loads(reader_report_with_gap_and_retroactive_source(draft))
    report["mechanism_audit"]["pov_source_ledger"][0][
        "source_quote"
    ] = "张洞看见门板上的灰。"
    return json.dumps(report, ensure_ascii=False)


FALSE_PACING_QUOTE = "张洞把簸箕挪到门边。"
TRUE_PACING_QUOTE = "张洞把簸箕移到门边。"
FALSE_PACING_BOUNDARY_QUOTE = "门闩已经落在原处。"


def reader_report_with_gap_and_pacing_quote_errors(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["pacing_diagnostics"]["first_costly_choice"] = {
        "quote": FALSE_PACING_QUOTE,
        "position_percent": 50.0,
        "effect": "张洞改变处置位置并接手现场。",
    }
    report["pacing_diagnostics"]["pressure_turns"].insert(
        2,
        {
            "quote": FALSE_PACING_QUOTE,
            "effect": "张洞改变处置位置并接手现场。",
        },
    )
    report["mechanism_audit"]["boundary_action_ledger"][0][
        "before_quote"
    ] = FALSE_PACING_BOUNDARY_QUOTE
    return json.dumps(report, ensure_ascii=False)


def repaired_gap_and_pacing_quote_report(draft: str) -> str:
    report = json.loads(reader_report_with_gap_and_pacing_quote_errors(draft))
    report["pacing_diagnostics"]["first_costly_choice"][
        "quote"
    ] = TRUE_PACING_QUOTE
    report["pacing_diagnostics"]["pressure_turns"][2][
        "quote"
    ] = TRUE_PACING_QUOTE
    report["mechanism_audit"]["boundary_action_ledger"][0][
        "before_quote"
    ] = "门闩仍在原处。"
    return json.dumps(report, ensure_ascii=False)


def factual_gap_reader_draft() -> str:
    title, body = retryable_reader_draft().split("\n", 1)
    return (
        title
        + "\n门外有人说：“银簪在包里。”\n"
        + body
        + "\n张洞看着门外的人说：“先把银簪递进来。”\n"
    )


def reader_report_with_gap_and_factual_overclaim(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["reader_recap"]["current_goal"] = "张洞要处置门外持有银簪的人。"
    return json.dumps(report, ensure_ascii=False)


def repaired_gap_and_factual_reader_report(draft: str) -> str:
    report = json.loads(reader_report_with_gap_and_factual_overclaim(draft))
    report["reader_recap"]["current_goal"] = (
        "张洞要处置门外声称银簪在包里、但尚未递入的人。"
    )
    return json.dumps(report, ensure_ascii=False)


def revised_reader_report(draft: str) -> str:
    report = json.loads(reader_report_with_omitted_middle_turn(draft))
    report["verdict"] = "REVISE"
    report["blocking_issues"] = [
        {
            "code": "PACE-GAP",
            "quote": "门闩仍在原处。",
            "reader_question": "中段是否产生了新的选择？",
            "explanation": "当前报告无法确认中段存在有效压力变化。",
        }
    ]
    report["revision_instructions"] = ["让中段选择变化在正文中更可辨认。"]
    return json.dumps(report, ensure_ascii=False)


def state_settlement_with_missing_required(
    state: dict, plan: dict, draft: str
) -> tuple[str, dict]:
    report = json.loads(state_settlement_json(state, plan, draft))
    obligation = next(
        item
        for item in expected_state_changes(state, plan)
        if item["required_for_promotion"] is True
    )
    report["change_evidence"] = [
        item
        for item in report["change_evidence"]
        if item["path"] != obligation["path"]
    ]
    report["missing_changes"] = [obligation["path"]]
    return json.dumps(report, ensure_ascii=False), obligation


def repaired_state_settlement_for_obligation(
    state: dict, plan: dict, draft: str, obligation: dict
) -> str:
    report = json.loads(state_settlement_json(state, plan, draft))
    row = next(
        item
        for item in report["change_evidence"]
        if item["path"] == obligation["path"]
    )
    row["quote"] = "天黑前，那层灰到了门内。"
    return json.dumps(report, ensure_ascii=False)


class RecordingFakeProvider(FakeProvider):
    def __init__(self, outputs):
        super().__init__(outputs)
        self.prompts: list[str] = []

    def generate(self, prompt, output_schema=None):
        self.prompts.append(prompt)
        return super().generate(prompt, output_schema)


class ManualCandidateWorkflowTests(unittest.TestCase):
    def _source_attempt(self, root: Path) -> ChapterWorkspace:
        workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
        workspace.write_json("plan.json", json.loads(valid_plan_json()))
        workspace.write_text("draft.txt", valid_draft())
        workspace.write_json(
            "semantic_review.json", {"verdict": "PASS", "source": "stale"}
        )
        return workspace

    def _enable_final_gates(self, root: Path) -> None:
        config_path = root / "config/prequel_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["quality_gates"].update(
            {
                "blind_reader_gate": {"enabled": True},
                "state_evidence_gate": {"enabled": True},
            }
        )
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (root / "agents/reader_reviewer.md").write_text(
            "盲读。", encoding="utf-8"
        )
        (root / "agents/state_settler.md").write_text(
            "结算。", encoding="utf-8"
        )
        (root / "schemas/reader_review.schema.json").write_text(
            "{}", encoding="utf-8"
        )
        (root / "schemas/state_settlement.schema.json").write_text(
            "{}", encoding="utf-8"
        )

    def _enable_reader_only(self, root: Path) -> None:
        config_path = root / "config/prequel_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["quality_gates"].update(
            {
                "blind_reader_gate": {"enabled": True},
                "state_evidence_gate": {"enabled": False},
            }
        )
        config_path.write_text(json.dumps(config), encoding="utf-8")
        (root / "agents/reader_reviewer.md").write_text(
            "盲读。", encoding="utf-8"
        )
        (root / "schemas/reader_review.schema.json").write_text(
            "{}", encoding="utf-8"
        )

    def test_import_review_accept_is_hash_bound_and_auditable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            source_draft_hash = source.digest("draft.txt")

            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            self.assertEqual(imported.workspace.name, "attempt_02")
            self.assertEqual(source.digest("draft.txt"), source_draft_hash)
            self.assertFalse((imported.workspace / "semantic_review.json").exists())
            self.assertFalse((imported.workspace / "integrated_review.json").exists())
            manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["mode"], "manual_import")
            self.assertEqual(manifest["manual_import"]["source_sha256"], source_draft_hash)
            self.assertEqual(manifest["manual_import"]["plan_validation"], "PASS")
            self.assertEqual(manifest["budget"]["spent"], 0)

            draft = (imported.workspace / "draft.txt").read_text(encoding="utf-8")
            reviewed = review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider([bound_review_json(draft)])
                ),
            )
            self.assertEqual(reviewed.semantic_review["verdict"], "PASS")
            self.assertEqual(
                reviewed.semantic_review["draft_sha256"],
                hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            )
            manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["budget"]["spent"], 1)
            call = next(iter(manifest["budget"]["calls"].values()))
            self.assertEqual(call["stage"], "manual_semantic_reviewer")
            self.assertEqual(call["reason_code"], "MANUAL_SEMANTIC_REVIEW")
            self.assertEqual(
                manifest["stages"]["manual_semantic_review"]["call_count"], 1
            )

            accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)
            final_manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final_manifest["status"], "COMPLETED")
            self.assertIsNone(final_manifest["waiting_reason"])
            self.assertTrue(
                (root / "novel/chapters/vol_01/chapter_001.txt").exists()
            )

    def test_accept_rejects_tampered_imported_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft_path = imported.workspace / "draft.txt"
            draft = draft_path.read_text(encoding="utf-8")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider([bound_review_json(draft)])
                ),
            )
            draft_path.write_text(draft + "\n篡改。\n", encoding="utf-8")

            with self.assertRaises(ArtifactValidationError):
                accept_dry_run(root, attempt=2)
            self.assertFalse(
                (root / "novel/chapters/vol_01/chapter_001.txt").exists()
            )

    def test_manual_import_contract_is_required_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            manifest_path = imported.workspace / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["manual_import"]["manual_review_contract"]
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            provider = RecordingFakeProvider([])

            with self.assertRaises(ArtifactValidationError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            self.assertEqual(provider.prompts, [])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["budget"]["spent"], 0)

    def test_manual_review_rejects_gate_budget_drift_before_any_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            self._enable_final_gates(root)
            provider = RecordingFakeProvider([])

            with self.assertRaises(ArtifactValidationError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            self.assertEqual(provider.prompts, [])
            manifest = json.loads(
                imported.workspace.joinpath("run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["budget"]["limit"], 1)
            self.assertEqual(manifest["budget"]["spent"], 0)

    def test_manual_review_runs_all_gates_and_accept_makes_no_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = (imported.workspace / "draft.txt").read_text(encoding="utf-8")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = json.loads(
                (imported.workspace / "plan.json").read_text(encoding="utf-8")
            )
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            blind_reader_json(draft),
                            state_settlement_json(state, plan, draft),
                        ]
                    )
                ),
            )
            manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(manifest["budget"]["remaining"], 2)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "COMPLETED",
            )
            self.assertEqual(
                manifest["stages"]["manual_state_settlement"]["status"],
                "COMPLETED",
            )

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)
            self.assertTrue((imported.workspace / "reader_review.json").exists())
            self.assertTrue((imported.workspace / "state_settlement.json").exists())

    def test_state_settlement_missing_required_gets_one_bounded_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            missing_report, obligation = state_settlement_with_missing_required(
                state, plan, draft
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    blind_reader_json(draft),
                    missing_report,
                    repaired_state_settlement_for_obligation(
                        state, plan, draft, obligation
                    ),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            manifest = workspace.read_json("run_manifest.json")
            diagnostic = workspace.read_json(
                "state_settlement.validation.json"
            )
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertTrue(diagnostic["retry_performed"])
            self.assertEqual(
                diagnostic["feedback_prompt_version"],
                "manual-state-settlement-missing-evidence-feedback",
            )
            self.assertEqual(
                diagnostic["required_missing_paths"],
                [obligation["path"]],
            )
            self.assertEqual(
                diagnostic["missing_obligations"][0]["value"],
                obligation["value"],
            )
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 4)
            self.assertEqual(manifest["budget"]["remaining"], 1)
            state_stage = manifest["stages"]["manual_state_settlement"]
            self.assertEqual(state_stage["status"], "COMPLETED")
            self.assertEqual(state_stage["call_count"], 2)
            self.assertTrue(
                {
                    "state_settlement.first.raw.txt",
                    "state_settlement.first.canonical.json",
                    "state_settlement.validation.json",
                    "state_settlement.retry.raw.txt",
                    "state_settlement.final.canonical.json",
                    "state_settlement.json",
                }.issubset(state_stage["outputs"])
            )
            calls = list(manifest["budget"]["calls"].values())
            self.assertEqual(
                [item["reason_code"] for item in calls[-2:]],
                [
                    "MANUAL_STATE_SETTLEMENT",
                    "MANUAL_STATE_SETTLEMENT_MISSING_EVIDENCE_FEEDBACK",
                ],
            )
            feedback_prompt = provider.prompts[3]
            self.assertIn("MISSING_REQUIRED_STATE_EVIDENCE_FEEDBACK", feedback_prompt)
            self.assertIn(obligation["path"], feedback_prompt)
            self.assertIn(obligation["value"], feedback_prompt)
            self.assertIn("唯一、连续、逐字存在于draft", feedback_prompt)
            self.assertIn("INSUFFICIENT_EVIDENCE", feedback_prompt)

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_state_settlement_feedback_still_missing_fails_without_third_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            missing_report, obligation = state_settlement_with_missing_required(
                state, plan, draft
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    blind_reader_json(draft),
                    missing_report,
                    missing_report,
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(len(provider.prompts), 4)
            self.assertEqual(manifest["budget"]["spent"], 4)
            self.assertEqual(manifest["budget"]["remaining"], 1)
            self.assertEqual(
                manifest["stages"]["manual_state_settlement"]["status"],
                "FAILED",
            )
            final_diagnostic = workspace.read_json(
                "state_settlement.final.validation.json"
            )
            self.assertIn(
                "SETTLEMENT_PASS_WITH_GAPS",
                [item["code"] for item in final_diagnostic["p1_issues"]],
            )

    def test_state_settlement_other_p1_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            missing_raw, _ = state_settlement_with_missing_required(
                state, plan, draft
            )
            report = json.loads(missing_raw)
            report["change_evidence"][0]["quote"] = "正文不存在的证据"
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    blind_reader_json(draft),
                    json.dumps(report, ensure_ascii=False),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            diagnostic = workspace.read_json(
                "state_settlement.validation.json"
            )
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertIn(
                "SETTLEMENT_FALSE_EVIDENCE",
                [item["code"] for item in diagnostic["p1_issues"]],
            )
            self.assertEqual(len(provider.prompts), 3)
            self.assertFalse(workspace.exists("state_settlement.retry.raw.txt"))

    def test_accept_rejects_tampered_state_settlement_first_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            missing_report, obligation = state_settlement_with_missing_required(
                state, plan, draft
            )
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            blind_reader_json(draft),
                            missing_report,
                            repaired_state_settlement_for_obligation(
                                state, plan, draft, obligation
                            ),
                        ]
                    )
                ),
            )

            raw = json.loads(
                workspace.read_text("state_settlement.first.raw.txt")
            )
            raw["reader_visible_summary"]["core"] = "被篡改的首报摘要"
            workspace.write_raw_text(
                "state_settlement.first.raw.txt",
                json.dumps(raw, ensure_ascii=False),
            )
            manifest = workspace.read_json("run_manifest.json")
            manifest["stages"]["manual_state_settlement"]["outputs"][
                "state_settlement.first.raw.txt"
            ] = workspace.digest("state_settlement.first.raw.txt")
            workspace.write_json("run_manifest.json", manifest)

            with self.assertRaisesRegex(
                ArtifactValidationError,
                "首报原始输出与规范化报告不一致",
            ):
                accept_dry_run(root, attempt=2)

    def test_reader_gap_is_diagnostic_and_accepts_without_feedback_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = imported.workspace.joinpath("draft.txt").read_text(
                encoding="utf-8"
            )
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = json.loads(
                imported.workspace.joinpath("plan.json").read_text(
                    encoding="utf-8"
                )
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_omitted_middle_turn(draft),
                    state_settlement_json(state, plan, draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            manifest = json.loads(
                imported.workspace.joinpath("run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(provider.prompts), 3)
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(manifest["budget"]["remaining"], 2)
            self.assertEqual(manifest["budget"]["active"], [])
            calls = list(manifest["budget"]["calls"].values())
            self.assertEqual(
                [item["stage"] for item in calls],
                [
                    "manual_semantic_reviewer",
                    "blind_reader_reviewer",
                    "state_settler",
                ],
            )
            self.assertTrue(all(item["status"] == "COMPLETED" for item in calls))
            reader_stage = manifest["stages"]["manual_blind_reader_review"]
            self.assertEqual(reader_stage["status"], "COMPLETED")
            self.assertEqual(reader_stage["call_count"], 1)
            self.assertNotIn("reader_review.retry.raw.txt", reader_stage["outputs"])
            diagnostic = json.loads(
                imported.workspace.joinpath(
                    "reader_review.validation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertFalse(diagnostic["retry_performed"])
            gap = diagnostic["pacing_normalization"]["over_limit_gaps"][0]
            self.assertEqual(gap["gap_chars"], 1097)
            self.assertEqual(gap["start_quote"], "门闩仍在原处。")
            self.assertEqual(gap["end_quote"], "天黑前，那层灰到了门内。")
            first_raw = json.loads(
                imported.workspace.joinpath(
                    "reader_review.first.raw.txt"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                first_raw["pacing_diagnostics"]["max_pressure_gap_chars"],
                650,
            )
            final_reader = json.loads(
                imported.workspace.joinpath("reader_review.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(final_reader["verdict"], "PASS")
            self.assertEqual(
                final_reader["pacing_diagnostics"]["max_pressure_gap_chars"],
                1097,
            )
            self.assertTrue(
                imported.workspace.joinpath("state_settlement.json").exists()
            )

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_gap_and_false_boundary_quote_get_one_bounded_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_false_boundary_quote(draft),
                    repaired_false_boundary_quote_report(draft),
                    state_settlement_json(state, plan, draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            manifest = workspace.read_json("run_manifest.json")
            diagnostic = workspace.read_json("reader_review.validation.json")
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertTrue(diagnostic["retry_performed"])
            self.assertEqual(
                diagnostic["feedback_prompt_version"],
                "manual-blind-reader-quote-only-feedback",
            )
            self.assertEqual(diagnostic["feedback_components"], ["QUOTE"])
            self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
            self.assertEqual(
                diagnostic["repairable_quote_issues"],
                [
                    {
                        "code": "SCENE_FALSE_BOUNDARY_QUOTE",
                        "field_path": (
                            "mechanism_audit.boundary_action_ledger"
                            "[0].after_quote"
                        ),
                        "ledger_field": "boundary_action_ledger",
                        "anchor_id": "BOUNDARY-001",
                        "item_index": 0,
                        "quote_field": "after_quote",
                        "invalid_quote": FALSE_BOUNDARY_QUOTE,
                    }
                ],
            )
            calls = list(manifest["budget"]["calls"].values())
            self.assertEqual(
                [item["reason_code"] for item in calls],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
                    "MANUAL_STATE_SETTLEMENT",
                ],
            )
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 4)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["call_count"],
                2,
            )
            feedback_prompt = provider.prompts[2]
            self.assertIn("QUOTE_ONLY_VALIDATION_FEEDBACK", feedback_prompt)
            self.assertIn("BOUNDARY-001", feedback_prompt)
            self.assertIn(FALSE_BOUNDARY_QUOTE, feedback_prompt)
            self.assertIn("不得删除该条目或引文字段", feedback_prompt)
            self.assertIn("不得改成null、空串", feedback_prompt)
            self.assertIn(
                "reader_recap、mechanism_audit.first_read_reconstruction",
                feedback_prompt,
            )
            self.assertIn(
                "adversarial_checks.unsupported_recap_claims",
                feedback_prompt,
            )
            self.assertIn("已经取得、已经递入或已经证实", feedback_prompt)
            self.assertIn("不得把尚未确认身份的人称为已确认身份", feedback_prompt)
            self.assertIn("本次没有压力空档反馈", feedback_prompt)
            self.assertEqual(workspace.read_json("reader_review.json")["verdict"], "PASS")

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_gap_and_retroactive_source_get_one_bounded_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retroactive_source_gap_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_retroactive_source(draft),
                    repaired_gap_and_retroactive_source_report(draft),
                    state_settlement_json(state, plan, draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            self.assertEqual(diagnostic["feedback_components"], ["QUOTE"])
            self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
            self.assertEqual(diagnostic["factual_escalations"], [])
            self.assertEqual(
                diagnostic["repairable_quote_issues"],
                [
                    {
                        "code": "SCENE_RETROACTIVE_POV_SOURCE",
                        "field_path": (
                            "mechanism_audit.pov_source_ledger[0].source_quote"
                        ),
                        "ledger_field": "pov_source_ledger",
                        "anchor_id": "POV-001",
                        "item_index": 0,
                        "quote_field": "source_quote",
                        "invalid_quote": "天黑前，那层灰到了门内。",
                        "claim_end": 20,
                        "constraint": "source_must_not_follow_claim",
                    }
                ],
            )
            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(
                [
                    item["reason_code"]
                    for item in manifest["budget"]["calls"].values()
                ],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
                    "MANUAL_STATE_SETTLEMENT",
                ],
            )
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["call_count"],
                2,
            )
            self.assertIn(
                "SCENE_RETROACTIVE_POV_SOURCE",
                provider.prompts[2],
            )
            self.assertIn("不得继续引用事后说明", provider.prompts[2])
            final = workspace.read_json("reader_review.json")
            self.assertEqual(
                final["mechanism_audit"]["pov_source_ledger"][0]["source_quote"],
                "张洞看见门板上的灰。",
            )

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_retroactive_source_retry_must_move_quote_before_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_reader_only(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retroactive_source_gap_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            still_retroactive = json.loads(
                repaired_gap_and_retroactive_source_report(draft)
            )
            still_retroactive["mechanism_audit"]["pov_source_ledger"][0][
                "source_quote"
            ] = "张洞看见门板上的灰。灰正落在张洞手上。"
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_retroactive_source(draft),
                    json.dumps(still_retroactive, ensure_ascii=False),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(manifest["budget"]["active"], [])
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "FAILED",
            )
            self.assertFalse(workspace.exists("state_settlement.json"))

    def test_reader_gap_and_factual_feedback_is_scoped_hash_bound_and_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_gates"].update(
                {
                    "blind_reader_gate": {"enabled": True},
                    "state_evidence_gate": {"enabled": False},
                }
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "agents/reader_reviewer.md").write_text(
                "盲读。", encoding="utf-8"
            )
            (root / "schemas/reader_review.schema.json").write_text(
                "{}", encoding="utf-8"
            )
            source = self._source_attempt(root)
            source.write_text("draft.txt", factual_gap_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_factual_overclaim(draft),
                    repaired_gap_and_factual_reader_report(draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            self.assertEqual(diagnostic["feedback_components"], ["FACTUAL"])
            self.assertEqual(diagnostic["feedback_kind"], "FACTUAL_ONLY")
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertEqual(
                [item["field_path"] for item in diagnostic["factual_escalations"]],
                ["reader_recap.current_goal"],
            )
            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(manifest["budget"]["limit"], 3)
            self.assertEqual(manifest["budget"]["spent"], 3)
            calls = list(manifest["budget"]["calls"].values())
            self.assertEqual(
                [item["reason_code"] for item in calls],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_FACTUAL_FEEDBACK",
                ],
            )
            self.assertIn(
                "reader_recap.current_goal", provider.prompts[2]
            )
            self.assertIn("凭条交付写成现金已经退回", provider.prompts[2])
            self.assertEqual(
                workspace.read_json("reader_review.json")["reader_recap"]["current_goal"],
                "张洞要处置门外声称银簪在包里、但尚未递入的人。",
            )

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_factual_feedback_cannot_drift_non_target_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_gates"].update(
                {
                    "blind_reader_gate": {"enabled": True},
                    "state_evidence_gate": {"enabled": False},
                }
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "agents/reader_reviewer.md").write_text(
                "盲读。", encoding="utf-8"
            )
            (root / "schemas/reader_review.schema.json").write_text(
                "{}", encoding="utf-8"
            )
            source = self._source_attempt(root)
            source.write_text("draft.txt", factual_gap_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            drifted = json.loads(repaired_gap_and_factual_reader_report(draft))
            drifted["reading_experience"]["opening_pull"] = 5
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_factual_overclaim(draft),
                    json.dumps(drifted, ensure_ascii=False),
                ]
            )

            with self.assertRaisesRegex(
                QualityGateError, "READER_FEEDBACK_SCOPE_DRIFT"
            ):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )
            self.assertEqual(len(provider.prompts), 3)
            self.assertFalse(workspace.path.joinpath("state_settlement.json").exists())

    def test_reader_factual_feedback_still_overclaiming_stops_after_one_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_gates"].update(
                {
                    "blind_reader_gate": {"enabled": True},
                    "state_evidence_gate": {"enabled": False},
                }
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "agents/reader_reviewer.md").write_text(
                "盲读。", encoding="utf-8"
            )
            (root / "schemas/reader_review.schema.json").write_text(
                "{}", encoding="utf-8"
            )
            source = self._source_attempt(root)
            source.write_text("draft.txt", factual_gap_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            still_wrong = json.loads(repaired_gap_and_factual_reader_report(draft))
            still_wrong["reader_recap"]["current_goal"] = (
                "张洞要处置门外持有银簪的人。"
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_factual_overclaim(draft),
                    json.dumps(still_wrong, ensure_ascii=False),
                ]
            )

            with self.assertRaisesRegex(
                QualityGateError, "READER_FACT_LEVEL_OVERSTATEMENT"
            ):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )
            self.assertEqual(len(provider.prompts), 3)
            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "FAILED",
            )

    def test_reader_gap_and_pacing_quote_feedback_repairs_existing_turn_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_reader_only(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_pacing_quote_errors(draft),
                    repaired_gap_and_pacing_quote_report(draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            final_report = workspace.read_json("reader_review.json")
            manifest = workspace.read_json("run_manifest.json")
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertEqual(diagnostic["feedback_components"], ["QUOTE"])
            self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
            self.assertEqual(
                [item["field_path"] for item in diagnostic["repairable_quote_issues"]],
                [
                    "mechanism_audit.boundary_action_ledger[0].before_quote",
                    "pacing_diagnostics.first_costly_choice.quote",
                    "pacing_diagnostics.pressure_turns[2].quote",
                ],
            )
            self.assertEqual(
                diagnostic["pacing_normalization"]["derived_max_pressure_gap_chars"],
                1097,
            )
            self.assertEqual(
                final_report["pacing_diagnostics"]["max_pressure_gap_chars"],
                550,
            )
            self.assertEqual(
                final_report["pacing_diagnostics"]["pressure_turns"][2]["effect"],
                "张洞改变处置位置并接手现场。",
            )
            self.assertEqual(manifest["budget"]["limit"], 3)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(
                [
                    call["reason_code"]
                    for call in manifest["budget"]["calls"].values()
                ],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
                ],
            )
            self.assertIn("field、anchor_id或item_index", provider.prompts[2])
            self.assertIn("节奏参考值本身不是本次报告修复目标", provider.prompts[2])
            self.assertIn("不等于已穿过边界", provider.prompts[2])

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_gap_and_pacing_quote_feedback_rejects_bad_target_edits(self):
        mutations = {
            "still_false": lambda report: report["pacing_diagnostics"][
                "pressure_turns"
            ][2].__setitem__("quote", FALSE_PACING_QUOTE),
            "deleted": lambda report: report["pacing_diagnostics"][
                "pressure_turns"
            ].pop(2),
            "changed_effect": lambda report: report["pacing_diagnostics"][
                "pressure_turns"
            ][2].__setitem__("effect", "借反馈改写了原判断。"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = make_project_fixture(Path(tmp))
                self._enable_reader_only(root)
                source = self._source_attempt(root)
                source.write_text("draft.txt", retryable_reader_draft())
                imported = import_manual_candidate(
                    root, source.path / "draft.txt", plan_attempt=1
                )
                workspace = ChapterWorkspace(imported.workspace, 1)
                draft = workspace.read_text("draft.txt")
                retry = json.loads(repaired_gap_and_pacing_quote_report(draft))
                mutate(retry)
                provider = RecordingFakeProvider(
                    [
                        bound_review_json(draft),
                        reader_report_with_gap_and_pacing_quote_errors(draft),
                        json.dumps(retry, ensure_ascii=False),
                    ]
                )

                with self.assertRaises(QualityGateError):
                    review_manual_candidate(
                        root,
                        attempt=2,
                        router=StageModelRouter.single(provider),
                    )

                final = workspace.read_json("reader_review.final.validation.json")
                codes = {item["code"] for item in final["p1_issues"]}
                self.assertTrue(
                    codes
                    & {
                        "READER_QUOTE_FEEDBACK_NOT_REPAIRED",
                        "READER_GAP_FEEDBACK_REPLACED_TURNS",
                        "READER_FALSE_PRESSURE_TURN",
                        "READER_QUOTE_FEEDBACK_SCOPE_DRIFT",
                        "READER_FEEDBACK_SCOPE_DRIFT",
                    }
                )
                self.assertEqual(len(provider.prompts), 3)
                self.assertFalse(workspace.exists("state_settlement.json"))

    def test_accept_rebuilds_gap_and_pacing_quote_retry_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_reader_only(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            reader_report_with_gap_and_pacing_quote_errors(draft),
                            repaired_gap_and_pacing_quote_report(draft),
                        ]
                    )
                ),
            )
            retry_raw = json.loads(workspace.read_text("reader_review.retry.raw.txt"))
            retry_raw["pacing_diagnostics"]["pressure_turns"][2][
                "effect"
            ] = "篡改后的效果。"
            workspace.write_raw_text(
                "reader_review.retry.raw.txt",
                json.dumps(retry_raw, ensure_ascii=False),
            )
            manifest = workspace.read_json("run_manifest.json")
            manifest["stages"]["manual_blind_reader_review"]["outputs"][
                "reader_review.retry.raw.txt"
            ] = workspace.digest("reader_review.retry.raw.txt")
            workspace.write_json("run_manifest.json", manifest)

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ), self.assertRaises(ArtifactValidationError):
                accept_dry_run(root, attempt=2)

    def test_reader_quote_only_feedback_repairs_source_and_evidence_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_quote_only_errors(draft),
                    repaired_quote_only_reader_report(draft),
                    state_settlement_json(state, plan, draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            manifest = workspace.read_json("run_manifest.json")
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
            self.assertEqual(
                diagnostic["feedback_prompt_version"],
                "manual-blind-reader-quote-only-feedback",
            )
            self.assertEqual(
                [item["field_path"] for item in diagnostic["repairable_quote_issues"]],
                [
                    "mechanism_audit.pov_source_ledger[0].source_quote",
                    "evidence[2].quote",
                ],
            )
            self.assertEqual(
                [item["invalid_quote"] for item in diagnostic["repairable_quote_issues"]],
                [FALSE_SOURCE_QUOTE, FALSE_READER_EVIDENCE_QUOTE],
            )
            self.assertEqual(len(provider.prompts), 4)
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 4)
            self.assertEqual(
                [
                    item["reason_code"]
                    for item in manifest["budget"]["calls"].values()
                ],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
                    "MANUAL_STATE_SETTLEMENT",
                ],
            )
            feedback_prompt = provider.prompts[2]
            self.assertIn("QUOTE_ONLY_VALIDATION_FEEDBACK", feedback_prompt)
            self.assertIn(FALSE_SOURCE_QUOTE, feedback_prompt)
            self.assertIn(FALSE_READER_EVIDENCE_QUOTE, feedback_prompt)
            self.assertIn("唯一、连续、逐字存在", feedback_prompt)
            self.assertIn("不得删除该条目或引文字段", feedback_prompt)
            self.assertIn(
                "reader_recap、mechanism_audit.first_read_reconstruction",
                feedback_prompt,
            )
            self.assertIn("已经取得、已经递入或已经证实", feedback_prompt)
            self.assertIn("不得把尚未确认身份的人称为已确认身份", feedback_prompt)

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_quote_only_feedback_repairs_benchmark_quote_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_reader_only(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_false_benchmark_quote(draft),
                    repaired_benchmark_quote_reader_report(draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            manifest = workspace.read_json("run_manifest.json")
            self.assertTrue(diagnostic["retry_eligible"])
            self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
            self.assertEqual(
                [
                    (item["code"], item["field_path"])
                    for item in diagnostic["repairable_quote_issues"]
                ],
                [
                    (
                        "READER_FALSE_BENCHMARK_QUOTE",
                        "benchmark_comparison.active_threat.quote",
                    )
                ],
            )
            self.assertEqual(
                workspace.read_json("reader_review.json")[
                    "benchmark_comparison"
                ]["active_threat"]["quote"],
                "到了门内",
            )
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(
                [
                    call["reason_code"]
                    for call in manifest["budget"]["calls"].values()
                ],
                [
                    "MANUAL_SEMANTIC_REVIEW",
                    "MANUAL_BLIND_READER_REVIEW",
                    "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
                ],
            )

            class NoCallRouter:
                def provider_for(self, stage):
                    raise AssertionError(
                        f"manual accept must not call the {stage} model"
                    )

            with patch(
                "scripts.prequel.pipeline.StageModelRouter.from_config",
                return_value=NoCallRouter(),
            ):
                accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_reader_unique_prefix_copy_errors_are_canonicalized_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_reader_only(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_unique_prefix_copy_errors(draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            raw = json.loads(workspace.read_text("reader_review.first.raw.txt"))
            canonical = workspace.read_json("reader_review.first.canonical.json")
            diagnostic = workspace.read_json("reader_review.validation.json")
            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(
                raw["pacing_diagnostics"]["pressure_turns"][1]["quote"],
                "屋内张洞把簸箕移到门边。",
            )
            self.assertEqual(
                canonical["pacing_diagnostics"]["pressure_turns"][1]["quote"],
                "张洞把簸箕移到门边。",
            )
            self.assertEqual(
                canonical["benchmark_comparison"]["active_threat"]["quote"],
                "天黑前，那层灰到了门内。",
            )
            self.assertEqual(diagnostic["p1_issues"], [])
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertFalse(workspace.exists("reader_review.retry.raw.txt"))
            self.assertEqual(manifest["budget"]["spent"], 2)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["call_count"],
                1,
            )

    def test_reader_quote_only_with_other_p1_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            report = json.loads(reader_report_with_quote_only_errors(draft))
            report["adversarial_checks"]["physical_or_spatial_gaps"] = [
                "人物位置无法支持这次观察。"
            ]
            provider = RecordingFakeProvider(
                [bound_review_json(draft), json.dumps(report, ensure_ascii=False)]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            diagnostic = workspace.read_json("reader_review.validation.json")
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertIsNone(diagnostic["feedback_kind"])
            self.assertIn(
                "READER_PASS_WITH_GAPS",
                [item["code"] for item in diagnostic["p1_issues"]],
            )
            self.assertEqual(len(provider.prompts), 2)
            self.assertFalse(workspace.exists("reader_review.retry.raw.txt"))

    def test_reader_quote_only_retry_still_false_stops_without_third_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            invalid_reader = reader_report_with_quote_only_errors(draft)
            provider = RecordingFakeProvider(
                [bound_review_json(draft), invalid_reader, invalid_reader]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = workspace.read_json("run_manifest.json")
            final_diagnostic = workspace.read_json(
                "reader_review.final.validation.json"
            )
            self.assertEqual(len(provider.prompts), 3)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(manifest["budget"]["remaining"], 2)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "FAILED",
            )
            self.assertIn(
                "READER_QUOTE_FEEDBACK_NOT_REPAIRED",
                [item["code"] for item in final_diagnostic["p1_issues"]],
            )
            self.assertFalse(workspace.exists("state_settlement.json"))

    def test_reader_quote_only_retry_cannot_delete_target_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            retry = json.loads(repaired_quote_only_reader_report(draft))
            retry["evidence"].pop(2)
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_quote_only_errors(draft),
                    json.dumps(retry, ensure_ascii=False),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            final_diagnostic = workspace.read_json(
                "reader_review.final.validation.json"
            )
            final_codes = [
                item["code"] for item in final_diagnostic["p1_issues"]
            ]
            self.assertIn("READER_NO_EVIDENCE", final_codes)
            self.assertIn("READER_QUOTE_FEEDBACK_NOT_REPAIRED", final_codes)
            self.assertIn("READER_QUOTE_FEEDBACK_SCOPE_DRIFT", final_codes)
            self.assertEqual(len(provider.prompts), 3)
            self.assertFalse(workspace.exists("state_settlement.json"))

    def test_reader_quote_only_retry_cannot_change_passing_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            retry = json.loads(repaired_quote_only_reader_report(draft))
            retry["reading_experience"]["opening_pull"] = 5
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_quote_only_errors(draft),
                    json.dumps(retry, ensure_ascii=False),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            final_diagnostic = workspace.read_json(
                "reader_review.final.validation.json"
            )
            self.assertIn(
                "READER_QUOTE_FEEDBACK_SCOPE_DRIFT",
                [item["code"] for item in final_diagnostic["p1_issues"]],
            )
            self.assertEqual(len(provider.prompts), 3)
            self.assertFalse(workspace.exists("state_settlement.json"))

    def test_accept_rejects_tampered_quote_only_retry_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", quote_only_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            reader_report_with_quote_only_errors(draft),
                            repaired_quote_only_reader_report(draft),
                            state_settlement_json(state, plan, draft),
                        ]
                    )
                ),
            )

            retry_raw = json.loads(
                workspace.read_text("reader_review.retry.raw.txt")
            )
            retry_raw["reader_recap"]["current_goal"] = "篡改后的反馈摘要"
            workspace.write_raw_text(
                "reader_review.retry.raw.txt",
                json.dumps(retry_raw, ensure_ascii=False),
            )
            manifest = workspace.read_json("run_manifest.json")
            manifest["stages"]["manual_blind_reader_review"]["outputs"][
                "reader_review.retry.raw.txt"
            ] = workspace.digest("reader_review.retry.raw.txt")
            workspace.write_json("run_manifest.json", manifest)

            with self.assertRaisesRegex(
                ArtifactValidationError,
                "反馈原始输出与最终报告不一致",
            ):
                accept_dry_run(root, attempt=2)

    def test_reader_quote_feedback_cannot_drop_invalid_quote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            retry = json.loads(repaired_reader_report(draft))
            retry["mechanism_audit"]["boundary_action_ledger"][0][
                "after_quote"
            ] = None
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_gap_and_false_boundary_quote(draft),
                    json.dumps(retry, ensure_ascii=False),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(len(provider.prompts), 3)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "FAILED",
            )
            self.assertFalse(workspace.exists("state_settlement.json"))
            final_diagnostic = workspace.read_json(
                "reader_review.final.validation.json"
            )
            self.assertIn(
                "READER_QUOTE_FEEDBACK_NOT_REPAIRED",
                [item["code"] for item in final_diagnostic["p1_issues"]],
            )

    def test_accept_does_not_require_retry_for_pacing_reference_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            reader_report_with_omitted_middle_turn(draft),
                            state_settlement_json(state, plan, draft),
                        ]
                    )
                ),
            )

            diagnostic = workspace.read_json("reader_review.validation.json")
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertFalse(diagnostic["retry_performed"])
            self.assertTrue(diagnostic["pacing_normalization"]["over_limit_gaps"])
            accepted = accept_dry_run(root, attempt=2)
            self.assertTrue(accepted.promoted)

    def test_accept_rebuilds_canonical_reader_report_from_bound_raw_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = workspace.read_json("plan.json")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider(
                        [
                            bound_review_json(draft),
                            blind_reader_json(draft),
                            state_settlement_json(state, plan, draft),
                        ]
                    )
                ),
            )

            raw = json.loads(workspace.read_text("reader_review.first.raw.txt"))
            raw["reader_recap"]["current_goal"] = "篡改后的原始输出"
            workspace.write_raw_text(
                "reader_review.first.raw.txt",
                json.dumps(raw, ensure_ascii=False),
            )
            manifest = workspace.read_json("run_manifest.json")
            manifest["stages"]["manual_blind_reader_review"]["outputs"][
                "reader_review.first.raw.txt"
            ] = workspace.digest("reader_review.first.raw.txt")
            workspace.write_json("run_manifest.json", manifest)

            with self.assertRaisesRegex(
                ArtifactValidationError,
                "首报原始输出与规范化报告不一致",
            ):
                accept_dry_run(root, attempt=2)

    def test_reader_can_still_downgrade_for_actual_pacing_harm(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = imported.workspace.joinpath("draft.txt").read_text(
                encoding="utf-8"
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    revised_reader_report(draft),
                ]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = json.loads(
                imported.workspace.joinpath("run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(provider.prompts), 2)
            self.assertEqual(manifest["budget"]["spent"], 2)
            self.assertEqual(manifest["budget"]["remaining"], 3)
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "COMPLETED",
            )
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["call_count"],
                1,
            )
            self.assertEqual(
                json.loads(
                    imported.workspace.joinpath("reader_review.json").read_text(
                        encoding="utf-8"
                    )
                )["verdict"],
                "REVISE",
            )
            self.assertFalse(
                imported.workspace.joinpath("state_settlement.json").exists()
            )
            self.assertFalse(
                any(
                    call["stage"] == "state_settler"
                    for call in manifest["budget"]["calls"].values()
                )
            )

    def test_reader_gap_does_not_consume_a_feedback_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = imported.workspace.joinpath("draft.txt").read_text(
                encoding="utf-8"
            )
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            plan = json.loads(
                imported.workspace.joinpath("plan.json").read_text(encoding="utf-8")
            )
            provider = RecordingFakeProvider(
                [
                    bound_review_json(draft),
                    reader_report_with_omitted_middle_turn(draft),
                    state_settlement_json(state, plan, draft),
                    blind_reader_json(draft),
                ]
            )

            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(provider),
            )

            manifest = json.loads(
                imported.workspace.joinpath("run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(provider.prompts), 3)
            self.assertEqual(manifest["budget"]["limit"], 5)
            self.assertEqual(manifest["budget"]["spent"], 3)
            self.assertEqual(manifest["budget"]["remaining"], 2)
            self.assertEqual(manifest["budget"]["active"], [])
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["status"],
                "COMPLETED",
            )
            self.assertEqual(
                manifest["stages"]["manual_blind_reader_review"]["call_count"],
                1,
            )
            self.assertTrue(
                imported.workspace.joinpath("state_settlement.json").exists()
            )

    def test_reader_feedback_does_not_retry_any_other_p1(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._enable_final_gates(root)
            source = self._source_attempt(root)
            source.write_text("draft.txt", retryable_reader_draft())
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = imported.workspace.joinpath("draft.txt").read_text(
                encoding="utf-8"
            )
            report = json.loads(reader_report_with_omitted_middle_turn(draft))
            report["adversarial_checks"]["physical_or_spatial_gaps"] = [
                "人物无法从已写位置看见院门。"
            ]
            provider = RecordingFakeProvider(
                [bound_review_json(draft), json.dumps(report, ensure_ascii=False)]
            )

            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            manifest = json.loads(
                imported.workspace.joinpath("run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            diagnostic = json.loads(
                imported.workspace.joinpath(
                    "reader_review.validation.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(len(provider.prompts), 2)
            self.assertFalse(diagnostic["retry_eligible"])
            self.assertFalse(diagnostic["retry_performed"])
            self.assertEqual(manifest["budget"]["spent"], 2)
            self.assertFalse(
                imported.workspace.joinpath("reader_review.retry.raw.txt").exists()
            )
            self.assertFalse(
                any(
                    call["reason_code"] == "MANUAL_BLIND_READER_GAP_FEEDBACK"
                    for call in manifest["budget"]["calls"].values()
                )
            )

    def test_accept_rejects_tampered_semantic_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = (imported.workspace / "draft.txt").read_text(encoding="utf-8")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider([bound_review_json(draft)])
                ),
            )
            review_path = imported.workspace / "semantic_review.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["style_assessment"] = "手工改写后的旧审查"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

            with self.assertRaises(ArtifactValidationError):
                accept_dry_run(root, attempt=2)

    def test_completed_manual_stage_drift_fails_without_another_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            workspace = ChapterWorkspace(imported.workspace, 1)
            draft = workspace.read_text("draft.txt")
            review_manual_candidate(
                root,
                attempt=2,
                router=StageModelRouter.single(
                    FakeProvider([bound_review_json(draft)])
                ),
            )
            review = workspace.read_json("semantic_review.json")
            review["style_assessment"] = "已完成后发生漂移"
            workspace.write_json("semantic_review.json", review)
            provider = RecordingFakeProvider([])

            with self.assertRaises(ArtifactValidationError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(provider),
                )

            self.assertEqual(provider.prompts, [])
            manifest = workspace.read_json("run_manifest.json")
            self.assertEqual(manifest["budget"]["spent"], 1)
            self.assertEqual(len(manifest["budget"]["calls"]), 1)

    def test_review_rejects_wrong_draft_hash_and_records_spent_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = (imported.workspace / "draft.txt").read_text(encoding="utf-8")
            with self.assertRaises(QualityGateError):
                review_manual_candidate(
                    root,
                    attempt=2,
                    router=StageModelRouter.single(
                        FakeProvider([bound_review_json(draft, digest="0" * 64)])
                    ),
                )
            manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["budget"]["spent"], 1)
            self.assertEqual(
                manifest["stages"]["manual_semantic_review"]["status"],
                "FAILED",
            )
            self.assertFalse((imported.workspace / "semantic_review.json").exists())

    def test_repeated_import_always_creates_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            original = source.path.joinpath("semantic_review.json").read_bytes()
            first = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            second = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            self.assertEqual(first.workspace.name, "attempt_02")
            self.assertEqual(second.workspace.name, "attempt_03")
            self.assertEqual(
                source.path.joinpath("semantic_review.json").read_bytes(), original
            )

    def test_import_revalidates_source_plan_before_creating_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._source_attempt(root)
            plan = json.loads((source.path / "plan.json").read_text(encoding="utf-8"))
            plan["chapter_number"] = 2
            (source.path / "plan.json").write_text(
                json.dumps(plan, ensure_ascii=False), encoding="utf-8"
            )
            with self.assertRaises(QualityGateError):
                import_manual_candidate(
                    root, source.path / "draft.txt", plan_attempt=1
                )
            attempts = sorted(
                path.name
                for path in (root / "novel/work/chapter_001").glob("attempt_*")
            )
            self.assertEqual(attempts, ["attempt_01"])

    def test_cli_exposes_explicit_manual_workflow(self):
        parser = build_parser()
        imported = parser.parse_args(
            ["manual-import", "draft.txt", "--plan-attempt", "14"]
        )
        reviewed = parser.parse_args(["manual-review", "--attempt", "15"])
        self.assertEqual(imported.plan_attempt, 14)
        self.assertEqual(reviewed.attempt, 15)


if __name__ == "__main__":
    unittest.main()
