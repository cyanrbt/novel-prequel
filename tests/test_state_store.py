import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.errors import StateValidationError
from scripts.prequel.state_store import atomic_save_state, load_state, validate_state


class StateStoreTests(unittest.TestCase):
    def test_broken_json_is_not_replaced_with_default_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.json"
            path.write_text('{"chapter": ', encoding="utf-8")
            with self.assertRaises(StateValidationError):
                load_state(path)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"chapter": ')

    def test_missing_required_fields_are_reported(self):
        errors = validate_state({"schema": "novel-prequel-state"})
        rendered = " ".join(errors)
        self.assertIn("machine_state", rendered)
        self.assertIn("chapter", rendered)
        self.assertIn("timeline", rendered)

    def test_state_requires_stable_schema_identifier(self):
        state = json.loads(
            Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["schema"], "novel-prequel-state")
        rendered = " ".join(validate_state({"schema": "numeric-format"}))
        self.assertIn("schema 必须为 novel-prequel-state", rendered)

    def test_atomic_save_leaves_valid_json_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "current.json"
            original = json.loads(
                Path("tests/fixtures/valid_state.json").read_text(encoding="utf-8")
            )
            atomic_save_state(path, original)
            updated = dict(original)
            updated["machine_state"] = "REVIEW"
            atomic_save_state(path, updated)
            self.assertEqual(load_state(path)["machine_state"], "REVIEW")
            self.assertTrue(path.with_suffix(".json.bak").exists())

    def test_project_state_has_canonical_timeline(self):
        root = Path(__file__).resolve().parents[1]
        state = load_state(root / "novel/state/current.json")
        formal_numbers = sorted(
            int(path.stem.rsplit("_", 1)[-1])
            for path in (root / "novel/chapters").glob("vol_*/chapter_*.txt")
        )
        self.assertEqual(state["timeline"]["current_year"], 1911)
        self.assertEqual(state["protagonist"]["age"], 17)
        self.assertEqual(
            formal_numbers,
            list(range(1, state["chapter"]["last_chapter"] + 1)),
        )
        self.assertEqual(
            state["chapter"]["next_chapter"],
            state["chapter"]["last_chapter"] + 1,
        )
        self.assertNotIn("birth_year_range", state["characters"]["pending"]["秦"])
        self.assertNotIn("周正", state["characters"].get("active", {}))

    def test_canon_registry_has_three_confidence_levels(self):
        registry = json.loads(
            Path("novel/knowledge/canon_registry.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(registry["confidence_levels"]), {"A", "B", "C"})
        self.assertIn("周正", registry["era_bans"]["1890-1950"]["characters"])
        self.assertIn("负责人", registry["era_bans"]["1890-1950"]["terms"])


if __name__ == "__main__":
    unittest.main()
