import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.call_budget import CallBudget
from scripts.prequel.errors import CallBudgetExceeded
from scripts.prequel.model_router import ResolvedModelSettings
from scripts.prequel.run_manifest import RunManifest


SETTINGS = ResolvedModelSettings(
    "terra_medium", "gpt-5.6-terra", "medium"
)


class CallBudgetTests(unittest.TestCase):
    def make_manifest(self, root: Path, limit: int) -> RunManifest:
        workspace = ChapterWorkspace.create(root, 1, 1)
        return RunManifest.create(
            workspace, 1, "state", call_limit=limit, mode="balanced"
        )

    def test_eleventh_reservation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget = CallBudget(self.make_manifest(Path(tmp), 10))
            for _ in range(10):
                budget.reserve("stage", SETTINGS, "TEST")
            with self.assertRaises(CallBudgetExceeded):
                budget.reserve("stage", SETTINGS, "TEST_11")
            self.assertEqual(budget.remaining, 0)

    def test_two_threads_cannot_oversubscribe_last_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget = CallBudget(self.make_manifest(Path(tmp), 1))
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def worker():
                barrier.wait()
                try:
                    value = budget.reserve("candidate", SETTINGS, "RACE").call_id
                except CallBudgetExceeded:
                    value = "blocked"
                with lock:
                    outcomes.append(value)

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: worker(), range(2)))
            self.assertEqual(len([x for x in outcomes if x != "blocked"]), 1)

    def test_stale_active_reservation_is_released_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_manifest(Path(tmp), 10)
            budget = CallBudget(manifest)
            reservation = budget.reserve("planner", SETTINGS, "PLAN")
            self.assertEqual(budget.remaining, 9)
            reloaded = RunManifest.load(manifest.workspace)
            CallBudget(reloaded).recover_interrupted()
            call = reloaded.data["budget"]["calls"][reservation.call_id]
            self.assertEqual(call["status"], "CANCELLED")
            self.assertEqual(reloaded.data["budget"]["spent"], 0)
            self.assertEqual(reloaded.data["budget"]["remaining"], 10)

    def test_interrupted_running_call_counts_as_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_manifest(Path(tmp), 10)
            budget = CallBudget(manifest)
            reservation = budget.reserve("planner", SETTINGS, "PLAN")
            budget.mark_running(reservation)
            reloaded = RunManifest.load(manifest.workspace)
            CallBudget(reloaded).recover_interrupted()
            call = reloaded.data["budget"]["calls"][reservation.call_id]
            self.assertEqual(call["status"], "FAILED")
            self.assertEqual(call["error_code"], "INTERRUPTED")
            self.assertEqual(reloaded.data["budget"]["spent"], 1)

    def test_reserve_many_is_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            budget = CallBudget(self.make_manifest(Path(tmp), 1))
            with self.assertRaises(CallBudgetExceeded):
                budget.reserve_many(
                    [
                        ("reviser", SETTINGS, "REVISE"),
                        ("verifier", SETTINGS, "VERIFY"),
                    ]
                )
            self.assertEqual(budget.remaining, 1)

    def test_cancelled_unstarted_reservation_does_not_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = self.make_manifest(Path(tmp), 1)
            budget = CallBudget(manifest)
            reservation = budget.reserve("verifier", SETTINGS, "VERIFY")
            budget.cancel_before_provider(reservation)
            self.assertEqual(budget.remaining, 1)
            self.assertEqual(manifest.data["budget"]["spent"], 0)


if __name__ == "__main__":
    unittest.main()
