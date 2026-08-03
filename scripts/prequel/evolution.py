from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .artifacts import ChapterWorkspace
from .context_builder import (
    build_ballot_packet,
    build_candidate_packet,
    build_integrated_review_packet,
    build_revision_packet,
    build_specialist_packet,
    build_verification_packet,
)
from .errors import (
    ArtifactValidationError,
    AtomicWriteError,
    CallBudgetExceeded,
    ProviderError,
)
from .evaluation import (
    DIMENSIONS,
    classify_candidate,
    merge_specialist_review,
    promotion_decision,
    scorecard_from_integrated,
    selection_policy,
    validate_ballot,
    validate_integrated_review,
    validate_revision_verification,
    validate_specialist_review,
)
from .model_calls import ModelCallExecutor
from .model_router import StageModelRouter
from .quality import scan_draft
from .run_manifest import RunManifest, fingerprint


@dataclass(frozen=True)
class GeneratedDraft:
    identifier: str
    draft: str
    static_review: dict[str, Any]


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


@dataclass(frozen=True)
class EvaluatedDraft:
    identifier: str
    draft: str
    static_review: dict[str, Any]
    integrated_review: dict[str, Any] | None
    reviews: dict[str, dict[str, Any]]
    scorecard: dict[str, Any]
    classification: str
    content_status: str = "VALID"
    review_status: str = "VALID"
    review_failure: ReviewFailure | None = None


@dataclass(frozen=True)
class SpecialistRequest:
    candidate_id: str
    dimension: str
    reason_code: str
    priority: int


@dataclass(frozen=True)
class SpecialistResult:
    request: SpecialistRequest
    review: dict[str, Any] | None
    failure: ReviewFailure | None


@dataclass(frozen=True)
class EvolutionResult:
    status: str
    selected_id: str | None
    draft: str | None
    static_review: dict[str, Any] | None
    reviews: dict[str, dict[str, Any]]
    scorecard: dict[str, Any] | None
    decision: dict[str, Any]


def _parse_json(raw: str, label: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"{label}不是合法JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label}根节点必须是object")
    return value


def _prompt(
    project_root: Path,
    agent: str,
    packet: dict[str, Any],
    instruction: str,
) -> str:
    try:
        role = (project_root / "agents" / f"{agent}.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取{agent}指令: {exc}") from exc
    return (
        role.rstrip()
        + "\n\n# 本次任务\n"
        + instruction
        + "\n\n# 唯一输入工件\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )


def _p1_messages(issues: list[Any]) -> list[str]:
    return [item.message for item in issues if item.severity == "P1"]


def _hard_scorecard(message: str) -> dict[str, Any]:
    return {
        "scores": {name: 0 for name in DIMENSIONS},
        "confidences": {name: 1.0 for name in DIMENSIONS},
        "weighted_score": 0.0,
        "hard_failures": [
            {
                "dimension": "continuity",
                "code": "LOCAL_HARD_FAIL",
                "quote": "",
                "explanation": message,
            }
        ],
        "required_revisions": [],
        "warnings": [],
        "summaries": {name: "未进入语义初筛" for name in DIMENSIONS},
    }


def _invalid_scorecard(
    message: str,
    diagnostic: str | None,
    *,
    stage: str,
    failure_kind: str,
) -> dict[str, Any]:
    return {
        "evaluation_status": "INVALID",
        "failure_stage": stage,
        "failure_kind": failure_kind,
        "diagnostic_artifact": diagnostic,
        "scores": {name: 0 for name in DIMENSIONS},
        "confidences": {name: 0.0 for name in DIMENSIONS},
        "weighted_score": 0.0,
        "hard_failures": [],
        "required_revisions": [],
        "warnings": [{"code": "REVIEW_INVALID", "explanation": message}],
        "summaries": {name: "审查工件无效" for name in DIMENSIONS},
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
            workspace.write_raw_text(diagnostic_path, raw)
            diagnostic = diagnostic_path
        except (ArtifactValidationError, AtomicWriteError, OSError) as write_error:
            detail = f"{message}；诊断写入失败: {write_error}"
    return ReviewFailure(stage, failure_kind, detail, diagnostic)


def _failure_from_scorecard(
    scorecard: dict[str, Any], stage: str
) -> ReviewFailure:
    warnings = scorecard.get("warnings", [])
    message = "审查工件无效"
    if warnings and isinstance(warnings[0], dict):
        message = str(warnings[0].get("explanation") or message)
    return ReviewFailure(
        str(scorecard.get("failure_stage") or stage),
        str(scorecard.get("failure_kind") or "SCHEMA_ERROR"),
        message,
        scorecard.get("diagnostic_artifact"),
    )


class QualityEvolutionEngine:
    """Budgeted two-candidate generation with adaptive review and one revision."""

    def __init__(
        self,
        project_root: Path,
        router: StageModelRouter,
        config: dict[str, Any],
        caller: ModelCallExecutor | None = None,
        *,
        mode: str = "balanced",
        shadow_dimension: str | None = None,
        max_workers: int = 2,
    ) -> None:
        if mode not in {"balanced", "fast"}:
            raise ArtifactValidationError(f"未知创作模式: {mode}")
        if max_workers != 2:
            raise ArtifactValidationError("质量管线最大并发固定为2")
        if shadow_dimension is not None and shadow_dimension not in DIMENSIONS:
            raise ArtifactValidationError("影子复核维度无效")
        self.project_root = project_root
        self.router = router
        self.caller = caller
        self.config = config.get("quality_evolution", {})
        self.length_policy = config.get("chapter_length")
        self.mode = mode
        self.shadow_dimension = shadow_dimension
        self.max_workers = max_workers
        self.candidate_count = 1 if mode == "fast" else 2
        self.weights = self.config.get("weights")
        self.floors = self.config.get("candidate_floors")
        self.score_gap = self.config.get("selector_score_gap", 4)

    def _caller(self, manifest: RunManifest) -> ModelCallExecutor:
        return self.caller or ModelCallExecutor(self.router, manifest)

    def _metadata(self, stage: str, call_count: int = 1) -> dict[str, Any]:
        settings = self.router.settings_for(stage)
        route = {
            "profile": settings.profile,
            "model": settings.model,
            "reasoning_effort": settings.reasoning_effort,
            "prompt_version": "budgeted-adaptive-v1",
        }
        return {
            "model_profile": self.router.profile_for(stage),
            "prompt_version": "budgeted-adaptive-v1",
            "call_count": call_count,
            "route_fingerprint": fingerprint(route),
        }

    def _route_fingerprint(self, stage: str) -> str:
        return self._metadata(stage)["route_fingerprint"]

    def _generate_candidate(
        self,
        *,
        index: int,
        state: dict[str, Any],
        plan: dict[str, Any],
        recent: list[str],
        planner_context: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        caller: ModelCallExecutor,
    ) -> GeneratedDraft | None:
        identifier = f"candidate_{index + 1:02d}"
        prefix = f"candidates/{identifier}"
        stage = f"generate_{identifier}"
        packet = build_candidate_packet(state, plan, recent, planner_context, index)
        input_hash = fingerprint(packet)
        outputs = [
            f"{prefix}/draft.txt",
            f"{prefix}/generation.json",
            f"{prefix}/static_review.json",
        ]
        if manifest.can_reuse(
            stage, input_hash, self._route_fingerprint("candidate_writer")
        ):
            caller.stage_reused(stage)
            return GeneratedDraft(
                identifier,
                workspace.read_text(outputs[0]),
                workspace.read_json(outputs[2]),
            )
        if manifest.stage_failed(stage):
            return None
        manifest.begin(stage)
        try:
            draft = caller.call(
                "candidate_writer",
                _prompt(
                    self.project_root,
                    "writer",
                    packet,
                    "生成一个独立完整章节。只输出正文，不读写项目文件。",
                ),
                None,
                f"GENERATE_{identifier.upper()}",
            )
            static = scan_draft(
                draft,
                recent,
                planner_context["era_bans"],
                plan,
                length_policy=self.length_policy,
            )
            hard = [
                item["message"]
                for item in static["issues"]
                if item["severity"] == "P1"
            ]
            workspace.write_text(outputs[0], draft)
            workspace.write_json(
                outputs[1],
                {
                    "status": "HARD_FAIL" if hard else "VALID",
                    "failures": hard,
                    "candidate_focus": packet["candidate_focus"],
                },
            )
            workspace.write_json(outputs[2], static)
            manifest.complete(
                stage,
                input_hash,
                outputs,
                self._metadata("candidate_writer"),
            )
            return GeneratedDraft(identifier, draft, static)
        except (ArtifactValidationError, ProviderError, CallBudgetExceeded) as exc:
            workspace.write_json(
                f"{prefix}/generation.json",
                {"status": "FAILED", "failures": [str(exc)]},
            )
            manifest.fail(stage, str(exc))
            return None

    def _triage(
        self,
        generated: GeneratedDraft,
        *,
        state: dict[str, Any],
        plan: dict[str, Any],
        planner_context: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        caller: ModelCallExecutor,
    ) -> EvaluatedDraft:
        prefix = f"candidates/{generated.identifier}"
        review_path = f"{prefix}/integrated_review.json"
        score_path = f"{prefix}/scorecard.json"
        stage = f"triage_{generated.identifier}"
        static_hard = [
            item["message"]
            for item in generated.static_review["issues"]
            if item["severity"] == "P1"
        ]
        if static_hard:
            card = _hard_scorecard("；".join(static_hard))
            workspace.write_json(score_path, card)
            return EvaluatedDraft(
                generated.identifier,
                generated.draft,
                generated.static_review,
                None,
                {},
                card,
                "HARD_FAIL",
                content_status="HARD_FAIL",
                review_status="SKIPPED",
            )
        packet = build_integrated_review_packet(
            state,
            plan,
            generated.draft,
            generated.static_review,
            planner_context,
        )
        input_hash = fingerprint(packet)
        if manifest.can_reuse(
            stage, input_hash, self._route_fingerprint("integrated_reviewer")
        ):
            caller.stage_reused(stage)
            integrated = workspace.read_json(review_path)
            card = workspace.read_json(score_path)
            return EvaluatedDraft(
                generated.identifier,
                generated.draft,
                generated.static_review,
                integrated,
                {},
                card,
                classify_candidate(card, self.floors),
            )
        if manifest.stage_failed(stage):
            card = workspace.read_json(score_path)
            invalid = card.get("evaluation_status") == "INVALID"
            failure = _failure_from_scorecard(card, stage) if invalid else None
            return EvaluatedDraft(
                generated.identifier,
                generated.draft,
                generated.static_review,
                None,
                {},
                card,
                "REVIEW_INVALID" if invalid else "HARD_FAIL",
                content_status="VALID",
                review_status="INVALID",
                review_failure=failure,
            )
        manifest.begin(stage)

        diagnostic_path = (
            f"{prefix}/diagnostics/integrated_review.invalid.txt"
        )

        def invalid_result(
            failure_kind: str,
            message: str,
            raw: str | None,
        ) -> EvaluatedDraft:
            failure = _review_failure(
                workspace,
                stage=stage,
                failure_kind=failure_kind,
                message=message,
                diagnostic_path=diagnostic_path,
                raw=raw,
            )
            manifest.fail(stage, failure.message)
            card = _invalid_scorecard(
                failure.message,
                failure.diagnostic_artifact,
                stage=stage,
                failure_kind=failure.failure_kind,
            )
            workspace.write_json(score_path, card)
            caller.artifact_invalid(
                stage=stage,
                failure_kind=failure.failure_kind,
                diagnostic_artifact=failure.diagnostic_artifact,
            )
            return EvaluatedDraft(
                generated.identifier,
                generated.draft,
                generated.static_review,
                None,
                {},
                card,
                "REVIEW_INVALID",
                content_status="VALID",
                review_status="INVALID",
                review_failure=failure,
            )

        raw: str | None = None
        try:
            raw = caller.call(
                "integrated_reviewer",
                _prompt(
                    self.project_root,
                    "reviewer_integrated",
                    packet,
                    "完整阅读候选并执行四维集成初筛。只输出JSON。",
                ),
                self.project_root / "schemas/integrated_review.schema.json",
                f"TRIAGE_{generated.identifier.upper()}",
            )
        except CallBudgetExceeded as exc:
            return invalid_result("BUDGET_ERROR", str(exc), raw)
        except ProviderError as exc:
            return invalid_result("PROVIDER_ERROR", str(exc), raw)
        except ArtifactValidationError as exc:
            return invalid_result("SCHEMA_ERROR", str(exc), raw)

        try:
            integrated = _parse_json(raw, f"{generated.identifier}-integrated")
        except ArtifactValidationError as exc:
            return invalid_result("PARSE_ERROR", str(exc), raw)

        issues = validate_integrated_review(
            integrated,
            generated.draft,
            plan["chapter_number"],
            set(packet["allowed_fact_ids"]),
        )
        failures = _p1_messages(issues)
        if failures:
            evidence_codes = {"REVIEW_FALSE_EVIDENCE", "INTEGRATED_UNKNOWN_FACT"}
            kind = (
                "EVIDENCE_VALIDATION"
                if any(
                    item.severity == "P1" and item.code in evidence_codes
                    for item in issues
                )
                else "SCHEMA_ERROR"
            )
            return invalid_result(kind, "；".join(failures), raw)

        card = scorecard_from_integrated(integrated, self.weights)
        workspace.write_json(review_path, integrated)
        workspace.write_json(score_path, card)
        manifest.complete(
            stage,
            input_hash,
            [review_path, score_path],
            self._metadata("integrated_reviewer"),
        )
        return EvaluatedDraft(
            generated.identifier,
            generated.draft,
            generated.static_review,
            integrated,
            {},
            card,
            classify_candidate(card, self.floors),
        )

    def plan_specialist_calls(
        self, evaluated: list[EvaluatedDraft], remaining: int
    ) -> list[SpecialistRequest]:
        if self.mode == "fast" or remaining <= 0:
            return []
        requests: dict[tuple[str, str], SpecialistRequest] = {}

        def add(candidate_id: str, dimension: str, reason: str, priority: int) -> None:
            key = (candidate_id, dimension)
            current = requests.get(key)
            item = SpecialistRequest(candidate_id, dimension, reason, priority)
            if current is None or item.priority < current.priority:
                requests[key] = item

        complete = [item for item in evaluated if item.integrated_review is not None]
        for item in complete:
            assert item.integrated_review is not None
            for dimension in DIMENSIONS:
                confidence = item.scorecard.get("confidences", {}).get(dimension, 0)
                score = item.scorecard["scores"][dimension]
                floor = (self.floors or {}).get(dimension, {
                    "continuity": 85,
                    "character": 75,
                    "craft": 75,
                    "anti_slop": 80,
                }[dimension])
                if confidence < 0.75:
                    add(item.identifier, dimension, "LOW_CONFIDENCE", 1)
                elif abs(score - floor) <= 5:
                    add(item.identifier, dimension, "CLASSIFICATION_BOUNDARY", 3)
            for request in item.integrated_review.get("specialist_requests", []):
                add(item.identifier, request["dimension"], "MODEL_REQUEST", 4)

        by_fact: dict[str, dict[str, str]] = {}
        for item in complete:
            for finding in (item.integrated_review or {}).get("fact_findings", []):
                by_fact.setdefault(finding["fact_id"], {})[item.identifier] = finding["value"]
        for values in by_fact.values():
            if len(set(values.values())) > 1:
                for candidate_id in values:
                    add(candidate_id, "continuity", "CROSS_CANDIDATE_FACT_CONFLICT", 0)

        eligible_items = [item for item in evaluated if item.classification == "ELIGIBLE"]
        if len(eligible_items) == 1:
            add(
                eligible_items[0].identifier,
                "continuity",
                "SINGLE_ELIGIBLE_AUTO_PROMOTE_GUARD",
                0,
            )
        if self.shadow_dimension and complete:
            best = max(complete, key=lambda item: item.scorecard["weighted_score"])
            add(best.identifier, self.shadow_dimension, "BENCHMARK_SHADOW_REVIEW", 9)
        return sorted(
            requests.values(),
            key=lambda item: (item.priority, item.candidate_id, DIMENSIONS.index(item.dimension)),
        )[: min(2, remaining)]

    def _run_specialist(
        self,
        request: SpecialistRequest,
        evaluated: dict[str, EvaluatedDraft],
        *,
        state: dict[str, Any],
        plan: dict[str, Any],
        planner_context: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        caller: ModelCallExecutor,
    ) -> SpecialistResult:
        item = evaluated[request.candidate_id]
        path = f"candidates/{item.identifier}/reviews/{request.dimension}.json"
        diagnostic_path = (
            f"candidates/{item.identifier}/diagnostics/"
            f"{request.dimension}_review.invalid.txt"
        )
        stage = f"specialist_{item.identifier}_{request.dimension}"
        packet = build_specialist_packet(
            state,
            plan,
            item.draft,
            item.static_review,
            planner_context,
            request.dimension,
        )
        input_hash = fingerprint({"packet": packet, "reason": request.reason_code})
        if manifest.can_reuse(
            stage,
            input_hash,
            self._route_fingerprint(f"{request.dimension}_reviewer"),
        ):
            caller.stage_reused(stage)
            return SpecialistResult(request, workspace.read_json(path), None)
        if manifest.stage_failed(stage):
            record = next(
                (
                    value
                    for value in reversed(manifest.data.get("failures", []))
                    if value.get("stage") == stage
                ),
                {},
            )
            failure = ReviewFailure(
                stage,
                "SCHEMA_ERROR",
                str(record.get("message") or "专项审查工件无效"),
                diagnostic_path if workspace.exists(diagnostic_path) else None,
            )
            return SpecialistResult(request, None, failure)
        manifest.begin(stage)

        def invalid_result(
            failure_kind: str,
            message: str,
            raw: str | None,
        ) -> SpecialistResult:
            failure = _review_failure(
                workspace,
                stage=stage,
                failure_kind=failure_kind,
                message=message,
                diagnostic_path=diagnostic_path,
                raw=raw,
            )
            manifest.fail(stage, failure.message)
            caller.artifact_invalid(
                stage=stage,
                failure_kind=failure.failure_kind,
                diagnostic_artifact=failure.diagnostic_artifact,
            )
            return SpecialistResult(request, None, failure)

        raw: str | None = None
        try:
            raw = caller.call(
                f"{request.dimension}_reviewer",
                _prompt(
                    self.project_root,
                    f"reviewer_{request.dimension}",
                    packet,
                    f"因{request.reason_code}执行专项复核。只输出JSON。",
                ),
                self.project_root / "schemas/specialist_review.schema.json",
                request.reason_code,
            )
        except CallBudgetExceeded as exc:
            return invalid_result("BUDGET_ERROR", str(exc), raw)
        except ProviderError as exc:
            return invalid_result("PROVIDER_ERROR", str(exc), raw)
        except ArtifactValidationError as exc:
            return invalid_result("SCHEMA_ERROR", str(exc), raw)

        try:
            review = _parse_json(
                raw, f"{item.identifier}-{request.dimension}"
            )
        except ArtifactValidationError as exc:
            return invalid_result("PARSE_ERROR", str(exc), raw)

        issues = validate_specialist_review(
            review, item.draft, plan["chapter_number"], request.dimension
        )
        failures = _p1_messages(issues)
        if failures:
            kind = (
                "EVIDENCE_VALIDATION"
                if any(
                    issue.severity == "P1"
                    and issue.code == "REVIEW_FALSE_EVIDENCE"
                    for issue in issues
                )
                else "SCHEMA_ERROR"
            )
            return invalid_result(kind, "；".join(failures), raw)

        workspace.write_json(path, review)
        manifest.complete(
            stage,
            input_hash,
            [path],
            self._metadata(f"{request.dimension}_reviewer"),
        )
        return SpecialistResult(request, review, None)

    def _select_once(
        self,
        left: EvaluatedDraft,
        right: EvaluatedDraft,
        *,
        plan: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        caller: ModelCallExecutor,
    ) -> tuple[str, bool]:
        path = "comparisons/initial/ballot_01.json"
        stage = "compare_initial"
        packet = build_ballot_packet(plan, left.draft, right.draft)
        input_hash = fingerprint(packet)
        if manifest.can_reuse(
            stage, input_hash, self._route_fingerprint("selector")
        ):
            record = workspace.read_json(path)
            return record.get("mapped_winner") or left.identifier, bool(record.get("valid"))
        if manifest.stage_failed(stage) and workspace.exists(path):
            record = workspace.read_json(path)
            return record.get("mapped_winner") or left.identifier, False
        manifest.begin(stage)
        fallback = max(
            (left, right), key=lambda item: (item.scorecard["weighted_score"], item.identifier)
        ).identifier
        try:
            ballot = _parse_json(
                caller.call(
                    "selector",
                    _prompt(
                        self.project_root,
                        "selector",
                        packet,
                        "匿名比较候选A与B，只输出一次盲选JSON。",
                    ),
                    self.project_root / "schemas/ballot.schema.json",
                    "CLOSE_ELIGIBLE_SCORES",
                ),
                "ballot",
            )
            failures = _p1_messages(validate_ballot(ballot, left.draft, right.draft))
            if failures:
                raise ArtifactValidationError("；".join(failures))
            mapped = (
                left.identifier
                if ballot["winner"] == "A"
                else right.identifier
                if ballot["winner"] == "B"
                else fallback
            )
            valid = ballot["winner"] in {"A", "B"}
            record = {
                **ballot,
                "candidate_map": {"A": left.identifier, "B": right.identifier},
                "mapped_winner": mapped,
                "valid": valid,
            }
            workspace.write_json(path, record)
            manifest.complete(
                stage,
                input_hash,
                [path],
                self._metadata("selector"),
            )
            return mapped, valid
        except (ArtifactValidationError, ProviderError, CallBudgetExceeded) as exc:
            workspace.write_json(
                path,
                {
                    "winner": "TIE",
                    "criteria": {},
                    "evidence": [],
                    "rationale": str(exc),
                    "candidate_map": {"A": left.identifier, "B": right.identifier},
                    "mapped_winner": fallback,
                    "valid": False,
                },
            )
            manifest.fail(stage, str(exc))
            return fallback, False

    @staticmethod
    def _revision_instructions(selected: EvaluatedDraft, floors: dict[str, int] | None) -> list[dict[str, Any]]:
        instructions = list(selected.scorecard.get("required_revisions", []))
        if instructions:
            return instructions
        floor_values = floors or {
            "continuity": 85,
            "character": 75,
            "craft": 75,
            "anti_slop": 80,
        }
        for dimension in DIMENSIONS:
            if selected.scorecard["scores"][dimension] < floor_values[dimension]:
                instructions.append(
                    {
                        "dimension": dimension,
                        "code": f"RAISE_{dimension.upper()}",
                        "quote": "",
                        "instruction": f"修复{dimension}维度的临界缺陷",
                        "acceptance": f"{dimension}达到{floor_values[dimension]}",
                    }
                )
        return instructions

    def _revise_once(
        self,
        selected: EvaluatedDraft,
        *,
        state: dict[str, Any],
        plan: dict[str, Any],
        recent: list[str],
        planner_context: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        caller: ModelCallExecutor,
    ) -> tuple[EvaluatedDraft, list[dict[str, Any]], bool, bool]:
        instructions = self._revision_instructions(selected, self.floors)
        if not instructions:
            return selected, [], True, False
        verifier_stage = (
            "verifier_complex"
            if any(
                item.get("dimension") in {"continuity", "character"}
                or "CAUS" in item.get("code", "")
                for item in instructions
            )
            else "verifier"
        )
        prefix = "revisions/round_01"
        packet = build_revision_packet(
            state,
            plan,
            recent,
            planner_context,
            selected.draft,
            instructions,
        )
        brief_path = f"{prefix}/brief.json"
        draft_path = f"{prefix}/draft.txt"
        static_path = f"{prefix}/static_review.json"
        workspace.write_json(brief_path, {"instructions": instructions})
        revision_input_hash = fingerprint(
            {"packet": packet, "verifier_stage": verifier_stage}
        )
        revision_route_fingerprint = fingerprint(
            {
                "reviser": self._route_fingerprint("reviser"),
                "verifier": self._route_fingerprint(verifier_stage),
            }
        )
        if manifest.can_reuse(
            "revision_round_01",
            revision_input_hash,
            revision_route_fingerprint,
        ):
            revised_draft = workspace.read_text(draft_path)
            static = workspace.read_json(static_path)
            verification = workspace.read_json(f"{prefix}/verification.json")
            return self._verified_revision_result(
                selected, revised_draft, static, verification
            )
        if manifest.stage_failed("revision_round_01"):
            return selected, [{"accepted": False, "reason": "PRIOR_REVISION_FAILED"}], False, False
        try:
            reservations = caller.reserve_many(
                [("reviser", "TARGETED_REVISION"), (verifier_stage, "VERIFY_REVISION")]
            )
        except CallBudgetExceeded:
            return selected, [], False, True
        manifest.begin("revision_round_01")
        try:
            revised_draft = caller.call_reserved(
                reservations[0],
                _prompt(
                    self.project_root,
                    "writer",
                    packet,
                    "按限定问题定向修订，输出完整章节纯文本。",
                ),
                None,
            )
        except (ArtifactValidationError, ProviderError):
            caller.cancel_before_provider(reservations[1])
            manifest.fail("revision_round_01", "修订调用失败")
            return selected, [{"accepted": False, "reason": "REVISION_CALL_FAILED"}], False, False
        static = scan_draft(
            revised_draft,
            recent,
            planner_context["era_bans"],
            plan,
            length_policy=self.length_policy,
        )
        workspace.write_text(draft_path, revised_draft)
        workspace.write_json(static_path, static)
        if not static["passed"]:
            caller.cancel_before_provider(reservations[1])
            manifest.fail("revision_round_01", "修订稿静态检查回归")
            return selected, [{"accepted": False, "reason": "STATIC_REGRESSION"}], False, False
        verify_packet = build_verification_packet(
            state,
            plan,
            planner_context,
            selected.draft,
            revised_draft,
            instructions,
        )
        verification_path = f"{prefix}/verification.json"
        try:
            verification = _parse_json(
                caller.call_reserved(
                    reservations[1],
                    _prompt(
                        self.project_root,
                        "reviewer_verifier",
                        verify_packet,
                        "只验证目标问题和回归。输出验证JSON。",
                    ),
                    self.project_root / "schemas/revision_verification.schema.json",
                ),
                "revision-verification",
            )
            failures = _p1_messages(
                validate_revision_verification(
                    verification,
                    revised_draft,
                    plan["chapter_number"],
                    baseline_scores=selected.scorecard["scores"],
                    max_dimension_regression=self.config.get(
                        "max_dimension_regression", 3
                    ),
                )
            )
            if failures:
                raise ArtifactValidationError("；".join(failures))
            workspace.write_json(verification_path, verification)
        except (ArtifactValidationError, ProviderError) as exc:
            manifest.fail("revision_round_01", str(exc))
            return selected, [{"accepted": False, "reason": str(exc)}], False, False
        revised, history, verification_passed, budget_exhausted = self._verified_revision_result(
            selected, revised_draft, static, verification
        )
        accepted = revised.identifier == "revision_01"
        if not accepted:
            manifest.fail("revision_round_01", "修订验证未通过")
            return revised, history, verification_passed, budget_exhausted
        manifest.complete(
            "revision_round_01",
            revision_input_hash,
            [brief_path, draft_path, static_path, verification_path],
            {
                **self._metadata("reviser", 2),
                "route_fingerprint": revision_route_fingerprint,
            },
        )
        return revised, history, verification_passed, budget_exhausted

    def _verified_revision_result(
        self,
        selected: EvaluatedDraft,
        revised_draft: str,
        static: dict[str, Any],
        verification: dict[str, Any],
    ) -> tuple[EvaluatedDraft, list[dict[str, Any]], bool, bool]:
        card = {
            **selected.scorecard,
            "scores": dict(selected.scorecard["scores"]),
        }
        for item in verification["updated_scores"]:
            card["scores"][item["dimension"]] = item["score"]
        weights = self.weights or {
            "continuity": 0.3,
            "character": 0.25,
            "craft": 0.3,
            "anti_slop": 0.15,
        }
        card["weighted_score"] = round(
            sum(card["scores"][name] * weights[name] for name in DIMENSIONS), 2
        )
        card["required_revisions"] = (
            [] if verification["passed"] else card.get("required_revisions", [])
        )
        resolved_codes = {
            item.get("code")
            for item in verification.get("resolved", [])
            if isinstance(item, dict) and item.get("resolved") is True
        }
        if verification["passed"]:
            card["hard_failures"] = [
                item
                for item in card.get("hard_failures", [])
                if item.get("code") not in resolved_codes
            ]
        accepted = bool(verification["passed"] and not verification["regressions"])
        history = [
            {
                "round": 1,
                "candidate": "revision_01",
                "accepted": accepted,
                "weighted_before": selected.scorecard["weighted_score"],
                "weighted_after": card["weighted_score"],
            }
        ]
        if not accepted:
            return selected, history, False, False
        revised = EvaluatedDraft(
            "revision_01",
            revised_draft,
            static,
            selected.integrated_review,
            selected.reviews,
            card,
            classify_candidate(card, self.floors),
        )
        return revised, history, True, False

    def run(
        self,
        *,
        state: dict[str, Any],
        plan: dict[str, Any],
        recent: list[str],
        planner_context: dict[str, Any],
        workspace: ChapterWorkspace,
        manifest: RunManifest,
    ) -> EvolutionResult:
        caller = self._caller(manifest)
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            generated = list(
                pool.map(
                    lambda index: self._generate_candidate(
                        index=index,
                        state=state,
                        plan=plan,
                        recent=recent,
                        planner_context=planner_context,
                        workspace=workspace,
                        manifest=manifest,
                        caller=caller,
                    ),
                    range(self.candidate_count),
                )
            )
        valid_generated = [item for item in generated if item is not None]
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            evaluated_list = list(
                pool.map(
                    lambda item: self._triage(
                        item,
                        state=state,
                        plan=plan,
                        planner_context=planner_context,
                        workspace=workspace,
                        manifest=manifest,
                        caller=caller,
                    ),
                    valid_generated,
                )
            )
        evaluated = {item.identifier: item for item in evaluated_list}
        generation_degraded = len(valid_generated) < self.candidate_count
        content_degraded = any(
            item.content_status == "HARD_FAIL" for item in evaluated_list
        )
        triage_failures = [
            item.review_failure
            for item in evaluated_list
            if item.review_failure is not None
        ]

        requests = self.plan_specialist_calls(
            list(evaluated.values()), caller.budget.remaining
        )
        complete_for_shadow = [
            item for item in evaluated.values() if item.integrated_review is not None
        ]
        shadow_target = None
        if self.shadow_dimension and complete_for_shadow:
            best_for_shadow = max(
                complete_for_shadow,
                key=lambda item: (item.scorecard["weighted_score"], item.identifier),
            )
            shadow_target = (best_for_shadow.identifier, self.shadow_dimension)
        shadow_review_skipped = bool(
            shadow_target
            and shadow_target
            not in {(item.candidate_id, item.dimension) for item in requests}
        )
        specialist_history: list[dict[str, Any]] = []
        specialist_results: list[SpecialistResult] = []
        if requests:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                specialist_results = list(
                    pool.map(
                        lambda request: self._run_specialist(
                            request,
                            evaluated,
                            state=state,
                            plan=plan,
                            planner_context=planner_context,
                            workspace=workspace,
                            manifest=manifest,
                            caller=caller,
                        ),
                        requests,
                    )
                )
            for result in specialist_results:
                request = result.request
                review = result.review
                before = evaluated[request.candidate_id].classification
                if review is None:
                    specialist_history.append(
                        {
                            "candidate_id": request.candidate_id,
                            "dimension": request.dimension,
                            "reason_code": request.reason_code,
                            "completed": False,
                            "classification_before": before,
                            "classification_after": before,
                            "shadow_review": shadow_target
                            == (request.candidate_id, request.dimension),
                            "failure_kind": (
                                result.failure.failure_kind
                                if result.failure is not None
                                else None
                            ),
                            "diagnostic_artifact": (
                                result.failure.diagnostic_artifact
                                if result.failure is not None
                                else None
                            ),
                        }
                    )
                    continue
                current = evaluated[request.candidate_id]
                reviews = {**current.reviews, request.dimension: review}
                card = merge_specialist_review(current.scorecard, review, self.weights)
                evaluated[request.candidate_id] = replace(
                    current,
                    reviews=reviews,
                    scorecard=card,
                    classification=classify_candidate(card, self.floors),
                )
                specialist_history.append(
                    {
                        "candidate_id": request.candidate_id,
                        "dimension": request.dimension,
                        "reason_code": request.reason_code,
                        "completed": True,
                        "classification_before": before,
                        "classification_after": evaluated[request.candidate_id].classification,
                        "shadow_review": shadow_target
                        == (request.candidate_id, request.dimension),
                    }
                )

        specialist_failures = [
            item.failure
            for item in specialist_results
            if item.failure is not None
        ]
        review_failures = [*triage_failures, *specialist_failures]
        evaluation_degraded = bool(review_failures)
        degraded = generation_degraded or content_degraded or evaluation_degraded

        items = sorted(evaluated.values(), key=lambda item: item.identifier)
        action = selection_policy(
            [
                {
                    "identifier": item.identifier,
                    "classification": item.classification,
                    "scorecard": item.scorecard,
                }
                for item in items
            ],
            self.score_gap,
        )
        if self.mode == "fast":
            action = replace(action, selection_confident=False)
        selected: EvaluatedDraft | None = None
        selection_mode = action.kind
        selection_confident = action.selection_confident
        if action.kind == "SELECTOR":
            eligible_items = [item for item in items if item.classification == "ELIGIBLE"]
            selected_id, selection_confident = self._select_once(
                eligible_items[0],
                eligible_items[1],
                plan=plan,
                workspace=workspace,
                manifest=manifest,
                caller=caller,
            )
            selected = evaluated[selected_id]
        elif action.selected_id:
            selected = evaluated[action.selected_id]

        if selected is None:
            status = (
                "BUDGET_EXHAUSTED"
                if caller.budget.remaining == 0
                else "WAITING_USER"
            )
            return self._finish(
                workspace,
                manifest,
                status=status,
                selected=None,
                evaluated=items,
                selection_mode=selection_mode,
                selection_confident=False,
                revision_history=[],
                specialist_history=specialist_history,
                shadow_review_skipped=shadow_review_skipped,
                generation_degraded=generation_degraded,
                content_degraded=content_degraded,
                evaluation_degraded=evaluation_degraded,
                review_failures=review_failures,
                reasons=["没有可自动选择的合格或近线候选"],
            )

        revision_history: list[dict[str, Any]] = []
        verification_passed = True
        budget_exhausted = False
        should_revise = (
            selected.classification == "NEAR_MISS"
            or bool(selected.scorecard.get("required_revisions"))
        )
        if should_revise and self.mode == "balanced":
            selected, revision_history, verification_passed, budget_exhausted = self._revise_once(
                selected,
                state=state,
                plan=plan,
                recent=recent,
                planner_context=planner_context,
                workspace=workspace,
                manifest=manifest,
                caller=caller,
            )

        continuity_guard_passed = "continuity" in selected.reviews
        if selection_mode == "DIRECT_SELECT_LOW_CONFIDENCE":
            complete_other = (
                len(items) == self.candidate_count
                and all(item.integrated_review is not None for item in items)
            )
            confident_scores = all(
                selected.scorecard.get("confidences", {}).get(name, 0) >= 0.85
                for name in DIMENSIONS
            )
            selection_confident = complete_other and confident_scores and continuity_guard_passed
        policy = {
            **self.config.get("auto_promote", {}),
            "manual_floor": self.config.get("manual_floor", 78),
        }
        outcome = promotion_decision(
            selected.scorecard,
            policy=policy,
            selection_confident=selection_confident,
            selection_mode=(
                "SINGLE_ELIGIBLE"
                if selection_mode == "DIRECT_SELECT_LOW_CONFIDENCE"
                else selection_mode
            ),
            continuity_guard_passed=continuity_guard_passed,
            verification_passed=verification_passed,
        )
        if outcome["status"] == "REPLAN":
            outcome = {
                "status": "WAITING_USER",
                "reasons": ["候选未达到人工确认线；请检查最佳工件后显式决定后续动作"],
            }
        # An invalid unselected alternative must not contaminate a candidate
        # with its own valid evidence. The selected draft still has to pass the
        # independent blind-reader gate before promotion.
        selected_review_invalid = selected.review_failure is not None
        if selected_review_invalid and outcome["status"] == "AUTO_PROMOTE":
            outcome = {
                "status": "WAITING_USER",
                "reasons": ["入选候选存在无效审查工件；严格门禁禁止自动晋级"],
            }
        status = "BUDGET_EXHAUSTED" if budget_exhausted else outcome["status"]
        return self._finish(
            workspace,
            manifest,
            status=status,
            selected=selected,
            evaluated=items,
            selection_mode=selection_mode,
            selection_confident=selection_confident,
            revision_history=revision_history,
            specialist_history=specialist_history,
            shadow_review_skipped=shadow_review_skipped,
            generation_degraded=generation_degraded,
            content_degraded=content_degraded,
            evaluation_degraded=evaluation_degraded,
            review_failures=review_failures,
            reasons=outcome["reasons"] if not budget_exhausted else ["剩余预算不足以同时执行修订和验证"],
        )

    def _finish(
        self,
        workspace: ChapterWorkspace,
        manifest: RunManifest,
        *,
        status: str,
        selected: EvaluatedDraft | None,
        evaluated: list[EvaluatedDraft],
        selection_mode: str,
        selection_confident: bool,
        revision_history: list[dict[str, Any]],
        specialist_history: list[dict[str, Any]],
        shadow_review_skipped: bool,
        generation_degraded: bool,
        content_degraded: bool,
        evaluation_degraded: bool,
        review_failures: list[ReviewFailure],
        reasons: list[str],
    ) -> EvolutionResult:
        prior_stage = manifest.data.get("current_stage")
        manifest.begin("decision")
        failure_records = list(manifest.data.get("failures", []))
        degraded = generation_degraded or content_degraded or evaluation_degraded
        failed_candidates = [
            f"candidate_{index + 1:02d}"
            for index in range(self.candidate_count)
            if f"candidate_{index + 1:02d}" not in {item.identifier for item in evaluated}
        ]
        structured_failures: list[dict[str, Any]] = []
        for candidate_id in failed_candidates:
            stage = f"generate_{candidate_id}"
            record = next(
                (
                    item
                    for item in failure_records
                    if item.get("stage") == stage
                ),
                {},
            )
            structured_failures.append(
                {
                    "candidate_id": candidate_id,
                    "stage": stage,
                    "failure_kind": "GENERATION_FAILED",
                    "message": str(record.get("message") or "候选生成失败"),
                    "diagnostic_artifact": None,
                }
            )
        for item in evaluated:
            if item.content_status != "HARD_FAIL":
                continue
            hard_failures = item.scorecard.get("hard_failures", [])
            message = "正文未通过静态硬门禁"
            if hard_failures and isinstance(hard_failures[0], dict):
                message = str(hard_failures[0].get("explanation") or message)
            structured_failures.append(
                {
                    "candidate_id": item.identifier,
                    "stage": f"static_{item.identifier}",
                    "failure_kind": "CONTENT_HARD_FAIL",
                    "message": message,
                    "diagnostic_artifact": None,
                }
            )
        for failure in review_failures:
            match = re.search(r"candidate_\d{2}", failure.stage)
            structured_failures.append(
                {
                    "candidate_id": match.group(0) if match else None,
                    **failure.as_dict(),
                }
            )
        first_failure = structured_failures[0] if structured_failures else None
        candidate_records = {
            item.identifier: {
                "classification": item.classification,
                "content_status": item.content_status,
                "review_status": item.review_status,
                "failure_stage": (
                    item.review_failure.stage if item.review_failure else None
                ),
                "failure_kind": (
                    item.review_failure.failure_kind if item.review_failure else None
                ),
                "diagnostic_artifact": (
                    item.review_failure.diagnostic_artifact
                    if item.review_failure
                    else None
                ),
                "scorecard": item.scorecard,
            }
            for item in evaluated
        }
        for candidate_id in failed_candidates:
            candidate_records[candidate_id] = {
                "classification": "GENERATION_FAILED",
                "content_status": "GENERATION_FAILED",
                "review_status": "NOT_RUN",
                "failure_stage": f"generate_{candidate_id}",
                "failure_kind": "GENERATION_FAILED",
                "diagnostic_artifact": None,
                "scorecard": None,
            }
        final_path = None
        if selected:
            final_path = (
                f"candidates/{selected.identifier}/draft.txt"
                if selected.identifier.startswith("candidate_")
                else "revisions/round_01/draft.txt"
            )
        budget = manifest.data.get("budget", {})
        decision = {
            "chapter_number": workspace.chapter_number,
            "status": status,
            "selected_id": selected.identifier if selected else None,
            "recommended_candidate": selected.identifier if selected else None,
            "final_draft_path": final_path,
            "selection_mode": selection_mode,
            "selection_confident": selection_confident,
            "reasons": reasons,
            "candidates": candidate_records,
            "final_scorecard": selected.scorecard if selected else None,
            "revision_history": revision_history,
            "specialist_history": specialist_history,
            "shadow_review_skipped": shadow_review_skipped,
            "degraded": degraded,
            "generation_degraded": generation_degraded,
            "content_degraded": content_degraded,
            "evaluation_degraded": evaluation_degraded,
            "failures": structured_failures,
            "failed_candidate": (
                failed_candidates[0]
                if failed_candidates
                else first_failure.get("candidate_id")
                if first_failure
                else None
            ),
            "failure_stage": first_failure.get("stage") if first_failure else None,
            "failure_reason": first_failure.get("message") if first_failure else None,
            "calls_spent": budget.get("spent", 0),
            "calls_remaining": budget.get("remaining", 0),
            "best_available_artifact": final_path,
            "automatic_retry_skipped_reason": (
                "固定调用预算禁止自动重跑无效审查"
                if evaluation_degraded
                else "正文静态硬门禁失败，固定预算禁止自动补写候选"
                if content_degraded
                else "固定调用预算禁止自动补写候选"
                if generation_degraded
                else None
            ),
            "recommended_actions": [
                "查看 decision.md、分数卡和当前最佳稿",
                "符合硬门禁时可用 accept --candidate N 人工接受",
            ],
            "safe_actions": [
                "查看已有规划、候选、分数卡和验证工件",
                "人工比较现有候选",
                "仅在硬门禁通过时人工接受现有候选",
            ],
            "new_budget_actions": [
                "不使用 --resume，显式执行 next 创建新的预算化运行",
                "显式执行独立 audit（使用审计自己的预算）",
            ],
            "resume_warning": "--resume只恢复现有预算，不会把上限扩展到第11次",
            "exhausted_stage": (
                "verifier"
                if status == "BUDGET_EXHAUSTED" and "修订和验证" in "；".join(reasons)
                else prior_stage
                if status == "BUDGET_EXHAUSTED"
                else None
            ),
        }
        workspace.write_json("decision.json", decision)
        lines = [
            "# 章节质量决策",
            "",
            f"- 状态: {status}",
            f"- 选中稿: {decision['selected_id'] or '无'}",
            f"- 调用: {budget.get('spent', 0)}/{budget.get('limit', 10)}",
        ]
        if degraded:
            lines.extend(
                [
                    "",
                    "## 降级说明",
                    f"- 失败候选: {decision['failed_candidate'] or '未知'}",
                    f"- 失败原因: {decision['failure_reason'] or '见运行清单'}",
                    "- 系统未自动重试: "
                    + str(decision["automatic_retry_skipped_reason"]),
                    f"- 当前最佳有效工件: {final_path or '无'}",
                ]
            )
            for failure in structured_failures:
                lines.append(
                    "- 失败明细: "
                    f"{failure.get('stage')} / {failure.get('failure_kind')} / "
                    f"{failure.get('message')}"
                )
                if failure.get("diagnostic_artifact"):
                    lines.append(
                        f"  - 诊断工件: {failure['diagnostic_artifact']}"
                    )
        if reasons:
            lines.extend(["", "## 原因", *[f"- {item}" for item in reasons]])
        if status == "BUDGET_EXHAUSTED":
            lines.extend(
                [
                    "",
                    "## 无需新增调用的安全操作",
                    *[f"- {item}" for item in decision["safe_actions"]],
                    "",
                    "## 会建立新预算的操作",
                    *[f"- {item}" for item in decision["new_budget_actions"]],
                    "",
                    f"- 恢复说明: {decision['resume_warning']}",
                ]
            )
        workspace.write_text("decision.md", "\n".join(lines))
        manifest.set_status(
            status,
            valid_candidates=sum(
                1 for item in evaluated if item.classification == "ELIGIBLE"
            ),
            waiting_reason="；".join(reasons) if status != "AUTO_PROMOTE" else None,
        )
        reviews: dict[str, dict[str, Any]] = {}
        if selected:
            if selected.integrated_review:
                reviews["integrated"] = selected.integrated_review
            reviews.update(selected.reviews)
        return EvolutionResult(
            status=status,
            selected_id=selected.identifier if selected else None,
            draft=selected.draft if selected else None,
            static_review=selected.static_review if selected else None,
            reviews=reviews,
            scorecard=selected.scorecard if selected else None,
            decision=decision,
        )
