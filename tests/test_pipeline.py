import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.prequel.errors import LegacyRunNotResumable, QualityGateError
from scripts.prequel.evolution import EvolutionResult
from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.run_manifest import RunManifest, fingerprint
from scripts.prequel.memory import MemoryStore
from scripts.prequel.pipeline import WritingPipeline, _new_state_after_chapter, accept_dry_run, merge_formal_chapters
from scripts.prequel.quality import scan_draft
from scripts.prequel.state_settlement import expected_state_changes


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def generate(self, prompt, output_schema=None):
        return next(self.outputs)


def valid_plan_json() -> str:
    return json.dumps(
        {
            "chapter_number": 1,
            "title": "门上的灰",
            "event_id": "event_1",
            "phase": "征兆",
            "chapter_purpose": "纸灰从祠堂进入张洞家中，日常安全边界第一次失效",
            "dramatic_spine": {
                "opening_pressure": "张洞即将离镇时家门失效。",
                "opening_genre_signal": "开篇即出现越过门板的纸灰。",
                "protagonist_immediate_want": "赶上渡船去木行。",
                "personal_stake": "错过学徒机会。",
                "destabilizing_event": "纸灰进入关闭的门内。",
                "protagonist_choice": "张洞留下检查纸灰。",
                "choice_cost": "离镇准备被耽搁。",
                "cost_realization": "张洞当场用自己的船钱补上家中缺口。",
                "relationship_friction": "母亲要他先走，张洞要先确认家人安全。",
                "question_progression": ["灰从哪里来？", "门是否仍安全？", "他还能否按时离镇？"],
                "emotional_turn": "离镇期待变成守家压力。",
                "serial_promise": "下一章必须处理失效的家门。",
                "ending_leverage": "张洞扣住唯一钥匙，使对方不能从后门取走证物。",
            },
            "scenes": [
                {
                    "location": "张家院子",
                    "characters": ["张洞", "张洞母亲"],
                    "goal": "建立日常",
                    "conflict": "纸灰出现在不该出现的位置",
                    "function": "升级",
                    "initial_state": "张家院门从内上栓，蒸笼有盖，张洞与母亲都在灶间。",
                    "discovery_path": "张洞清点米粮时打开蒸笼，先检查笼盖再看见内部纸灰。",
                    "knowledge_limits": "两人只知道祠堂当日无人烧纸，不知道纸灰何时进入。",
                    "ordinary_explanations": {
                        "considered": ["灶灰飘入", "家人曾开盖"],
                        "excluded": ["灰从笼盖上方直接落入"],
                        "remaining": ["此前已经有人把灰留在蒸笼中"]
                    },
                    "choice_reason": "张洞要保留现场，母亲则担心存粮受污染。",
                    "end_state": "蒸笼纸灰被分碗留存，祠堂钥匙转由母亲贴身保管。",
                    "pressure_change": "张洞必须记录纸灰位置",
                    "irreversible_change": "张洞开始记录纸灰位置",
                }
            ],
            "new_information": ["纸灰会离开祠堂"],
            "state_changes": {
                "protagonist_known_info_add": ["纸灰会离开祠堂"],
                "protagonist_inventory_add": [],
                "protagonist_inventory_remove": [],
                "protagonist_location": None,
                "protagonist_body_updates": [],
                "ability_updates": [],
                "timeline_year": 1908,
                "timeline_elapsed_days": 1,
                "character_updates": [],
                "world_confirmed_add": [],
                "world_hypotheses_add": ["纸灰可能标记被敲门者"],
            },
            "rule_hypotheses": ["纸灰可能标记被敲门者"],
            "canon_evidence_ids": ["CANON-RULE-001", "PREQUEL-EVENT-001"],
            "foreshadow_operations": {"plant": ["F-A01"], "recover": []},
            "milestone_operations": {"complete": []},
            "hook": {"type": "安全区崩坏", "content": "纸灰出现在门内"},
            "prohibited_elements": ["周正", "负责人"],
        },
        ensure_ascii=False,
    )


def valid_draft() -> str:
    body = "张洞把门板上的灰扫进簸箕。灰没有落到底，贴在竹篾缝里。" * 40
    return "第1章：门上的灰\n\n" + body + "\n\n天黑前，那层灰到了门内。"


def review_json(verdict: str) -> str:
    return json.dumps(
        {
            "chapter_number": 1,
            "verdict": verdict,
            "grade": "A" if verdict == "PASS" else "C",
            "p1_failures": [],
            "p2_warnings": [],
            "evidence": [
                {"quote": "门板上的灰", "finding": "异常落在具体物件"},
                {"quote": "没有落到底", "finding": "异常通过事实呈现"},
                {"quote": "到了门内", "finding": "安全边界发生变化"},
            ],
            "character_assessment": "张洞通过动作观察，没有越过信息范围",
            "canon_assessment": "没有引入正传现代人物或新组织",
            "style_assessment": "叙事克制，结尾有安全区崩坏",
            "revision_instructions": [] if verdict == "PASS" else ["重新安排不可逆变化"],
        },
        ensure_ascii=False,
    )


def blind_reader_json(draft: str) -> str:
    return json.dumps(
        {
            "chapter_number": 1,
            "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            "verdict": "PASS",
            "reader_recap": {
                "current_goal": "张洞检查门上的灰。",
                "character_positions": "张洞在门边。",
                "spatial_map": "灰从门板外侧进入门内。",
                "causal_chain": "张洞观察纸灰，随后安全边界失效。",
                "next_question": "纸灰怎样越过关闭的门？",
            },
            "adversarial_checks": {
                "ordinary_explanations": [],
                "missing_preconditions": [],
                "knowledge_or_behavior_gaps": [],
                "physical_or_spatial_gaps": [],
                "unsupported_recap_claims": [],
            },
            "reading_experience": {
                "prose_accessibility": 4,
                "character_believability": 4,
                "target_emotion_effect": 4,
                "narrative_momentum": 4,
                "opening_pull": 4,
                "protagonist_ownership": 4,
                "question_progression": 4,
                "ending_compulsion": 4,
                "competitive_readiness": "NEAR",
                "next_click_reason": "张洞必须在离镇前查清纸灰如何越过门板。",
                "continue_reading": True,
                "first_drop_point": None,
                "friction_reasons": [],
            },
            "blocking_issues": [],
            "warnings": [],
            "evidence": [
                {"quote": "门板上的灰", "finding": "目标有物理落点"},
                {"quote": "没有落到底", "finding": "异常可以观察"},
                {"quote": "到了门内", "finding": "章末边界发生变化"},
            ],
            "revision_instructions": [],
        },
        ensure_ascii=False,
    )


def state_settlement_json(state: dict, plan: dict, draft: str) -> str:
    return json.dumps(
        {
            "chapter_number": 1,
            "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
            "verdict": "PASS",
            "reader_visible_summary": {
                "core": "张洞检查门板纸灰，发现它最终越过门板进入屋内。",
                "evidence": ["门板上的灰", "到了门内"],
            },
            "hook": {
                "type": plan["hook"]["type"],
                "content": "纸灰已经进入门内",
                "quote": "到了门内",
            },
            "change_evidence": [
                {
                    "path": item["path"],
                    "value": item["value"],
                    "quote": "门板上的灰",
                    "finding": item["meaning"],
                }
                for item in expected_state_changes(state, plan)
            ],
            "missing_changes": [],
        },
        ensure_ascii=False,
    )


def make_project_fixture(root: Path) -> Path:
    for path in [
        "config",
        "novel/state",
        "novel/knowledge",
        "novel/plots",
        "novel/chapters/vol_01",
        "novel/chapters/meta",
        "novel/work",
        "schemas",
        "agents",
    ]:
        (root / path).mkdir(parents=True, exist_ok=True)
    state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
    (root / "novel/state/current.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    registry = {
        "confidence_levels": {"A": "明确", "B": "暗示", "C": "原创"},
        "facts": [
            {"id": "CANON-RULE-001", "level": "A", "claim": "鬼无法被杀死", "allowed_use": "底层规则", "forbidden_overclaim": "不提前讲解"},
            {"id": "PREQUEL-EVENT-001", "level": "C", "claim": "疑棺事件", "allowed_use": "第一事件", "forbidden_overclaim": "不冒充原著"},
        ],
        "era_bans": {"1890-1950": {"characters": ["周正"], "terms": ["负责人"], "reason": "时代禁入"}},
    }
    (root / "novel/knowledge/canon_registry.json").write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    (root / "novel/plots/event_1.md").write_text("# event_1\n\nCh01 纸灰。", encoding="utf-8")
    (root / "novel/plots/series_architecture.md").write_text("# test architecture\n", encoding="utf-8")
    (root / "novel/knowledge/arc_registry.json").write_text(
        json.dumps({"schema": "novel-arc-registry", "milestones": {"M1-TEST": {}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "novel/knowledge/foreshadow_registry.json").write_text(
        json.dumps({"schema": "novel-foreshadow-registry", "entries": {"F-A01": {}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    config = {
        "provider": {
            "type": "codex_cli",
            "command": ["codex", "exec"],
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "timeout_seconds": 10,
        },
        "quality_gates": {"recent_chapters_for_repetition": 5, "max_retries": 1},
        "git": {"auto_commit": False},
    }
    (root / "config/prequel_config.json").write_text(json.dumps(config), encoding="utf-8")
    for name in ("plan", "review"):
        (root / f"schemas/{name}.schema.json").write_text("{}", encoding="utf-8")
    for name in ("planner", "writer", "reviewer"):
        (root / f"agents/{name}.md").write_text(f"你是{name}。", encoding="utf-8")
    return root


class PipelineTests(unittest.TestCase):
    def test_final_reader_and_state_gates_run_before_atomic_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_evolution"] = {"call_limit": 12}
            config["quality_gates"] = {
                "recent_chapters_for_repetition": 5,
                "blind_reader_gate": {"enabled": True},
                "state_evidence_gate": {"enabled": True},
            }
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "agents/reader_reviewer.md").write_text("盲读。", encoding="utf-8")
            (root / "agents/state_settler.md").write_text("结算。", encoding="utf-8")
            (root / "schemas/reader_review.schema.json").write_text("{}", encoding="utf-8")
            (root / "schemas/state_settlement.schema.json").write_text("{}", encoding="utf-8")
            MemoryStore(root)
            state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            plan = json.loads(valid_plan_json())
            draft = valid_draft()
            static = scan_draft(
                draft, [], {"characters": ["周正"], "terms": ["负责人"]}, plan
            )
            reviews = {
                dimension: {
                    "summary": f"{dimension}通过",
                    "evidence": [
                        {"quote": "门板上的灰", "finding": "具体异常"},
                        {"quote": "没有落到底", "finding": "观察成立"},
                        {"quote": "到了门内", "finding": "边界改变"},
                    ],
                    "warnings": [],
                }
                for dimension in ("continuity", "character", "craft", "anti_slop")
            }
            evolution = EvolutionResult(
                "AUTO_PROMOTE",
                "candidate_01",
                draft,
                static,
                reviews,
                {
                    "scores": {"continuity": 92, "character": 88, "craft": 88, "anti_slop": 86},
                    "weighted_score": 89.1,
                    "hard_failures": [],
                    "required_revisions": [],
                },
                {"status": "AUTO_PROMOTE"},
            )

            class Router:
                def __init__(self):
                    self.providers = {
                        "planner": FakeProvider([valid_plan_json()]),
                        "blind_reader_reviewer": FakeProvider([blind_reader_json(draft)]),
                        "state_settler": FakeProvider([state_settlement_json(state, plan, draft)]),
                    }

                def provider_for(self, stage):
                    return self.providers[stage]

                def profile_for(self, stage):
                    return stage

            with patch("scripts.prequel.pipeline.QualityEvolutionEngine") as engine:
                engine.return_value.run.return_value = evolution
                result = WritingPipeline(root, providers=Router()).run_next()
            self.assertTrue(result.promoted)
            manifest = json.loads((result.workspace / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["stages"]["blind_reader_review"]["status"], "COMPLETED")
            self.assertEqual(manifest["stages"]["state_settlement"]["status"], "COMPLETED")
            self.assertEqual(manifest["budget"]["spent"], 3)
            promoted_state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            self.assertEqual(
                promoted_state["chapter_summaries"]["summaries"]["1"]["core"],
                "张洞检查门板纸灰，发现它最终越过门板进入屋内。",
            )

    def test_state_summary_and_hook_come_from_final_text_settlement(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = json.loads(valid_plan_json())
        settlement = {
            "reader_visible_summary": {"core": "张洞在门内发现异常纸灰并留下样本。", "evidence": []},
            "hook": {"type": "安全区崩坏", "content": "纸灰已经越过门板", "quote": "到了门内"},
        }
        updated = _new_state_after_chapter(
            state, plan, json.loads(review_json("PASS")), settlement
        )
        self.assertEqual(
            updated["chapter_summaries"]["summaries"]["1"]["core"],
            settlement["reader_visible_summary"]["core"],
        )
        self.assertEqual(updated["recent_hooks"][-1]["content"], "纸灰已经越过门板")

    def test_unevidenced_plan_candidates_do_not_pollute_live_state(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = json.loads(valid_plan_json())
        expected = {
            item["path"]: item for item in expected_state_changes(state, plan)
        }
        settled_paths = {
            "state_changes.protagonist_known_info_add[0]",
            "foreshadow_operations.plant[0]",
        }
        settlement = {
            "reader_visible_summary": {
                "core": "张洞发现纸灰已经离开祠堂并留下异常样本。",
                "evidence": [],
            },
            "hook": {
                "type": "安全区崩坏",
                "content": "纸灰进入门内",
                "quote": "到了门内",
            },
            "change_evidence": [
                {
                    "path": path,
                    "value": expected[path]["value"],
                    "quote": "纸灰",
                    "finding": expected[path]["meaning"],
                }
                for path in settled_paths
            ],
        }
        updated = _new_state_after_chapter(
            state, plan, json.loads(review_json("PASS")), settlement
        )
        self.assertIn("纸灰会离开祠堂", updated["protagonist"]["known_info"])
        self.assertEqual(updated["timeline"]["elapsed_days"], 0)
        self.assertNotIn(
            "纸灰可能标记被敲门者", updated["world_lore"]["hypotheses"]
        )
        self.assertIn("F-A01", updated["active_foreshadows"])

    def test_exit_milestone_advances_volume_event_and_reveal_layer(self):
        state = json.loads(Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8"))
        plan = json.loads(valid_plan_json())
        plan["milestone_operations"]["complete"] = ["M1-CITY-EXIT"]
        config = {
            "volume_structure": [
                {"volume": 1, "name": "大汉市", "exit_milestone": "M1-CITY-EXIT"},
                {"volume": 2, "name": "乱世觉醒", "entry_event": "event_2", "entry_event_name": "鬼邮局初现"},
            ],
            "world_reveal_layers": [
                {"layer": 2, "after": "M1-CITY-EXIT"}
            ],
        }
        updated = _new_state_after_chapter(
            state, plan, json.loads(review_json("PASS")), config=config
        )
        self.assertEqual(updated["chapter"]["current_volume"], 2)
        self.assertEqual(updated["chapter"]["current_event"], "event_2")
        self.assertEqual(updated["world_lore"]["reveal_layer"], 2)

    def test_legacy_replan_is_read_only_and_not_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            pipeline = WritingPipeline(root, FakeProvider([]))
            state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
            manifest = RunManifest.create(workspace, 1, fingerprint(state))
            manifest.set_status("REPLAN", valid_candidates=0)
            self.assertEqual(manifest.display_status(), "LEGACY_REPLAN")
            with self.assertRaises(LegacyRunNotResumable):
                pipeline._attempt_number(1, True, fingerprint(state))

    def test_next_cli_accepts_modes_shadow_and_unbounded_positive_candidate(self):
        from scripts.orchestrator import build_parser

        parser = build_parser()
        self.assertEqual(parser.parse_args(["next"]).mode, "balanced")
        self.assertEqual(parser.parse_args(["next", "--mode", "fast"]).mode, "fast")
        self.assertEqual(
            parser.parse_args(["next", "--shadow-review", "continuity"]).shadow_review,
            "continuity",
        )
        self.assertEqual(parser.parse_args(["accept", "--candidate", "4"]).candidate, 4)

    def test_review_parser_accepts_specialist_calibration(self):
        from scripts.orchestrator import build_parser

        args = build_parser().parse_args(["review", "--last", "2", "--specialists"])
        self.assertTrue(args.specialists)

    def test_high_confidence_evolution_result_promotes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_evolution"] = {}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            MemoryStore(root)
            draft = valid_draft()
            plan = json.loads(valid_plan_json())
            static = scan_draft(
                draft,
                [],
                {"characters": ["周正"], "terms": ["负责人"]},
                plan,
            )
            reviews = {
                dimension: {
                    "summary": f"{dimension}通过",
                    "evidence": [
                        {"quote": "门板上的灰", "finding": "具体异常"},
                        {"quote": "没有落到底", "finding": "观察成立"},
                        {"quote": "到了门内", "finding": "边界改变"},
                    ],
                    "warnings": [],
                }
                for dimension in ("continuity", "character", "craft", "anti_slop")
            }
            card = {
                "scores": {
                    "continuity": 92,
                    "character": 88,
                    "craft": 88,
                    "anti_slop": 86,
                },
                "weighted_score": 89.1,
                "hard_failures": [],
                "required_revisions": [],
            }
            evolution = EvolutionResult(
                "AUTO_PROMOTE",
                "candidate_01",
                draft,
                static,
                reviews,
                card,
                {"status": "AUTO_PROMOTE"},
            )

            class Router:
                def __init__(self):
                    self.planner = FakeProvider([valid_plan_json()])

                def provider_for(self, stage):
                    return self.planner

                def profile_for(self, stage):
                    return "default"

            with patch("scripts.prequel.pipeline.QualityEvolutionEngine") as engine:
                engine.return_value.run.return_value = evolution
                events = []
                result = WritingPipeline(root, providers=Router()).run_next(
                    progress=events.append
                )
            self.assertTrue(result.promoted)
            self.assertTrue((root / "novel/chapters/vol_01/chapter_001.txt").exists())
            manifest = json.loads((result.workspace / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["budget"]["spent"], 1)
            self.assertEqual(manifest["budget"]["calls"]["call_001"]["stage"], "planner")
            self.assertEqual(
                [event["kind"] for event in events],
                ["CALL_STARTED", "CALL_COMPLETED"],
            )

    def test_script_entrypoint_can_import_project_package(self):
        result = subprocess.run(
            [sys.executable, "scripts/orchestrator.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("事务型创作管道", result.stdout)

    def test_failed_review_does_not_change_formal_state_or_chapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            original = (root / "novel/state/current.json").read_bytes()
            provider = FakeProvider([valid_plan_json(), valid_draft(), review_json("REVISE")])
            with self.assertRaises(QualityGateError):
                WritingPipeline(root, provider).run_next()
            self.assertEqual((root / "novel/state/current.json").read_bytes(), original)
            self.assertFalse((root / "novel/chapters/vol_01/chapter_001.txt").exists())

    def test_success_promotes_chapter_and_advances_state_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            provider = FakeProvider([valid_plan_json(), valid_draft(), review_json("PASS")])
            result = WritingPipeline(root, provider).run_next()
            state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            self.assertEqual(result.chapter_number, 1)
            self.assertTrue(result.promoted)
            self.assertTrue((root / "novel/chapters/vol_01/chapter_001.txt").exists())
            self.assertTrue((root / "novel/chapters/meta/chapter_001.md").exists())
            self.assertTrue((root / "novel/state/current.json.bak").exists())
            self.assertEqual(state["chapter"]["last_chapter"], 1)
            self.assertEqual(state["chapter"]["next_chapter"], 2)
            self.assertIn("F-A01", state["active_foreshadows"])
            self.assertEqual(
                state["chapter_summaries"]["summaries"]["1"]["irreversible_changes"],
                ["protagonist_known_info_add", "timeline_elapsed_days", "world_hypotheses_add"],
            )

    def test_foreshadow_note_is_normalized_to_stable_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            plan = json.loads(valid_plan_json())
            plan["foreshadow_operations"]["plant"] = ["F-A01：纸灰越过灶间边界"]
            provider = FakeProvider([
                json.dumps(plan, ensure_ascii=False), valid_draft(), review_json("PASS")
            ])
            WritingPipeline(root, provider).run_next()
            state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            self.assertEqual(set(state["active_foreshadows"]), {"F-A01"})

    def test_dry_run_keeps_formal_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            original = (root / "novel/state/current.json").read_bytes()
            provider = FakeProvider([valid_plan_json(), valid_draft(), review_json("PASS")])
            result = WritingPipeline(root, provider).run_next(dry_run=True)
            self.assertFalse(result.promoted)
            self.assertEqual((root / "novel/state/current.json").read_bytes(), original)
            self.assertTrue((root / "novel/work/chapter_001/attempt_01/semantic_review.json").exists())

    def test_accept_revalidates_and_promotes_passed_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            provider = FakeProvider([valid_plan_json(), valid_draft(), review_json("PASS")])
            WritingPipeline(root, provider).run_next(dry_run=True)
            accepted = accept_dry_run(root)
            state = json.loads((root / "novel/state/current.json").read_text(encoding="utf-8"))
            self.assertTrue(accepted.promoted)
            self.assertEqual(state["chapter"]["last_chapter"], 1)
            self.assertTrue((root / "novel/chapters/vol_01/chapter_001.txt").exists())

    def test_revision_is_isolated_and_second_attempt_can_promote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            config_path = root / "config/prequel_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["quality_gates"]["max_retries"] = 2
            config_path.write_text(json.dumps(config), encoding="utf-8")
            provider = FakeProvider([
                valid_plan_json(), valid_draft(), review_json("REVISE"),
                valid_draft(), review_json("PASS"),
            ])
            result = WritingPipeline(root, provider).run_next()
            self.assertTrue(result.promoted)
            self.assertTrue((root / "novel/work/chapter_001/attempt_01/semantic_review.json").exists())
            self.assertTrue((root / "novel/work/chapter_001/attempt_02/semantic_review.json").exists())

    def test_merge_uses_validated_formal_chapter_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            provider = FakeProvider([valid_plan_json(), valid_draft(), review_json("PASS")])
            WritingPipeline(root, provider).run_next()
            target, count = merge_formal_chapters(root)
            self.assertEqual(count, 1)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "\n".join(
                    line
                    for line in (root / "novel/chapters/vol_01/chapter_001.txt")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                )
                + "\n",
            )
            self.assertNotIn("\n\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
