# Review Observability and Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve invalid reviewer output, distinguish review-artifact failure from prose failure, show live safe progress, and report wall-clock time separately from accumulated concurrent call time.

**Architecture:** Keep the existing strict validation and budget pipeline. Add a small thread-safe progress event boundary at `ModelCallExecutor`, enrich evaluated drafts and specialist results with structured failure metadata, persist only invalid raw reviewer output through the existing artifact whitelist, and make CLI/metrics consume the same timing calculations.

**Tech Stack:** Python 3 standard library, `unittest`, dataclasses, JSON artifacts, Codex CLI provider test doubles.

## Global Constraints

- The hard call limit remains 10 in balanced mode and 3 in fast mode.
- No automatic reviewer retry and no real `codex exec` call during implementation or tests.
- Preserve exact-quote evidence validation, quality thresholds, model routes, reasoning efforts, candidate count, and maximum concurrency 2.
- `REVIEW_INVALID` cannot be scored, selected, revised, or automatically promoted.
- Preserve existing uncommitted work; stage or commit only paths explicitly listed in the relevant task, and skip commits when a path contains inseparable pre-existing changes.
- Do not migrate or rewrite `novel/work/chapter_003/attempt_07`.

## File Structure

- Create `scripts/prequel/progress.py`: thread-safe progress event dispatcher and shared event type.
- Modify `scripts/prequel/model_calls.py`: emit model-call lifecycle and artifact-invalid events.
- Modify `scripts/prequel/artifacts.py`: permit only approved diagnostic artifact paths.
- Modify `scripts/prequel/evolution.py`: save invalid raw output, classify review failures, and emit structured degradation data.
- Modify `scripts/prequel/pipeline.py`: accept a progress sink and pass it into the shared executor.
- Modify `scripts/prequel/metrics.py`: expose wall-clock and accumulated model-call durations separately.
- Modify `scripts/orchestrator.py`: render live progress and actionable final diagnostics.
- Modify `tests/test_model_calls.py`, `tests/test_evolution.py`, `tests/test_metrics.py`, and `tests/test_pipeline.py`: cover new behavior without live model calls.
- Create `tests/test_orchestrator.py`: cover safe deterministic CLI formatting.
- Modify `README.md`: document runtime output and diagnostic artifact locations.

---

### Task 1: Diagnostic Artifact Boundary

**Files:**
- Modify: `scripts/prequel/artifacts.py`
- Modify: `tests/test_evolution.py`

**Interfaces:**
- Consumes: `ChapterWorkspace.write_text(name: str, content: str) -> Path`.
- Produces: exact whitelist support for `candidates/candidate_XX/diagnostics/integrated_review.invalid.txt` and `candidates/candidate_XX/diagnostics/<dimension>_review.invalid.txt`.

- [ ] **Step 1: Write failing whitelist tests**

Add a test that writes the two approved diagnostic forms and rejects an arbitrary file and path traversal:

```python
def test_review_diagnostic_paths_are_narrowly_whitelisted(self):
    with tempfile.TemporaryDirectory() as tmp:
        workspace = ChapterWorkspace.create(Path(tmp), 1, 1)
        workspace.write_text(
            "candidates/candidate_01/diagnostics/integrated_review.invalid.txt",
            "raw integrated output",
        )
        workspace.write_text(
            "candidates/candidate_01/diagnostics/continuity_review.invalid.txt",
            "raw specialist output",
        )
        with self.assertRaises(ArtifactValidationError):
            workspace.write_text(
                "candidates/candidate_01/diagnostics/arbitrary.txt", "x"
            )
        with self.assertRaises(ArtifactValidationError):
            workspace.write_text("../escaped.invalid.txt", "x")
```

Import `ArtifactValidationError` in the test module.

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
python3 -m unittest tests.test_evolution.EvolutionTests.test_review_diagnostic_paths_are_narrowly_whitelisted
```

Expected: FAIL with `不允许的章节工件` for the first approved diagnostic path.

- [ ] **Step 3: Add exact artifact patterns**

Extend `NESTED_PATTERNS` with only this candidate diagnostic expression:

```python
re.compile(
    r"^candidates/candidate_\d{2}/diagnostics/"
    r"(?:integrated_review|(?:continuity|character|craft|anti_slop)_review)\.invalid\.txt$"
),
```

Do not add a general `diagnostics/.*` pattern.

- [ ] **Step 4: Run the focused test and artifact regressions**

Run:

```bash
python3 -m unittest tests.test_evolution.EvolutionTests.test_review_diagnostic_paths_are_narrowly_whitelisted tests.test_pipeline -v
```

Expected: PASS; no provider other than local test doubles is invoked.

- [ ] **Step 5: Record the task checkpoint**

Run `git diff -- scripts/prequel/artifacts.py tests/test_evolution.py`. If these paths contain only attributable task changes, stage them with `git add -- scripts/prequel/artifacts.py tests/test_evolution.py`; otherwise leave them unstaged and record the overlap in the implementation report.

---

### Task 2: Review Failure Classification and Raw Preservation

**Files:**
- Modify: `scripts/prequel/evolution.py`
- Modify: `tests/test_evolution.py`

**Interfaces:**
- Consumes: diagnostic paths from Task 1 and `ModelCallExecutor.call(...) -> str`.
- Produces: `ReviewFailure`, `SpecialistResult`, `EvaluatedDraft.content_status`, `EvaluatedDraft.review_status`, `EvaluatedDraft.review_failure`, and classification `REVIEW_INVALID`.

- [ ] **Step 1: Add failing integrated-review tests**

Add tests for invalid evidence and invalid JSON. The evidence case must assert that the candidate is not labeled `HARD_FAIL`:

```python
def test_invalid_integrated_evidence_preserves_raw_and_is_not_content_hard_fail(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = make_project_fixture(Path(tmp))
        bad = integrated(
            {"continuity": 95, "character": 90, "craft": 92, "anti_slop": 90}
        ).replace("门板上的灰", "正文里不存在的句子")
        result, _, workspace, _ = self.setup_run(
            root,
            writer_outputs=[valid_draft(), valid_draft()],
            triage_outputs=[
                bad,
                integrated({"continuity": 91, "character": 82, "craft": 82, "anti_slop": 86}),
            ],
            specialist_outputs=[specialist("continuity", 92)],
        )
        candidate = result.decision["candidates"]["candidate_01"]
        self.assertEqual(candidate["content_status"], "VALID")
        self.assertEqual(candidate["review_status"], "INVALID")
        self.assertEqual(candidate["classification"], "REVIEW_INVALID")
        self.assertTrue(workspace.exists(candidate["diagnostic_artifact"]))
        self.assertIn(
            "正文里不存在的句子",
            workspace.read_text(candidate["diagnostic_artifact"]),
        )
```

Add a second test with `triage_outputs=["not-json", valid integrated JSON]`; assert `failure_kind == "PARSE_ERROR"` and exact raw preservation.

- [ ] **Step 2: Add a failing specialist-review test**

Use a valid eligible candidate and a specialist response whose evidence quote is absent. Assert:

```python
self.assertTrue(result.decision["evaluation_degraded"])
failure = next(
    item for item in result.decision["failures"]
    if item["stage"].startswith("specialist_")
)
self.assertEqual(failure["failure_kind"], "EVIDENCE_VALIDATION")
self.assertTrue(workspace.exists(failure["diagnostic_artifact"]))
self.assertEqual(result.status, "WAITING_USER")
```

Add `test_provider_failure_without_raw_has_null_diagnostic`, using a `ProviderError` as one triage output and finding the candidate whose classification is `REVIEW_INVALID`; assert `failure_kind == "PROVIDER_ERROR"` and `diagnostic_artifact is None`.

Add `test_diagnostic_write_failure_preserves_primary_review_error`, patching the workspace's `write_text` to raise `ArtifactValidationError("disk full")` when the diagnostic helper is called; assert the returned failure message starts with the original review-validation error, contains the secondary write error, and leaves `diagnostic_artifact` as `None`.

- [ ] **Step 3: Run the three tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_evolution.EvolutionTests.test_invalid_integrated_evidence_preserves_raw_and_is_not_content_hard_fail \
  tests.test_evolution.EvolutionTests.test_invalid_integrated_json_is_classified_as_parse_error \
  tests.test_evolution.EvolutionTests.test_invalid_specialist_evidence_degrades_without_erasing_integrated_score \
  tests.test_evolution.EvolutionTests.test_provider_failure_without_raw_has_null_diagnostic \
  tests.test_evolution.EvolutionTests.test_diagnostic_write_failure_preserves_primary_review_error -v
```

Expected: FAIL because raw files and structured review-failure fields do not exist.

- [ ] **Step 4: Add explicit failure data types**

Add this contract near the existing evolution dataclasses:

```python
@dataclass(frozen=True)
class ReviewFailure:
    stage: str
    failure_kind: str
    message: str
    diagnostic_artifact: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "failure_kind": self.failure_kind,
            "message": self.message,
            "diagnostic_artifact": self.diagnostic_artifact,
        }
```

Extend `EvaluatedDraft` after existing required fields:

```python
content_status: str = "VALID"
review_status: str = "VALID"
review_failure: ReviewFailure | None = None
```

Define this after `SpecialistRequest`:

```python
@dataclass(frozen=True)
class SpecialistResult:
    request: SpecialistRequest
    review: dict[str, Any] | None
    failure: ReviewFailure | None
```

- [ ] **Step 5: Add deterministic failure helpers**

Implement helpers that never call a model:

```python
def _invalid_scorecard(message: str, diagnostic: str | None) -> dict[str, Any]:
    return {
        "evaluation_status": "INVALID",
        "scores": {name: 0 for name in DIMENSIONS},
        "confidences": {name: 0.0 for name in DIMENSIONS},
        "weighted_score": 0.0,
        "hard_failures": [],
        "required_revisions": [],
        "warnings": [{"code": "REVIEW_INVALID", "explanation": message}],
        "summaries": {name: "审查工件无效" for name in DIMENSIONS},
        "diagnostic_artifact": diagnostic,
    }


def _review_failure(
    workspace: ChapterWorkspace,
    *,
    stage: str,
    failure_kind: str,
    message: str,
    diagnostic_path: str,
    raw: str | None,
) -> ReviewFailure:
    diagnostic: str | None = None
    detail = message
    if isinstance(raw, str) and raw.strip():
        try:
            workspace.write_text(diagnostic_path, raw)
            diagnostic = diagnostic_path
        except ArtifactValidationError as write_error:
            detail = f"{message}；诊断写入失败: {write_error}"
    return ReviewFailure(stage, failure_kind, detail, diagnostic)
```

Use explicit phase branches rather than parsing exception text: caller failures become `PROVIDER_ERROR` or `BUDGET_ERROR`; `_parse_json` failures become `PARSE_ERROR`; local P1 evidence failures become `EVIDENCE_VALIDATION`.

- [ ] **Step 6: Refactor `_triage` into call, parse, and validate phases**

Initialize `raw: str | None = None`. On parse or evidence failure, save to:

```python
diagnostic_path = (
    f"candidates/{generated.identifier}/diagnostics/"
    "integrated_review.invalid.txt"
)
```

Return `content_status="VALID"`, `review_status="INVALID"`, `classification="REVIEW_INVALID"`, `_invalid_scorecard(...)`, and a `ReviewFailure`. For a static P1 return `content_status="HARD_FAIL"`, `review_status="SKIPPED"`, and keep `HARD_FAIL`. When resuming a failed triage stage, reconstruct `REVIEW_INVALID` from `scorecard["evaluation_status"] == "INVALID"`.

- [ ] **Step 7: Refactor `_run_specialist` to return `SpecialistResult`**

Save invalid raw at:

```python
diagnostic_path = (
    f"candidates/{item.identifier}/diagnostics/"
    f"{request.dimension}_review.invalid.txt"
)
```

Return `SpecialistResult(request, review, None)` for valid output and `SpecialistResult(request, None, failure)` for invalid output. Store the parallel return list in a variable named `specialist_results`; update that result loop so a failed specialist does not replace the valid integrated scorecard. Task 3 consumes this exact variable name.

- [ ] **Step 8: Run full evolution tests**

Run `python3 -m unittest tests.test_evolution -v`.

Expected: all evolution tests PASS, calls remain within existing 3/5/10 expectations, and no live provider is used.

- [ ] **Step 9: Record the task checkpoint**

Run `git diff -- scripts/prequel/evolution.py tests/test_evolution.py`. Stage only attributable changes; otherwise document overlap.

---

### Task 3: Decision-Level Degradation and Backward Compatibility

**Files:**
- Modify: `scripts/prequel/evolution.py`
- Modify: `tests/test_evolution.py`

**Interfaces:**
- Consumes: `ReviewFailure.as_dict()` and `SpecialistResult` from Task 2.
- Produces: `generation_degraded`, `content_degraded`, `evaluation_degraded`, structured `failures`, compatibility projections, and actionable guidance in `decision.json` and `decision.md`.

- [ ] **Step 1: Add failing decision-shape assertions**

Extend invalid integrated and specialist tests with:

```python
self.assertFalse(result.decision["generation_degraded"])
self.assertTrue(result.decision["evaluation_degraded"])
self.assertTrue(result.decision["degraded"])
self.assertIn("无效审查", result.decision["automatic_retry_skipped_reason"])
self.assertTrue(result.decision["safe_actions"])
self.assertTrue(result.decision["new_budget_actions"])
self.assertIn("不会把上限扩展到第11次", result.decision["resume_warning"])
```

Keep the generation-failure test and assert `generation_degraded=True` and `evaluation_degraded=False`.

- [ ] **Step 2: Run the focused decision tests and verify failure**

Run the three named evolution tests from Task 2 plus `test_failed_candidate_is_not_retried_and_degradation_is_explained`.

Expected: FAIL on missing degradation fields or old candidate-only wording.

- [ ] **Step 3: Compute degradation from both axes**

In `run`, replace the generation-only flag with:

```python
generation_degraded = len(valid_generated) < self.candidate_count
triage_failures = [
    item.review_failure for item in evaluated_list if item.review_failure is not None
]
specialist_failures = [
    item.failure for item in specialist_results if item.failure is not None
]
evaluation_degraded = bool(triage_failures or specialist_failures)
degraded = generation_degraded or evaluation_degraded
```

Pass the two flags and combined failure list into `_finish` on every return path.

- [ ] **Step 4: Enrich `_finish` without breaking old readers**

Each candidate entry must include:

```python
{
    "classification": item.classification,
    "content_status": item.content_status,
    "review_status": item.review_status,
    "failure_stage": item.review_failure.stage if item.review_failure else None,
    "failure_kind": item.review_failure.failure_kind if item.review_failure else None,
    "diagnostic_artifact": (
        item.review_failure.diagnostic_artifact if item.review_failure else None
    ),
    "scorecard": item.scorecard,
}
```

Add `generation_degraded`, `evaluation_degraded`, and `failures`. Preserve `failed_candidate`, `failure_stage`, and `failure_reason` as projections of the first applicable failure. Select retry wording by cause:

```python
retry_reason = (
    "固定调用预算禁止自动重跑无效审查"
    if evaluation_degraded
    else "固定调用预算禁止自动补写候选"
    if generation_degraded
    else None
)
```

Render every structured failure and diagnostic path in `decision.md`.

- [ ] **Step 5: Run evolution tests**

Run `python3 -m unittest tests.test_evolution -v`.

Expected: PASS, including selector, fast-mode, and ten-call-cap tests.

- [ ] **Step 6: Record the task checkpoint**

Run `git diff -- scripts/prequel/evolution.py tests/test_evolution.py`. Do not stage unrelated hunks.

---

### Task 4: Thread-Safe Live Progress Events

**Files:**
- Create: `scripts/prequel/progress.py`
- Modify: `scripts/prequel/model_calls.py`
- Modify: `scripts/prequel/evolution.py`
- Modify: `scripts/prequel/pipeline.py`
- Modify: `tests/test_model_calls.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `ProgressSink = Callable[[dict[str, Any]], None]` and `ProgressReporter.emit(kind: str, **fields: Any) -> None`.
- Produces: `ModelCallExecutor.__init__(router, manifest, progress: ProgressSink | None = None)`.
- Produces: `WritingPipeline.run_next(..., progress: ProgressSink | None = None) -> PipelineResult`.

- [ ] **Step 1: Add failing call-lifecycle tests**

Update `make_executor` to accept `events` and construct the executor with `events.append`. Add:

```python
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
        self.assertIn("duration_ms", events[1])
```

Add a failure equivalent asserting `CALL_STARTED`, `CALL_FAILED`, the same call id, and a safe `error_code` without prompt text.

- [ ] **Step 2: Run focused tests and verify failure**

Run `python3 -m unittest tests.test_model_calls -v`.

Expected: FAIL because the executor does not accept a progress sink.

- [ ] **Step 3: Create the synchronized event dispatcher**

Create `scripts/prequel/progress.py`:

```python
from __future__ import annotations

import threading
from typing import Any, Callable

from .run_manifest import utc_now

ProgressSink = Callable[[dict[str, Any]], None]


class ProgressReporter:
    def __init__(self, sink: ProgressSink | None = None):
        self._sink = sink
        self._lock = threading.Lock()

    def emit(self, kind: str, **fields: Any) -> None:
        if self._sink is None:
            return
        event = {"kind": kind, "at": utc_now(), **fields}
        with self._lock:
            self._sink(event)
```

- [ ] **Step 4: Emit model-call lifecycle events**

Store one `ProgressReporter` in `ModelCallExecutor`. After `mark_running`, emit `CALL_STARTED` with the persisted call record's stage/model/reasoning effort. After budget settlement emit `CALL_COMPLETED` or `CALL_FAILED` with `duration_ms`; failure fields are limited to exception class and bounded summary. Add:

```python
def artifact_invalid(
    self,
    *,
    stage: str,
    failure_kind: str,
    diagnostic_artifact: str | None,
) -> None:
    self.progress.emit(
        "ARTIFACT_INVALID",
        stage=stage,
        failure_kind=failure_kind,
        diagnostic_artifact=diagnostic_artifact,
    )

def stage_reused(self, stage: str) -> None:
    self.progress.emit("STAGE_REUSED", stage=stage)
```

Never pass prompt or model output to these methods.

- [ ] **Step 5: Emit validation and reuse events from the pipeline**

Call `caller.artifact_invalid(...)` whenever Task 2 constructs a `ReviewFailure`. Call `caller.stage_reused(stage)` in planner, candidate generation, integrated triage, and specialist reuse branches. This separates Provider completion from artifact validity.

Update pipeline signatures:

```python
def run_next(
    self,
    *,
    dry_run: bool = False,
    resume: bool = False,
    mode: str = "balanced",
    shadow_review: str | None = None,
    progress: ProgressSink | None = None,
) -> PipelineResult:
```

Pass `progress` through `_run_evolution` and construct `ModelCallExecutor(self.router, manifest, progress)`.

- [ ] **Step 6: Verify progress propagation with a pipeline mock**

In `tests/test_pipeline.py`, pass `events.append` to `run_next` in the mocked evolution happy path and assert that planner emits one `CALL_STARTED` and one `CALL_COMPLETED`. Do not assert cross-call global ordering because candidate calls are concurrent.

- [ ] **Step 7: Run progress and pipeline tests**

Run:

```bash
python3 -m unittest tests.test_model_calls tests.test_pipeline tests.test_evolution -v
```

Expected: PASS and no `codex exec` subprocess.

- [ ] **Step 8: Record the task checkpoint**

Inspect only the seven task paths with `git diff -- ...`. Stage only attributable changes; do not fold other dirty-worktree changes into a commit.

---

### Task 5: Shared Duration Metrics and Actionable CLI

**Files:**
- Modify: `scripts/prequel/metrics.py`
- Modify: `scripts/orchestrator.py`
- Modify: `tests/test_metrics.py`
- Create: `tests/test_orchestrator.py`

**Interfaces:**
- `chapter_metrics(...)` produces `wall_time_seconds: float | None` and `model_call_time_seconds: float`.
- `format_progress_event(event: dict[str, Any]) -> str` produces one safe display line.

- [ ] **Step 1: Add failing metric tests**

Extend metrics tests:

```python
def test_chapter_metrics_separates_wall_and_accumulated_call_time(self):
    value = chapter_metrics(manifest())
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
```

Import `datetime` and `timezone` in the test module.

- [ ] **Step 2: Run metric tests and verify failure**

Run `python3 -m unittest tests.test_metrics -v`.

Expected: FAIL because missing timestamps fall back to accumulated call time and the new field is absent.

- [ ] **Step 3: Split timing calculations**

Replace `_elapsed_seconds` with:

```python
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
        return max(
            0.0,
            (end_time - start_time).total_seconds(),
        )
    except (TypeError, ValueError):
        return None


def _model_call_time_seconds(manifest: dict[str, Any]) -> float:
    return sum(
        (item.get("duration_ms") or 0)
        for item in manifest.get("budget", {}).get("calls", {}).values()
    ) / 1000
```

Import `timezone` beside `datetime`. Return both values from `chapter_metrics`. In `benchmark_summary`, reject any of ten benchmark rows whose wall time is `None` with `ArtifactValidationError("新版试运行缺少可用墙钟时间")`.

- [ ] **Step 4: Add failing CLI formatter tests**

Create `tests/test_orchestrator.py`:

```python
import unittest

from scripts.orchestrator import format_progress_event


class OrchestratorFormattingTests(unittest.TestCase):
    def test_progress_formatter_never_includes_prompt_or_output(self):
        event = {
            "kind": "CALL_STARTED",
            "call_id": "call_004",
            "stage": "integrated_reviewer",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
            "prompt": "secret prompt",
            "output": "secret output",
        }
        line = format_progress_event(event)
        self.assertIn("call_004", line)
        self.assertIn("gpt-5.6-terra/medium", line)
        self.assertNotIn("secret", line)

    def test_invalid_artifact_formatter_includes_diagnostic_path(self):
        line = format_progress_event({
            "kind": "ARTIFACT_INVALID",
            "stage": "triage_candidate_01",
            "failure_kind": "EVIDENCE_VALIDATION",
            "diagnostic_artifact": "candidates/candidate_01/diagnostics/integrated_review.invalid.txt",
        })
        self.assertIn("审查无效", line)
        self.assertIn("integrated_review.invalid.txt", line)
```

- [ ] **Step 5: Implement deterministic CLI rendering**

Add `format_progress_event` with an explicit branch per supported kind. Unknown kinds render only `kind` and `stage`. Add `_cli_progress(event)` that executes `print(format_progress_event(event), flush=True)`.

Pass `_cli_progress` to `WritingPipeline(PROJECT_ROOT).run_next(..., progress=_cli_progress)`.

Use `chapter_metrics(manifest_path)` for final timing output:

```python
wall = metrics["wall_time_seconds"]
print("实际墙钟耗时: 未知" if wall is None else f"实际墙钟耗时: {wall:.1f}秒")
print(f"并发调用耗时合计: {metrics['model_call_time_seconds']:.1f}秒")
```

For every `decision["failures"]`, print stage, failure kind, and diagnostic artifact when present. Preserve old `failed_candidate` fallback. Print `reasons`, `safe_actions`, `new_budget_actions`, and `resume_warning` whenever status is `WAITING_USER` or `BUDGET_EXHAUSTED`.

- [ ] **Step 6: Run metric and CLI tests**

Run `python3 -m unittest tests.test_metrics tests.test_orchestrator -v`.

Expected: PASS; formatting is deterministic and contains no raw prompt/output.

- [ ] **Step 7: Run the script import smoke test**

Run:

```bash
python3 -m unittest tests.test_pipeline.PipelineTests.test_script_entrypoint_can_import_project_package -v
```

Expected: PASS and help output contains `事务型创作管道`.

- [ ] **Step 8: Record the task checkpoint**

Run `git diff -- scripts/prequel/metrics.py scripts/orchestrator.py tests/test_metrics.py tests/test_orchestrator.py`. Do not stage unrelated changes already present in `scripts/orchestrator.py`.

---

### Task 6: Documentation and Full Regression

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-02-review-observability-and-progress.md`
- Create: `docs/superpowers/reports/2026-08-02-review-observability-and-progress-implementation.md`

**Interfaces:**
- Consumes: final CLI wording and diagnostic paths from Tasks 1–5.
- Produces: operator instructions and an evidence-backed implementation report.

- [ ] **Step 1: Document runtime expectations**

Add a concise README section containing:

```text
python3 scripts/orchestrator.py next --dry-run --mode balanced

运行期间逐行显示调用开始/完成和审查工件校验结果。
“实际墙钟耗时”是用户等待时间；“并发调用耗时合计”是各模型调用时长之和。
无效审查原文位于候选目录的 diagnostics/*.invalid.txt，且不会自动触发重试。
```

Do not claim that any new live trial has been run.

- [ ] **Step 2: Run the complete local test suite**

Run `python3 -m unittest discover -s tests -v`.

Expected: all tests PASS. If a test attempts to launch a real provider, stop and correct the test setup rather than allowing the call.

- [ ] **Step 3: Run deterministic CLI checks only**

Run:

```bash
python3 scripts/orchestrator.py status
python3 scripts/orchestrator.py preflight
```

Expected: both exit 0. Do not run `next`, `review --specialists`, or `audit` because those may consume model quota.

- [ ] **Step 4: Scan for placeholders and accidental retry changes**

Run:

```bash
rg -n "TB[D]|TO[D]O|implement late[r]|fill i[n]|后续实[现]|自动重[试]" \
  scripts/prequel scripts/orchestrator.py tests README.md \
  docs/superpowers/specs/2026-08-02-review-observability-and-progress-design.md
```

Expected: no placeholders; any `自动重试` occurrence states that automatic retry is prohibited.

- [ ] **Step 5: Write the implementation report**

Create `docs/superpowers/reports/2026-08-02-review-observability-and-progress-implementation.md` with:

```markdown
# 审查可观测性与运行进度修复实施报告

**状态：** 已完成
**日期：** 2026-08-02

## 交付内容

- 无效审查 raw 工件与严格路径白名单
- `REVIEW_INVALID` 与正文硬失败分离
- 结构化降级决策与用户指引
- 线程安全的实时 CLI 进度
- 墙钟与并发调用累计耗时分离

## 验证

- 全量测试：记录执行命令、通过数和失败数
- CLI 只读检查：记录每条命令的退出码
- 真实模型调用：0

## 兼容性

- `attempt_07` 未改写
- 旧 decision 字段仍可读取
- 评分门禁、预算和模型路由未改变
```

Write the observed command results and counts; do not estimate them.

- [ ] **Step 6: Review the complete diff without destructive cleanup**

Run:

```bash
git status --short
git diff --stat
```

Inspect all task paths, preserve unrelated user changes, and report any pre-existing overlap. Do not use `git reset`, `git checkout --`, or delete existing artifacts.

- [ ] **Step 7: Final handoff**

Report the implementation report path, test result, changed behavior, and explicitly state that zero real model calls were made. Do not run a live chapter trial until the user separately approves it.
