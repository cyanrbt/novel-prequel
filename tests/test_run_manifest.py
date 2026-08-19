import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.run_manifest import RunManifest, fingerprint


class RunManifestTests(unittest.TestCase):
    def test_nested_candidate_artifact_is_allowed_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            workspace.write_text(
                "candidates/candidate_01/draft.txt", "第3章：试门"
            )
            self.assertEqual(
                len(workspace.digest("candidates/candidate_01/draft.txt")), 64
            )

    def test_parent_escape_and_unknown_leaf_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            for name in (
                "../chapter_003.txt",
                "candidates/candidate_01/formal.txt",
            ):
                with self.assertRaises(ArtifactValidationError):
                    workspace.write_text(name, "越界")

    def test_completed_stage_requires_matching_inputs_and_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            manifest = RunManifest.create(workspace, 3, "state-hash")
            path = "candidates/candidate_01/draft.txt"
            workspace.write_text(path, "正文")
            manifest.complete("candidate.01.generate", "input-hash", [path])
            self.assertTrue(
                manifest.can_reuse("candidate.01.generate", "input-hash")
            )
            workspace.write_text(path, "被修改")
            self.assertFalse(
                manifest.can_reuse("candidate.01.generate", "input-hash")
            )

    def test_promotion_integrity_check_rejects_modified_stage_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            manifest = RunManifest.create(workspace, 3, "state-hash")
            path = "draft.txt"
            workspace.write_text(path, "正文")
            manifest.complete("manual_import", "input-hash", [path])
            self.assertEqual(
                manifest.require_stage_outputs("manual_import")["status"],
                "COMPLETED",
            )
            workspace.write_text(path, "被修改")
            with self.assertRaises(ArtifactValidationError):
                manifest.require_stage_outputs("manual_import")

    def test_fingerprint_is_stable_for_key_order(self):
        self.assertEqual(
            fingerprint({"b": 2, "a": 1}),
            fingerprint({"a": 1, "b": 2}),
        )

    def test_route_fingerprint_change_prevents_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            manifest = RunManifest.create(workspace, 3, "state-hash")
            path = "candidates/candidate_01/draft.txt"
            workspace.write_text(path, "正文")
            manifest.complete(
                "generate",
                "input",
                [path],
                {
                    "model_profile": "sol_medium",
                    "prompt_version": "v1",
                    "call_count": 1,
                    "route_fingerprint": "route-a",
                },
            )
            self.assertTrue(manifest.can_reuse("generate", "input", "route-a"))
            self.assertFalse(manifest.can_reuse("generate", "input", "route-b"))


if __name__ == "__main__":
    unittest.main()
