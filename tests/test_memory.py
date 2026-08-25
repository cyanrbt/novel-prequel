import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.memory import MemoryStore
from tests.project_fixture import write_project_manifest


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "novel/knowledge").mkdir(parents=True)
        (self.root / "novel/chapters/vol_01").mkdir(parents=True)
        (self.root / "novel/knowledge/memory_index.json").write_text(
            '{"schema":"novel-memory-index","entries":[]}', encoding="utf-8"
        )
        (self.root / "novel/knowledge/quality_lessons.json").write_text(
            '{"schema":"novel-quality-lessons","lessons":[]}', encoding="utf-8"
        )
        (self.root / "novel/knowledge/creative_debts.json").write_text(
            '{"schema":"novel-creative-debts","debts":[]}', encoding="utf-8"
        )
        write_project_manifest(self.root)
        self.store = MemoryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieve_uses_stable_exact_matches(self):
        chapter = self.root / "novel/chapters/vol_01/chapter_001.txt"
        chapter.write_text("第1章：纸灰", encoding="utf-8")
        entry = {
            "chapter": 1,
            "source_path": "novel/chapters/vol_01/chapter_001.txt",
            "source_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
            "characters": ["张洞"],
            "locations": ["祠堂"],
            "event_id": "event_1",
            "foreshadows": ["F-A01"],
            "irreversible_changes": ["known_info"],
            "hook_type": "安全区崩坏",
            "summary": "纸灰越界",
        }
        (self.root / "novel/knowledge/memory_index.json").write_text(
            json.dumps({"schema": "novel-memory-index", "entries": [entry]}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = self.store.retrieve(
            {
                "characters": ["张洞"],
                "event_id": "event_1",
                "locations": ["祠堂"],
                "foreshadows": ["F-A01"],
            }
        )
        self.assertEqual(result[0]["chapter"], 1)

    def test_changed_source_hash_invalidates_entry(self):
        chapter = self.root / "novel/chapters/vol_01/chapter_001.txt"
        chapter.write_text("原正文", encoding="utf-8")
        entry = {
            "chapter": 1,
            "source_path": "novel/chapters/vol_01/chapter_001.txt",
            "source_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(),
        }
        (self.root / "novel/knowledge/memory_index.json").write_text(
            json.dumps({"schema": "novel-memory-index", "entries": [entry]}),
            encoding="utf-8",
        )
        chapter.write_text("被修改的正式正文", encoding="utf-8")
        self.assertEqual(self.store.valid_entries(), [])

    def test_lesson_activates_and_retires(self):
        finding = {
            "code": "REPEATED_INVESTIGATION",
            "scope": {"event_id": "event_1"},
            "instruction": "改变调查信息抵达方式",
            "quote": "逐项核对",
        }
        for chapter in (2, 5, 9):
            self.store.update_lessons(chapter, [finding])
        self.assertEqual(self.store.active_lessons()[0]["status"], "active")
        self.store.retire_lessons(19)
        self.assertEqual(self.store.all_lessons()[0]["status"], "retired")

    def test_core_context_limits_lessons_to_eight(self):
        findings = [
            {
                "code": f"STYLE_{index}",
                "scope": {"characters": ["张洞"]},
                "instruction": f"避免模式{index}",
                "quote": f"证据{index}",
            }
            for index in range(12)
        ]
        for chapter in (1, 2, 3):
            self.store.update_lessons(chapter, findings)
        plan = {
            "event_id": "event_1",
            "scenes": [{"characters": ["张洞"], "location": "祠堂"}],
            "foreshadow_operations": {"plant": [], "recover": []},
        }
        self.assertEqual(len(self.store.core_context(plan)["lessons"]), 8)


if __name__ == "__main__":
    unittest.main()
