from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .quality import Issue


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
            add(f"state_changes.protagonist_known_info_add[{index}]", value, "主角实际获得的新信息")
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
            add(f"state_changes.character_updates[{index}]", value, "配角章末状态变化")
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
        role = (project_root / "agents/state_settler.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取状态结算指令: {exc}") from exc
    return role.rstrip() + "\n\n# 唯一输入工件\n" + json.dumps(packet, ensure_ascii=False, indent=2)


def validate_state_settlement(
    settlement: dict[str, Any],
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
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
    return issues
