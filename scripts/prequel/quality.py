from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str
    evidence: str


PLAN_REQUIRED = {
    "chapter_number", "title", "event_id", "phase", "chapter_purpose", "dramatic_spine", "scenes",
    "new_information", "state_changes", "rule_hypotheses", "canon_evidence_ids",
    "foreshadow_operations", "milestone_operations", "hook", "prohibited_elements",
}

REVIEW_REQUIRED = {
    "chapter_number", "verdict", "grade", "p1_failures", "p2_warnings", "evidence",
    "character_assessment", "canon_assessment", "style_assessment", "revision_instructions",
}

STATE_CHANGE_REQUIRED = {
    "protagonist_known_info_add", "protagonist_inventory_add",
    "protagonist_inventory_remove", "protagonist_location",
    "protagonist_body_updates", "ability_updates", "timeline_year",
    "timeline_elapsed_days", "character_updates", "world_confirmed_add",
    "world_hypotheses_add",
}

SCENE_MODEL_REQUIRED = {
    "initial_state",
    "discovery_path",
    "knowledge_limits",
    "ordinary_explanations",
    "choice_reason",
    "end_state",
}

DRAMATIC_SPINE_REQUIRED = {
    "opening_pressure",
    "opening_genre_signal",
    "protagonist_immediate_want",
    "personal_stake",
    "destabilizing_event",
    "protagonist_choice",
    "choice_cost",
    "cost_realization",
    "relationship_friction",
    "question_progression",
    "emotional_turn",
    "serial_promise",
}

FORBIDDEN_POV = ["他不知道的是", "她不知道的是", "与此同时", "在另一边", "另一边却"]
ACTION_PATTERNS = {
    "停步不回头": r"停下来.{0,10}没有回头",
    "折纸入怀": r"纸折好.{0,10}塞进怀里",
    "守灯到天亮": r"看着.{0,10}(?:油灯|火苗).{0,30}天亮",
    "渡口扔石": r"(?:渡口|江边).{0,60}扔.{0,10}石",
    "眼光变暗": r"眼睛里的光变了.{0,20}更暗",
}

DEFAULT_LENGTH_POLICY = {
    "safe_min": 1,
    "target_min": 3500,
    "target_max": 5000,
    "safe_max": 8000,
}


def _result(issues: list[Issue], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": not any(issue.severity == "P1" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "metrics": metrics,
    }


def validate_plan(
    plan: dict[str, Any],
    state: dict[str, Any],
    allowed_canon_ids: set[str] | None = None,
    allowed_foreshadow_ids: set[str] | None = None,
    allowed_milestone_ids: set[str] | None = None,
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(plan, dict):
        return [Issue("PLAN_NOT_OBJECT", "P1", "规划不是JSON object", repr(plan)[:120])]
    for field in sorted(PLAN_REQUIRED - plan.keys()):
        issues.append(Issue("PLAN_FIELD_MISSING", "P1", f"规划缺失字段: {field}", field))
    expected = state.get("chapter", {}).get("next_chapter")
    if plan.get("chapter_number") != expected:
        issues.append(Issue("PLAN_CHAPTER_MISMATCH", "P1", f"规划章号应为{expected}", str(plan.get("chapter_number"))))
    if plan.get("event_id") != state.get("chapter", {}).get("current_event"):
        issues.append(Issue("PLAN_EVENT_MISMATCH", "P1", "规划事件与状态不一致", str(plan.get("event_id"))))
    spine = plan.get("dramatic_spine")
    if not isinstance(spine, dict) or set(spine) != DRAMATIC_SPINE_REQUIRED:
        issues.append(
            Issue(
                "BAD_DRAMATIC_SPINE",
                "P1",
                "规划必须完整定义人物欲望、选择、代价与问题升级",
                repr(spine)[:240],
            )
        )
    else:
        for field in DRAMATIC_SPINE_REQUIRED - {"question_progression"}:
            if not isinstance(spine.get(field), str) or not spine[field].strip():
                issues.append(
                    Issue(
                        "EMPTY_DRAMATIC_SPINE",
                        "P1",
                        f"dramatic_spine.{field}不得为空",
                        repr(spine.get(field)),
                    )
                )
        progression = spine.get("question_progression")
        if (
            not isinstance(progression, list)
            or not 3 <= len(progression) <= 4
            or not all(isinstance(item, str) and item.strip() for item in progression)
            or len(set(progression)) != len(progression)
        ):
            issues.append(
                Issue(
                    "BAD_QUESTION_PROGRESSION",
                    "P1",
                    "问题链必须包含3到4个互不重复的具体问题",
                    repr(progression),
                )
            )
    changes = plan.get("state_changes")
    if not isinstance(changes, dict):
        issues.append(Issue("NO_STATE_CHANGE", "P1", "本章没有不可逆状态变化", "state_changes"))
    else:
        for field in sorted(STATE_CHANGE_REQUIRED - changes.keys()):
            issues.append(Issue("STATE_CHANGE_FIELD_MISSING", "P1", f"状态变化缺失字段: {field}", field))
        material_lists = (
            "protagonist_known_info_add", "protagonist_inventory_add",
            "protagonist_inventory_remove", "protagonist_body_updates",
            "ability_updates", "character_updates", "world_confirmed_add",
            "world_hypotheses_add",
        )
        material = any(changes.get(field) for field in material_lists)
        material = material or (
            changes.get("protagonist_location") not in {None, state.get("protagonist", {}).get("location")}
        )
        material = material or changes.get("timeline_year") != state.get("timeline", {}).get("current_year")
        material = material or changes.get("timeline_elapsed_days") != state.get("timeline", {}).get("elapsed_days")
        if not material:
            issues.append(Issue("NO_STATE_CHANGE", "P1", "本章状态字段没有任何真实变化", repr(changes)[:200]))
    evidence_ids = plan.get("canon_evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        issues.append(Issue("NO_CANON_EVIDENCE", "P1", "规划没有声明原著/前传依据", repr(evidence_ids)))
    elif allowed_canon_ids is not None:
        unknown = sorted(set(evidence_ids) - allowed_canon_ids)
        if unknown:
            issues.append(Issue("UNKNOWN_CANON_EVIDENCE", "P1", "规划引用了未注册依据", ", ".join(unknown)))
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        issues.append(Issue("NO_SCENES", "P1", "规划没有场景", "scenes"))
    else:
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                issues.append(Issue("SCENE_NOT_OBJECT", "P1", f"场景{index}不是object", repr(scene)[:160]))
                continue
            if not scene.get("goal") or not scene.get("conflict"):
                issues.append(Issue("SCENE_NO_DRAMATIC_PRESSURE", "P1", f"场景{index}缺少目标或阻力", repr(scene)[:160]))
            if not scene.get("function") and not scene.get("irreversible_change"):
                issues.append(Issue("SCENE_NO_FUNCTION", "P1", f"场景{index}缺少场景功能", repr(scene)[:160]))
            if not scene.get("pressure_change") and not scene.get("irreversible_change"):
                issues.append(Issue("SCENE_NO_PRESSURE_CHANGE", "P1", f"场景{index}没有压力变化", repr(scene)[:160]))
            missing_model = sorted(SCENE_MODEL_REQUIRED - scene.keys())
            if missing_model:
                issues.append(Issue(
                    "SCENE_MODEL_MISSING",
                    "P1",
                    f"场景{index}缺少可复原场景字段",
                    ", ".join(missing_model),
                ))
                continue
            for field in (
                "initial_state",
                "discovery_path",
                "knowledge_limits",
                "choice_reason",
                "end_state",
            ):
                if not isinstance(scene.get(field), str) or not scene[field].strip():
                    issues.append(Issue(
                        "SCENE_MODEL_EMPTY",
                        "P1",
                        f"场景{index}的{field}不得为空",
                        repr(scene.get(field)),
                    ))
            explanations = scene.get("ordinary_explanations")
            if not isinstance(explanations, dict):
                issues.append(Issue(
                    "SCENE_BAD_ALTERNATIVES",
                    "P1",
                    f"场景{index}的普通解释必须是object",
                    repr(explanations),
                ))
            else:
                required_alternatives = {"considered", "excluded", "remaining"}
                if set(explanations) != required_alternatives:
                    issues.append(Issue(
                        "SCENE_BAD_ALTERNATIVES",
                        "P1",
                        f"场景{index}的普通解释字段不完整",
                        repr(explanations),
                    ))
                elif not all(
                    isinstance(explanations[field], list)
                    and all(isinstance(item, str) and item.strip() for item in explanations[field])
                    for field in required_alternatives
                ):
                    issues.append(Issue(
                        "SCENE_BAD_ALTERNATIVES",
                        "P1",
                        f"场景{index}的普通解释必须是字符串数组",
                        repr(explanations),
                    ))
    foreshadows = plan.get("foreshadow_operations")
    planned_foreshadows: dict[str, set[str]] = {"plant": set(), "recover": set()}
    if not isinstance(foreshadows, dict):
        issues.append(Issue("BAD_FORESHADOW_OPS", "P1", "伏笔操作必须是object", repr(foreshadows)))
    else:
        for operation in ("plant", "recover"):
            values = foreshadows.get(operation)
            if not isinstance(values, list):
                issues.append(Issue("BAD_FORESHADOW_OPS", "P1", f"伏笔{operation}必须是数组", repr(values)))
                continue
            for value in values:
                if not isinstance(value, str) or not re.match(r"^F-[A-Z]-?\d+", value.strip()):
                    issues.append(Issue("BAD_FORESHADOW_ID", "P1", "伏笔必须以稳定ID开头，如F-A01", repr(value)))
                    continue
                matched = re.match(r"^(F-[A-Z]-?\d+)", value.strip())
                if matched:
                    item_id = matched.group(1)
                    planned_foreshadows[operation].add(item_id)
                    if allowed_foreshadow_ids is not None and item_id not in allowed_foreshadow_ids:
                        issues.append(Issue("UNKNOWN_FORESHADOW", "P1", "伏笔未在登记表中定义", item_id))
    milestones = plan.get("milestone_operations")
    if not isinstance(milestones, dict) or not isinstance(milestones.get("complete"), list):
        issues.append(Issue("BAD_MILESTONE_OPS", "P1", "里程碑操作必须包含complete数组", repr(milestones)))
        planned_milestones: set[str] = set()
    else:
        planned_milestones = {item for item in milestones["complete"] if isinstance(item, str)}
        if allowed_milestone_ids is not None:
            for item in sorted(planned_milestones - allowed_milestone_ids):
                issues.append(Issue("UNKNOWN_MILESTONE", "P1", "里程碑未在登记表中定义", item))
    completed_before = set(state.get("completed_milestones", []))
    active_foreshadows = state.get("active_foreshadows", {})
    overlap = planned_foreshadows["plant"] & planned_foreshadows["recover"]
    for item_id in sorted(overlap):
        issues.append(Issue("FORESHADOW_SAME_CHAPTER_RECOVERY", "P1", "伏笔不得在同章播种并回收", item_id))
    for item_id in sorted(planned_foreshadows["plant"]):
        if item_id in active_foreshadows:
            issues.append(Issue("FORESHADOW_ALREADY_PLANTED", "P1", "伏笔已经播种，不得重复播种", item_id))
    for item_id in sorted(planned_foreshadows["recover"]):
        runtime = active_foreshadows.get(item_id)
        if not isinstance(runtime, dict) or runtime.get("status") != "已播种":
            issues.append(Issue("FORESHADOW_NOT_PLANTED", "P1", "伏笔必须在更早章节播种后才能回收", item_id))
        elif runtime.get("plant_chapter", expected) >= expected:
            issues.append(Issue("FORESHADOW_NOT_MATURE", "P1", "伏笔不能在播种章立即回收", item_id))
    registry_entries = (foreshadow_registry or {}).get("entries", {})
    for item_id in sorted(planned_foreshadows["plant"]):
        entry = registry_entries.get(item_id, {})
        prerequisites = entry.get("plant_after", []) if isinstance(entry, dict) else []
        missing = [item for item in prerequisites if item not in completed_before]
        if missing:
            issues.append(Issue("FORESHADOW_PREREQUISITE_MISSING", "P1", "伏笔播种前置里程碑未完成", f"{item_id}: {', '.join(missing)}"))
    for milestone in planned_milestones:
        for item_id, runtime in active_foreshadows.items():
            entry = registry_entries.get(item_id, {})
            if (
                isinstance(entry, dict)
                and entry.get("recover_by") == milestone
                and isinstance(runtime, dict)
                and runtime.get("status") == "已播种"
                and item_id not in planned_foreshadows["recover"]
            ):
                issues.append(Issue("FORESHADOW_RECOVERY_OVERDUE", "P1", "完成该里程碑前必须回收已到期伏笔", f"{item_id} -> {milestone}"))

    milestone_entries = (arc_registry or {}).get("milestones", {})
    current_volume = state.get("chapter", {}).get("current_volume")
    for milestone in sorted(planned_milestones):
        if milestone in completed_before:
            issues.append(Issue("MILESTONE_ALREADY_COMPLETED", "P1", "里程碑已经完成，不得重复提交", milestone))
        entry = milestone_entries.get(milestone, {})
        if not isinstance(entry, dict):
            continue
        missing = [item for item in entry.get("after", []) if item not in completed_before]
        if missing:
            issues.append(Issue("MILESTONE_PREREQUISITE_MISSING", "P1", "里程碑前置条件未完成", f"{milestone}: {', '.join(missing)}"))
        if entry.get("volume") is not None and entry.get("volume") != current_volume:
            issues.append(Issue("MILESTONE_WRONG_VOLUME", "P1", "里程碑不属于当前卷", f"{milestone}: volume {entry.get('volume')}"))
    abilities = state.get("protagonist", {}).get("abilities", {})
    rendered = repr({
        key: plan.get(key)
        for key in ("scenes", "new_information", "state_changes", "rule_hypotheses")
    })
    completed_milestones = completed_before | planned_milestones
    for name, ability in abilities.items():
        unlock_after = ability.get("unlock_after")
        if isinstance(unlock_after, list) and name in rendered:
            missing = [item for item in unlock_after if item not in completed_milestones]
            if missing:
                issues.append(Issue("ABILITY_GATE", "P1", f"能力前置里程碑未完成: {name}", ", ".join(missing)))
        elif expected and expected < ability.get("unlock_chapter", 0) and name in rendered:
            issues.append(Issue("ABILITY_GATE", "P1", f"能力提前出现: {name}", name))
    return issues


def validate_review(
    review: dict[str, Any],
    static_review: dict[str, Any],
    *,
    expected_chapter: int | None = None,
    draft: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(review, dict):
        return [Issue("REVIEW_NOT_OBJECT", "P1", "审查不是JSON object", repr(review)[:120])]
    for field in sorted(REVIEW_REQUIRED - review.keys()):
        issues.append(Issue("REVIEW_FIELD_MISSING", "P1", f"审查缺失字段: {field}", field))
    if review.get("verdict") not in {"PASS", "REVISE", "REPLAN"}:
        issues.append(Issue("REVIEW_BAD_VERDICT", "P1", "审查结论无效", str(review.get("verdict"))))
    if expected_chapter is not None and review.get("chapter_number") != expected_chapter:
        issues.append(Issue("REVIEW_CHAPTER_MISMATCH", "P1", f"审查章号应为{expected_chapter}", str(review.get("chapter_number"))))
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 3:
        issues.append(Issue("REVIEW_NO_EVIDENCE", "P1", "审查必须提供至少3条原文定位证据", repr(evidence)))
    elif draft is not None:
        for item in evidence:
            quote = item.get("quote") if isinstance(item, dict) else None
            if not quote or quote not in draft:
                issues.append(Issue("REVIEW_FALSE_EVIDENCE", "P1", "审查证据未在正文中找到", repr(quote)))
    if not static_review.get("passed") and review.get("verdict") == "PASS":
        issues.append(Issue("REVIEW_CONTRADICTS_STATIC", "P1", "语义审查不得推翻P1硬检查", "verdict=PASS"))
    if review.get("verdict") == "PASS" and review.get("p1_failures"):
        issues.append(Issue("REVIEW_SELF_CONTRADICTION", "P1", "PASS审查仍包含P1失败", repr(review.get("p1_failures"))[:160]))
    if review.get("verdict") == "PASS" and review.get("grade") not in {"A", "B"}:
        issues.append(Issue("REVIEW_PASS_LOW_GRADE", "P1", "PASS审查等级只能是A或B", str(review.get("grade"))))
    if review.get("verdict") == "PASS" and review.get("revision_instructions"):
        issues.append(Issue("REVIEW_PASS_WITH_REVISIONS", "P1", "PASS审查不得再要求修订", repr(review.get("revision_instructions"))[:160]))
    return issues


def _paragraphs(text: str, minimum: int = 30) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= minimum]


def _long_sentences(text: str, minimum: int = 24) -> list[str]:
    sentences = re.split(r"[。！？!?\n]+", text)
    return [re.sub(r"\s+", "", sentence) for sentence in sentences if len(re.sub(r"\s+", "", sentence)) >= minimum]


def scan_draft(
    text: str,
    recent_texts: list[str],
    era_bans: dict[str, Any],
    plan: dict[str, Any],
    *,
    length_policy: dict[str, int] | None = None,
) -> dict[str, Any]:
    issues: list[Issue] = []
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped or stripped in {"[PLACEHOLDER]", "PLACEHOLDER", "待生成"}:
        issues.append(Issue("PLACEHOLDER_TEXT", "P1", "正文为空或为占位内容", stripped[:80]))
        return _result(issues, {"char_count": len(stripped)})

    for name in era_bans.get("characters", []):
        if name and name in stripped:
            issues.append(Issue("ERA_BANNED_CHARACTER", "P1", f"当前年代禁入人物: {name}", name))
    for term in era_bans.get("terms", []):
        if term and term in stripped:
            issues.append(Issue("ERA_BANNED_TERM", "P1", f"当前年代禁入概念: {term}", term))
    for item in plan.get("prohibited_elements", []):
        if item and item in stripped and not any(x.evidence == item for x in issues):
            issues.append(Issue("PLAN_PROHIBITED_ELEMENT", "P1", f"规划明确禁入: {item}", item))

    header_match = re.match(r"^第\s*(\d+)\s*章", stripped)
    expected = plan.get("chapter_number")
    if not header_match or int(header_match.group(1)) != expected:
        evidence = stripped.splitlines()[0][:100] if stripped else ""
        issues.append(Issue("CHAPTER_NUMBER_MISMATCH", "P1", f"正文标题章号应为{expected}", evidence))

    for phrase in FORBIDDEN_POV:
        if phrase in stripped:
            issues.append(Issue("FORBIDDEN_POV", "P1", f"出现越视角句式: {phrase}", phrase))

    recent_paragraphs = set()
    recent_sentences: Counter[str] = Counter()
    for recent in recent_texts:
        recent_paragraphs.update(_paragraphs(recent))
        recent_sentences.update(_long_sentences(recent))
    for paragraph in _paragraphs(stripped):
        if paragraph in recent_paragraphs:
            issues.append(Issue("EXACT_PARAGRAPH_REUSE", "P1", "与最近章节重复完整段落", paragraph[:120]))
    current_sentences = Counter(_long_sentences(stripped))
    for sentence, count in current_sentences.items():
        if count + recent_sentences[sentence] >= 3:
            issues.append(Issue("CROSS_CHAPTER_REPETITION", "P1", "长句或动作句在近期累计至少3次", sentence[:120]))

    action_counts: dict[str, int] = {}
    combined_recent = "\n".join(recent_texts)
    for name, pattern in ACTION_PATTERNS.items():
        current_count = len(re.findall(pattern, stripped))
        recent_count = len(re.findall(pattern, combined_recent))
        action_counts[name] = current_count
        if current_count >= 2 or current_count + recent_count >= 3:
            issues.append(Issue("REPEATED_ACTION", "P2", f"动作模板重复: {name}", f"本章{current_count}，近期{recent_count}"))

    char_count = len(re.sub(r"\s+", "", stripped))
    dash_count = stripped.count("——")
    dash_per_thousand = round(dash_count / max(char_count, 1) * 1000, 2)
    if dash_per_thousand > 8:
        issues.append(Issue("DASH_OVERUSE", "P2", "破折号密度过高", str(dash_per_thousand)))
    negation_count = len(re.findall(r"不是.{0,24}是", stripped))
    if negation_count > 3:
        issues.append(Issue("NEGATION_TEMPLATE_OVERUSE", "P2", "‘不是…是…’句式过多", str(negation_count)))
    policy = {**DEFAULT_LENGTH_POLICY, **(length_policy or {})}
    safe_min = int(policy["safe_min"])
    target_min = int(policy["target_min"])
    target_max = int(policy["target_max"])
    safe_max = int(policy["safe_max"])
    if char_count < safe_min or char_count > safe_max:
        issues.append(
            Issue(
                "WORD_COUNT_HARD_FAIL",
                "P1",
                f"正文长度超出{safe_min}-{safe_max}字安全范围",
                str(char_count),
            )
        )
    elif char_count < target_min or char_count > target_max:
        issues.append(
            Issue(
                "WORD_COUNT_TARGET_MISS",
                "P2",
                f"正文未达到{target_min}-{target_max}字目标范围，需审查是否充分演出",
                str(char_count),
            )
        )

    return _result(
        issues,
        {
            "char_count": char_count,
            "dash_count": dash_count,
            "dash_per_thousand": dash_per_thousand,
            "negation_template_count": negation_count,
            "action_counts": action_counts,
            "length_policy": policy,
        },
    )
