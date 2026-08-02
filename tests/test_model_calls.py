import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ProviderError
from scripts.prequel.model_calls import ModelCallExecutor
from scripts.prequel.model_router import StageModelRouter
from scripts.prequel.run_manifest import RunManifest


class RecordingProvider:
    model = "gpt-5.6-terra"
    reasoning_effort = "medium"

    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = 0

    def generate(self, prompt, output_schema=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output


class ModelCallTests(unittest.TestCase):
    def make_executor(self, root: Path, provider, limit=10, events=None):
        workspace = ChapterWorkspace.create(root, 1, 1)
        manifest = RunManifest.create(workspace, 1, "state", call_limit=limit)
        executor = ModelCallExecutor(
            StageModelRouter.single(provider),
            manifest,
            None if events is None else events.append,
        )
        return executor, manifest

    def test_success_emits_started_and_completed_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            provider = RecordingProvider(output="{}")
            executor, _ = self.make_executor(Path(tmp), provider, events=events)
            executor.call("planner", "p", None, "PLAN")
            self.assertEqual(
                [item["kind"] for item in events],
                ["CALL_STARTED", "CALL_COMPLETED"],
            )
            self.assertEqual(events[0]["call_id"], events[1]["call_id"])
            self.assertEqual(events[0]["model"], "gpt-5.6-terra")
            self.assertEqual(events[0]["reasoning_effort"], "medium")
            self.assertIn("duration_ms", events[1])

    def test_failure_event_is_safe_and_uses_same_call_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            provider = RecordingProvider(error=ProviderError("boom"))
            executor, _ = self.make_executor(Path(tmp), provider, events=events)
            with self.assertRaises(ProviderError):
                executor.call("planner", "secret prompt", None, "PLAN")
            self.assertEqual(
                [item["kind"] for item in events],
                ["CALL_STARTED", "CALL_FAILED"],
            )
            self.assertEqual(events[0]["call_id"], events[1]["call_id"])
            self.assertEqual(events[1]["error_code"], "ProviderError")
            self.assertNotIn("prompt", events[0])
            self.assertNotIn("prompt", events[1])

    def test_progress_sink_failure_does_not_abort_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingProvider(output="{}")
            workspace = ChapterWorkspace.create(Path(tmp), 1, 1)
            manifest = RunManifest.create(workspace, 1, "state", call_limit=10)

            def broken_sink(event):
                raise RuntimeError("terminal closed")

            executor = ModelCallExecutor(
                StageModelRouter.single(provider), manifest, broken_sink
            )
            self.assertEqual(executor.call("planner", "p", None, "PLAN"), "{}")
            self.assertEqual(provider.calls, 1)
            self.assertEqual(manifest.data["budget"]["spent"], 1)

    def test_success_is_recorded_with_resolved_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingProvider(output="{}")
            executor, manifest = self.make_executor(Path(tmp), provider)
            self.assertEqual(executor.call("planner", "p", None, "PLAN"), "{}")
            record = next(iter(manifest.data["budget"]["calls"].values()))
            self.assertEqual(record["status"], "COMPLETED")
            self.assertEqual(record["model"], "gpt-5.6-terra")
            self.assertEqual(manifest.data["budget"]["spent"], 1)

    def test_provider_failure_still_spends_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingProvider(error=ProviderError("boom"))
            executor, manifest = self.make_executor(Path(tmp), provider)
            with self.assertRaises(ProviderError):
                executor.call("planner", "p", None, "PLAN")
            self.assertEqual(manifest.data["budget"]["remaining"], 9)
            record = next(iter(manifest.data["budget"]["calls"].values()))
            self.assertEqual(record["status"], "FAILED")

    def test_reserved_pair_can_be_called_without_double_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = RecordingProvider(output="正文")
            executor, manifest = self.make_executor(Path(tmp), provider, limit=2)
            reservations = executor.reserve_many(
                [("reviser", "REVISE"), ("verifier", "VERIFY")]
            )
            executor.call_reserved(reservations[0], "p", None)
            executor.cancel_before_provider(reservations[1])
            self.assertEqual(provider.calls, 1)
            self.assertEqual(manifest.data["budget"]["spent"], 1)


if __name__ == "__main__":
    unittest.main()
