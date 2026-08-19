import copy
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

    def test_current_opening_chapter_is_injected_without_future_cards(self):
        context = build_planner_context(ROOT, self.state)
        blueprint = context["chapter_blueprint"]
        next_chapter = self.state["chapter"]["next_chapter"]

        self.assertRegex(blueprint, rf"(?m)^## 第{next_chapter}章")
        if next_chapter > 1:
            self.assertNotRegex(
                blueprint, rf"(?m)^## 第{next_chapter - 1}章"
            )
        if next_chapter < 16:
            self.assertNotRegex(
                blueprint, rf"(?m)^## 第{next_chapter + 1}章"
            )
        self.assertIn("## 写作执行红线", blueprint)

        pack = build_chapter_context_pack(self.state, context, [])
        self.assertEqual(pack["core"]["chapter_blueprint"], blueprint)

    def test_blueprint_is_disabled_after_chapter_sixteen(self):
        state = copy.deepcopy(self.state)
        state["chapter"]["last_chapter"] = 16
        state["chapter"]["next_chapter"] = 17

        context = build_planner_context(ROOT, state)

        self.assertEqual(context["chapter_blueprint"], "")


if __name__ == "__main__":
    unittest.main()
