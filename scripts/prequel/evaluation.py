from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from .quality import Issue


DIMENSIONS = ("continuity", "character", "craft", "anti_slop")
WEIGHTS = {
    "continuity": 0.30,
    "character": 0.25,
    "craft": 0.30,
    "anti_slop": 0.15,
}
DEFAULT_FLOORS = {
    "continuity": 85,
    "character": 75,
    "craft": 75,
    "anti_slop": 80,
}


@dataclass(frozen=True)
class SelectionAction:
    kind: str
    selected_id: str | None
    selection_confident: bool


def _canonical_quote(quote: str, draft: str) -> str:
    """Repair only deterministic boundary/whitespace errors in model quotes.

    This does not fuzzy-match words or invent evidence.  A repaired quote must
    still map to one unique, contiguous source slice in the draft.
    """
    if quote in draft:
        return quote
    candidates = [quote.strip()]
    wrappers = {"“": "”", "‘": "’", '"': '"', "'": "'"}
    stripped = candidates[0]
    if (
        len(stripped) >= 8
        and stripped[:1] in wrappers
        and stripped[-1:] == wrappers[stripped[0]]
    ):
        candidates.append(stripped[1:-1])
    if candidates[0].endswith(("”", "’", '"', "'")) and len(candidates[0]) >= 8:
        candidates.append(candidates[0][:-1])
    if candidates[0].endswith(("。", "！", "？", "；")) and len(candidates[0]) >= 8:
        candidates.append(candidates[0][:-1])
    for candidate in candidates:
        if candidate in draft:
            return candidate
        compact_quote = "".join(candidate.split())
        if len(compact_quote) < 8:
            continue
        compact_draft: list[str] = []
        source_positions: list[int] = []
        for index, character in enumerate(draft):
            if character.isspace():
                continue
            compact_draft.append(character)
            source_positions.append(index)
        rendered = "".join(compact_draft)
        if rendered.count(compact_quote) != 1:
            continue
        start = rendered.index(compact_quote)
        end = start + len(compact_quote) - 1
        return draft[source_positions[start] : source_positions[end] + 1]
    return quote


def canonicalize_artifact_quotes(artifact: Any, draft: str) -> int:
    """Canonicalize model evidence in place and return the repair count."""
    repaired = 0

    def visit(value: Any, parent_key: str | None = None) -> None:
        nonlocal repaired
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "quote" and isinstance(item, str):
                    canonical = _canonical_quote(item, draft)
                    if canonical != item:
                        value[key] = canonical
                        repaired += 1
                else:
                    visit(item, key)
        elif isinstance(value, list):
            if parent_key == "evidence" and all(
                isinstance(item, str) for item in value
            ):
                for index, item in enumerate(value):
                    canonical = _canonical_quote(item, draft)
                    if canonical != item:
                        value[index] = canonical
                        repaired += 1
            else:
                for item in value:
                    visit(item, parent_key)

    visit(artifact)
    return repaired


def validate_integrated_review(
    review: dict[str, Any],
    draft: str,
    expected_chapter: int,
    allowed_fact_ids: set[str] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    required = {
        "chapter_number",
        "scores",
        "confidences",
        "hard_failures",
        "warnings",
        "evidence",
        "required_revisions",
        "specialist_requests",
        "fact_findings",
        "summaries",
    }
    if not isinstance(review, dict):
        return [Issue("INTEGRATED_NOT_OBJECT", "P1", "集成审查不是object", repr(review))]
    for field in sorted(required - review.keys()):
        issues.append(Issue("INTEGRATED_FIELD_MISSING", "P1", f"集成审查缺失字段: {field}", field))
    if review.get("chapter_number") != expected_chapter:
        issues.append(Issue("INTEGRATED_CHAPTER_MISMATCH", "P1", "集成审查章号不匹配", str(review.get("chapter_number"))))
    scores = review.get("scores")
    confidences = review.get("confidences")
    summaries = review.get("summaries")
    for name, value, code in (
        ("scores", scores, "INTEGRATED_BAD_SCORES"),
        ("confidences", confidences, "INTEGRATED_BAD_CONFIDENCES"),
        ("summaries", summaries, "INTEGRATED_BAD_SUMMARIES"),
    ):
        if not isinstance(value, dict) or set(value) != set(DIMENSIONS):
            issues.append(Issue(code, "P1", f"{name}必须完整包含四个维度", repr(value)))
    if isinstance(scores, dict):
        for dimension in DIMENSIONS:
            score = scores.get(dimension)
            if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
                issues.append(Issue("INTEGRATED_BAD_SCORE", "P1", f"{dimension}分数无效", repr(score)))
    if isinstance(confidences, dict):
        for dimension in DIMENSIONS:
            confidence = confidences.get(dimension)
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                issues.append(Issue("INTEGRATED_BAD_CONFIDENCE", "P1", f"{dimension}置信度无效", repr(confidence)))
    evidence = review.get("evidence")
    if not isinstance(evidence, dict) or set(evidence) != set(DIMENSIONS):
        issues.append(Issue("INTEGRATED_BAD_EVIDENCE", "P1", "集成证据必须按四维组织", repr(evidence)))
        evidence = {}
    quoted_items: list[Any] = []
    for dimension in DIMENSIONS:
        items = evidence.get(dimension, [])
        if not isinstance(items, list) or len(items) < 2:
            issues.append(Issue("INTEGRATED_NO_EVIDENCE", "P1", f"{dimension}至少需要两条证据", repr(items)))
        elif isinstance(items, list):
            quoted_items.extend(items)
    for field in ("hard_failures", "warnings", "required_revisions", "fact_findings"):
        value = review.get(field, [])
        if not isinstance(value, list):
            issues.append(Issue("INTEGRATED_BAD_LIST", "P1", f"{field}必须是数组", repr(value)))
        else:
            quoted_items.extend(value)
    for item in quoted_items:
        quote = item.get("quote") if isinstance(item, dict) else None
        if not quote or quote not in draft:
            issues.append(Issue("REVIEW_FALSE_EVIDENCE", "P1", "集成审查引文不在正文", repr(quote)))
    if allowed_fact_ids is not None:
        for finding in review.get("fact_findings", []):
            fact_id = finding.get("fact_id") if isinstance(finding, dict) else None
            if fact_id not in allowed_fact_ids:
                issues.append(Issue("INTEGRATED_UNKNOWN_FACT", "P1", "集成审查使用未知事实ID", repr(fact_id)))
    return issues


def scorecard_from_integrated(
    review: dict[str, Any], weights: dict[str, float] | None = None
) -> dict[str, Any]:
    weights = weights or WEIGHTS
    scores = {name: review["scores"][name] for name in DIMENSIONS}
    return {
        "scores": scores,
        "confidences": {name: review["confidences"][name] for name in DIMENSIONS},
        "weighted_score": round(sum(scores[name] * weights[name] for name in DIMENSIONS), 2),
        "hard_failures": list(review["hard_failures"]),
        "required_revisions": list(review["required_revisions"]),
        "warnings": list(review["warnings"]),
        "summaries": dict(review["summaries"]),
    }


def merge_specialist_review(
    card: dict[str, Any], review: dict[str, Any], weights: dict[str, float] | None = None
) -> dict[str, Any]:
    dimension = review["dimension"]
    result = {
        **card,
        "scores": dict(card["scores"]),
        "confidences": dict(card.get("confidences", {})),
        "summaries": dict(card.get("summaries", {})),
    }
    result["scores"][dimension] = review["score"]
    result["confidences"][dimension] = 1.0
    result["summaries"][dimension] = review.get("summary", "")
    for field in ("hard_failures", "required_revisions", "warnings"):
        kept = [item for item in card.get(field, []) if item.get("dimension") != dimension]
        result[field] = [*kept, *[{**item, "dimension": dimension} for item in review.get(field, [])]]
    selected_weights = weights or WEIGHTS
    result["weighted_score"] = round(
        sum(result["scores"][name] * selected_weights[name] for name in DIMENSIONS), 2
    )
    return result


def classify_candidate(
    card: dict[str, Any],
    floors: dict[str, int] | None = None,
    near_miss_weighted: float = 82,
    max_floor_deficit: int = 5,
) -> str:
    floors = floors or DEFAULT_FLOORS
    if card.get("hard_failures"):
        return "HARD_FAIL"
    if all(card["scores"][name] >= floors[name] for name in DIMENSIONS):
        return "ELIGIBLE"
    deficits = [max(0, floors[name] - card["scores"][name]) for name in DIMENSIONS]
    if card["weighted_score"] >= near_miss_weighted and max(deficits) <= max_floor_deficit:
        return "NEAR_MISS"
    return "LOW_SCORE"


def selection_policy(
    candidates: list[dict[str, Any]], score_gap: float = 4
) -> SelectionAction:
    eligible_items = [item for item in candidates if item["classification"] == "ELIGIBLE"]
    eligible_items.sort(key=lambda item: (-item["scorecard"]["weighted_score"], item["identifier"]))
    if len(eligible_items) >= 2:
        gap = eligible_items[0]["scorecard"]["weighted_score"] - eligible_items[1]["scorecard"]["weighted_score"]
        if gap > score_gap:
            return SelectionAction("DIRECT_SELECT", eligible_items[0]["identifier"], True)
        return SelectionAction("SELECTOR", None, False)
    if len(eligible_items) == 1:
        return SelectionAction("DIRECT_SELECT_LOW_CONFIDENCE", eligible_items[0]["identifier"], False)
    near = [item for item in candidates if item["classification"] == "NEAR_MISS"]
    near.sort(key=lambda item: (-item["scorecard"]["weighted_score"], item["identifier"]))
    if near:
        return SelectionAction("REVISE", near[0]["identifier"], False)
    # A single, precisely located factual contradiction in an otherwise strong
    # candidate is safer to repair-and-verify than to discard blindly.  This
    # path is deliberately narrow: content-level hard failures have already
    # been filtered before semantic scoring, and multiple/low-score failures
    # still require human intervention or a fresh run.
    revisable_hard = []
    for item in candidates:
        hard_failures = item["scorecard"].get("hard_failures", [])
        revisions = item["scorecard"].get("required_revisions", [])
        hard_codes = {
            failure.get("code")
            for failure in hard_failures
            if isinstance(failure, dict)
        }
        linked_revisions = [
            revision
            for revision in revisions
            if isinstance(revision, dict) and revision.get("code") in hard_codes
        ]
        if (
            item["classification"] == "HARD_FAIL"
            and item["scorecard"].get("weighted_score", 0) >= 82
            and len(hard_failures) == 1
            and len(linked_revisions) == 1
            and len(revisions) <= 3
        ):
            revisable_hard.append(item)
    revisable_hard.sort(
        key=lambda item: (-item["scorecard"]["weighted_score"], item["identifier"])
    )
    if revisable_hard:
        return SelectionAction("REVISE", revisable_hard[0]["identifier"], False)
    return SelectionAction("WAITING_USER", None, False)


def validate_revision_verification(
    verification: dict[str, Any],
    revised_draft: str,
    expected_chapter: int,
    *,
    baseline_scores: dict[str, int] | None = None,
    max_dimension_regression: int | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if verification.get("chapter_number") != expected_chapter:
        issues.append(Issue("VERIFY_CHAPTER_MISMATCH", "P1", "验证章号不匹配", repr(verification.get("chapter_number"))))
    if not isinstance(verification.get("passed"), bool):
        issues.append(Issue("VERIFY_BAD_STATUS", "P1", "passed必须是布尔值", repr(verification.get("passed"))))
    for field in ("resolved", "regressions", "evidence", "updated_scores"):
        if not isinstance(verification.get(field), list):
            issues.append(Issue("VERIFY_BAD_LIST", "P1", f"{field}必须是数组", repr(verification.get(field))))
    for item in verification.get("evidence", []):
        quote = item.get("quote") if isinstance(item, dict) else None
        if not quote or quote not in revised_draft:
            issues.append(Issue("VERIFY_FALSE_EVIDENCE", "P1", "验证引文不在修订稿", repr(quote)))
    if (
        verification.get("passed") is True
        and not verification.get("regressions")
        and baseline_scores is not None
        and max_dimension_regression is not None
    ):
        for item in verification.get("updated_scores", []):
            if not isinstance(item, dict):
                continue
            dimension = item.get("dimension")
            score = item.get("score")
            baseline = baseline_scores.get(dimension)
            if (
                isinstance(score, int)
                and isinstance(baseline, int)
                and score < baseline - max_dimension_regression
            ):
                issues.append(
                    Issue(
                        "VERIFY_UNDECLARED_SCORE_REGRESSION",
                        "P1",
                        "验证声称无回归，却将"
                        f"{dimension}从{baseline}降至{score}",
                        repr(item),
                    )
                )
    return issues


def validate_specialist_review(
    review: dict[str, Any],
    draft: str,
    expected_chapter: int,
    expected_dimension: str,
) -> list[Issue]:
    issues: list[Issue] = []
    required = {
        "chapter_number",
        "dimension",
        "score",
        "hard_failures",
        "warnings",
        "evidence",
        "required_revisions",
        "summary",
    }
    if not isinstance(review, dict):
        return [
            Issue(
                "SPECIALIST_NOT_OBJECT",
                "P1",
                "专项审查不是object",
                repr(review)[:120],
            )
        ]
    for field in sorted(required - review.keys()):
        issues.append(
            Issue(
                "SPECIALIST_FIELD_MISSING",
                "P1",
                f"专项审查缺失字段: {field}",
                field,
            )
        )
    if review.get("chapter_number") != expected_chapter:
        issues.append(
            Issue(
                "SPECIALIST_CHAPTER_MISMATCH",
                "P1",
                "专项审查章号不匹配",
                str(review.get("chapter_number")),
            )
        )
    if review.get("dimension") != expected_dimension:
        issues.append(
            Issue(
                "SPECIALIST_DIMENSION_MISMATCH",
                "P1",
                "专项审查维度不匹配",
                str(review.get("dimension")),
            )
        )
    score = review.get("score")
    if (
        not isinstance(score, int)
        or isinstance(score, bool)
        or not 0 <= score <= 100
    ):
        issues.append(
            Issue(
                "SPECIALIST_BAD_SCORE",
                "P1",
                "专项分数必须是0到100的整数",
                repr(score),
            )
        )
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 3:
        issues.append(
            Issue(
                "SPECIALIST_NO_EVIDENCE",
                "P1",
                "专项审查至少需要三条证据",
                repr(evidence),
            )
        )
    evidence_items: list[Any] = []
    for field in (
        "evidence",
        "hard_failures",
        "warnings",
        "required_revisions",
    ):
        value = review.get(field, [])
        if not isinstance(value, list):
            issues.append(
                Issue(
                    "SPECIALIST_BAD_LIST",
                    "P1",
                    f"专项审查字段必须是数组: {field}",
                    repr(value),
                )
            )
            continue
        evidence_items.extend(value)
    for item in evidence_items:
        quote = item.get("quote") if isinstance(item, dict) else None
        if not quote or quote not in draft:
            issues.append(
                Issue(
                    "REVIEW_FALSE_EVIDENCE",
                    "P1",
                    "专项审查引文不在正文",
                    repr(quote),
                )
            )
    return issues


def validate_ballot(
    ballot: dict[str, Any], draft_a: str, draft_b: str
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(ballot, dict):
        return [Issue("BALLOT_NOT_OBJECT", "P1", "盲选不是object", repr(ballot))]
    if ballot.get("winner") not in {"A", "B", "TIE"}:
        issues.append(
            Issue(
                "BALLOT_BAD_WINNER",
                "P1",
                "盲选胜者无效",
                str(ballot.get("winner")),
            )
        )
    evidence = ballot.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 4:
        issues.append(
            Issue(
                "BALLOT_NO_EVIDENCE",
                "P1",
                "盲选至少需要四条证据",
                repr(evidence),
            )
        )
        return issues
    for item in evidence:
        candidate = item.get("candidate") if isinstance(item, dict) else None
        quote = item.get("quote") if isinstance(item, dict) else None
        source = draft_a if candidate == "A" else draft_b if candidate == "B" else ""
        if not quote or quote not in source:
            issues.append(
                Issue(
                    "BALLOT_FALSE_EVIDENCE",
                    "P1",
                    "盲选引文不在对应候选",
                    repr(quote),
                )
            )
    return issues


def build_scorecard(
    reviews: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = weights or WEIGHTS
    scores = {name: reviews[name]["score"] for name in DIMENSIONS}
    return {
        "scores": scores,
        "weighted_score": round(
            sum(scores[name] * weights[name] for name in DIMENSIONS), 2
        ),
        "hard_failures": [
            item
            for name in DIMENSIONS
            for item in reviews[name]["hard_failures"]
        ],
        "required_revisions": [
            item
            for name in DIMENSIONS
            for item in reviews[name]["required_revisions"]
        ],
    }


def eligible(
    card: dict[str, Any], floors: dict[str, int] | None = None
) -> bool:
    floors = floors or DEFAULT_FLOORS
    return not card["hard_failures"] and all(
        card["scores"][name] >= floors[name] for name in DIMENSIONS
    )


def tally_ballots(winners: list[str | None]) -> tuple[str | None, int]:
    counts = Counter(winner for winner in winners if winner is not None)
    if not counts:
        return None, 0
    winner, votes = counts.most_common(1)[0]
    return (winner, votes) if votes >= 2 else (None, votes)


def revision_improved(
    previous: dict[str, Any],
    current: dict[str, Any],
    supporting_votes: int,
    max_regression: int = 3,
) -> bool:
    if (
        supporting_votes < 2
        or current["weighted_score"] <= previous["weighted_score"]
    ):
        return False
    return all(
        current["scores"][name]
        >= previous["scores"][name] - max_regression
        for name in DIMENSIONS
    )


def promotion_decision(
    card: dict[str, Any],
    score_winner: str | None = None,
    ballot_winner: str | None = None,
    ballot_votes: int = 0,
    policy: dict[str, Any] | None = None,
    *,
    selection_confident: bool | None = None,
    selection_mode: str = "DUAL",
    continuity_guard_passed: bool = False,
    verification_passed: bool = True,
) -> dict[str, Any]:
    defaults = {
        "weighted_score": 85,
        "continuity": 90,
        "character": 82,
        "craft": 82,
        "anti_slop": 82,
        "ballot_votes": 2,
        "manual_floor": 78,
    }
    policy = {**defaults, **(policy or {})}
    if selection_confident is None:
        selection_confident = (
            score_winner is not None
            and score_winner == ballot_winner
            and ballot_votes >= policy["ballot_votes"]
        )
    guard_ok = selection_mode != "SINGLE_ELIGIBLE" or continuity_guard_passed
    auto = (
        card["weighted_score"] >= policy["weighted_score"]
        and card["scores"]["continuity"] >= policy["continuity"]
        and all(
            card["scores"][name] >= policy[name]
            for name in ("character", "craft", "anti_slop")
        )
        and not card["hard_failures"]
        and not card["required_revisions"]
        and selection_confident
        and guard_ok
        and verification_passed
    )
    if auto:
        return {"status": "AUTO_PROMOTE", "reasons": []}
    if card["weighted_score"] >= policy["manual_floor"] and not card["hard_failures"]:
        return {
            "status": "WAITING_USER",
            "reasons": ["未同时满足全部自动提升条件"],
        }
    return {
        "status": "REPLAN",
        "reasons": ["总分低于人工确认线或存在硬失败"],
    }
