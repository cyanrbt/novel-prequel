from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .errors import ArtifactValidationError


def _load(value: dict[str, Any] | str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(value, dict):
        return value, value.get("decision", {})
    path = Path(value)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取指标清单 {path}: {exc}") from exc
    decision_path = path.parent / "decision.json"
    decision: dict[str, Any] = {}
    if decision_path.exists():
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError(f"无法读取决策工件 {decision_path}: {exc}") from exc
    return manifest, decision


def _wall_time_seconds(manifest: dict[str, Any]) -> float | None:
    started = manifest.get("started_at")
    finished = manifest.get("finished_at")
    if not started:
        return None
    try:
        start_time = datetime.fromisoformat(started)
        if finished:
            end_time = datetime.fromisoformat(finished)
        elif manifest.get("status") == "RUNNING":
            end_time = datetime.now(timezone.utc)
        else:
            return None
        return max(0.0, (end_time - start_time).total_seconds())
    except (TypeError, ValueError):
        return None


def _model_call_time_seconds(manifest: dict[str, Any]) -> float:
    return sum(
        (item.get("duration_ms") or 0)
        for item in manifest.get("budget", {}).get("calls", {}).values()
    ) / 1000


def chapter_metrics(value: dict[str, Any] | str | Path) -> dict[str, Any]:
    manifest, decision = _load(value)
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        raise ArtifactValidationError("旧清单没有新版调用预算")
    calls = list(budget.get("calls", {}).values())
    spent = [item for item in calls if item.get("status") in {"COMPLETED", "FAILED"}]
    models = Counter(item.get("model", "unknown") for item in spent)
    specialist = decision.get("specialist_history", [])
    shadows = [
        item
        for item in specialist
        if (
            item.get("shadow_review")
            or item.get("reason_code") == "BENCHMARK_SHADOW_REVIEW"
        )
        and item.get("completed")
    ]
    classifications = [
        item.get("classification")
        for item in decision.get("candidates", {}).values()
        if isinstance(item, dict)
    ]
    status = manifest.get("status", decision.get("status", "UNKNOWN"))
    return {
        "status": status,
        "calls_total": len(spent),
        "call_limit": budget.get("limit"),
        "calls_by_model": dict(sorted(models.items())),
        "wall_time_seconds": _wall_time_seconds(manifest),
        "model_call_time_seconds": _model_call_time_seconds(manifest),
        "eligible_or_near_miss": any(
            item in {"ELIGIBLE", "NEAR_MISS"} for item in classifications
        ),
        "silent_fallback": bool(
            decision.get("degraded")
            and not decision.get("automatic_retry_skipped_reason")
        ),
        "hard_fail_auto_promote": bool(
            status in {"AUTO_PROMOTE", "COMPLETED"}
            and decision.get("final_scorecard", {}).get("hard_failures")
        ),
        "shadow_reviews_completed": len(shadows),
        "shadow_hard_fail_misses": sum(
            item.get("classification_before") != "HARD_FAIL"
            and item.get("classification_after") == "HARD_FAIL"
            for item in shadows
        ),
        "shadow_classification_disagreements": sum(
            item.get("classification_before") != item.get("classification_after")
            for item in shadows
        ),
    }


def benchmark_summary(
    values: Iterable[dict[str, Any] | str | Path],
) -> dict[str, Any]:
    loaded = list(values)
    new_runs: list[dict[str, Any] | str | Path] = []
    excluded = 0
    for value in loaded:
        manifest, _ = _load(value)
        if manifest.get("status") == "REPLAN" or "budget" not in manifest:
            excluded += 1
        else:
            new_runs.append(value)
    if len(new_runs) != 10:
        raise ArtifactValidationError(
            f"十次试运行汇总需要恰好10份新版清单，当前为{len(new_runs)}"
        )
    rows = [chapter_metrics(value) for value in new_runs]
    total_calls = sum(row["calls_total"] for row in rows)
    calls_by_model: Counter[str] = Counter()
    for row in rows:
        calls_by_model.update(row["calls_by_model"])
    durations = [row["wall_time_seconds"] for row in rows]
    if any(value is None for value in durations):
        raise ArtifactValidationError("新版试运行缺少可用墙钟时间")
    numeric_durations = [float(value) for value in durations if value is not None]
    statuses = Counter(row["status"] for row in rows)
    shadows = sum(row["shadow_reviews_completed"] for row in rows)
    shadow_misses = sum(row["shadow_hard_fail_misses"] for row in rows)
    disagreements = sum(row["shadow_classification_disagreements"] for row in rows)
    calls_mean = total_calls / 10
    duration_p50 = statistics.median(numeric_durations)
    result = {
        "runs": 10,
        "excluded_legacy_runs": excluded,
        "calls_total": total_calls,
        "calls_mean": calls_mean,
        "calls_max": max(row["calls_total"] for row in rows),
        "calls_by_model": dict(sorted(calls_by_model.items())),
        "sol_calls_mean": calls_by_model.get("gpt-5.6-sol", 0) / 10,
        "duration_p50": duration_p50,
        "duration_max": max(numeric_durations),
        "status_counts": dict(sorted(statuses.items())),
        "eligible_or_near_miss_count": sum(row["eligible_or_near_miss"] for row in rows),
        "silent_fallback_count": sum(row["silent_fallback"] for row in rows),
        "hard_fail_auto_promote_count": sum(row["hard_fail_auto_promote"] for row in rows),
        "shadow_reviews_completed": shadows,
        "shadow_hard_fail_misses": shadow_misses,
        "shadow_classification_disagreements": disagreements,
    }
    result["acceptance"] = {
        "hard_call_cap": result["calls_max"] <= 10,
        "average_calls_le_8": calls_mean <= 8,
        "median_minutes_le_25": duration_p50 <= 25 * 60,
        "shadow_reviews_at_least_5": shadows >= 5,
        "shadow_hard_fail_misses_zero": shadow_misses == 0,
    }
    return result
