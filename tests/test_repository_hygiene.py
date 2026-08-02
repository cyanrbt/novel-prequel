import re
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryHygieneTests(unittest.TestCase):
    def is_ignored(self, relative: str) -> bool:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", relative],
            cwd=ROOT,
            check=False,
        )
        return result.returncode == 0

    def test_local_reference_material_has_ignore_rules(self):
        local_paths = (
            "shenmifushu.txt",
            ".local-reference/archive/审查报告_2026-07-02.md",
            ".local-reference/implementation-notes/specs/public-repository-design.md",
            ".local-reference/knowledge/dialogue/全人物对话风格库.md",
            ".local-reference/knowledge/raw_excerpts/人物统计表.md",
            ".local-reference/state/memory/short_term/current_state.md",
            ".local-reference/drafts/vol_01/chapter_003.txt",
        )
        for relative in local_paths:
            self.assertTrue(self.is_ignored(relative), relative)

    def test_codex_sqlite_runtime_stays_in_ignored_work_area(self):
        config = json.loads(
            (ROOT / "config/prequel_config.json").read_text(encoding="utf-8")
        )
        command = config["provider"]["command"]
        index = command.index("--config")
        self.assertEqual(
            command[index + 1],
            'sqlite_home="novel/work/.codex-runtime"',
        )
        self.assertTrue(
            self.is_ignored("novel/work/.codex-runtime/state.sqlite")
        )

    def test_operational_files_use_stable_identifiers(self):
        paths = (
            "README.md",
            "init.md",
            "config/prequel_config.json",
            "schemas/state.schema.json",
            "scripts/prequel/state_store.py",
            "scripts/prequel/pipeline.py",
            "novel/state/current.json",
            "novel/knowledge/canon_registry.json",
            "novel/knowledge/README.md",
            "novel/rules/rulebook.md",
            "novel/style/compact_style.yaml",
        )
        numbered_label = re.compile(r"\b" + chr(118) + r"\d+(?:\.\d+)*\b", re.I)
        retired_schema_key = "schema" + "_" + "version"
        retired_anchor_name = "style_anchors" + "_v2"
        machine_home = "/" + "home/"
        for relative in paths:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIsNone(numbered_label.search(text), relative)
            self.assertNotIn(retired_schema_key, text, relative)
            self.assertNotIn(retired_anchor_name, text, relative)
            self.assertNotIn(machine_home, text, relative)

    def test_readme_local_links_exist(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        image_targets = re.findall(r'src="([^"#]+)"', text)
        link_targets = re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", text)
        for target in image_targets + link_targets:
            if "://" in target:
                continue
            self.assertTrue((ROOT / target).exists(), target)

    def test_readme_documents_quality_evolution_commands(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in ("next --resume", "accept --candidate", "audit --arc"):
            self.assertIn(command, text)

    def test_engine_manual_links_to_quality_design(self):
        text = (ROOT / "init.md").read_text(encoding="utf-8")
        self.assertIn(
            "docs/superpowers/specs/2026-08-01-quality-evolution-pipeline-design.md",
            text,
        )


if __name__ == "__main__":
    unittest.main()
