from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts.prequel.audit_profiles import load_audit_profile
from scripts.prequel.pipeline import run_preflight
from scripts.prequel.project import (
    activate_project,
    load_project_spec,
    load_role_text,
    reset_active_project,
)
from scripts.prequel.scene_audit import extract_scene_audit_anchors


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/decoupled_story/project.json"


class ProjectDecouplingTests(unittest.TestCase):
    def test_default_project_merges_engine_and_story_configuration(self):
        spec = load_project_spec(ROOT)
        config = spec.load_config()

        self.assertEqual(spec.project_id, "zhangdong-prequel")
        self.assertEqual(config["schema"], "creative-story-config/1")
        self.assertIn("quality_evolution", config)
        self.assertEqual(config["protagonist"], "张洞")
        self.assertEqual(
            spec.path("state"), (ROOT / "novel/state/current.json").resolve()
        )

    def test_unrelated_story_uses_its_own_plot_state_and_role(self):
        spec = load_project_spec(ROOT, FIXTURE)
        token = activate_project(spec)
        try:
            config = spec.load_config()
            role = load_role_text(ROOT, "planner")
        finally:
            reset_active_project(token)

        self.assertEqual(spec.title, "《山风测站》")
        self.assertEqual(config["protagonist"], "远岫")
        self.assertEqual(spec.profiles, ())
        self.assertIn("当代山地现实题材", role)
        self.assertNotIn("张洞", role)
        self.assertNotIn("灵异", role)

    def test_unrelated_story_passes_real_preflight_without_code_changes(self):
        spec = load_project_spec(ROOT, FIXTURE)
        token = activate_project(spec)
        try:
            checks = run_preflight(ROOT)
        finally:
            reset_active_project(token)

        self.assertIn("state schema validated", checks)
        self.assertIn("formal chapters contiguous: empty baseline", checks)
        self.assertIn("positive voice profile calibrated by user blind selection", checks)

    def test_genre_audits_are_selected_by_project_profile(self):
        horror = load_audit_profile(ROOT)
        fixture_spec = load_project_spec(ROOT, FIXTURE)
        token = activate_project(fixture_spec)
        try:
            realism = load_audit_profile(ROOT)
        finally:
            reset_active_project(token)

        draft = "他看见棺木停在院墙外，又跨过了院墙。"
        horror_anchors = extract_scene_audit_anchors(draft, horror)
        realism_anchors = extract_scene_audit_anchors(draft, realism)
        self.assertEqual(horror["active_profiles"], ["horror-mystery"])
        self.assertEqual(realism["active_profiles"], [])
        self.assertTrue(horror["evidence_hierarchy"]["enabled"])
        self.assertFalse(realism["evidence_hierarchy"]["enabled"])
        self.assertGreater(
            len(horror_anchors["boundary_actions"]),
            len(realism_anchors["boundary_actions"]),
        )

    def test_story_and_genre_role_overlays_do_not_leak_into_base_role(self):
        base = (ROOT / "agents/reviewer_character.md").read_text(encoding="utf-8")
        composed = load_role_text(ROOT, "reviewer_character")

        self.assertNotIn("疑似死者声音", base)
        self.assertIn("疑似死者声音", composed)
        self.assertIn("本项目当前主角是十七岁的张洞", composed)

    def test_cli_selects_project_manifest(self):
        result = subprocess.run(
            [
                sys.executable,
                "scripts/orchestrator.py",
                "--project",
                str(FIXTURE.relative_to(ROOT)),
                "status",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("《山风测站》 创作状态", result.stdout)
        self.assertIn("第1章待写", result.stdout)


if __name__ == "__main__":
    unittest.main()
