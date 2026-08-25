import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.pipeline import (
    _new_state_after_chapter,
    accept_dry_run,
    formal_review_binding_status,
    import_manual_candidate,
    merge_formal_chapters,
)
from scripts.prequel.reader_review import canonicalize_pacing_diagnostics
from scripts.prequel.scene_audit import extract_scene_audit_anchors
from scripts.prequel.state_settlement import expected_state_changes
from tests.project_fixture import write_project_manifest


def valid_plan_json() -> str:
    return json.dumps(
        {
            "chapter_number": 1,
            "title": "门上的灰",
            "event_id": "event_1",
            "phase": "征兆",
            "chapter_purpose": "纸灰从祠堂进入张洞家中，日常安全边界第一次失效",
            "serial_continuity": {
                "prior_human_wound": "首章无上一章人物伤口。",
                "opening_consequence": "首章从离镇期限正在逼近开始。",
                "carried_object_state": "门栓完好，纸灰尚未被发现。",
                "pressure_novelty": "首章建立纸灰侵入生活边界的威胁。",
            },
            "reader_investment": {
                "attachment_anchor": {
                    "focus": "张洞凭木工手艺得到的学徒身份和母亲对他的信任。",
                    "on_page_moment": "母亲试坐他修好的针线凳，并包好他进木行要交的样榫。",
                    "private_meaning": "样榫证明他不是只会替父亲誊账的孩子。",
                    "lived_value": "母亲每天在针线凳上接活，并坚持自己选择客户。",
                    "threatened_loss": "错过期限会失去学徒身份，强行锁门会失去母亲信任。",
                    "loss_carrier": "针线凳和样榫分别承载母亲的生计与张洞的身份，章末都改变用途。",
                },
                "protagonist_contradiction": "他想离开，却习惯用查清异常拖延承认自己正在离开母亲。",
                "threat_in_motion": "纸灰正在越过关闭家门，直接改变母亲能否留下。",
                "core_threat_continuation": {
                    "prior_hook": "首章将建立关闭家门为何仍出现纸灰的核心疑问。",
                    "current_effect": "纸灰当场污染送行饭并改变母子的离镇选择。",
                    "local_answer": "倒扣容器不能解释纸灰来源，原有日常边界已不可靠。",
                    "old_defense": "关门并倒扣饭碗。",
                    "defense_failure": "纸灰仍进入倒扣饭碗，证明普通关闭不足。",
                    "replacement_rule": "先撤开活人并保持现场，再追查进入路径。",
                    "forced_change": "张洞必须在赶船前决定是否保留现场并阻止母亲动饭。",
                    "human_pressure_link": "母亲催船与父亲留证的现实目标因纸灰直接冲突。",
                },
                "revelation_shift": {
                    "from": "灰从哪里来？",
                    "on_page_answer": "纸灰已进入倒扣饭碗。",
                    "to": "关闭的家门还能不能保护母亲？",
                    "changes": "SAFETY",
                    "old_response": "关门后继续留在桌边检查。",
                    "counterexample": "饭碗被污染，留在桌边会继续使母亲接触纸灰。",
                    "new_response": "先让母亲离开饭桌并保持现场。",
                    "executed_change": "张洞移走饭碗并放弃赶船。",
                },
                "emotional_afterimage": {
                    "person": "张洞与母亲。",
                    "immediate_wound": "张洞失去学徒期限，母亲不再接受他替自己决定。",
                    "material_aftereffect": "送行饭被弃置，渡船和学徒期限已经错过。",
                    "relationship_aftereffect": "母亲不再接受张洞借保护之名替她决定去留。",
                    "unresolved_choice": "两人必须在不互相控制的前提下共同面对家门危险。",
                    "mystery_subordinate_to": "纸灰来源只在它继续伤害母子去留时才重要。",
                },
                "clue_delivery": {"method": "张洞以船钱和时间换取现场确认", "resistance": "母亲反对且异常继续逼近", "coincidence_risk": "LOW"},
            },
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
                    "threat_action": "纸灰越过关闭的家门，夺走一家对门栓的信任。",
                    "human_turn": "母亲不再支持张洞留下调查，母子对去留第一次冲突。",
                    "payoff_type": "MIXED",
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
    repeated = "张洞把门板上的灰扫进簸箕。灰没有落到底，贴在竹篾缝里。"
    body = repeated * 20 + "张洞把簸箕移到门边。" + repeated * 20
    return "第1章：门上的灰\n\n灰正落在张洞手上。" + body + "\n\n天黑前，那层灰到了门内。"


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
    anchors = extract_scene_audit_anchors(draft)
    mechanism_audit = {
        "artifact_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "verdict": "PASS",
        "pov_source_ledger": [
            {"anchor_id": item["anchor_id"], "claim_quote": item["quote"], "information_source": "当场观察", "source_quote": item["quote"], "verdict": "SUPPORTED", "explanation": "来源成立"}
            for item in anchors["pov_claims"]
        ],
        "boundary_action_ledger": [
            {"anchor_id": item["anchor_id"], "action_quote": item["quote"], "before_quote": item["quote"], "after_quote": item["quote"], "visible_to_pov": True, "verdict": "COHERENT", "explanation": "动作连续"}
            for item in anchors["boundary_actions"]
        ],
        "shock_response_ledger": [
            {"anchor_id": item["anchor_id"], "trigger_quote": item["quote"], "response_quote": None, "response_window": "旧事实", "verdict": "NOT_NEW_INFORMATION", "explanation": "没有新冲击"}
            for item in anchors["shock_triggers"]
        ],
        "dialogue_register_ledger": [
            {"anchor_id": item["anchor_id"], "dialogue_quote": item["quote"], "speaker": "人物", "goal": "行动", "verdict": "NATURAL", "explanation": "自然"}
            for item in anchors["dialogue_samples"]
        ],
        "first_read_reconstruction": {"reader_can_reconstruct": True, "required_rereads": 0, "character_positions": "张洞在门边", "visibility_limits": "只见门内", "action_chain": "纸灰进入门内", "confusing_quotes": []},
        "blocking_issues": [],
        "revision_instructions": [],
    }
    report = {
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
            "mechanism_audit": mechanism_audit,
            "pacing_diagnostics": {
                "first_1000_chars_result": "张洞检查家门，灰的位置迫使他改变处置。",
                "first_active_pressure": {
                    "quote": "灰正落在张洞手上。", "position_percent": 0,
                    "effect": "家门异常阻断张洞的原定安排。",
                },
                "core_threat_activation": {
                    "quote": "灰正落在张洞手上。", "position_percent": 0,
                    "effect": "纸灰直接侵入家门边界。",
                },
                "first_costly_choice": {
                    "quote": "张洞把簸箕移到门边。", "position_percent": 0,
                    "effect": "张洞放弃原位置并接手处置现场。",
                },
                "pressure_turns": [
                    {"quote": "灰正落在张洞手上。", "effect": "异常进入目标。"},
                    {"quote": "张洞把簸箕移到门边。", "effect": "处置方式改变。"},
                    {"quote": "天黑前，那层灰到了门内。", "effect": "家门防线失效。"},
                ],
                "max_pressure_gap_chars": 0,
                "exposition_runs": [],
                "information_only_passages": [],
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
                "competitive_readiness": "MATCH",
                "next_click_reason": "张洞必须在离镇前查清纸灰如何越过门板。",
                "continue_reading": True,
                "first_drop_point": None,
                "friction_reasons": [],
                "friction_severity": "NONE",
            },
            "benchmark_comparison": {
                "character_attachment": {"score": 4, "quote": "门板上的灰", "assessment": "家门和离镇愿望均受威胁"},
                "active_threat": {"score": 4, "quote": "到了门内", "assessment": "异常正在越过家门"},
                "protagonist_specificity": {"score": 4, "quote": "张洞", "assessment": "选择绑定张洞离镇与守家矛盾"},
                "revelation_transformation": {"score": 4, "quote": "到了门内", "assessment": "问题从灰的位置变成家门失效"},
                "emotional_aftereffect": {"score": 4, "quote": "天黑前", "assessment": "读者担心活人当夜安全"},
                "evidence_payoff_mode": "MIXED",
                "would_choose_over_competent_peer": True,
                "major_gaps": [],
            },
            "blocking_issues": [],
            "warnings": [],
            "evidence": [
                {"quote": "门板上的灰", "finding": "目标有物理落点"},
                {"quote": "没有落到底", "finding": "异常可以观察"},
                {"quote": "到了门内", "finding": "章末边界发生变化"},
            ],
            "revision_instructions": [],
        }
    canonicalize_pacing_diagnostics(report, draft)
    return json.dumps(report, ensure_ascii=False)


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
        "novel/benchmarks",
        "novel/style",
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
    (root / "novel/benchmarks/opening_compulsion.md").write_text(
        "# test benchmark\n\n## 五项硬校准\n", encoding="utf-8"
    )
    (root / "novel/style/user_taste_contract.json").write_text(
        Path("novel/style/user_taste_contract.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "novel/style/reference_voice_profile.md").write_text(
        "calibration_status: READY\n", encoding="utf-8"
    )
    (root / "novel/knowledge/arc_registry.json").write_text(
        json.dumps({"schema": "novel-arc-registry", "milestones": {"M1-TEST": {}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "novel/knowledge/foreshadow_registry.json").write_text(
        json.dumps({"schema": "novel-foreshadow-registry", "entries": {"F-A01": {}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    config = {
        "quality_gates": {"recent_chapters_for_repetition": 5, "max_retries": 1},
        "context_policy": {
            "planner_canon_fact_ids": ["CANON-RULE-001", "PREQUEL-EVENT-001"]
        },
        "git": {"auto_commit": False},
    }
    write_project_manifest(root, engine_config=config)
    for name in ("plan", "review"):
        (root / f"schemas/{name}.schema.json").write_text("{}", encoding="utf-8")
    for name in ("planner", "writer", "reviewer"):
        (root / f"agents/{name}.md").write_text(f"你是{name}。", encoding="utf-8")
    return root


class PipelineTests(unittest.TestCase):
    def _reviewed_attempt(self, root: Path) -> ChapterWorkspace:
        workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
        workspace.write_json("plan.json", json.loads(valid_plan_json()))
        workspace.write_text("draft.txt", valid_draft())
        workspace.write_json("semantic_review.json", json.loads(review_json("PASS")))
        return workspace

    def test_prompt_native_artifacts_promote_without_execution_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._reviewed_attempt(root)

            result = accept_dry_run(root)

            self.assertTrue(result.promoted)
            chapter = root / "novel/chapters/vol_01/chapter_001.txt"
            self.assertEqual(
                chapter.read_text(encoding="utf-8").rstrip(),
                valid_draft().rstrip(),
            )
            state = json.loads(
                (root / "novel/state/current.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                formal_review_binding_status(root, state)["status"], "VALID"
            )

    def test_manual_import_accepts_agent_written_bound_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._reviewed_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft = (imported.workspace / "draft.txt").read_text(encoding="utf-8")
            review = json.loads(review_json("PASS"))
            review["draft_sha256"] = hashlib.sha256(
                draft.encode("utf-8")
            ).hexdigest()
            (imported.workspace / "semantic_review.json").write_text(
                json.dumps(review, ensure_ascii=False), encoding="utf-8"
            )

            result = accept_dry_run(root, attempt=2)

            self.assertTrue(result.promoted)
            manifest = json.loads(
                (imported.workspace / "run_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["status"], "COMPLETED")
            self.assertNotIn("budget", manifest)

    def test_manual_import_rejects_draft_changed_after_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            source = self._reviewed_attempt(root)
            imported = import_manual_candidate(
                root, source.path / "draft.txt", plan_attempt=1
            )
            draft_path = imported.workspace / "draft.txt"
            draft = draft_path.read_text(encoding="utf-8")
            review = json.loads(review_json("PASS"))
            review["draft_sha256"] = hashlib.sha256(
                draft.encode("utf-8")
            ).hexdigest()
            (imported.workspace / "semantic_review.json").write_text(
                json.dumps(review, ensure_ascii=False), encoding="utf-8"
            )
            draft_path.write_text(draft + "\n篡改。\n", encoding="utf-8")

            with self.assertRaises(ArtifactValidationError):
                accept_dry_run(root, attempt=2)

    def test_merge_uses_only_formal_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_project_fixture(Path(tmp))
            self._reviewed_attempt(root)
            accept_dry_run(root)

            target, count = merge_formal_chapters(root)

            self.assertEqual(count, 1)
            self.assertNotIn("\n\n", target.read_text(encoding="utf-8"))

    def test_state_changes_only_commit_evidenced_values(self):
        state = json.loads(
            Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8")
        )
        plan = json.loads(valid_plan_json())
        settlement = json.loads(state_settlement_json(state, plan, valid_draft()))

        updated = _new_state_after_chapter(
            state,
            plan,
            json.loads(review_json("PASS")),
            settlement,
        )

        self.assertEqual(updated["chapter"]["last_chapter"], 1)
        self.assertEqual(updated["chapter"]["next_chapter"], 2)


if __name__ == "__main__":
    unittest.main()
