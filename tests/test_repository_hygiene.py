import json
import re
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

    def test_agent_cli_launchers_are_absent(self):
        retired = (
            "config/execution.example.json",
            "scripts/prequel/cli_capabilities.py",
            "scripts/scene_generation_experiment.py",
            "scripts/provider_style_benchmark.py",
            "scripts/provider_style_benchmark_supplement.py",
        )
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)

        markers = (
            "co" + "dex exec",
            "Codex" + "CliProvider",
            "Agy" + "CliProvider",
            "OpenCode" + "CliProvider",
            "Grok" + "CliProvider",
        )
        for path in (ROOT / "scripts").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for marker in markers:
                self.assertNotIn(marker, source, str(path.relative_to(ROOT)))

    def test_core_config_does_not_select_an_agent_or_model(self):
        config = json.loads(
            (ROOT / "config/prequel_config.json").read_text(encoding="utf-8")
        )
        for key in ("provider", "model_profiles", "stage_routes"):
            self.assertNotIn(key, config)

    def test_operational_files_use_stable_identifiers(self):
        paths = (
            "README.md",
            "WORKFLOW.md",
            "init.md",
            "config/prequel_config.json",
            "schemas/task_envelope.schema.json",
            "schemas/agent_result.schema.json",
            "schemas/protocol_smoke_artifact.schema.json",
            "schemas/style_comparison.schema.json",
            "schemas/state.schema.json",
            "scripts/prequel/state_store.py",
            "scripts/prequel/pipeline.py",
            "novel/state/current.json",
            "novel/knowledge/canon_registry.json",
            "novel/knowledge/README.md",
            "novel/rules/rulebook.md",
            "novel/style/compact_style.yaml",
            "novel/style/reference_voice_profile.md",
            "agents/prose_director.md",
            "agents/reference_style_reviewer.md",
            "workflows/style-calibration.md",
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

    def test_readme_documents_prompt_native_and_deterministic_commands(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in (
            "workflow-check",
            "accept --candidate",
            "scene-experiment validate",
        ):
            self.assertIn(command, text)
        self.assertIn("当前 Agent", text)
        self.assertNotIn("orchestrator.py next", text)

    def test_engine_manual_links_to_quality_design(self):
        text = (ROOT / "init.md").read_text(encoding="utf-8")
        self.assertIn(
            "docs/superpowers/specs/2026-08-01-quality-evolution-pipeline-design.md",
            text,
        )


if __name__ == "__main__":
    unittest.main()
