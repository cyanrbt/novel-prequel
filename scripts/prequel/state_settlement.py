from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .evidence_hierarchy import (
    detect_evidence_hierarchy_escalations,
    settlement_factual_claims,
)
from .quality import Issue
from .project import load_role_text


def _state_factual_escalations(
    settlement: dict[str, Any],
    draft: str,
    audit_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if audit_profile is not None and not audit_profile.get(
        "evidence_hierarchy", {}
    ).get("enabled", False):
        return []
    findings = detect_evidence_hierarchy_escalations(
        draft, settlement_factual_claims(settlement)
    )
    code_map = {
        "REPORT_EVIDENCE_LEVEL_ESCALATION": "SETTLEMENT_FACT_LEVEL_OVERSTATEMENT",
        "REPORT_BOUNDARY_STATE_ESCALATION": "SETTLEMENT_BOUNDARY_STATE_CONTRADICTION",
    }
    return [
        {**item, "code": code_map[item["code"]]}
        for item in findings
        if item.get("code") in code_map
    ]


def _render_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def expected_state_changes(
    state: dict[str, Any],
    plan: dict[str, Any],
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Flatten only state mutations that would materially change the live state."""
    changes = plan.get("state_changes", {})
    expected: list[dict[str, Any]] = []

    def add(
        path: str,
        value: Any,
        meaning: str,
        *,
        required_for_promotion: bool = False,
    ) -> None:
        expected.append(
            {
                "path": path,
                "value": _render_value(value),
                "meaning": meaning,
                "required_for_promotion": required_for_promotion,
            }
        )

    known = set(state.get("protagonist", {}).get("known_info", []))
    for index, value in enumerate(changes.get("protagonist_known_info_add", [])):
        if value not in known:
            add(
                f"state_changes.protagonist_known_info_add[{index}]",
                value,
                "主角实际获得的新信息",
                required_for_promotion=True,
            )
    inventory = set(state.get("protagonist", {}).get("inventory", []))
    for index, value in enumerate(changes.get("protagonist_inventory_add", [])):
        if value not in inventory:
            add(
                f"state_changes.protagonist_inventory_add[{index}]",
                value,
                "主角实际取得的物品",
                required_for_promotion=True,
            )
    for index, value in enumerate(changes.get("protagonist_inventory_remove", [])):
        if value in inventory:
            add(
                f"state_changes.protagonist_inventory_remove[{index}]",
                value,
                "主角实际失去的物品",
                required_for_promotion=True,
            )
    location = changes.get("protagonist_location")
    if location is not None and location != state.get("protagonist", {}).get("location"):
        add(
            "state_changes.protagonist_location",
            location,
            "章末主角实际所在地点",
            required_for_promotion=True,
        )
    body = state.get("protagonist", {}).get("body", {})
    for index, value in enumerate(changes.get("protagonist_body_updates", [])):
        if body.get(value.get("key")) != value.get("value"):
            add(
                f"state_changes.protagonist_body_updates[{index}]",
                value,
                "正文造成的身体状态变化",
                required_for_promotion=True,
            )
    abilities = state.get("protagonist", {}).get("abilities", {})
    for index, value in enumerate(changes.get("ability_updates", [])):
        current = abilities.get(value.get("name"), {}).get("status")
        if current != value.get("status"):
            add(
                f"state_changes.ability_updates[{index}]",
                value,
                "正文明确发生的能力状态变化",
                required_for_promotion=True,
            )
    if changes.get("timeline_year") != state.get("timeline", {}).get("current_year"):
        add(
            "state_changes.timeline_year",
            changes.get("timeline_year"),
            "正文可定位的新年份",
            required_for_promotion=True,
        )
    if changes.get("timeline_elapsed_days") != state.get("timeline", {}).get("elapsed_days"):
        add("state_changes.timeline_elapsed_days", changes.get("timeline_elapsed_days"), "正文可推出的累计天数")
    active = state.get("characters", {}).get("active", {})
    for index, value in enumerate(changes.get("character_updates", [])):
        target = {"status": value.get("status"), "note": value.get("note")}
        if active.get(value.get("name")) != target:
            add(
                f"state_changes.character_updates[{index}]",
                value,
                "配角章末状态变化",
                required_for_promotion=True,
            )
    confirmed = set(state.get("world_lore", {}).get("confirmed", []))
    for index, value in enumerate(changes.get("world_confirmed_add", [])):
        if value not in confirmed:
            add(
                f"state_changes.world_confirmed_add[{index}]",
                value,
                "正文已经充分确认的世界事实",
                required_for_promotion=True,
            )
    hypotheses = set(state.get("world_lore", {}).get("hypotheses", []))
    for index, value in enumerate(changes.get("world_hypotheses_add", [])):
        if value not in hypotheses:
            add(f"state_changes.world_hypotheses_add[{index}]", value, "正文中人物实际形成的假说")

    foreshadows = plan.get("foreshadow_operations", {})
    for operation in ("plant", "recover"):
        for index, value in enumerate(foreshadows.get(operation, [])):
            entry = (foreshadow_registry or {}).get("entries", {}).get(value, {})
            operation_label = "播种" if operation == "plant" else "回收"
            narrative = entry.get("plant") or entry.get("meaning") or value
            long_term = entry.get("meaning", "")
            meaning = f"伏笔{operation_label}在正文中的落点：{narrative}"
            if long_term and long_term != narrative:
                meaning += f"；长期含义：{long_term}"
            add(
                f"foreshadow_operations.{operation}[{index}]",
                value,
                meaning,
                required_for_promotion=True,
            )
    for index, value in enumerate(plan.get("milestone_operations", {}).get("complete", [])):
        entry = (arc_registry or {}).get("milestones", {}).get(value, {})
        meaning = "里程碑完成的正文依据"
        if entry.get("meaning"):
            meaning += f"：{entry['meaning']}"
        add(
            f"milestone_operations.complete[{index}]",
            value,
            meaning,
            required_for_promotion=True,
        )
    return expected


def build_state_settlement_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "chapter_number": plan["chapter_number"],
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "state_before": {
            "timeline": state.get("timeline", {}),
            "protagonist": state.get("protagonist", {}),
            "active_characters": state.get("characters", {}).get("active", {}),
            "world_lore": state.get("world_lore", {}),
            "active_foreshadows": state.get("active_foreshadows", {}),
            "completed_milestones": state.get("completed_milestones", []),
        },
        "planned_change_candidates": expected_state_changes(
            state, plan, foreshadow_registry, arc_registry
        ),
        "planned_hook_type": plan.get("hook", {}).get("type", ""),
        "draft": draft,
        "instruction_boundary": (
            "大纲只提供待验证候选；最终正文没有逐字证据的变化不得结算。"
            "required_for_promotion为true的关键义务必须有证据；其他候选可以缺失，"
            "缺失时不得写入长期状态。"
        ),
    }


def canonicalize_missing_change_paths(
    settlement: dict[str, Any], expected: list[dict[str, Any]]
) -> int:
    """Repair only annotations appended to an otherwise exact candidate path."""
    missing = settlement.get("missing_changes")
    if not isinstance(missing, list):
        return 0
    expected_paths = {
        item["path"] for item in expected if isinstance(item.get("path"), str)
    }
    repaired = 0
    for index, item in enumerate(missing):
        if not isinstance(item, str) or item in expected_paths:
            continue
        matches = [
            path
            for path in expected_paths
            if item.startswith(path)
            and item[len(path) : len(path) + 1] in {":", "：", "（", "(", " "}
        ]
        if len(matches) == 1:
            missing[index] = matches[0]
            repaired += 1
    return repaired


def build_state_settlement_prompt(project_root: Path, packet: dict[str, Any]) -> str:
    try:
        role = load_role_text(project_root, "state_settler")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取状态结算指令: {exc}") from exc
    return role.rstrip() + "\n\n# 唯一输入工件\n" + json.dumps(packet, ensure_ascii=False, indent=2)


def build_state_settlement_validation_diagnostic(
    settlement: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    issues: list[Issue],
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe the one narrow report-coverage failure eligible for feedback."""
    expected_items = expected_state_changes(
        state, plan, foreshadow_registry, arc_registry
    )
    expected_by_path = {
        item["path"]: item
        for item in expected_items
        if isinstance(item.get("path"), str)
    }
    missing = settlement.get("missing_changes")
    missing_paths = (
        list(missing)
        if isinstance(missing, list)
        and all(isinstance(item, str) for item in missing)
        else []
    )
    missing_obligations = [
        expected_by_path[path]
        for path in missing_paths
        if path in expected_by_path
    ]
    required_missing = [
        item
        for item in missing_obligations
        if item.get("required_for_promotion") is True
    ]
    p1_issues = [item for item in issues if item.severity == "P1"]
    p1_codes = [item.code for item in p1_issues]
    evidence = settlement.get("change_evidence")
    retry_eligible = bool(
        settlement.get("draft_sha256")
        == hashlib.sha256(draft.encode("utf-8")).hexdigest()
        and settlement.get("verdict") == "PASS"
        and p1_codes == ["SETTLEMENT_PASS_WITH_GAPS"]
        and missing_paths
        and len(set(missing_paths)) == len(missing_paths)
        and len(missing_obligations) == len(missing_paths)
        and required_missing
        and settlement.get("hook") is not None
        and isinstance(evidence, list)
        and bool(evidence)
    )
    return {
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "initial_verdict": settlement.get("verdict"),
        "p1_issues": [
            {
                "code": item.code,
                "severity": item.severity,
                "message": item.message,
                "evidence": item.evidence,
            }
            for item in p1_issues
        ],
        "missing_obligations": missing_obligations,
        "required_missing_paths": [item["path"] for item in required_missing],
        "retry_eligible": retry_eligible,
        "retry_performed": False,
        "feedback_prompt_version": None,
    }


def build_state_settlement_missing_feedback_prompt(
    project_root: Path,
    packet: dict[str, Any],
    first_settlement: dict[str, Any],
    diagnostic: dict[str, Any],
) -> str:
    """Request one complete re-settlement for precisely identified omissions."""
    try:
        role = load_role_text(project_root, "state_settler")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取状态结算指令: {exc}") from exc
    feedback = {
        "mode": "MISSING_REQUIRED_STATE_EVIDENCE_FEEDBACK",
        "draft_sha256": packet.get("draft_sha256"),
        "deterministic_validation": diagnostic,
        "first_canonicalized_settlement": first_settlement,
        "original_settlement_packet": packet,
        "required_action": (
            "程序确认首报的唯一硬错误是PASS遗漏了计划中的待举证状态义务。"
            "逐项检查missing_obligations给出的精确path和value；只有当一条唯一、"
            "连续、逐字存在于draft的正文引文足以支持整个value时，才能把该义务"
            "加入change_evidence，并从missing_changes移除。不得拼接引文、改写value、"
            "复用不支持整项结论的短语，也不得只删missing_changes。"
            "required_missing_paths中的任一路径若没有这种证据，必须按本schema把"
            "verdict改为INSUFFICIENT_EVIDENCE，不得维持PASS。draft_sha256不得改变。"
            "请重新输出一份完整的状态结算JSON，并重新核对摘要、钩子、全部已有证据"
            "及missing_changes覆盖关系；反馈不代表首报其他陈述已获程序背书。"
        ),
    }
    return (
        role.rstrip()
        + "\n\n# 一次性状态结算缺项反馈\n"
        + json.dumps(feedback, ensure_ascii=False, indent=2)
    )


def validate_state_settlement_feedback_contract(
    final_settlement: dict[str, Any],
    draft: str,
    diagnostic: dict[str, Any],
) -> list[Issue]:
    """A repaired PASS must add exact, uniquely grounded required evidence."""
    if final_settlement.get("verdict") != "PASS":
        return []
    evidence = final_settlement.get("change_evidence")
    rows = evidence if isinstance(evidence, list) else []
    issues: list[Issue] = []
    for obligation in diagnostic.get("missing_obligations", []):
        if (
            not isinstance(obligation, dict)
            or obligation.get("required_for_promotion") is not True
        ):
            continue
        matching = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("path") == obligation.get("path")
            and row.get("value") == obligation.get("value")
        ]
        quote = matching[0].get("quote") if len(matching) == 1 else None
        if (
            not isinstance(quote, str)
            or not quote
            or draft.count(quote) != 1
        ):
            issues.append(
                Issue(
                    "SETTLEMENT_FEEDBACK_OBLIGATION_UNRESOLVED",
                    "P1",
                    "缺项反馈后的PASS必须为每项关键义务提供唯一连续正文引文",
                    repr(
                        {
                            "path": obligation.get("path"),
                            "value": obligation.get("value"),
                            "quote": quote,
                        }
                    ),
                )
            )
    return issues


def validate_state_settlement(
    settlement: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
    audit_profile: dict[str, Any] | None = None,
) -> list[Issue]:
    if not isinstance(settlement, dict):
        return [Issue("SETTLEMENT_NOT_OBJECT", "P1", "状态结算报告不是object", repr(settlement))]
    issues: list[Issue] = []
    required = {
        "chapter_number", "draft_sha256", "verdict", "reader_visible_summary",
        "hook", "change_evidence", "missing_changes",
    }
    if set(settlement) != required:
        issues.append(Issue("SETTLEMENT_BAD_FIELDS", "P1", "状态结算字段不完整或包含额外字段", repr(set(settlement))))
    if settlement.get("chapter_number") != plan.get("chapter_number"):
        issues.append(Issue("SETTLEMENT_CHAPTER_MISMATCH", "P1", "状态结算章号不匹配", repr(settlement.get("chapter_number"))))
    expected_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    if settlement.get("draft_sha256") != expected_hash:
        issues.append(Issue("SETTLEMENT_DRAFT_MISMATCH", "P1", "状态结算未绑定最终正文", repr(settlement.get("draft_sha256"))))
    verdict = settlement.get("verdict")
    if verdict not in {"PASS", "INSUFFICIENT_EVIDENCE"}:
        issues.append(Issue("SETTLEMENT_BAD_VERDICT", "P1", "状态结算结论无效", repr(verdict)))

    summary = settlement.get("reader_visible_summary")
    if not isinstance(summary, dict) or set(summary) != {"core", "evidence"}:
        issues.append(Issue("SETTLEMENT_BAD_SUMMARY", "P1", "读者可见摘要结构无效", repr(summary)))
    else:
        core = summary.get("core")
        evidence = summary.get("evidence")
        if not isinstance(core, str) or not 8 <= len(core.strip()) <= 160:
            issues.append(Issue("SETTLEMENT_BAD_SUMMARY", "P1", "读者可见摘要须为8到160字", repr(core)))
        if not isinstance(evidence, list) or len(evidence) < 2 or not all(
            isinstance(quote, str) and quote and quote in draft for quote in evidence
        ):
            issues.append(Issue("SETTLEMENT_FALSE_SUMMARY_EVIDENCE", "P1", "摘要至少需要两条正文逐字证据", repr(evidence)))

    hook = settlement.get("hook")
    if hook is not None:
        if not isinstance(hook, dict) or set(hook) != {"type", "content", "quote"}:
            issues.append(Issue("SETTLEMENT_BAD_HOOK", "P1", "章末钩子结构无效", repr(hook)))
        else:
            if hook.get("type") != plan.get("hook", {}).get("type"):
                issues.append(Issue("SETTLEMENT_HOOK_TYPE_MISMATCH", "P1", "章末钩子类型偏离规划", repr(hook.get("type"))))
            if not isinstance(hook.get("content"), str) or not hook["content"].strip():
                issues.append(Issue("SETTLEMENT_BAD_HOOK", "P1", "章末钩子内容为空", repr(hook)))
            if not isinstance(hook.get("quote"), str) or hook["quote"] not in draft:
                issues.append(Issue("SETTLEMENT_FALSE_HOOK", "P1", "章末钩子引文不在正文", repr(hook.get("quote"))))

    expected_items = expected_state_changes(
        state, plan, foreshadow_registry, arc_registry
    )
    expected = {item["path"]: item["value"] for item in expected_items}
    required_paths = {
        item["path"]
        for item in expected_items
        if item.get("required_for_promotion") is True
    }
    evidence_items = settlement.get("change_evidence")
    provided: dict[str, str] = {}
    if not isinstance(evidence_items, list):
        issues.append(Issue("SETTLEMENT_BAD_EVIDENCE", "P1", "状态变化证据必须是数组", repr(evidence_items)))
    else:
        for item in evidence_items:
            if not isinstance(item, dict) or set(item) != {"path", "value", "quote", "finding"}:
                issues.append(Issue("SETTLEMENT_BAD_EVIDENCE", "P1", "状态变化证据结构无效", repr(item)))
                continue
            path, value, quote = item.get("path"), item.get("value"), item.get("quote")
            if path in provided:
                issues.append(Issue("SETTLEMENT_DUPLICATE_PATH", "P1", "同一状态变化被重复结算", repr(path)))
            if path not in expected or expected.get(path) != value:
                issues.append(Issue("SETTLEMENT_UNPLANNED_CHANGE", "P1", "结算包含未规划变化或值不匹配", repr({"path": path, "value": value})))
            if not isinstance(quote, str) or not quote or quote not in draft:
                issues.append(Issue("SETTLEMENT_FALSE_EVIDENCE", "P1", "状态变化引文不在正文", repr(quote)))
            if isinstance(path, str) and isinstance(value, str):
                provided[path] = value

    missing = settlement.get("missing_changes")
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        issues.append(Issue("SETTLEMENT_BAD_MISSING", "P1", "missing_changes必须是路径数组", repr(missing)))
        missing_set: set[str] = set()
    else:
        missing_set = set(missing)
        if len(missing_set) != len(missing):
            issues.append(Issue("SETTLEMENT_BAD_MISSING", "P1", "missing_changes不得重复", repr(missing)))
    actual_missing = set(expected) - set(provided)
    if missing_set != actual_missing:
        issues.append(Issue("SETTLEMENT_COVERAGE_MISMATCH", "P1", "缺失变化清单与证据覆盖不一致", repr({"expected": sorted(actual_missing), "reported": sorted(missing_set)})))
    required_missing = required_paths - set(provided)
    promotion_gaps = bool(required_missing or hook is None or not provided)
    if verdict == "PASS" and promotion_gaps:
        issues.append(
            Issue(
                "SETTLEMENT_PASS_WITH_GAPS",
                "P1",
                "PASS必须覆盖关键状态义务、至少一项实际变化和章末钩子",
                repr(sorted(required_missing)),
            )
        )
    if verdict == "INSUFFICIENT_EVIDENCE" and not promotion_gaps:
        issues.append(Issue("SETTLEMENT_FAIL_WITHOUT_GAPS", "P1", "关键证据充足时不得判定证据不足", repr(settlement)))
    for finding in _state_factual_escalations(
        settlement, draft, audit_profile
    ):
        messages = {
            "SETTLEMENT_FACT_LEVEL_OVERSTATEMENT": (
                "状态结算不得把声称、请求或待核验动作升级成已持有、"
                "已交付、已验证或已转移"
            ),
            "SETTLEMENT_BOUNDARY_STATE_CONTRADICTION": (
                "状态结算不得把仍保留的边界开口写成已关闭或封死"
            ),
        }
        issues.append(
            Issue(
                finding["code"],
                "P1",
                messages[finding["code"]],
                json.dumps(finding, ensure_ascii=False, sort_keys=True),
            )
        )
    return issues
