import copy
import hashlib
import json
import unittest
from pathlib import Path

from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.prompt_native import validate_style_comparison


ROOT = Path(__file__).resolve().parents[1]


class StyleCalibrationTests(unittest.TestCase):
    def comparison_fixture(self) -> tuple[dict, dict[str, str]]:
        candidates = {
            "A": "甲端着冷饭，先看了一眼账本。",
            "B": "乙没有回答，只把门重新关上。",
            "C": "丙笑了一声，碗里的水却没有动。",
        }
        comparison = {
            "schema": "novel-style-comparison",
            "calibration_id": "calibration-test",
            "source_fingerprint": "a" * 64,
            "candidate_fingerprints": {
                label: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for label, text in candidates.items()
            },
            "ranking": ["B", "A", "C"],
            "preferred_candidate": "B",
            "confidence": "MEDIUM",
            "dimension_winners": {
                "natural_narration": "B",
                "character_presence": "A",
                "explanation_restraint": "B",
                "horror_causality": "C",
                "dialogue_voice": "B",
                "serial_pull": "C",
            },
            "candidate_findings": [
                {
                    "candidate": label,
                    "strengths": [{"quote": text[:4], "finding": "可读"}],
                    "gaps": [],
                }
                for label, text in candidates.items()
            ],
            "all_candidates_need_revision": False,
            "decision_reasons": ["B 的人物反应更自然。"],
            "user_questions": ["最愿意继续读哪一版？"],
        }
        return comparison, candidates

    def test_positive_profile_is_explicitly_pending_user_calibration(self):
        profile = (ROOT / "novel/style/reference_voice_profile.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("schema: novel-reference-voice-profile", profile)
        self.assertIn("calibration_status: CALIBRATING", profile)
        self.assertIn("至少完成一轮三候选盲选", profile)

    def test_writer_role_is_compact_and_positive(self):
        writer = (ROOT / "agents/writer.md").read_text(encoding="utf-8")
        self.assertLess(len(writer), 9000)
        for phrase in (
            "正向写作原则",
            "防止提示词泄漏进正文",
            "不把场景整理成完整证明链",
        ):
            self.assertIn(phrase, writer)

    def test_calibration_workflow_freezes_three_independent_strategies(self):
        workflow = (ROOT / "workflows/style-calibration.md").read_text(
            encoding="utf-8"
        )
        for strategy in (
            "plain_cold_narration",
            "character_interest_filter",
            "ordinary_life_intrusion",
        ):
            self.assertIn(strategy, workflow)
        self.assertIn("候选之间不得相互读取", workflow)
        self.assertIn("不得覆盖正式第1章", workflow)

    def test_style_comparison_schema_requires_blind_three_way_ranking(self):
        schema = json.loads(
            (ROOT / "schemas/style_comparison.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            schema["properties"]["schema"]["const"],
            "novel-style-comparison",
        )
        ranking = schema["properties"]["ranking"]
        self.assertEqual(ranking["minItems"], 3)
        self.assertEqual(ranking["maxItems"], 3)
        self.assertTrue(ranking["uniqueItems"])

    def test_style_comparison_is_bound_to_candidates_and_verbatim_quotes(self):
        comparison, candidates = self.comparison_fixture()
        checks = validate_style_comparison(
            ROOT,
            comparison,
            candidates,
            source_fingerprint="a" * 64,
            calibration_id="calibration-test",
        )
        self.assertTrue(any("verbatim quotes" in item for item in checks))

        mutations = []
        changed = copy.deepcopy(comparison)
        changed["preferred_candidate"] = "A"
        mutations.append((changed, "preferred_candidate"))
        changed = copy.deepcopy(comparison)
        changed["candidate_fingerprints"]["A"] = "0" * 64
        mutations.append((changed, "候选指纹"))
        changed = copy.deepcopy(comparison)
        changed["candidate_findings"][2]["candidate"] = "A"
        mutations.append((changed, "candidate_findings"))
        changed = copy.deepcopy(comparison)
        changed["candidate_findings"][0]["strengths"][0]["quote"] = "不存在的引用"
        mutations.append((changed, "观察引用"))

        for changed, message in mutations:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ArtifactValidationError, message):
                    validate_style_comparison(ROOT, changed, candidates)

    def test_core_config_registers_platform_neutral_style_roles(self):
        config = json.loads(
            (ROOT / "stories/zhangdong/story_config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            config["agents"]["prose_director"], "agents/prose_director.md"
        )
        self.assertEqual(
            config["agents"]["reference_style_reviewer"],
            "agents/reference_style_reviewer.md",
        )


if __name__ == "__main__":
    unittest.main()
