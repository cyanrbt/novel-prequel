from __future__ import annotations

import hashlib
import json
import re
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
    "opening_pull",
    "protagonist_ownership",
    "question_progression",
    "ending_compulsion",
    "competitive_readiness",
    "next_click_reason",
    "continue_reading",
    "first_drop_point",
    "friction_reasons",
    "friction_severity",
}

BENCHMARK_DIMENSIONS = {
    "character_attachment",
    "active_threat",
    "protagonist_specificity",
    "revelation_transformation",
    "emotional_aftereffect",
}

BENCHMARK_COMPARISON_FIELDS = {
    *BENCHMARK_DIMENSIONS,
    "evidence_payoff_mode",
    "would_choose_over_competent_peer",
    "major_gaps",
}

PACING_DIAGNOSTIC_FIELDS = {
    "first_1000_chars_result",
    "first_active_pressure",
    "core_threat_activation",
    "first_costly_choice",
    "pressure_turns",
    "max_pressure_gap_chars",
    "exposition_runs",
    "information_only_passages",
}

PACING_MILESTONE_FIELDS = {
    "first_active_pressure",
    "core_threat_activation",
    "first_costly_choice",
}

PASS_PACING_LIMITS = {
    "first_active_pressure": 25.0,
    "core_threat_activation": 30.0,
    "first_costly_choice": 60.0,
    "max_pressure_gap_chars": 800,
    "last_pressure_turn_min_percent": 85.0,
    "information_only_passage_max_chars": 120,
}

PASS_EXPERIENCE_FLOORS = {
    "prose_accessibility": 4,
    "character_believability": 4,
    "target_emotion_effect": 4,
    "narrative_momentum": 4,
    "opening_pull": 4,
    "protagonist_ownership": 4,
    "question_progression": 4,
    "ending_compulsion": 4,
}


def build_blind_reader_packet(
    state: dict[str, Any], chapter_number: int, draft: str,
    project_root: Path | None = None,
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
    packet = {
        "chapter_number": chapter_number,
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "prior_reader_facts": prior[-3:],
        "draft": draft,
        "instruction_boundary": "没有提供的信息不得推断为作者既定设定。",
    }
    if project_root is not None:
        benchmark_path = project_root / "novel/benchmarks/opening_compulsion.md"
        try:
            packet["benchmark_calibration"] = benchmark_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactValidationError(f"无法读取开篇追读力标杆卡: {exc}") from exc
    return packet


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


def _compact_length(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _chapter_body(draft: str) -> tuple[str, int]:
    first_line, separator, remainder = draft.partition("\n")
    if separator and re.match(r"^第\d+章(?:[：:].*)?$", first_line.strip()):
        return remainder.lstrip("\r\n"), len(draft) - len(remainder.lstrip("\r\n"))
    return draft, 0


def _pacing_length(draft: str) -> int:
    body, _ = _chapter_body(draft)
    return _compact_length(body)


def _quote_metrics(draft: str, quote: Any) -> tuple[int, float] | None:
    if not isinstance(quote, str) or not quote or draft.count(quote) != 1:
        return None
    raw_offset = draft.find(quote)
    body, body_offset = _chapter_body(draft)
    if raw_offset < body_offset:
        return None
    compact_offset = _compact_length(draft[body_offset:raw_offset])
    total = max(_compact_length(body), 1)
    return compact_offset, round(compact_offset / total * 100, 1)


def canonicalize_pacing_diagnostics(review: dict[str, Any], draft: str) -> None:
    """Derive every countable pacing metric from quoted draft evidence."""
    pacing = review.get("pacing_diagnostics")
    if not isinstance(pacing, dict):
        return

    for field in PACING_MILESTONE_FIELDS:
        item = pacing.get(field)
        if not isinstance(item, dict):
            continue
        metrics = _quote_metrics(draft, item.get("quote"))
        if metrics is not None:
            item["position_percent"] = metrics[1]

    turn_positions: list[int] = []
    turn_quotes: set[str] = set()
    pressure_turns = pacing.get("pressure_turns")
    if isinstance(pressure_turns, list):
        for item in pressure_turns:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            metrics = _quote_metrics(draft, quote)
            if metrics is None or quote in turn_quotes:
                continue
            turn_quotes.add(quote)
            turn_positions.append(metrics[0])
    if len(turn_positions) >= 2 and turn_positions == sorted(turn_positions):
        pacing["max_pressure_gap_chars"] = max(
            right - left
            for left, right in zip(turn_positions, turn_positions[1:])
        )

    exposition_runs = pacing.get("exposition_runs")
    if isinstance(exposition_runs, list):
        for item in exposition_runs:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote or quote not in draft:
                continue
            item["paragraph_count"] = len(
                [part for part in re.split(r"\n\s*\n", quote) if part.strip()]
            )
            item["approx_chars"] = _compact_length(quote)

    information_only = pacing.get("information_only_passages")
    if isinstance(information_only, list):
        for item in information_only:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            if isinstance(quote, str) and quote and quote in draft:
                item["approx_chars"] = _compact_length(quote)


def _validate_pacing_diagnostics(
    pacing: Any, draft: str, verdict: Any
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(pacing, dict) or set(pacing) != PACING_DIAGNOSTIC_FIELDS:
        return [
            Issue(
                "READER_BAD_PACING",
                "P1",
                "节奏诊断字段不完整",
                repr(pacing),
            )
        ]

    first_result = pacing.get("first_1000_chars_result")
    if not isinstance(first_result, str) or not first_result.strip():
        issues.append(
            Issue(
                "READER_BAD_PACING",
                "P1",
                "节奏诊断必须说明前1000字造成的结果",
                repr(first_result),
            )
        )

    for field in PACING_MILESTONE_FIELDS:
        item = pacing.get(field)
        if (
            not isinstance(item, dict)
            or set(item) != {"quote", "position_percent", "effect"}
            or isinstance(item.get("position_percent"), bool)
            or not isinstance(item.get("position_percent"), (int, float))
            or not isinstance(item.get("effect"), str)
            or not item["effect"].strip()
        ):
            issues.append(
                Issue(
                    "READER_BAD_PACING_MILESTONE",
                    "P1",
                    f"{field}必须提供唯一正文引文、位置百分比和实际效果",
                    repr(item),
                )
            )
            continue
        metrics = _quote_metrics(draft, item.get("quote"))
        if metrics is None:
            issues.append(
                Issue(
                    "READER_FALSE_PACING_EVIDENCE",
                    "P1",
                    f"{field}引文必须在正文中且只出现一次",
                    repr(item.get("quote")),
                )
            )
            continue
        _, actual_percent = metrics
        if abs(float(item["position_percent"]) - actual_percent) > 0.2:
            issues.append(
                Issue(
                    "READER_FALSE_PACING_POSITION",
                    "P1",
                    f"{field}位置百分比与正文不符",
                    repr(
                        {
                            "reported": item["position_percent"],
                            "actual": actual_percent,
                        }
                    ),
                )
            )

    pressure_turns = pacing.get("pressure_turns")
    turn_positions: list[int] = []
    turn_quotes: set[str] = set()
    if not isinstance(pressure_turns, list) or len(pressure_turns) < 3:
        issues.append(
            Issue(
                "READER_BAD_PRESSURE_TURNS",
                "P1",
                "节奏诊断至少需要三次有效压力变化",
                repr(pressure_turns),
            )
        )
    else:
        for item in pressure_turns:
            if (
                not isinstance(item, dict)
                or set(item) != {"quote", "effect"}
                or not isinstance(item.get("effect"), str)
                or not item["effect"].strip()
            ):
                issues.append(
                    Issue(
                        "READER_BAD_PRESSURE_TURNS",
                        "P1",
                        "压力变化必须提供唯一正文引文和新产生的选择、关系或危险",
                        repr(item),
                    )
                )
                continue
            quote = item.get("quote")
            metrics = _quote_metrics(draft, quote)
            if metrics is None or quote in turn_quotes:
                issues.append(
                    Issue(
                        "READER_FALSE_PRESSURE_TURN",
                        "P1",
                        "压力变化引文必须在正文中唯一且不得重复",
                        repr(quote),
                    )
                )
                continue
            turn_quotes.add(quote)
            turn_positions.append(metrics[0])
        if turn_positions != sorted(turn_positions):
            issues.append(
                Issue(
                    "READER_UNORDERED_PRESSURE_TURNS",
                    "P1",
                    "压力变化必须按正文出现顺序提交",
                    repr(turn_positions),
                )
            )

    reported_gap = pacing.get("max_pressure_gap_chars")
    actual_gap: int | None = None
    if len(turn_positions) >= 2:
        actual_gap = max(
            right - left
            for left, right in zip(turn_positions, turn_positions[1:])
        )
    if isinstance(reported_gap, bool) or not isinstance(reported_gap, int) or reported_gap < 0:
        issues.append(
            Issue(
                "READER_BAD_PRESSURE_GAP",
                "P1",
                "max_pressure_gap_chars必须是非负整数",
                repr(reported_gap),
            )
        )
    elif actual_gap is not None and reported_gap != actual_gap:
        issues.append(
            Issue(
                "READER_FALSE_PRESSURE_GAP",
                "P1",
                "最大压力空档与正文引文位置不符",
                repr({"reported": reported_gap, "actual": actual_gap}),
            )
        )

    exposition_runs = pacing.get("exposition_runs")
    long_exposition = False
    if not isinstance(exposition_runs, list):
        issues.append(
            Issue(
                "READER_BAD_EXPOSITION_RUNS",
                "P1",
                "exposition_runs必须是数组",
                repr(exposition_runs),
            )
        )
    else:
        for item in exposition_runs:
            if (
                not isinstance(item, dict)
                or set(item) != {"quote", "paragraph_count", "approx_chars", "explanation"}
                or isinstance(item.get("paragraph_count"), bool)
                or not isinstance(item.get("paragraph_count"), int)
                or item["paragraph_count"] < 1
                or isinstance(item.get("approx_chars"), bool)
                or not isinstance(item.get("approx_chars"), int)
                or item["approx_chars"] < 1
                or not isinstance(item.get("explanation"), str)
                or not item["explanation"].strip()
            ):
                issues.append(
                    Issue(
                        "READER_BAD_EXPOSITION_RUNS",
                        "P1",
                        "纯解释段必须提供连续引文、段数、字数和停滞原因",
                        repr(item),
                    )
                )
                continue
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote or quote not in draft:
                issues.append(
                    Issue(
                        "READER_FALSE_EXPOSITION_RUN",
                        "P1",
                        "纯解释段引文不在正文",
                        repr(quote),
                    )
                )
                continue
            actual_paragraphs = len(
                [part for part in re.split(r"\n\s*\n", quote) if part.strip()]
            )
            actual_chars = _compact_length(quote)
            if (
                item["paragraph_count"] != actual_paragraphs
                or abs(item["approx_chars"] - actual_chars) > 2
            ):
                issues.append(
                    Issue(
                        "READER_FALSE_EXPOSITION_METRICS",
                        "P1",
                        "纯解释段的段数或字数与引文不符",
                        repr(
                            {
                                "reported_paragraphs": item["paragraph_count"],
                                "actual_paragraphs": actual_paragraphs,
                                "reported_chars": item["approx_chars"],
                                "actual_chars": actual_chars,
                            }
                        ),
                    )
                )
            long_exposition = long_exposition or actual_paragraphs >= 3

    information_only = pacing.get("information_only_passages")
    oversized_information = False
    if not isinstance(information_only, list):
        issues.append(
            Issue(
                "READER_BAD_INFORMATION_ONLY",
                "P1",
                "information_only_passages必须是数组",
                repr(information_only),
            )
        )
    else:
        for item in information_only:
            if (
                not isinstance(item, dict)
                or set(item) != {"quote", "approx_chars", "explanation"}
                or isinstance(item.get("approx_chars"), bool)
                or not isinstance(item.get("approx_chars"), int)
                or item["approx_chars"] < 1
                or not isinstance(item.get("explanation"), str)
                or not item["explanation"].strip()
            ):
                issues.append(
                    Issue(
                        "READER_BAD_INFORMATION_ONLY",
                        "P1",
                        "纯信息段必须提供连续引文、字数和删除影响",
                        repr(item),
                    )
                )
                continue
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote or quote not in draft:
                issues.append(
                    Issue(
                        "READER_FALSE_INFORMATION_ONLY",
                        "P1",
                        "纯信息段引文不在正文",
                        repr(quote),
                    )
                )
                continue
            actual_chars = _compact_length(quote)
            if abs(item["approx_chars"] - actual_chars) > 2:
                issues.append(
                    Issue(
                        "READER_FALSE_INFORMATION_METRICS",
                        "P1",
                        "纯信息段字数与引文不符",
                        repr({"reported": item["approx_chars"], "actual": actual_chars}),
                    )
                )
            oversized_information = oversized_information or (
                actual_chars
                >= PASS_PACING_LIMITS["information_only_passage_max_chars"]
            )

    if verdict == "PASS" and not any(issue.severity == "P1" for issue in issues):
        late_milestones = {}
        for field in PACING_MILESTONE_FIELDS:
            metrics = _quote_metrics(draft, pacing[field]["quote"])
            if metrics and metrics[1] > PASS_PACING_LIMITS[field]:
                late_milestones[field] = metrics[1]
        last_turn_percent = (
            round(turn_positions[-1] / max(_pacing_length(draft), 1) * 100, 1)
            if turn_positions
            else 0.0
        )
        if (
            late_milestones
            or (actual_gap is not None and actual_gap > PASS_PACING_LIMITS["max_pressure_gap_chars"])
            or last_turn_percent < PASS_PACING_LIMITS["last_pressure_turn_min_percent"]
            or long_exposition
            or oversized_information
        ):
            issues.append(
                Issue(
                    "READER_PASS_WITH_SLOW_PACING",
                    "P1",
                    "PASS必须满足威胁、选择、压力空档和解释密度门禁",
                    repr(
                        {
                            "late_milestones": late_milestones,
                            "max_pressure_gap_chars": actual_gap,
                            "last_pressure_turn_percent": last_turn_percent,
                            "long_exposition": long_exposition,
                            "oversized_information": oversized_information,
                        }
                    ),
                )
            )
    return issues


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
        "pacing_diagnostics",
        "reading_experience",
        "benchmark_comparison",
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

    issues.extend(
        _validate_pacing_diagnostics(
            review.get("pacing_diagnostics"), draft, review.get("verdict")
        )
    )

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
        if experience.get("competitive_readiness") not in {"BELOW", "NEAR", "MATCH"}:
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "competitive_readiness必须是BELOW、NEAR或MATCH", repr(experience.get("competitive_readiness"))))
            experience_valid = False
        if not isinstance(experience.get("next_click_reason"), str) or not experience["next_click_reason"].strip():
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "next_click_reason不得为空", repr(experience.get("next_click_reason"))))
            experience_valid = False
        friction = experience.get("friction_reasons")
        if not isinstance(friction, list) or not all(
            isinstance(item, str) and item.strip() for item in friction
        ):
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "friction_reasons必须是非空字符串数组的集合", repr(friction)))
            experience_valid = False
        if experience.get("friction_severity") not in {"NONE", "MINOR", "MAJOR"}:
            issues.append(Issue("READER_BAD_EXPERIENCE", "P1", "friction_severity必须是NONE、MINOR或MAJOR", repr(experience.get("friction_severity"))))
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

    benchmark = review.get("benchmark_comparison")
    benchmark_valid = True
    if not isinstance(benchmark, dict) or set(benchmark) != BENCHMARK_COMPARISON_FIELDS:
        issues.append(Issue("READER_BAD_BENCHMARK", "P1", "标杆比较字段不完整", repr(benchmark)))
        benchmark_valid = False
    else:
        for dimension in BENCHMARK_DIMENSIONS:
            item = benchmark.get(dimension)
            if (
                not isinstance(item, dict)
                or set(item) != {"score", "quote", "assessment"}
                or isinstance(item.get("score"), bool)
                or not isinstance(item.get("score"), int)
                or not 1 <= item["score"] <= 5
                or not isinstance(item.get("quote"), str)
                or not item["quote"]
                or item["quote"] not in draft
                or not isinstance(item.get("assessment"), str)
                or not item["assessment"].strip()
            ):
                issues.append(Issue("READER_BAD_BENCHMARK", "P1", f"{dimension}必须给出1到5分、正文引文和判断", repr(item)))
                benchmark_valid = False
        if benchmark.get("evidence_payoff_mode") not in {"HUMAN_CHANGE", "MIXED", "EVIDENCE_ONLY"}:
            issues.append(Issue("READER_BAD_BENCHMARK", "P1", "evidence_payoff_mode取值无效", repr(benchmark.get("evidence_payoff_mode"))))
            benchmark_valid = False
        if not isinstance(benchmark.get("would_choose_over_competent_peer"), bool):
            issues.append(Issue("READER_BAD_BENCHMARK", "P1", "would_choose_over_competent_peer必须是布尔值", repr(benchmark.get("would_choose_over_competent_peer"))))
            benchmark_valid = False
        gaps = benchmark.get("major_gaps")
        if not isinstance(gaps, list) or not all(isinstance(item, str) and item.strip() for item in gaps):
            issues.append(Issue("READER_BAD_BENCHMARK", "P1", "major_gaps必须是非空字符串数组的集合", repr(gaps)))
            benchmark_valid = False

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
            or experience["friction_severity"] == "MAJOR"
            or experience["competitive_readiness"] != "MATCH"
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
    if verdict == "PASS" and benchmark_valid:
        low_benchmark_scores = {
            field: benchmark[field]["score"]
            for field in BENCHMARK_DIMENSIONS
            if benchmark[field]["score"] < 4
        }
        if (
            low_benchmark_scores
            or benchmark["evidence_payoff_mode"] == "EVIDENCE_ONLY"
            or not benchmark["would_choose_over_competent_peer"]
            or benchmark["major_gaps"]
        ):
            issues.append(
                Issue(
                    "READER_PASS_BELOW_BENCHMARK",
                    "P1",
                    "正式开篇必须在人物依恋、主动威胁、主角独特性、揭示变形和情绪余震上达到标杆级",
                    repr({"low_scores": low_benchmark_scores, "benchmark_comparison": benchmark}),
                )
            )
    if verdict in {"REVISE", "REPLAN"}:
        benchmark_gaps = (
            benchmark.get("major_gaps", [])
            if isinstance(benchmark, dict)
            else []
        )
        low_benchmark = (
            any(
                isinstance(benchmark.get(field), dict)
                and benchmark[field].get("score", 5) < 4
                for field in BENCHMARK_DIMENSIONS
            )
            if isinstance(benchmark, dict)
            else False
        )
        low_experience = (
            any(
                isinstance(experience.get(field), int)
                and experience[field] < floor
                for field, floor in PASS_EXPERIENCE_FLOORS.items()
            )
            if isinstance(experience, dict)
            else False
        )
        actionable_diagnosis = bool(
            blockers or benchmark_gaps or low_benchmark or low_experience
        )
        if not actionable_diagnosis or not instructions:
            issues.append(Issue(
                "READER_FAIL_WITHOUT_ACTION",
                "P1",
                "未通过必须给出阻断问题，或由标杆低分/重大差距提供诊断，并同时给出修订指令",
                repr(review),
            ))
    return issues
