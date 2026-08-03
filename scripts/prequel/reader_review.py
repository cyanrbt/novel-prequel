from __future__ import annotations

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
        "verdict",
        "reader_recap",
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
    if review.get("verdict") not in {"PASS", "REVISE", "REPLAN"}:
        issues.append(Issue("READER_BAD_VERDICT", "P1", "盲读者结论无效", repr(review.get("verdict"))))
    recap = review.get("reader_recap")
    if not isinstance(recap, dict) or set(recap) != RECAP_FIELDS:
        issues.append(Issue("READER_BAD_RECAP", "P1", "读者复述字段不完整", repr(recap)))
    elif any(not isinstance(value, str) or not value.strip() for value in recap.values()):
        issues.append(Issue("READER_EMPTY_RECAP", "P1", "读者复述不得为空", repr(recap)))

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
    if verdict in {"REVISE", "REPLAN"} and (not blockers or not instructions):
        issues.append(Issue("READER_FAIL_WITHOUT_ACTION", "P1", "未通过必须给出阻断问题和修订指令", repr(review)))
    return issues
