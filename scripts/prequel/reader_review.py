from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .quality import Issue


RECAP_FIELDS = {
    "current_goal",
    "character_positions",
    "spatial_map",
    "causal_chain",
    "next_question",
}

ADVERSARIAL_CHECK_FIELDS = {
    "ordinary_explanations",
    "missing_preconditions",
    "knowledge_or_behavior_gaps",
    "physical_or_spatial_gaps",
    "unsupported_recap_claims",
}

BLOCKING_ADVERSARIAL_FIELDS = ADVERSARIAL_CHECK_FIELDS - {
    "ordinary_explanations"
}

READING_EXPERIENCE_FIELDS = {
    "prose_accessibility",
    "character_believability",
    "target_emotion_effect",
    "narrative_momentum",
    "continue_reading",
    "first_drop_point",
    "friction_reasons",
}

PASS_EXPERIENCE_FLOORS = {
    "prose_accessibility": 4,
    "character_believability": 4,
    "target_emotion_effect": 3,
    "narrative_momentum": 4,
}


def build_blind_reader_packet(
    state: dict[str, Any], chapter_number: int, draft: str
) -> dict[str, Any]:
    """Expose only reader-visible prior summaries; never send plans or hidden state."""
    summaries = state.get("chapter_summaries", {}).get("summaries", {})
    prior: list[dict[str, Any]] = []
    if isinstance(summaries, dict):
        for key, value in sorted(summaries.items(), key=lambda item: int(item[0])):
            if int(key) >= chapter_number or not isinstance(value, dict):
                continue
            prior.append(
                {
                    "chapter": int(key),
                    "title": value.get("title", ""),
                    "reader_visible_summary": value.get("core", ""),
                }
            )
    return {
        "chapter_number": chapter_number,
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "prior_reader_facts": prior[-3:],
        "draft": draft,
        "instruction_boundary": "没有提供的信息不得推断为作者既定设定。",
    }


def build_blind_reader_prompt(project_root: Path, packet: dict[str, Any]) -> str:
    try:
        role = (project_root / "agents/reader_reviewer.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取盲读者指令: {exc}") from exc
    return (
        role.rstrip()
        + "\n\n# 唯一输入工件\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )


def validate_blind_reader_review(
    review: dict[str, Any], draft: str, expected_chapter: int
) -> list[Issue]:
    issues: list[Issue] = []
    required = {
        "chapter_number",
        "draft_sha256",
        "verdict",
        "reader_recap",
        "adversarial_checks",
        "reading_experience",
        "blocking_issues",
        "warnings",
        "evidence",
        "revision_instructions",
    }
    if not isinstance(review, dict):
        return [Issue("READER_NOT_OBJECT", "P1", "盲读者报告不是object", repr(review))]
    for field in sorted(required - review.keys()):
        issues.append(Issue("READER_FIELD_MISSING", "P1", f"盲读者报告缺失字段: {field}", field))
    if review.get("chapter_number") != expected_chapter:
        issues.append(Issue("READER_CHAPTER_MISMATCH", "P1", "盲读者报告章号不匹配", str(review.get("chapter_number"))))
    expected_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    if review.get("draft_sha256") != expected_hash:
        issues.append(Issue("READER_DRAFT_MISMATCH", "P1", "盲读者报告未绑定当前正文版本", repr(review.get("draft_sha256"))))
    if review.get("verdict") not in {"PASS", "REVISE", "REPLAN"}:
        issues.append(Issue("READER_BAD_VERDICT", "P1", "盲读者结论无效", repr(review.get("verdict"))))
    recap = review.get("reader_recap")
    if not isinstance(recap, dict) or set(recap) != RECAP_FIELDS:
        issues.append(Issue("READER_BAD_RECAP", "P1", "读者复述字段不完整", repr(recap)))
    elif any(not isinstance(value, str) or not value.strip() for value in recap.values()):
        issues.append(Issue("READER_EMPTY_RECAP", "P1", "读者复述不得为空", repr(recap)))

    adversarial_checks = review.get("adversarial_checks")
    if not isinstance(adversarial_checks, dict) or set(adversarial_checks) != ADVERSARIAL_CHECK_FIELDS:
        issues.append(Issue("READER_BAD_ADVERSARIAL_CHECKS", "P1", "反证检查字段不完整", repr(adversarial_checks)))
    elif any(
        not isinstance(value, list)
        or not all(isinstance(item, str) and item.strip() for item in value)
        for value in adversarial_checks.values()
    ):
        issues.append(Issue("READER_BAD_ADVERSARIAL_CHECKS", "P1", "反证检查必须是非空字符串数组的集合", repr(adversarial_checks)))

    experience = review.get("reading_experience")
    experience_valid = True
    if not isinstance(experience, dict) or set(experience) != READING_EXPERIENCE_FIELDS:
        issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "阅读体验字段不完整", repr(experience)))
        experience_valid = False
    else:
        for field in PASS_EXPERIENCE_FLOORS:
            value = experience.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                issues.append(Issue("READER_BAD_EXPERIENCE", "P1", f"{field}必须是1到5的整数", repr(value)))
                experience_valid = False
        if not isinstance(experience.get("continue_reading"), bool):
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "continue_reading必须是布尔值", repr(experience.get("continue_reading"))))
            experience_valid = False
        friction = experience.get("friction_reasons")
        if not isinstance(friction, list) or not all(
            isinstance(item, str) and item.strip() for item in friction
        ):
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "friction_reasons必须是非空字符串数组的集合", repr(friction)))
            experience_valid = False
        drop = experience.get("first_drop_point")
        if drop is not None:
            if (
                not isinstance(drop, dict)
                or set(drop) != {"quote", "explanation"}
                or not all(isinstance(drop.get(key), str) and drop[key].strip() for key in ("quote", "explanation"))
            ):
                issues.append(Issue("READER_BAD_DROP_POINT", "P1", "首个弃读点结构无效", repr(drop)))
                experience_valid = False
            elif drop["quote"] not in draft:
                issues.append(Issue("READER_FALSE_DROP_POINT", "P1", "首个弃读点引文不在正文", repr(drop["quote"])))

    all_quoted: list[Any] = []
    for field, expected_fields in (
        ("blocking_issues", {"code", "quote", "reader_question", "explanation"}),
        ("warnings", {"code", "quote", "explanation"}),
        ("evidence", {"quote", "finding"}),
    ):
        value = review.get(field)
        if not isinstance(value, list):
            issues.append(Issue("READER_BAD_LIST", "P1", f"{field}必须是数组", repr(value)))
            continue
        if field == "evidence" and len(value) < 3:
            issues.append(Issue("READER_NO_EVIDENCE", "P1", "盲读者至少提供三条正文证据", repr(value)))
        for item in value:
            if not isinstance(item, dict) or set(item) != expected_fields:
                issues.append(Issue("READER_BAD_ITEM", "P1", f"{field}条目结构无效", repr(item)))
                continue
            all_quoted.append(item)
    for item in all_quoted:
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote or quote not in draft:
            issues.append(Issue("READER_FALSE_EVIDENCE", "P1", "盲读者引文不在正文", repr(quote)))

    instructions = review.get("revision_instructions")
    if not isinstance(instructions, list) or not all(isinstance(item, str) and item.strip() for item in instructions):
        issues.append(Issue("READER_BAD_INSTRUCTIONS", "P1", "修订指令必须是非空字符串数组", repr(instructions)))
    verdict = review.get("verdict")
    blockers = review.get("blocking_issues")
    if verdict == "PASS" and (blockers or instructions):
        issues.append(Issue("READER_PASS_CONFLICT", "P1", "PASS不得包含阻断问题或修订指令", repr(review)))
    if (
        verdict == "PASS"
        and isinstance(adversarial_checks, dict)
        and any(adversarial_checks.get(field) for field in BLOCKING_ADVERSARIAL_FIELDS)
    ):
        issues.append(
            Issue(
                "READER_PASS_WITH_GAPS",
                "P1",
                "PASS时不得留有前置、知识、空间或复述缺口",
                repr(adversarial_checks),
            )
        )
    if verdict == "PASS" and experience_valid:
        low_scores = {
            field: experience[field]
            for field, floor in PASS_EXPERIENCE_FLOORS.items()
            if experience[field] < floor
        }
        if (
            not experience["continue_reading"]
            or experience["first_drop_point"] is not None
            or experience["friction_reasons"]
            or low_scores
        ):
            issues.append(
                Issue(
                    "READER_PASS_WITHOUT_PULL",
                    "P1",
                    "PASS必须达到阅读体验下限且读者愿意继续阅读",
                    repr({"low_scores": low_scores, "reading_experience": experience}),
                )
            )
    if verdict in {"REVISE", "REPLAN"} and (not blockers or not instructions):
        issues.append(Issue("READER_FAIL_WITHOUT_ACTION", "P1", "未通过必须给出阻断问题和修订指令", repr(review)))
    return issues
