from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.run_manifest import fingerprint
from scripts.prequel.scene_experiment import (
    build_pov_event_trace,
    prepare_blind_bundle,
    scene_packet_fingerprint,
    text_sha256,
    validate_character_intention,
    validate_rolling_horizon,
    validate_scene_experiment_comparison,
    validate_scene_packet,
    validate_world_resolution,
)


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = (
    ROOT
    / "novel/benchmarks/scene_generation_mechanism_2026-08-24/scene_packet.json"
)


class SceneExperimentTests(unittest.TestCase):
    def packet(self) -> dict:
        return json.loads(PACKET_PATH.read_text(encoding="utf-8"))

    def intentions(self, packet: dict, tick: int = 1) -> dict[str, dict]:
        source = scene_packet_fingerprint(packet)
        result = {}
        for index, card in enumerate(packet["character_cards"], 1):
            actor_id = card["actor_id"]
            result[actor_id] = {
                "schema": "novel-character-intention",
                "experiment_id": packet["experiment_id"],
                "source_fingerprint": source,
                "tick": tick,
                "actor_id": actor_id,
                "action_id": f"T{tick}_ACTION_{index}",
                "immediate_want": card["private_goal"],
                "chosen_action": f"{card['display_name']}采取一个受位置约束的动作",
                "target": "当前边界",
                "expected_result": "保住当前最在意的东西",
                "fallback_action": "动作受阻时暂时停下并观察",
                "withheld_information": "",
                "emotional_pressure": card["pressures"][0],
                "used_fact_ids": [card["known_facts"][0]["id"]],
                "uses_forbidden_author_knowledge": False,
            }
        return result

    def resolution(
        self, packet: dict, intentions: dict[str, dict], tick: int = 1
    ) -> dict:
        events = []
        actor_ids = list(intentions)
        for order, (actor_id, intention) in enumerate(intentions.items(), 1):
            observers = [actor_id]
            if "zhang_dong" not in observers and order % 2 == 0:
                observers.append("zhang_dong")
            events.append(
                {
                    "event_id": f"T{tick}_EVENT_{order}",
                    "order": order,
                    "intent_ref": intention["action_id"],
                    "actor_id": actor_id,
                    "visible_actor": next(
                        card["display_name"]
                        for card in packet["character_cards"]
                        if card["actor_id"] == actor_id
                    ),
                    "action": intention["chosen_action"],
                    "observable_result": f"事件{order}产生可见后果",
                    "observable_by": observers,
                    "hidden_cause": f"不应进入POV轨迹的隐藏原因{order}",
                    "pov_may_infer": "只能判断行动已经发生",
                    "state_changes": [f"状态变化{order}"],
                }
            )
        return {
            "schema": "novel-world-resolution",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": scene_packet_fingerprint(packet),
            "tick": tick,
            "intent_fingerprints": {
                actor_id: fingerprint(value)
                for actor_id, value in intentions.items()
            },
            "events": events,
            "world_state_after": [f"{actor_id}行动已结算" for actor_id in actor_ids],
            "unresolved_pressure": "身份仍未确认，边界仍需维持",
            "rule_ids_used": ["R001", "R002"],
            "unconfirmed_truths_preserved": True,
            "no_prose": True,
        }

    def test_frozen_packet_is_valid_and_bound_to_current_sources(self):
        checks = validate_scene_packet(ROOT, self.packet())
        self.assertTrue(any("source hashes" in item for item in checks))
        self.assertTrue(any("packet fingerprint" in item for item in checks))

    def test_character_intention_rejects_author_knowledge(self):
        packet = self.packet()
        intentions = self.intentions(packet)
        intention = intentions["zhang_dong"]
        validate_character_intention(
            ROOT, packet, intention, actor_id="zhang_dong", tick=1
        )
        changed = copy.deepcopy(intention)
        changed["used_fact_ids"].append("IN-K01")
        with self.assertRaisesRegex(ArtifactValidationError, "越界事实"):
            validate_character_intention(
                ROOT, packet, changed, actor_id="zhang_dong", tick=1
            )

    def test_world_resolution_is_bound_to_all_intentions(self):
        packet = self.packet()
        intentions = self.intentions(packet)
        resolution = self.resolution(packet, intentions)
        checks = validate_world_resolution(
            ROOT, packet, resolution, intentions, tick=1
        )
        self.assertTrue(any("observer references" in item for item in checks))

        changed = copy.deepcopy(resolution)
        changed["intent_fingerprints"]["father"] = "0" * 64
        with self.assertRaisesRegex(ArtifactValidationError, "意图指纹"):
            validate_world_resolution(
                ROOT, packet, changed, intentions, tick=1
            )

    def test_pov_trace_deterministically_removes_hidden_fields(self):
        packet = self.packet()
        intentions = self.intentions(packet)
        resolution = self.resolution(packet, intentions)
        trace = build_pov_event_trace(
            ROOT, packet, resolution, intentions, tick=1
        )
        encoded = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn("hidden_cause", encoded)
        self.assertNotIn("隐藏原因", encoded)
        expected = sum(
            "zhang_dong" in event["observable_by"]
            for event in resolution["events"]
        )
        self.assertEqual(len(trace["visible_events"]), expected)
        self.assertTrue(trace["hidden_fields_removed"])

    def test_rolling_horizon_must_keep_far_milestone_and_cite_events(self):
        packet = self.packet()
        intentions = self.intentions(packet)
        resolution = self.resolution(packet, intentions)
        horizon = {
            "schema": "novel-rolling-horizon",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": scene_packet_fingerprint(packet),
            "after_resolution_fingerprint": fingerprint(resolution),
            "retained_far_milestone": packet["public_seed"]["far_milestone"],
            "old_beat_ids": ["H2-VERIFICATION-COST"],
            "forcing_event_ids": [resolution["events"][0]["event_id"]],
            "revision_reason": "第一项行动改变了第二节拍的可用边界",
            "revised_beats": [
                {
                    "beat_id": "RH2-NEW-PRESSURE",
                    "pressure": "角色必须面对已经发生的边界变化",
                    "open_question": "谁愿意承担下一次验证的风险？",
                    "why_tentative": "角色仍可拒绝或改变行动",
                    "invalidated_by": resolution["events"][0]["event_id"],
                }
            ],
            "no_prose": True,
        }
        validate_rolling_horizon(ROOT, packet, resolution, horizon)
        changed = copy.deepcopy(horizon)
        changed["retained_far_milestone"] = "提前确认身份"
        with self.assertRaisesRegex(ArtifactValidationError, "远期里程碑"):
            validate_rolling_horizon(ROOT, packet, resolution, changed)

    def test_blind_bundle_hides_conditions_and_refuses_overwrite(self):
        packet = self.packet()
        candidates = {
            "contract_first": "甲" * 1500,
            "simulation_fixed": "乙" * 1500,
            "simulation_rolling": "丙" * 1500,
        }
        work_root = ROOT / "novel/work"
        work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work_root) as tmp:
            output = Path(tmp)
            bundle = prepare_blind_bundle(
                ROOT, packet, candidates, output, seed="fixed-test-seed"
            )
            blind = bundle["blind_packet"]
            self.assertEqual(set(blind["candidates"]), {"A", "B", "C"})
            self.assertNotIn("contract_first", json.dumps(blind, ensure_ascii=False))
            self.assertEqual(blind["workflow_state"], "WAITING_USER")
            with self.assertRaisesRegex(ArtifactValidationError, "拒绝覆盖"):
                prepare_blind_bundle(
                    ROOT, packet, candidates, output, seed="fixed-test-seed"
                )

    def test_comparison_is_bound_to_blind_text_and_verbatim_quotes(self):
        packet = self.packet()
        candidates = {
            "A": "甲端着冷饭，先看了一眼门闩。\n",
            "B": "乙没有回答，只把手收了回去。\n",
            "C": "丙去扶木楔，嘴里还在算那笔钱。\n",
        }
        comparison = {
            "schema": "novel-scene-experiment-comparison",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": scene_packet_fingerprint(packet),
            "candidate_fingerprints": {
                label: text_sha256(text) for label, text in candidates.items()
            },
            "ranking": ["C", "B", "A"],
            "preferred_candidate": "C",
            "confidence": "MEDIUM",
            "dimension_winners": {
                "character_specificity": "C",
                "causal_life": "C",
                "reaction_naturalness": "B",
                "explanation_restraint": "B",
                "prose_naturalness": "A",
                "serial_pull": "C",
            },
            "candidate_findings": [
                {
                    "candidate": label,
                    "strengths": [{"quote": text[:4], "finding": "人物动作可见"}],
                    "gaps": [],
                }
                for label, text in candidates.items()
            ],
            "all_candidates_need_revision": False,
            "decision_reasons": ["C 的选择与人物眼前损失结合更紧。"],
            "user_questions": ["最愿意继续读哪一版？"],
        }
        validate_scene_experiment_comparison(
            ROOT, packet, comparison, candidates
        )
        changed = copy.deepcopy(comparison)
        changed["candidate_findings"][0]["strengths"][0]["quote"] = "不存在"
        with self.assertRaisesRegex(ArtifactValidationError, "不在正文"):
            validate_scene_experiment_comparison(
                ROOT, packet, changed, candidates
            )


if __name__ == "__main__":
    unittest.main()
