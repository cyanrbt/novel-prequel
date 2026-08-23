import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StyleCalibrationTests(unittest.TestCase):
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

    def test_core_config_registers_platform_neutral_style_roles(self):
        config = json.loads(
            (ROOT / "config/prequel_config.json").read_text(encoding="utf-8")
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
