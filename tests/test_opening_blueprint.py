import json
import unittest
from pathlib import Path

from scripts.prequel.context_builder import (
    build_chapter_context_pack,
    build_planner_context,
)


ROOT = Path(__file__).resolve().parents[1]


class OpeningBlueprintTests(unittest.TestCase):
    def setUp(self):
        self.state = json.loads(
            (ROOT / "novel/state/current.json").read_text(encoding="utf-8")
        )

    def test_opening_card_is_not_injected(self):
        context = build_planner_context(ROOT, self.state)

        self.assertEqual(context["chapter_blueprint"], "")
        self.assertIn("创作脊柱", context["series_architecture"])
        self.assertIn("不得执行", context["event_outline"])
        self.assertNotIn("粮棚", context["event_outline"])
        self.assertNotIn("## 第2章", context["series_architecture"])

        pack = build_chapter_context_pack(self.state, context, [])
        self.assertEqual(pack["core"]["chapter_blueprint"], "")


if __name__ == "__main__":
    unittest.main()
