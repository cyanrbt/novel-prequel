import unittest
from datetime import datetime, timezone

from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.metrics import benchmark_summary, chapter_metrics


def manifest(*, shadow=True, status="WAITING_USER"):
    calls = {
        f"call_{index:03d}": {
            "status": "COMPLETED",
            "model": "gpt-5.6-sol" if index <= 3 else "gpt-5.6-terra",
            "duration_ms": 1000,
        }
        for index in range(1, 9)
    }
    return {
        "status": status,
        "started_at": "2026-08-02T00:00:00+00:00",
        "finished_at": "2026-08-02T00:20:00+00:00",
        "budget": {"limit": 10, "calls": calls},
        "decision": {
            "candidates": {"candidate_01": {"classification": "ELIGIBLE"}},
            "specialist_history": [
                {
                    "reason_code": "BENCHMARK_SHADOW_REVIEW",
                    "completed": True,
                    "classification_before": "ELIGIBLE",
                    "classification_after": "ELIGIBLE",
                }
            ] if shadow else [],
        },
    }


class MetricsTests(unittest.TestCase):
    def test_chapter_metrics_counts_models_and_wall_time(self):
        value = chapter_metrics(manifest())
        self.assertEqual(value["calls_total"], 8)
        self.assertEqual(value["calls_by_model"]["gpt-5.6-sol"], 3)
        self.assertEqual(value["wall_time_seconds"], 1200)
        self.assertEqual(value["model_call_time_seconds"], 8)

    def test_missing_timestamps_do_not_fall_back_to_call_sum(self):
        value = manifest()
        value.pop("started_at")
        value.pop("finished_at")
        metrics = chapter_metrics(value)
        self.assertIsNone(metrics["wall_time_seconds"])
        self.assertEqual(metrics["model_call_time_seconds"], 8)

    def test_running_manifest_uses_current_wall_time(self):
        value = manifest(status="RUNNING")
        value["started_at"] = datetime.now(timezone.utc).isoformat()
        value["finished_at"] = None
        metrics = chapter_metrics(value)
        self.assertIsNotNone(metrics["wall_time_seconds"])
        self.assertGreaterEqual(metrics["wall_time_seconds"], 0)

    def test_benchmark_requires_exactly_ten_runs(self):
        with self.assertRaises(ArtifactValidationError):
            benchmark_summary([manifest()] * 9)

    def test_acceptance_flags_match_approved_thresholds(self):
        result = benchmark_summary([manifest() for _ in range(10)])
        self.assertTrue(all(result["acceptance"].values()))

    def test_legacy_replan_is_excluded(self):
        result = benchmark_summary(
            [{"status": "REPLAN", "stages": {}}, *[manifest() for _ in range(10)]]
        )
        self.assertEqual(result["runs"], 10)
        self.assertEqual(result["excluded_legacy_runs"], 1)


if __name__ == "__main__":
    unittest.main()
