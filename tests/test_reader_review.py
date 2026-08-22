import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.reader_review import (
    build_blind_reader_packet,
    build_reader_validation_diagnostic,
    canonicalize_pacing_diagnostics,
    validate_blind_reader_review,
)
from scripts.prequel.scene_audit import extract_scene_audit_anchors


def passing_mechanism_audit(draft: str) -> dict:
    anchors = extract_scene_audit_anchors(draft)
    return {
        "artifact_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "verdict": "PASS",
        "pov_source_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "claim_quote": item["quote"],
                "information_source": "该句写明当场来源。",
                "source_quote": item["quote"],
                "verdict": "SUPPORTED",
                "explanation": "测试基线来源成立。",
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
                "explanation": "测试基线动作连续。",
            }
            for item in anchors["boundary_actions"]
        ],
        "shock_response_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "trigger_quote": item["quote"],
                "response_quote": None,
                "response_window": "该句只复述已知事实。",
                "verdict": "NOT_NEW_INFORMATION",
                "explanation": "测试基线不构成新冲击。",
            }
            for item in anchors["shock_triggers"]
        ],
        "dialogue_register_ledger": [
            {
                "anchor_id": item["anchor_id"],
                "dialogue_quote": item["quote"],
                "speaker": "测试人物",
                "goal": "推进当下行动",
                "verdict": "NATURAL",
                "explanation": "测试基线对白自然。",
            }
            for item in anchors["dialogue_samples"]
        ],
        "first_read_reconstruction": {
            "reader_can_reconstruct": True,
            "required_rereads": 0,
            "character_positions": "张洞在船边。",
            "visibility_limits": "舱门关闭。",
            "action_chain": "张洞要上船，舱门关闭，纸从缝中出来。",
            "confusing_quotes": [],
        },
        "blocking_issues": [],
        "revision_instructions": [],
    }


class BlindReaderReviewTests(unittest.TestCase):
    def setUp(self):
        self.draft = "张洞要上船。\n\n舱门没有开。\n\n纸从门缝里出来。"
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
            "mechanism_audit": passing_mechanism_audit(self.draft),
            "pacing_diagnostics": {
                "first_1000_chars_result": "张洞的上船目标立刻被关闭的舱门阻断。",
                "first_active_pressure": {
                    "quote": "张洞要上船。", "position_percent": 0,
                    "effect": "上船目标进入场面。",
                },
                "core_threat_activation": {
                    "quote": "舱门没有开。", "position_percent": 0,
                    "effect": "关闭边界成为必须处理的阻力。",
                },
                "first_costly_choice": {
                    "quote": "要上船。", "position_percent": 0,
                    "effect": "张洞承诺立刻离开当前安全位置。",
                },
                "pressure_turns": [
                    {"quote": "张洞要上船。", "effect": "目标开始。"},
                    {"quote": "舱门没有开。", "effect": "通路被阻断。"},
                    {"quote": "来。", "effect": "纸越过关闭边界。"},
                ],
                "max_pressure_gap_chars": 0,
                "exposition_runs": [],
                "information_only_passages": [],
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
        canonicalize_pacing_diagnostics(self.report, self.draft)

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
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter_dir = root / "novel/chapters/vol_01"
            benchmark_dir = root / "novel/benchmarks"
            style_dir = root / "novel/style"
            chapter_dir.mkdir(parents=True)
            benchmark_dir.mkdir(parents=True)
            style_dir.mkdir(parents=True)
            (chapter_dir / "chapter_001.txt").write_text(
                "第1章：旧章\n\n已发布正文。\n", encoding="utf-8"
            )
            (benchmark_dir / "opening_compulsion.md").write_text(
                (Path.cwd() / "novel/benchmarks/opening_compulsion.md").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            (style_dir / "user_taste_contract.json").write_text(
                (Path.cwd() / "novel/style/user_taste_contract.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            packet = build_blind_reader_packet(state, 2, self.draft, root)
        self.assertEqual(packet["prior_reader_facts"], [{"chapter": 1, "title": "旧章", "reader_visible_summary": "已发生的事"}])
        self.assertEqual(packet["immediate_prior_chapter"]["chapter"], 1)
        self.assertIn("第1章", packet["immediate_prior_chapter"]["published_text"])
        self.assertNotIn("world_lore", packet)
        self.assertNotIn("protagonist", packet)
        self.assertEqual(packet["draft_sha256"], self.report["draft_sha256"])
        self.assertIn("五项硬校准", packet["benchmark_calibration"])

    def test_valid_report_requires_reader_visible_evidence(self):
        self.assertEqual(validate_blind_reader_review(self.report, self.draft, 1), [])

    def test_pacing_metrics_are_derived_from_exact_quotes(self):
        pacing = self.report["pacing_diagnostics"]
        self.assertEqual(pacing["core_threat_activation"]["position_percent"], 30.0)
        self.assertEqual(pacing["max_pressure_gap_chars"], 12)

    def test_attempt20_style_gap_identifies_missing_middle_turn(self):
        prior = "若误了这一趟，铺里的空位不会留给他。"
        start = "退掉。押钱也拿回来。"
        middle = "先别认她。"
        end = "一家两个说法，我不能带你。"
        draft = (
            prior
            + ("甲" * (763 - len(prior)))
            + start
            + ("乙" * (469 - len(start)))
            + middle
            + ("丙" * (614 - len(middle)))
            + end
        )
        report = {
            "pacing_diagnostics": {
                "pressure_turns": [
                    {"quote": prior, "effect": "期限开始挤压选择。"},
                    {"quote": start, "effect": "工作资格开始被撤销。"},
                    {"quote": end, "effect": "伙计拒绝带人。"},
                ],
                "max_pressure_gap_chars": 650,
            }
        }

        diagnostic = canonicalize_pacing_diagnostics(report, draft)

        self.assertEqual(
            diagnostic["reported_max_pressure_gap_chars"], 650
        )
        self.assertEqual(
            diagnostic["derived_max_pressure_gap_chars"], 1083
        )
        self.assertEqual(len(diagnostic["over_limit_gaps"]), 1)
        gap = diagnostic["over_limit_gaps"][0]
        self.assertEqual(gap["start_quote"], start)
        self.assertEqual(gap["start_offset"], 763)
        self.assertEqual(gap["end_quote"], end)
        self.assertEqual(gap["end_offset"], 1846)
        self.assertEqual(gap["gap_chars"], 1083)
        self.assertEqual(len(report["pacing_diagnostics"]["pressure_turns"]), 3)

        report["pacing_diagnostics"]["pressure_turns"].insert(
            2, {"quote": middle, "effect": "亲属身份变成现场风险。"}
        )
        repaired = canonicalize_pacing_diagnostics(report, draft)
        self.assertEqual(repaired["derived_max_pressure_gap_chars"], 763)
        self.assertEqual(repaired["over_limit_gaps"], [])

    def test_pacing_positions_exclude_chapter_title(self):
        draft = "第1章：试题\n\n正文从这里开始。\n后续压力。"
        report = {
            "pacing_diagnostics": {
                "first_active_pressure": {
                    "quote": "正文从这里开始。", "position_percent": 99,
                },
            }
        }
        canonicalize_pacing_diagnostics(report, draft)
        self.assertEqual(
            report["pacing_diagnostics"]["first_active_pressure"][
                "position_percent"
            ],
            0.0,
        )

    def test_pass_reports_late_core_threat_as_nonblocking_reference(self):
        self.report["pacing_diagnostics"]["core_threat_activation"]["quote"] = "来。"
        canonicalize_pacing_diagnostics(self.report, self.draft)
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("READER_PACING_REFERENCE_EXCEEDED", codes)
        self.assertNotIn("READER_PASS_WITH_SLOW_PACING", codes)

    def test_pass_reports_three_paragraph_exposition_run_as_nonblocking_reference(self):
        self.report["pacing_diagnostics"]["exposition_runs"] = [{
            "quote": self.draft,
            "paragraph_count": 0,
            "approx_chars": 0,
            "explanation": "连续三段只解释既有状态。",
        }]
        canonicalize_pacing_diagnostics(self.report, self.draft)
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("READER_PACING_REFERENCE_EXCEEDED", codes)
        self.assertNotIn("READER_PASS_WITH_SLOW_PACING", codes)

    def test_pass_reports_oversized_information_only_passage_as_nonblocking_reference(self):
        passage = "账册旧目" * 30
        self.draft = (
            "张洞要上船。\n\n舱门没有开。\n\n"
            + passage
            + "\n\n纸从门缝里出来。"
        )
        self.report["draft_sha256"] = hashlib.sha256(
            self.draft.encode("utf-8")
        ).hexdigest()
        self.report["pacing_diagnostics"]["information_only_passages"] = [{
            "quote": passage,
            "approx_chars": 0,
            "explanation": "删除后只少重复账目。",
        }]
        canonicalize_pacing_diagnostics(self.report, self.draft)
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("READER_PACING_REFERENCE_EXCEEDED", codes)
        self.assertNotIn("READER_PASS_WITH_SLOW_PACING", codes)

    def test_pass_reports_pressure_gap_over_800_chars_as_nonblocking_reference(self):
        passage = "旧账" * 420
        self.draft = (
            "张洞要上船。\n\n舱门没有开。\n\n"
            + passage
            + "\n\n纸从门缝里出来。"
        )
        self.report["draft_sha256"] = hashlib.sha256(
            self.draft.encode("utf-8")
        ).hexdigest()
        canonicalize_pacing_diagnostics(self.report, self.draft)
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertGreater(
            self.report["pacing_diagnostics"]["max_pressure_gap_chars"], 800
        )
        self.assertIn("READER_PACING_REFERENCE_EXCEEDED", codes)
        self.assertNotIn("READER_PASS_WITH_SLOW_PACING", codes)

    def test_report_rejects_falsified_pacing_position(self):
        self.report["pacing_diagnostics"]["first_active_pressure"][
            "position_percent"
        ] = 90
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("READER_FALSE_PACING_POSITION", codes)

    def test_existing_duplicate_or_unordered_pacing_quotes_never_gain_retry(self):
        for mode in ("duplicate", "unordered"):
            with self.subTest(mode=mode):
                report = copy.deepcopy(self.report)
                turns = report["pacing_diagnostics"]["pressure_turns"]
                if mode == "duplicate":
                    turns[1]["quote"] = turns[0]["quote"]
                else:
                    turns[0], turns[1] = turns[1], turns[0]
                normalization = canonicalize_pacing_diagnostics(report, self.draft)
                issues = validate_blind_reader_review(report, self.draft, 1)
                diagnostic = build_reader_validation_diagnostic(
                    report, self.draft, issues, normalization
                )
                self.assertFalse(diagnostic["retry_eligible"])
                self.assertEqual(diagnostic["repairable_quote_issues"], [])

    def test_report_rejects_quote_not_present_in_draft(self):
        self.report["evidence"][0]["quote"] = "作者的解释"
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_FALSE_EVIDENCE", codes)

    def test_false_benchmark_quote_is_precise_and_retryable(self):
        false_quote = "纸已经从门缝里出来。"
        self.report["benchmark_comparison"]["active_threat"][
            "quote"
        ] = false_quote
        normalization = canonicalize_pacing_diagnostics(
            self.report, self.draft
        )
        issues = validate_blind_reader_review(self.report, self.draft, 1)
        codes = [item.code for item in issues if item.severity == "P1"]
        self.assertEqual(codes, ["READER_FALSE_BENCHMARK_QUOTE"])
        diagnostic = build_reader_validation_diagnostic(
            self.report, self.draft, issues, normalization
        )
        self.assertTrue(diagnostic["retry_eligible"])
        self.assertEqual(diagnostic["feedback_kind"], "QUOTE_ONLY")
        self.assertEqual(
            diagnostic["repairable_quote_issues"],
            [
                {
                    "code": "READER_FALSE_BENCHMARK_QUOTE",
                    "field_path": "benchmark_comparison.active_threat.quote",
                    "field": "active_threat",
                    "benchmark_field": "active_threat",
                    "quote_field": "quote",
                    "invalid_quote": false_quote,
                }
            ],
        )

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

    def test_pass_rejects_missing_mechanism_anchor_coverage(self):
        self.report["mechanism_audit"]["boundary_action_ledger"].pop()
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("SCENE_ANCHOR_COVERAGE_MISSING", codes)

    def test_pass_rejects_underreaction_to_death_revelation(self):
        self.draft = "周秀兰昨天已经下葬。\n\n门外响起她的声音。\n\n李二问：“你来还什么？”"
        self.report["draft_sha256"] = hashlib.sha256(
            self.draft.encode("utf-8")
        ).hexdigest()
        self.report["mechanism_audit"] = passing_mechanism_audit(self.draft)
        row = self.report["mechanism_audit"]["shock_response_ledger"][0]
        row.update({
            "response_quote": "李二问：",
            "response_window": "紧接着进入问账。",
            "verdict": "UNDERREACTION",
            "explanation": "没有认知冲击的过渡。",
        })
        self.report["mechanism_audit"]["verdict"] = "PASS"
        codes = {
            item.code
            for item in validate_blind_reader_review(self.report, self.draft, 1)
        }
        self.assertIn("SCENE_PASS_CONFLICT", codes)

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

    def test_revise_can_use_benchmark_gaps_as_actionable_diagnosis(self):
        self.report["verdict"] = "REVISE"
        self.report["blocking_issues"] = []
        self.report["benchmark_comparison"]["character_attachment"]["score"] = 3
        self.report["benchmark_comparison"]["major_gaps"] = ["人物依恋尚未达到标杆。"]
        self.report["revision_instructions"] = ["在威胁前演出人物不可替代的生活价值。"]
        self.assertEqual(validate_blind_reader_review(self.report, self.draft, 1), [])

    def test_revise_still_requires_an_actionable_diagnosis(self):
        self.report["verdict"] = "REVISE"
        self.report["blocking_issues"] = []
        self.report["revision_instructions"] = ["继续修改。"]
        codes = {item.code for item in validate_blind_reader_review(self.report, self.draft, 1)}
        self.assertIn("READER_FAIL_WITHOUT_ACTION", codes)


if __name__ == "__main__":
    unittest.main()
