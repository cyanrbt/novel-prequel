from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit_profiles import load_audit_profile
from .artifacts import ChapterWorkspace, canonical_text
from .audits import due_audits
from .context_builder import (
    build_chapter_context_pack,
    build_planner_context,
    build_reviewer_packet,
    build_writer_packet,
)
from .errors import (
    ArtifactValidationError,
    AtomicWriteError,
    CallBudgetExceeded,
    LegacyRunNotResumable,
    ProviderError,
    QualityGateError,
)
from .evaluation import DIMENSIONS, canonicalize_artifact_quotes, eligible
from .evolution import EvolutionResult, QualityEvolutionEngine
from .memory import MemoryStore, memory_record
from .model_calls import ModelCallExecutor
from .progress import ProgressSink
from .project import load_project_spec, load_role_text, project_path
from .model_router import StageModelRouter
from .provider import ModelProvider
from .quality import Issue, scan_draft, validate_plan, validate_review
from .reader_review import (
    build_blind_reader_gap_feedback_prompt,
    build_blind_reader_packet,
    build_blind_reader_prompt,
    build_reader_validation_diagnostic,
    canonicalize_pacing_diagnostics,
    validate_blind_reader_review,
)
from .scene_audit import canonicalize_scene_audit_anchor_quotes
from .run_manifest import RunManifest, fingerprint
from .state_store import load_state, validate_state
from .taste_contract import load_taste_contract, taste_contract_sha256
from .state_settlement import (
    build_state_settlement_missing_feedback_prompt,
    build_state_settlement_packet,
    build_state_settlement_prompt,
    build_state_settlement_validation_diagnostic,
    canonicalize_missing_change_paths,
    expected_state_changes,
    validate_state_settlement,
    validate_state_settlement_feedback_contract,
)


@dataclass(frozen=True)
class PipelineResult:
    chapter_number: int
    workspace: Path
    promoted: bool
    static_review: dict[str, Any] | None
    semantic_review: dict[str, Any] | None
    status: str = "COMPLETED"


def load_config(project_root: Path) -> dict[str, Any]:
    return load_project_spec(project_root).load_config()


def load_voice_profile_status(
    project_root: Path, core_config: dict[str, Any] | None = None
) -> str | None:
    """Return the prompt-native voice calibration state when configured."""
    config = core_config if core_config is not None else load_config(project_root)
    relative = config.get("key_files", {}).get("reference_voice_profile")
    if relative is None:
        return None
    if not isinstance(relative, str) or not relative.strip():
        raise ArtifactValidationError("正向文风画像路径无效")
    path = project_root / relative
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取正向文风画像: {exc}") from exc
    match = re.search(
        r"(?m)^calibration_status: (CALIBRATING|READY)$", text
    )
    if match is None:
        raise ArtifactValidationError("正向文风画像缺少有效 calibration_status")
    return match.group(1)


def parse_json_artifact(raw: str, name: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"{name}不是合法JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{name}根节点必须是object")
    return value


def _format_issues(issues: list[Issue]) -> str:
    return "；".join(f"{item.code}: {item.message}" for item in issues)


def _foreshadow_id(value: str) -> str:
    """Keep state keys stable even if a planner appends a human-readable note."""
    match = re.match(r"^(F-[A-Z]-?\d+)", value.strip())
    return match.group(1) if match else value.strip()


def _material_change_keys(changes: dict[str, Any], state: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key, value in changes.items():
        if isinstance(value, list) and value:
            keys.append(key)
        elif key == "protagonist_location" and value is not None:
            keys.append(key)
        elif key == "timeline_year" and value != state["timeline"].get("current_year"):
            keys.append(key)
        elif key == "timeline_elapsed_days" and value != state["timeline"].get("elapsed_days"):
            keys.append(key)
    return keys


def require_no_p1(issues: list[Issue], label: str) -> None:
    failures = [item for item in issues if item.severity == "P1"]
    if failures:
        raise QualityGateError(f"{label}未通过: {_format_issues(failures)}")


def formal_chapter_paths(project_root: Path) -> list[Path]:
    return sorted(
        project_path(project_root, "chapters_dir").glob("vol_*/chapter_*.txt"),
        key=lambda path: int(re.search(r"chapter_(\d+)", path.name).group(1)),
    )


def formal_chapter_numbers(project_root: Path) -> list[int]:
    return [int(re.search(r"chapter_(\d+)", path.name).group(1)) for path in formal_chapter_paths(project_root)]


def formal_review_binding_status(
    project_root: Path,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify that every formal text is still the exact reviewed artifact."""
    last_chapter = state.get("chapter", {}).get("last_chapter", 0)
    if last_chapter == 0:
        return {"status": "VALID", "chapter": 0, "reason": "尚无正式章节"}
    by_number = {
        int(re.search(r"chapter_(\d+)", path.name).group(1)): path
        for path in formal_chapter_paths(project_root)
    }
    path = by_number.get(last_chapter)
    if path is None:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "reason": "最新正式章节文件缺失",
        }
    try:
        hashes = {
            number: hashlib.sha256(chapter_path.read_bytes()).hexdigest()
            for number, chapter_path in by_number.items()
        }
        actual_hash = hashes[last_chapter]
    except OSError as exc:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "reason": f"无法读取最新正式章节: {exc}",
        }
    active_config = config or load_config(project_root)
    blind_enabled = active_config.get("quality_gates", {}).get(
        "blind_reader_gate", {}
    ).get("enabled", False)
    try:
        current_contract_hash = taste_contract_sha256(load_taste_contract(project_root))
    except ArtifactValidationError as exc:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": str(exc),
        }
    bindings = state.get("formal_review_bindings")
    if not isinstance(bindings, dict):
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": "缺少逐章正式稿审核绑定",
        }
    for number in range(1, last_chapter + 1):
        binding = bindings.get(str(number))
        if not isinstance(binding, dict):
            return {
                "status": "STALE",
                "chapter": number,
                "reason": f"正式第{number}章缺少审核绑定",
            }
        if binding.get("draft_sha256") != hashes.get(number):
            return {
                "status": "STALE",
                "chapter": number,
                "expected_sha256": binding.get("draft_sha256"),
                "actual_sha256": hashes.get(number),
                "reason": f"正式第{number}章在审核后发生改动",
            }
        if blind_enabled and binding.get("reader_verdict") != "PASS":
            return {
                "status": "STALE",
                "chapter": number,
                "reason": f"正式第{number}章没有绑定PASS盲读报告",
            }
        if binding.get("taste_contract_sha256") != current_contract_hash:
            return {
                "status": "STALE",
                "chapter": number,
                "reason": f"正式第{number}章未按当前用户偏好合同审核",
            }

    review = state.get("last_review")
    if not isinstance(review, dict):
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": "缺少最新审查绑定",
        }
    if review.get("chapter") != last_chapter:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": "最新审查绑定指向了其他章节",
        }
    if review.get("verdict") != "PASS":
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": f"最新审查结论为{review.get('verdict')!r}，不是PASS",
        }
    bound_hash = review.get("draft_sha256")
    if bound_hash != actual_hash:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "expected_sha256": bound_hash,
            "actual_sha256": actual_hash,
            "reason": "最新正式章节在记录的审核后发生改动",
        }
    if blind_enabled and review.get("reader_verdict") != "PASS":
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": "最新正式章节没有绑定PASS盲读报告",
        }
    if review.get("taste_contract_sha256") != current_contract_hash:
        return {
            "status": "STALE",
            "chapter": last_chapter,
            "actual_sha256": actual_hash,
            "reason": "最新正式章节未按当前用户偏好合同审核",
        }
    return {
        "status": "VALID",
        "chapter": last_chapter,
        "draft_sha256": actual_hash,
        "reader_verdict": review.get("reader_verdict", "DISABLED"),
    }


def run_preflight(
    project_root: Path,
    state: dict[str, Any] | None = None,
    *,
    require_voice_ready: bool = True,
) -> list[str]:
    checks: list[str] = []
    state = state or load_state(project_path(project_root, "state"))
    errors = validate_state(state)
    if errors:
        raise QualityGateError("状态预检失败: " + "；".join(errors))
    checks.append("state schema validated")

    config = load_config(project_root)
    checks.append("agent-agnostic story config loaded")
    load_taste_contract(project_root)
    checks.append("cumulative user taste contract validated")
    if "quality_evolution" in config:
        for path_key, field in (
            ("memory_index", "entries"),
            ("quality_lessons", "lessons"),
            ("creative_debts", "debts"),
        ):
            path = project_path(project_root, path_key)
            try:
                store = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualityGateError(f"长期记忆文件无效 {path.name}: {exc}") from exc
            if not isinstance(store, dict) or not isinstance(store.get(field), list):
                raise QualityGateError(f"长期记忆文件缺少数组 {field}: {path.name}")
        checks.append("long-book memory stores validated")

    registry_path = project_path(project_root, "canon_registry")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if set(registry.get("confidence_levels", {})) != {"A", "B", "C"}:
        raise QualityGateError("canon registry缺少A/B/C三级")
    checks.append("canon registry and era bans loaded")

    architecture_path = project_path(project_root, "series_architecture")
    arc_registry_path = project_path(project_root, "arc_registry")
    foreshadow_registry_path = project_path(project_root, "foreshadow_registry")
    if not architecture_path.exists():
        raise QualityGateError(f"总架构文件不存在: {architecture_path}")
    try:
        arc_registry = json.loads(arc_registry_path.read_text(encoding="utf-8"))
        foreshadow_registry = json.loads(foreshadow_registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateError(f"里程碑或伏笔登记无效: {exc}") from exc
    if not isinstance(arc_registry, dict) or arc_registry.get("schema") != "novel-arc-registry":
        raise QualityGateError("里程碑登记格式无效")
    milestones = arc_registry.get("milestones")
    if not isinstance(milestones, dict) or not milestones:
        raise QualityGateError("里程碑登记缺少 milestones")
    if not isinstance(foreshadow_registry, dict) or foreshadow_registry.get("schema") != "novel-foreshadow-registry":
        raise QualityGateError("伏笔登记格式无效")
    foreshadows = foreshadow_registry.get("entries")
    if not isinstance(foreshadows, dict):
        raise QualityGateError("伏笔登记缺少 entries")
    unknown_completed = set(state.get("completed_milestones", [])) - set(milestones)
    if unknown_completed:
        raise QualityGateError(f"状态包含未登记里程碑: {sorted(unknown_completed)}")
    unknown_active = set(state.get("active_foreshadows", {})) - set(foreshadows)
    if unknown_active:
        raise QualityGateError(f"状态包含未登记伏笔: {sorted(unknown_active)}")
    checks.append("milestone and foreshadow registries validated")

    event_path = project_path(project_root, "plots_dir") / f"{state['chapter']['current_event']}.md"
    if not event_path.exists():
        raise QualityGateError(f"当前事件大纲不存在: {event_path}")
    checks.append("event outline exists")

    numbers = formal_chapter_numbers(project_root)
    expected = list(range(1, state["chapter"]["last_chapter"] + 1))
    if numbers != expected:
        raise QualityGateError(f"正式章节与状态不一致: 文件{numbers}，状态应为{expected}")
    label = f"1-{numbers[-1]}" if numbers else "empty baseline"
    checks.append(f"formal chapters contiguous: {label}")
    binding = formal_review_binding_status(project_root, state, config)
    if binding["status"] != "VALID":
        raise QualityGateError(
            "正式正文审核绑定已过期: " + str(binding.get("reason", "unknown"))
        )
    checks.append("latest formal chapter review hash and taste contract are bound")
    voice_status = load_voice_profile_status(project_root, config)
    if voice_status is not None:
        if require_voice_ready and voice_status != "READY":
            raise QualityGateError(
                "正向文风画像仍在校准；先执行 workflows/style-calibration.md "
                "并完成用户盲选"
            )
        if voice_status == "READY":
            checks.append("positive voice profile calibrated by user blind selection")
        else:
            checks.append(f"positive voice profile status validated: {voice_status}")
    checks.append(f"next chapter: {state['chapter']['next_chapter']}")
    return checks


def recent_chapters(project_root: Path, state: dict[str, Any], limit: int = 5) -> list[str]:
    paths = formal_chapter_paths(project_root)
    return [path.read_text(encoding="utf-8") for path in paths[-limit:]]


def _agent_prompt(project_root: Path, agent: str, packet: dict[str, Any], instruction: str) -> str:
    try:
        role = load_role_text(project_root, agent)
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取{agent}指令: {exc}") from exc
    return (
        role.rstrip()
        + "\n\n# 本次任务\n"
        + instruction
        + "\n\n# 唯一输入工件\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )


def _validated_manual_plan_context(
    project_root: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    planner_context = build_planner_context(project_root, state)
    allowed_canon_ids = {fact["id"] for fact in planner_context["canon_facts"]}
    allowed_foreshadow_ids = set(
        planner_context.get("foreshadow_registry", {}).get("entries", {})
    )
    allowed_milestone_ids = set(
        planner_context.get("arc_registry", {}).get("milestones", {})
    )
    require_no_p1(
        validate_plan(
            plan,
            state,
            allowed_canon_ids,
            allowed_foreshadow_ids,
            allowed_milestone_ids,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
        ),
        "手工导入规划",
    )
    return planner_context


def _audit_source_path(project_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)


def _create_manual_attempt(
    project_root: Path, chapter: int
) -> tuple[int, ChapterWorkspace]:
    chapter_work = project_path(project_root, "work_dir") / f"chapter_{chapter:03d}"
    chapter_work.mkdir(parents=True, exist_ok=True)
    while True:
        attempts = [
            int(path.name.split("_")[-1])
            for path in chapter_work.glob("attempt_*")
            if path.is_dir() and path.name.split("_")[-1].isdigit()
        ]
        attempt = max(attempts, default=0) + 1
        path = chapter_work / f"attempt_{attempt:02d}"
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return attempt, ChapterWorkspace(path, chapter)


def _manual_review_metadata(
    router: StageModelRouter,
    stage: str,
    prompt_version: str,
) -> dict[str, Any]:
    settings = router.settings_for(stage)
    route = {
        "profile": settings.profile,
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "prompt_version": prompt_version,
    }
    return {
        "model_profile": router.profile_for(stage),
        "prompt_version": prompt_version,
        "call_count": 1,
        "route_fingerprint": fingerprint(route),
    }


def _manual_review_budget_contract(
    config: dict[str, Any],
) -> tuple[bool, bool, int]:
    quality_gates = config.get("quality_gates", {})
    reader_enabled = bool(
        quality_gates.get("blind_reader_gate", {}).get("enabled", False)
    )
    state_enabled = bool(
        quality_gates.get("state_evidence_gate", {}).get("enabled", False)
    )
    if state_enabled and not reader_enabled:
        raise QualityGateError("state_evidence_gate要求同时启用blind_reader_gate")
    return (
        reader_enabled,
        state_enabled,
        1 + (2 * int(reader_enabled)) + (2 * int(state_enabled)),
    )


def _reader_feedback_identifiers(
    diagnostic: dict[str, Any],
) -> tuple[str, str, str]:
    """Return the auditable prompt, reason, and failure labels for one retry."""
    feedback_kind = diagnostic.get("feedback_kind")
    components = set(diagnostic.get("feedback_components", []))
    if "FACTUAL" in components:
        if "GAP" in components:
            return (
                "manual-blind-reader-gap-factual-feedback",
                "MANUAL_BLIND_READER_GAP_FACTUAL_FEEDBACK",
                "PRESSURE_GAP_AND_FACTUAL_REPORT",
            )
        return (
            "manual-blind-reader-factual-feedback",
            "MANUAL_BLIND_READER_FACTUAL_FEEDBACK",
            "FACTUAL_RECAP_REPORT",
        )
    if feedback_kind == "QUOTE_ONLY":
        return (
            "manual-blind-reader-quote-only-feedback",
            "MANUAL_BLIND_READER_QUOTE_ONLY_FEEDBACK",
            "QUOTE_ONLY_REPORT",
        )
    if feedback_kind == "GAP_AND_QUOTE":
        return (
            "manual-blind-reader-gap-and-quote-feedback",
            "MANUAL_BLIND_READER_GAP_AND_QUOTE_FEEDBACK",
            "PRESSURE_GAP_AND_QUOTE_REPORT",
        )
    return (
        "manual-blind-reader-gap-feedback",
        "MANUAL_BLIND_READER_GAP_FEEDBACK",
        "PRESSURE_GAP_REPORT",
    )


_REPORT_PATH_TOKEN_RE = re.compile(r"([^.[\]]+)|\[(\d+)\]")


def _report_path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    consumed = ""
    for match in _REPORT_PATH_TOKEN_RE.finditer(path):
        if match.start() != len(consumed):
            separator = path[len(consumed) : match.start()]
            if separator != ".":
                return []
        tokens.append(
            int(match.group(2)) if match.group(2) is not None else match.group(1)
        )
        consumed = path[: match.end()]
    return tokens if consumed == path else []


def _mask_report_path(report: dict[str, Any], path: str, sentinel: str) -> bool:
    tokens = _report_path_tokens(path)
    if not tokens:
        return False
    current: Any = report
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(current, list) or not 0 <= token < len(current):
                return False
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False
            current = current[token]
    final = tokens[-1]
    if isinstance(final, int):
        if not isinstance(current, list) or not 0 <= final < len(current):
            return False
        current[final] = sentinel
    else:
        if not isinstance(current, dict) or final not in current:
            return False
        current[final] = sentinel
    return True


def _gap_feedback_contract_issues(
    first_report: dict[str, Any],
    final_report: dict[str, Any],
    diagnostic: dict[str, Any],
    final_pacing_normalization: dict[str, Any] | None,
) -> list[Issue]:
    """Require a repaired PASS to add turns, never replace prior evidence."""
    if final_report.get("verdict") != "PASS":
        return []
    first_pacing = first_report.get("pacing_diagnostics")
    final_pacing = final_report.get("pacing_diagnostics")
    if not isinstance(first_pacing, dict) or not isinstance(final_pacing, dict):
        return []
    first_turns = first_pacing.get("pressure_turns")
    final_turns = final_pacing.get("pressure_turns")
    if not isinstance(first_turns, list) or not isinstance(final_turns, list):
        return []
    first_rows = [item for item in first_turns if isinstance(item, dict)]
    final_rows = [item for item in final_turns if isinstance(item, dict)]
    if len(first_rows) != len(first_turns) or len(final_rows) != len(final_turns):
        return []
    first_quotes = [item.get("quote") for item in first_rows]
    final_quotes = [item.get("quote") for item in final_rows]
    repairable_turn_indices = {
        item.get("item_index")
        for item in diagnostic.get("repairable_quote_issues", [])
        if isinstance(item, dict)
        and item.get("pacing_list_field") == "pressure_turns"
        and isinstance(item.get("item_index"), int)
        and not isinstance(item.get("item_index"), bool)
    }

    def preserved_row_matches(
        first_index: int, first_row: dict[str, Any], final_row: dict[str, Any]
    ) -> bool:
        if first_index not in repairable_turn_indices:
            return first_row == final_row
        first_masked = copy.deepcopy(first_row)
        final_masked = copy.deepcopy(final_row)
        first_masked["quote"] = "__REPAIRED_PRESSURE_QUOTE__"
        final_masked["quote"] = "__REPAIRED_PRESSURE_QUOTE__"
        return first_masked == final_masked

    cursor = 0
    added_rows: list[dict[str, Any]] = []
    for row in final_rows:
        if (
            cursor < len(first_rows)
            and preserved_row_matches(cursor, first_rows[cursor], row)
        ):
            cursor += 1
        else:
            added_rows.append(row)
    if cursor != len(first_quotes):
        return [
            Issue(
                "READER_GAP_FEEDBACK_REPLACED_TURNS",
                "P1",
                "压力空档反馈只能补充真实转折，不得删除、改序或改写首报锚点及效果",
                repr({"first": first_rows, "final": final_rows}),
            )
        ]

    final_anchors = (
        final_pacing_normalization.get("turn_anchors", [])
        if isinstance(final_pacing_normalization, dict)
        else []
    )
    first_quote_set = set(first_quotes)
    unresolved = []
    for gap in diagnostic.get("pacing_normalization", {}).get(
        "over_limit_gaps", []
    ):
        if (
            not isinstance(gap, dict)
            or not isinstance(gap.get("start_offset"), int)
            or not isinstance(gap.get("end_offset"), int)
        ):
            continue
        added_inside = any(
            isinstance(anchor, dict)
            and anchor.get("quote") not in first_quote_set
            and isinstance(anchor.get("offset"), int)
            and gap.get("start_offset") < anchor["offset"] < gap.get("end_offset")
            for anchor in final_anchors
        )
        if not added_inside:
            unresolved.append(gap)
    if unresolved:
        return [
            Issue(
                "READER_GAP_FEEDBACK_NO_NEW_TURN",
                "P1",
                "修复后的PASS没有在每个超限空档内补入新压力转折",
                repr(unresolved),
            )
        ]
    diagnosed_gaps = [
        gap
        for gap in diagnostic.get("pacing_normalization", {}).get(
            "over_limit_gaps", []
        )
        if isinstance(gap, dict)
        and isinstance(gap.get("start_offset"), int)
        and isinstance(gap.get("end_offset"), int)
    ]
    anchors_by_quote = {
        item.get("quote"): item.get("offset")
        for item in final_anchors
        if isinstance(item, dict)
    }
    outside = [
        row
        for row in added_rows
        if not isinstance(anchors_by_quote.get(row.get("quote")), int)
        or not any(
            gap["start_offset"]
            < anchors_by_quote[row.get("quote")]
            < gap["end_offset"]
            for gap in diagnosed_gaps
        )
    ]
    if outside:
        return [
            Issue(
                "READER_GAP_FEEDBACK_OUT_OF_SCOPE_TURN",
                "P1",
                "压力空档反馈只能在程序指出的超限区间补入真实转折",
                repr(outside),
            )
        ]
    return []


def _reader_quote_feedback_contract_issues(
    first_report: dict[str, Any],
    final_report: dict[str, Any],
    draft: str,
    diagnostic: dict[str, Any],
) -> list[Issue]:
    """Require every feedback-eligible false quote to be repaired, not dropped."""
    expected = diagnostic.get("repairable_quote_issues", [])
    if not expected:
        return []
    issues: list[Issue] = []
    for item in expected:
        if not isinstance(item, dict):
            issues.append(
                Issue(
                    "READER_QUOTE_FEEDBACK_DIAGNOSTIC_INVALID",
                    "P1",
                    "引文反馈诊断条目结构无效",
                    repr(item),
                )
            )
            continue

        quote: Any = None
        location_valid = False
        ledger_field = item.get("ledger_field")
        list_field = item.get("list_field")
        pacing_field = item.get("pacing_field")
        pacing_list_field = item.get("pacing_list_field")
        benchmark_field = item.get("benchmark_field")
        quote_field = item.get("quote_field")
        if isinstance(ledger_field, str):
            audit = final_report.get("mechanism_audit")
            rows = audit.get(ledger_field) if isinstance(audit, dict) else None
            anchor_id = item.get("anchor_id")
            matching = (
                [
                    row
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("anchor_id") == anchor_id
                ]
                if isinstance(rows, list) and isinstance(anchor_id, str)
                else []
            )
            if len(matching) == 1 and isinstance(quote_field, str):
                quote = matching[0].get(quote_field)
                location_valid = True
        elif isinstance(list_field, str):
            rows = final_report.get(list_field)
            item_index = item.get("item_index")
            expected_length = item.get("list_length")
            identity_field = item.get("identity_field")
            identity = item.get("item_identity")
            if (
                isinstance(rows, list)
                and isinstance(expected_length, int)
                and not isinstance(expected_length, bool)
                and len(rows) == expected_length
                and isinstance(item_index, int)
                and not isinstance(item_index, bool)
                and 0 <= item_index < len(rows)
                and isinstance(rows[item_index], dict)
                and isinstance(identity_field, str)
                and rows[item_index].get(identity_field) == identity
                and quote_field == "quote"
            ):
                quote = rows[item_index].get("quote")
                location_valid = True
        elif isinstance(pacing_field, str):
            pacing = final_report.get("pacing_diagnostics")
            row = pacing.get(pacing_field) if isinstance(pacing, dict) else None
            if isinstance(row, dict) and quote_field == "quote":
                quote = row.get("quote")
                location_valid = True
        elif isinstance(pacing_list_field, str):
            pacing = final_report.get("pacing_diagnostics")
            rows = (
                pacing.get(pacing_list_field)
                if isinstance(pacing, dict)
                else None
            )
            item_index = item.get("item_index")
            expected_length = item.get("list_length")
            if (
                isinstance(rows, list)
                and isinstance(expected_length, int)
                and not isinstance(expected_length, bool)
                and len(rows) >= expected_length
                and isinstance(item_index, int)
                and not isinstance(item_index, bool)
                and 0 <= item_index < len(rows)
                and isinstance(rows[item_index], dict)
                and quote_field == "quote"
            ):
                quote = rows[item_index].get("quote")
                location_valid = True
        elif isinstance(benchmark_field, str):
            benchmark = final_report.get("benchmark_comparison")
            row = (
                benchmark.get(benchmark_field)
                if isinstance(benchmark, dict)
                else None
            )
            if isinstance(row, dict) and quote_field == "quote":
                quote = row.get("quote")
                location_valid = True
        else:
            location_valid = False

        if (
            not location_valid
            or not isinstance(quote, str)
            or not quote
            or draft.count(quote) != 1
        ):
            issues.append(
                Issue(
                    "READER_QUOTE_FEEDBACK_NOT_REPAIRED",
                    "P1",
                    "反馈必须把精确定位的假引文改为唯一、连续的正文引文，不得删除或置空",
                    repr(
                        {
                            "field_path": item.get("field_path"),
                            "anchor_id": item.get("anchor_id"),
                            "item_index": item.get("item_index"),
                            "quote_field": quote_field,
                            "invalid_quote": item.get("invalid_quote"),
                            "final_quote": quote,
                        }
                    ),
                )
            )
            continue
        if item.get("code") == "SCENE_RETROACTIVE_POV_SOURCE":
            claim_end = item.get("claim_end")
            final_offset = draft.find(quote)
            if (
                isinstance(claim_end, bool)
                or not isinstance(claim_end, int)
                or final_offset + len(quote) > claim_end
            ):
                issues.append(
                    Issue(
                        "READER_QUOTE_FEEDBACK_RETROACTIVE_SOURCE",
                        "P1",
                        "认知来源反馈必须改用不晚于认知结论的唯一正文引文",
                        repr(
                            {
                                "field_path": item.get("field_path"),
                                "anchor_id": item.get("anchor_id"),
                                "claim_end": claim_end,
                                "final_quote": quote,
                                "final_offset": final_offset,
                                "final_end": final_offset + len(quote),
                            }
                        ),
                    )
                )

    if (
        diagnostic.get("feedback_kind") == "QUOTE_ONLY"
        and final_report.get("verdict") == "PASS"
    ):
        first_scope = copy.deepcopy(first_report)
        final_scope = copy.deepcopy(final_report)
        target_paths_valid = True
        for index, item in enumerate(expected):
            sentinel = f"__QUOTE_FEEDBACK_TARGET_{index}__"
            path = item.get("field_path")
            if not isinstance(path, str) or not (
                _mask_report_path(first_scope, path, sentinel)
                and _mask_report_path(final_scope, path, sentinel)
            ):
                target_paths_valid = False
            pacing_field = item.get("pacing_field")
            if isinstance(pacing_field, str):
                position_path = (
                    f"pacing_diagnostics.{pacing_field}.position_percent"
                )
                position_sentinel = f"__QUOTE_POSITION_{index}__"
                if not (
                    _mask_report_path(first_scope, position_path, position_sentinel)
                    and _mask_report_path(final_scope, position_path, position_sentinel)
                ):
                    target_paths_valid = False
            if item.get("pacing_list_field") == "pressure_turns":
                gap_path = "pacing_diagnostics.max_pressure_gap_chars"
                gap_sentinel = "__QUOTE_DERIVED_PRESSURE_GAP__"
                if not (
                    _mask_report_path(first_scope, gap_path, gap_sentinel)
                    and _mask_report_path(final_scope, gap_path, gap_sentinel)
                ):
                    target_paths_valid = False

        # The retry may alter only the exact false-quote leaves.  Recaps,
        # reconstructions, scores, pacing, evidence findings, ordering, and
        # every other judgement remain byte-for-byte equivalent as JSON.
        if not target_paths_valid or first_scope != final_scope:
            issues.append(
                Issue(
                    "READER_QUOTE_FEEDBACK_SCOPE_DRIFT",
                    "P1",
                    "纯引文反馈不得改动评分、节奏、证据判断或其他非目标字段",
                    repr(
                        {
                            "target_paths_valid": target_paths_valid,
                            "first_equals_final": first_scope == final_scope,
                        }
                    ),
                )
            )
    return issues


def _reader_feedback_scope_issues(
    first_report: dict[str, Any],
    final_report: dict[str, Any],
    diagnostic: dict[str, Any],
) -> list[Issue]:
    """Freeze every PASS field outside the deterministic feedback targets."""
    if final_report.get("verdict") != "PASS":
        return []
    feedback_kind = diagnostic.get("feedback_kind")
    components = set(diagnostic.get("feedback_components", []))
    if (
        not components
        or not components.issubset({"GAP", "QUOTE", "FACTUAL"})
        or feedback_kind
        != (
            "_AND_".join(diagnostic["feedback_components"]) + "_ONLY"
            if len(diagnostic["feedback_components"]) == 1
            else "_AND_".join(diagnostic["feedback_components"])
        )
    ):
        return [
            Issue(
                "READER_FEEDBACK_SCOPE_INVALID",
                "P1",
                "盲读反馈缺少可审计的修复类型",
                repr(feedback_kind),
            )
        ]
    first_scope = copy.deepcopy(first_report)
    final_scope = copy.deepcopy(final_report)
    targets: list[str] = []
    if "QUOTE" in components:
        repairs_pressure_turn = False
        for item in diagnostic.get("repairable_quote_issues", []):
            if not isinstance(item, dict):
                continue
            targets.append(item.get("field_path"))
            repairs_pressure_turn = repairs_pressure_turn or (
                item.get("pacing_list_field") == "pressure_turns"
            )
            pacing_field = item.get("pacing_field")
            if isinstance(pacing_field, str):
                targets.append(
                    f"pacing_diagnostics.{pacing_field}.position_percent"
                )
        if repairs_pressure_turn:
            targets.append("pacing_diagnostics.max_pressure_gap_chars")
    if "FACTUAL" in components:
        factual_paths = [
            item.get("field_path")
            for item in diagnostic.get("factual_escalations", [])
            if isinstance(item, dict)
        ]
        targets.extend(factual_paths)
        for path in set(factual_paths):
            tokens = _report_path_tokens(path) if isinstance(path, str) else []
            current: Any = final_report
            for token in tokens:
                if isinstance(token, int):
                    current = (
                        current[token]
                        if isinstance(current, list) and 0 <= token < len(current)
                        else None
                    )
                else:
                    current = current.get(token) if isinstance(current, dict) else None
            if not isinstance(current, str) or not current.strip():
                return [
                    Issue(
                        "READER_FACTUAL_FEEDBACK_TARGET_MISSING",
                        "P1",
                        "事实层级反馈不得删除、置空或改变目标字段结构",
                        repr(path),
                    )
                ]

    targets_valid = True
    for index, path in enumerate(dict.fromkeys(targets)):
        sentinel = f"__READER_FEEDBACK_TARGET_{index}__"
        if not isinstance(path, str):
            targets_valid = False
            continue
        targets_valid = (
            _mask_report_path(first_scope, path, sentinel)
            and _mask_report_path(final_scope, path, sentinel)
            and targets_valid
        )

    if "GAP" in components:
        for path, sentinel in (
            (
                "pacing_diagnostics.pressure_turns",
                "__READER_GAP_PRESSURE_TURNS__",
            ),
            (
                "pacing_diagnostics.max_pressure_gap_chars",
                "__READER_GAP_MAX__",
            ),
        ):
            targets_valid = (
                _mask_report_path(first_scope, path, sentinel)
                and _mask_report_path(final_scope, path, sentinel)
                and targets_valid
            )

    if not targets_valid or first_scope != final_scope:
        return [
            Issue(
                "READER_FEEDBACK_SCOPE_DRIFT",
                "P1",
                "一次性反馈不得改写评分、解释、既有压力效果或任何非目标字段",
                repr(
                    {
                        "feedback_kind": feedback_kind,
                        "targets": targets,
                        "targets_valid": targets_valid,
                        "first_equals_final": first_scope == final_scope,
                    }
                ),
            )
        ]
    return []


def _reader_feedback_contract_issues(
    first_report: dict[str, Any],
    final_report: dict[str, Any],
    draft: str,
    diagnostic: dict[str, Any],
    final_pacing_normalization: dict[str, Any] | None,
) -> list[Issue]:
    issues = []
    if "GAP" in set(diagnostic.get("feedback_components", [])):
        issues.extend(
            _gap_feedback_contract_issues(
                first_report,
                final_report,
                diagnostic,
                final_pacing_normalization,
            )
        )
    issues.extend(
        _reader_quote_feedback_contract_issues(
            first_report, final_report, draft, diagnostic
        )
    )
    issues.extend(
        _reader_feedback_scope_issues(first_report, final_report, diagnostic)
    )
    return issues


def _canonical_reader_model_output(
    raw: str,
    draft: str,
    artifact_name: str,
    audit_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Rebuild the persisted reader report from the exact model output."""
    report = parse_json_artifact(raw, artifact_name)
    canonicalize_artifact_quotes(report, draft)
    canonicalize_scene_audit_anchor_quotes(
        report.get("mechanism_audit"), draft, audit_profile
    )
    pacing_normalization = canonicalize_pacing_diagnostics(report, draft)
    return report, pacing_normalization


def _canonical_state_settlement_model_output(
    raw: str,
    draft: str,
    expected: list[dict[str, Any]],
    artifact_name: str,
) -> dict[str, Any]:
    """Rebuild one settlement artifact from the exact provider output."""
    settlement = parse_json_artifact(raw, artifact_name)
    canonicalize_artifact_quotes(settlement, draft)
    canonicalize_missing_change_paths(settlement, expected)
    return settlement


def _require_manual_import_integrity(
    workspace: ChapterWorkspace,
    state: dict[str, Any],
) -> tuple[RunManifest, dict[str, Any]]:
    manifest = RunManifest.load(workspace)
    if manifest.data.get("mode") != "manual_import":
        raise ArtifactValidationError("指定尝试不是手工导入运行")
    if manifest.data.get("state_hash") != fingerprint(state):
        raise QualityGateError("手工导入运行绑定的正式状态已经变化")
    provenance = manifest.data.get("manual_import")
    required = {
        "source_path",
        "source_sha256",
        "imported_draft_sha256",
        "plan_source_path",
        "plan_source_sha256",
        "imported_plan_sha256",
        "plan_validation",
        "manual_review_contract",
    }
    if not isinstance(provenance, dict) or not required.issubset(provenance):
        raise ArtifactValidationError("手工导入运行缺少完整来源记录")
    if provenance.get("plan_validation") != "PASS":
        raise ArtifactValidationError("手工导入规划没有通过记录的重新验证")
    if provenance.get("manual_review_contract") != "gap-feedback":
        raise ArtifactValidationError(
            "手工导入运行不是当前反馈审查合同；请重新导入新尝试"
        )
    import_stage = manifest.require_stage_outputs("manual_import")
    expected_import_input_hash = fingerprint(
        {
            "state_hash": fingerprint(state),
            "source_path": provenance["source_path"],
            "source_sha256": provenance["source_sha256"],
            "imported_draft_sha256": provenance["imported_draft_sha256"],
            "plan_source_path": provenance["plan_source_path"],
            "plan_source_sha256": provenance["plan_source_sha256"],
            "imported_plan_sha256": provenance["imported_plan_sha256"],
            "manual_review_contract": provenance["manual_review_contract"],
        }
    )
    if import_stage.get("input_hash") != expected_import_input_hash:
        raise ArtifactValidationError("手工导入来源记录与运行清单绑定不一致")
    if workspace.digest("draft.txt") != provenance.get("imported_draft_sha256"):
        raise ArtifactValidationError("手工导入正文与来源记录哈希不一致")
    if workspace.digest("plan.json") != provenance.get("imported_plan_sha256"):
        raise ArtifactValidationError("手工导入规划与来源记录哈希不一致")
    return manifest, provenance


def _reject_completed_manual_stage_drift(
    manifest: RunManifest,
    stage: str,
    label: str,
) -> None:
    """Never spend another call when a completed manual stage stops matching."""
    record = manifest.data.get("stages", {}).get(stage, {})
    if not isinstance(record, dict) or record.get("status") != "COMPLETED":
        return
    manifest.set_status(
        "WAITING_USER",
        valid_candidates=0,
        waiting_reason=f"{label}已完成但审计绑定发生变化；需重新导入新尝试",
    )
    raise ArtifactValidationError(
        f"{label}已完成但输入、路由或输出哈希不再匹配；请重新导入新尝试"
    )


def import_manual_candidate(
    project_root: Path,
    source: Path,
    *,
    plan_attempt: int,
) -> PipelineResult:
    """Create a fresh, auditable attempt from a hand-edited draft."""
    project_root = project_root.resolve()
    if plan_attempt < 1:
        raise ArtifactValidationError("规划来源尝试序号必须大于0")
    state = load_state(project_path(project_root, "state"))
    run_preflight(project_root, state)
    config = load_config(project_root)
    _, _, call_limit = _manual_review_budget_contract(config)
    chapter = state["chapter"]["next_chapter"]
    source_path = source.expanduser().resolve()
    plan_source = (
        project_path(project_root, "work_dir")
        / f"chapter_{chapter:03d}"
        / f"attempt_{plan_attempt:02d}"
        / "plan.json"
    )
    try:
        source_bytes = source_path.read_bytes()
        draft = canonical_text(source_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取UTF-8手工稿: {exc}") from exc
    if not draft.strip():
        raise ArtifactValidationError("手工稿为空")
    try:
        plan_bytes = plan_source.read_bytes()
        plan = json.loads(plan_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取规划来源: {exc}") from exc
    if not isinstance(plan, dict):
        raise ArtifactValidationError("规划来源根节点必须是object")
    _validated_manual_plan_context(project_root, state, plan)

    attempt, workspace = _create_manual_attempt(project_root, chapter)
    manifest = RunManifest.create(
        workspace,
        chapter,
        fingerprint(state),
        call_limit=call_limit,
        mode="manual_import",
    )
    workspace.write_json("plan.json", plan)
    workspace.write_text("draft.txt", draft)
    provenance = {
        "source_path": _audit_source_path(project_root, source_path),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "imported_draft_sha256": workspace.digest("draft.txt"),
        "source_was_canonicalized": source_bytes != workspace.read_text("draft.txt").encode("utf-8"),
        "plan_source_path": _audit_source_path(project_root, plan_source),
        "plan_source_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "imported_plan_sha256": workspace.digest("plan.json"),
        "plan_validation": "PASS",
        "manual_review_contract": "gap-feedback",
    }
    manifest.mutate(lambda data: data.__setitem__("manual_import", provenance))
    import_input_hash = fingerprint(
        {
            "state_hash": fingerprint(state),
            "source_path": provenance["source_path"],
            "source_sha256": provenance["source_sha256"],
            "imported_draft_sha256": provenance["imported_draft_sha256"],
            "plan_source_path": provenance["plan_source_path"],
            "plan_source_sha256": provenance["plan_source_sha256"],
            "imported_plan_sha256": provenance["imported_plan_sha256"],
            "manual_review_contract": provenance["manual_review_contract"],
        }
    )
    manifest.begin("manual_import")
    manifest.complete(
        "manual_import",
        import_input_hash,
        ["plan.json", "draft.txt"],
        {
            "model_profile": "manual",
            "prompt_version": "manual-import",
            "call_count": 0,
        },
    )
    manifest.set_status(
        "WAITING_USER",
        valid_candidates=0,
        waiting_reason="手工稿已导入；等待精确哈希语义审查",
    )
    return PipelineResult(
        chapter,
        workspace.path,
        False,
        None,
        None,
        status="WAITING_USER",
    )


def review_manual_candidate(
    project_root: Path,
    *,
    attempt: int,
    router: StageModelRouter | None = None,
    progress: ProgressSink | None = None,
) -> PipelineResult:
    """Semantically review one imported draft and bind the result to its hash."""
    project_root = project_root.resolve()
    if attempt < 1:
        raise ArtifactValidationError("手工导入尝试序号必须大于0")
    state = load_state(project_path(project_root, "state"))
    run_preflight(project_root, state)
    chapter = state["chapter"]["next_chapter"]
    workspace = ChapterWorkspace(
        project_path(project_root, "work_dir")
        / f"chapter_{chapter:03d}"
        / f"attempt_{attempt:02d}",
        chapter,
    )
    manifest, _ = _require_manual_import_integrity(workspace, state)
    plan = workspace.read_json("plan.json")
    draft = workspace.read_text("draft.txt")
    planner_context = _validated_manual_plan_context(project_root, state, plan)
    config = load_config(project_root)
    audit_profile = load_audit_profile(project_root)
    reader_enabled, state_enabled, expected_call_limit = (
        _manual_review_budget_contract(config)
    )
    if manifest.data.get("budget", {}).get("limit") != expected_call_limit:
        manifest.set_status(
            "WAITING_USER",
            valid_candidates=0,
            waiting_reason="手工稿导入后的审查门禁配置发生变化；需重新导入新尝试",
        )
        raise ArtifactValidationError(
            "手工稿调用预算与当前审查门禁不一致；请重新导入新尝试"
        )
    recent_limit = config.get("quality_gates", {}).get(
        "recent_chapters_for_repetition", 5
    )
    recent = recent_chapters(project_root, state, recent_limit)
    static_review = scan_draft(
        draft,
        recent,
        planner_context["era_bans"],
        plan,
        length_policy=config.get("chapter_length"),
        taste_contract=planner_context.get("user_taste_contract"),
    )
    static_input_hash = fingerprint(
        {
            "draft": draft,
            "recent": recent,
            "era_bans": planner_context["era_bans"],
            "plan": plan,
            "length_policy": config.get("chapter_length"),
            "taste_contract": planner_context.get("user_taste_contract"),
        }
    )
    manifest.reopen("manual_static_review")
    workspace.write_json("static_review.json", static_review)
    manifest.complete(
        "manual_static_review",
        static_input_hash,
        ["static_review.json"],
    )
    if not static_review["passed"]:
        manifest.set_status(
            "WAITING_USER",
            valid_candidates=0,
            waiting_reason="手工稿未通过确定性检查",
        )
        failures = [
            Issue(**item)
            for item in static_review["issues"]
            if item["severity"] == "P1"
        ]
        raise QualityGateError("手工稿硬检查未通过: " + _format_issues(failures))

    packet = build_reviewer_packet(
        state, plan, draft, static_review, planner_context
    )
    input_hash = fingerprint(packet)
    if router is None:
        raise ProviderError(
            "手工稿语义任务必须由当前 Agent 按 WORKFLOW.md 执行；"
            "仓库不再从配置构造 Agent 后端"
        )
    active_router = router
    caller = ModelCallExecutor(active_router, manifest, progress)
    metadata = _manual_review_metadata(
        active_router,
        "manual_semantic_reviewer",
        "manual-semantic-review",
    )
    stage = "manual_semantic_review"
    manifest.begin(stage)
    if manifest.can_reuse(stage, input_hash, metadata["route_fingerprint"]):
        semantic_review = workspace.read_json("semantic_review.json")
        require_no_p1(
            validate_review(
                semantic_review,
                static_review,
                expected_chapter=chapter,
                draft=draft,
                require_draft_hash=True,
            ),
            "手工稿语义审查",
        )
    else:
        _reject_completed_manual_stage_drift(
            manifest, stage, "手工稿语义审查"
        )
        if manifest.stage_failed(stage):
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=0,
                waiting_reason="手工稿语义审查此前失败；需重新导入新尝试",
            )
            raise QualityGateError("手工稿语义审查此前失败；请修订后重新导入新尝试")
        raw: str | None = None
        try:
            raw = caller.call(
                "manual_semantic_reviewer",
                _agent_prompt(
                    project_root,
                    "reviewer",
                    packet,
                    "独立重新审查这份手工精修稿；draft_sha256必须逐字复制输入。只输出满足review schema的JSON。",
                ),
                project_root / "schemas/review.schema.json",
                "MANUAL_SEMANTIC_REVIEW",
            )
            semantic_review = parse_json_artifact(raw, "manual-semantic-review")
            canonicalize_artifact_quotes(semantic_review, draft)
            require_no_p1(
                validate_review(
                    semantic_review,
                    static_review,
                    expected_chapter=chapter,
                    draft=draft,
                    require_draft_hash=True,
                ),
                "手工稿语义审查",
            )
        except (ArtifactValidationError, CallBudgetExceeded, ProviderError, QualityGateError):
            if raw:
                workspace.write_raw_text("semantic_review.invalid.txt", raw)
            manifest.fail(stage, "手工稿语义审查调用或证据校验失败")
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=0,
                waiting_reason="手工稿语义审查失败；需重新导入新尝试",
            )
            raise
        workspace.write_json("semantic_review.json", semantic_review)
        manifest.complete(stage, input_hash, ["semantic_review.json"], metadata)

    if semantic_review.get("verdict") != "PASS":
        manifest.set_status(
            "WAITING_USER",
            valid_candidates=0,
            waiting_reason=f"手工稿语义审查要求{semantic_review.get('verdict', 'UNKNOWN')}",
        )
        return PipelineResult(
            chapter,
            workspace.path,
            False,
            static_review,
            semantic_review,
            status="WAITING_USER",
        )

    if reader_enabled:
        reader_packet = build_blind_reader_packet(
            state, chapter, draft, project_root
        )
        reader_input_hash = fingerprint(reader_packet)
        reader_metadata = _manual_review_metadata(
            active_router,
            "blind_reader_reviewer",
            "manual-blind-reader-review",
        )
        reader_stage = "manual_blind_reader_review"
        manifest.begin(reader_stage)
        if manifest.can_reuse(
            reader_stage,
            reader_input_hash,
            reader_metadata["route_fingerprint"],
        ):
            reader_review = workspace.read_json("reader_review.json")
            require_no_p1(
                validate_blind_reader_review(
                    reader_review, draft, chapter, audit_profile
                ),
                "手工稿盲读审查",
            )
        else:
            _reject_completed_manual_stage_drift(
                manifest, reader_stage, "手工稿盲读审查"
            )
            if manifest.stage_failed(reader_stage):
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=0,
                    waiting_reason="手工稿盲读审查此前失败；需重新导入新尝试",
                )
                raise QualityGateError(
                    "手工稿盲读审查此前失败；请修订后重新导入新尝试"
                )
            reader_raw: str | None = None
            reader_retry_raw: str | None = None
            reader_call_count = 1
            reader_outputs: list[str] = []
            try:
                reader_raw = caller.call(
                    "blind_reader_reviewer",
                    build_blind_reader_prompt(project_root, reader_packet),
                    project_root / "schemas/reader_review.schema.json",
                    "MANUAL_BLIND_READER_REVIEW",
                )
                workspace.write_raw_text(
                    "reader_review.first.raw.txt", reader_raw
                )
                reader_outputs.append("reader_review.first.raw.txt")
                first_reader_review = parse_json_artifact(
                    reader_raw, "manual-blind-reader-review"
                )
                canonicalize_artifact_quotes(first_reader_review, draft)
                canonicalize_scene_audit_anchor_quotes(
                    first_reader_review.get("mechanism_audit"),
                    draft,
                    audit_profile,
                )
                first_pacing_normalization = canonicalize_pacing_diagnostics(
                    first_reader_review, draft
                )
                workspace.write_json(
                    "reader_review.first.canonical.json", first_reader_review
                )
                reader_outputs.append("reader_review.first.canonical.json")
                first_reader_issues = validate_blind_reader_review(
                    first_reader_review, draft, chapter, audit_profile
                )
                reader_diagnostic = build_reader_validation_diagnostic(
                    first_reader_review,
                    draft,
                    first_reader_issues,
                    first_pacing_normalization,
                    audit_profile,
                )
                reader_review = first_reader_review
                final_pacing_normalization = first_pacing_normalization
                if reader_diagnostic["retry_eligible"]:
                    (
                        feedback_prompt_version,
                        feedback_reason_code,
                        feedback_failure_kind,
                    ) = _reader_feedback_identifiers(reader_diagnostic)
                    reader_diagnostic["retry_performed"] = True
                    reader_diagnostic["feedback_prompt_version"] = (
                        feedback_prompt_version
                    )
                workspace.write_json(
                    "reader_review.validation.json", reader_diagnostic
                )
                reader_outputs.append("reader_review.validation.json")

                if reader_diagnostic["retry_eligible"]:
                    caller.artifact_invalid(
                        stage=reader_stage,
                        failure_kind=feedback_failure_kind,
                        diagnostic_artifact="reader_review.validation.json",
                    )
                    reader_retry_raw = caller.call(
                        "blind_reader_reviewer",
                        build_blind_reader_gap_feedback_prompt(
                            project_root,
                            reader_packet,
                            first_reader_review,
                            reader_diagnostic,
                        ),
                        project_root / "schemas/reader_review.schema.json",
                        feedback_reason_code,
                    )
                    reader_call_count = 2
                    workspace.write_raw_text(
                        "reader_review.retry.raw.txt", reader_retry_raw
                    )
                    reader_outputs.append("reader_review.retry.raw.txt")
                    reader_review = parse_json_artifact(
                        reader_retry_raw,
                        feedback_prompt_version,
                    )
                    canonicalize_artifact_quotes(reader_review, draft)
                    canonicalize_scene_audit_anchor_quotes(
                        reader_review.get("mechanism_audit"),
                        draft,
                        audit_profile,
                    )
                    final_pacing_normalization = (
                        canonicalize_pacing_diagnostics(reader_review, draft)
                    )

                workspace.write_json(
                    "reader_review.final.canonical.json", reader_review
                )
                reader_outputs.append("reader_review.final.canonical.json")
                final_reader_issues = validate_blind_reader_review(
                    reader_review, draft, chapter, audit_profile
                )
                if reader_call_count == 2:
                    final_reader_issues.extend(
                        _reader_feedback_contract_issues(
                            first_reader_review,
                            reader_review,
                            draft,
                            reader_diagnostic,
                            final_pacing_normalization,
                        )
                    )
                final_p1 = [
                    item for item in final_reader_issues if item.severity == "P1"
                ]
                if final_p1:
                    final_diagnostic = build_reader_validation_diagnostic(
                        reader_review,
                        draft,
                        final_reader_issues,
                        final_pacing_normalization,
                        audit_profile,
                    )
                    final_diagnostic["retry_performed"] = (
                        reader_call_count == 2
                    )
                    workspace.write_json(
                        "reader_review.final.validation.json",
                        final_diagnostic,
                    )
                    require_no_p1(final_reader_issues, "手工稿盲读审查")
            except (
                ArtifactValidationError,
                CallBudgetExceeded,
                ProviderError,
                QualityGateError,
            ):
                invalid_raw = reader_retry_raw or reader_raw
                if invalid_raw:
                    workspace.write_raw_text(
                        "reader_review.invalid.txt", invalid_raw
                    )
                manifest.fail(reader_stage, "手工稿盲读调用或证据校验失败")
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=0,
                    waiting_reason="手工稿盲读审查失败；需重新导入新尝试",
                )
                raise
            workspace.write_json("reader_review.json", reader_review)
            reader_outputs.append("reader_review.json")
            reader_metadata = dict(reader_metadata)
            reader_metadata["call_count"] = reader_call_count
            manifest.complete(
                reader_stage,
                reader_input_hash,
                reader_outputs,
                reader_metadata,
            )
        if reader_review.get("verdict") != "PASS":
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=0,
                waiting_reason="手工稿盲读审查未通过",
            )
            raise QualityGateError("手工稿盲读审查未通过")

    if state_enabled:
        settlement_packet = build_state_settlement_packet(
            state,
            plan,
            draft,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
        )
        settlement_input_hash = fingerprint(settlement_packet)
        settlement_metadata = _manual_review_metadata(
            active_router,
            "state_settler",
            "manual-state-settlement",
        )
        settlement_stage = "manual_state_settlement"
        manifest.begin(settlement_stage)
        if manifest.can_reuse(
            settlement_stage,
            settlement_input_hash,
            settlement_metadata["route_fingerprint"],
        ):
            state_settlement = workspace.read_json("state_settlement.json")
            require_no_p1(
                validate_state_settlement(
                    state_settlement,
                    state,
                    plan,
                    draft,
                    planner_context.get("foreshadow_registry"),
                    planner_context.get("arc_registry"),
                    audit_profile,
                ),
                "手工稿状态结算",
            )
        else:
            _reject_completed_manual_stage_drift(
                manifest, settlement_stage, "手工稿状态结算"
            )
            if manifest.stage_failed(settlement_stage):
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=0,
                    waiting_reason="手工稿状态结算此前失败；需重新导入新尝试",
                )
                raise QualityGateError(
                    "手工稿状态结算此前失败；请修订后重新导入新尝试"
                )
            settlement_raw: str | None = None
            settlement_retry_raw: str | None = None
            settlement_call_count = 1
            settlement_outputs: list[str] = []
            settlement_expected = expected_state_changes(
                state,
                plan,
                planner_context.get("foreshadow_registry"),
                planner_context.get("arc_registry"),
            )
            try:
                settlement_raw = caller.call(
                    "state_settler",
                    build_state_settlement_prompt(project_root, settlement_packet),
                    project_root / "schemas/state_settlement.schema.json",
                    "MANUAL_STATE_SETTLEMENT",
                )
                workspace.write_raw_text(
                    "state_settlement.first.raw.txt", settlement_raw
                )
                settlement_outputs.append("state_settlement.first.raw.txt")
                first_state_settlement = _canonical_state_settlement_model_output(
                    settlement_raw,
                    draft,
                    settlement_expected,
                    "manual-state-settlement",
                )
                workspace.write_json(
                    "state_settlement.first.canonical.json",
                    first_state_settlement,
                )
                settlement_outputs.append(
                    "state_settlement.first.canonical.json"
                )
                first_settlement_issues = validate_state_settlement(
                    first_state_settlement,
                    state,
                    plan,
                    draft,
                    planner_context.get("foreshadow_registry"),
                    planner_context.get("arc_registry"),
                    audit_profile,
                )
                settlement_diagnostic = (
                    build_state_settlement_validation_diagnostic(
                        first_state_settlement,
                        state,
                        plan,
                        draft,
                        first_settlement_issues,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    )
                )
                state_settlement = first_state_settlement
                if settlement_diagnostic["retry_eligible"]:
                    settlement_diagnostic["retry_performed"] = True
                    settlement_diagnostic["feedback_prompt_version"] = (
                        "manual-state-settlement-missing-evidence-feedback"
                    )
                workspace.write_json(
                    "state_settlement.validation.json",
                    settlement_diagnostic,
                )
                settlement_outputs.append("state_settlement.validation.json")

                if settlement_diagnostic["retry_eligible"]:
                    caller.artifact_invalid(
                        stage=settlement_stage,
                        failure_kind="MISSING_REQUIRED_STATE_EVIDENCE_REPORT",
                        diagnostic_artifact="state_settlement.validation.json",
                    )
                    settlement_retry_raw = caller.call(
                        "state_settler",
                        build_state_settlement_missing_feedback_prompt(
                            project_root,
                            settlement_packet,
                            first_state_settlement,
                            settlement_diagnostic,
                        ),
                        project_root / "schemas/state_settlement.schema.json",
                        "MANUAL_STATE_SETTLEMENT_MISSING_EVIDENCE_FEEDBACK",
                    )
                    settlement_call_count = 2
                    workspace.write_raw_text(
                        "state_settlement.retry.raw.txt",
                        settlement_retry_raw,
                    )
                    settlement_outputs.append("state_settlement.retry.raw.txt")
                    state_settlement = _canonical_state_settlement_model_output(
                        settlement_retry_raw,
                        draft,
                        settlement_expected,
                        "manual-state-settlement-missing-evidence-feedback",
                    )

                workspace.write_json(
                    "state_settlement.final.canonical.json",
                    state_settlement,
                )
                settlement_outputs.append(
                    "state_settlement.final.canonical.json"
                )
                final_settlement_issues = validate_state_settlement(
                    state_settlement,
                    state,
                    plan,
                    draft,
                    planner_context.get("foreshadow_registry"),
                    planner_context.get("arc_registry"),
                    audit_profile,
                )
                if settlement_call_count == 2:
                    final_settlement_issues.extend(
                        validate_state_settlement_feedback_contract(
                            state_settlement,
                            draft,
                            settlement_diagnostic,
                        )
                    )
                final_settlement_p1 = [
                    item
                    for item in final_settlement_issues
                    if item.severity == "P1"
                ]
                if final_settlement_p1:
                    final_settlement_diagnostic = (
                        build_state_settlement_validation_diagnostic(
                            state_settlement,
                            state,
                            plan,
                            draft,
                            final_settlement_issues,
                            planner_context.get("foreshadow_registry"),
                            planner_context.get("arc_registry"),
                        )
                    )
                    final_settlement_diagnostic["retry_performed"] = (
                        settlement_call_count == 2
                    )
                    if settlement_call_count == 2:
                        final_settlement_diagnostic[
                            "feedback_prompt_version"
                        ] = "manual-state-settlement-missing-evidence-feedback"
                    workspace.write_json(
                        "state_settlement.final.validation.json",
                        final_settlement_diagnostic,
                    )
                    require_no_p1(
                        final_settlement_issues,
                        "手工稿状态结算",
                    )
            except (
                ArtifactValidationError,
                CallBudgetExceeded,
                ProviderError,
                QualityGateError,
            ):
                invalid_settlement_raw = settlement_retry_raw or settlement_raw
                if invalid_settlement_raw:
                    workspace.write_raw_text(
                        "state_settlement.invalid.txt",
                        invalid_settlement_raw,
                    )
                manifest.fail(
                    settlement_stage, "手工稿状态结算调用或证据校验失败"
                )
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=0,
                    waiting_reason="手工稿状态结算失败；需重新导入新尝试",
                )
                raise
            workspace.write_json("state_settlement.json", state_settlement)
            settlement_outputs.append("state_settlement.json")
            settlement_metadata = dict(settlement_metadata)
            settlement_metadata["call_count"] = settlement_call_count
            manifest.complete(
                settlement_stage,
                settlement_input_hash,
                settlement_outputs,
                settlement_metadata,
            )
        if state_settlement.get("verdict") != "PASS":
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=0,
                waiting_reason="手工稿状态结算证据不足",
            )
            raise QualityGateError("手工稿状态结算证据不足")

    manifest.set_status(
        "WAITING_USER",
        valid_candidates=1,
        waiting_reason="手工稿全部启用审查已绑定同一正文；等待accept",
    )
    return PipelineResult(
        chapter,
        workspace.path,
        False,
        static_review,
        semantic_review,
        status="WAITING_USER",
    )


def _require_manual_budget_integrity(
    manifest: RunManifest,
    expected_call_counts: dict[str, int],
) -> None:
    budget = manifest.data.get("budget")
    if not isinstance(budget, dict) or not isinstance(budget.get("calls"), dict):
        raise ArtifactValidationError("手工导入运行缺少调用预算账本")
    calls = budget["calls"]
    active = budget.get("active")
    if active != []:
        raise ArtifactValidationError("手工导入运行仍有未结算模型调用")
    spent = sum(
        1
        for record in calls.values()
        if isinstance(record, dict)
        and record.get("status") in {"COMPLETED", "FAILED"}
    )
    if budget.get("spent") != spent:
        raise ArtifactValidationError("手工导入运行预算spent与调用记录不一致")
    expected_remaining = max(0, int(budget.get("limit", 0)) - spent)
    if budget.get("remaining") != expected_remaining:
        raise ArtifactValidationError("手工导入运行预算remaining与调用记录不一致")
    if len(calls) != sum(expected_call_counts.values()):
        raise ArtifactValidationError("手工导入运行存在未绑定到审查阶段的调用")
    for stage, expected_count in expected_call_counts.items():
        matching = [
            record
            for record in calls.values()
            if isinstance(record, dict) and record.get("stage") == stage
        ]
        if (
            len(matching) != expected_count
            or any(record.get("status") != "COMPLETED" for record in matching)
        ):
            raise ArtifactValidationError(f"手工导入审查调用未完整结算: {stage}")


def _validate_manual_attempt_for_accept(
    project_root: Path,
    workspace: ChapterWorkspace,
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    static_review: dict[str, Any],
    semantic_review: dict[str, Any],
    planner_context: dict[str, Any],
) -> RunManifest | None:
    if not workspace.exists("run_manifest.json"):
        return None
    manifest = RunManifest.load(workspace)
    if manifest.data.get("mode") != "manual_import":
        return None
    manifest, _ = _require_manual_import_integrity(workspace, state)
    manifest.require_stage_outputs("manual_static_review")
    semantic_stage = manifest.require_stage_outputs("manual_semantic_review")
    if semantic_stage.get("call_count") != 1:
        raise ArtifactValidationError("手工稿语义审查阶段调用数无效")
    stored_static = workspace.read_json("static_review.json")
    if stored_static != static_review:
        raise ArtifactValidationError("手工稿确定性审查已过期，必须重新导入并审查")
    packet = build_reviewer_packet(
        state, plan, draft, static_review, planner_context
    )
    if semantic_stage.get("input_hash") != fingerprint(packet):
        raise ArtifactValidationError("手工稿语义审查输入与当前正文或上下文不一致")
    require_no_p1(
        validate_review(
            semantic_review,
            static_review,
            expected_chapter=plan["chapter_number"],
            draft=draft,
            require_draft_hash=True,
        ),
        "手工稿接受前语义审查绑定",
    )
    if semantic_review.get("verdict") != "PASS":
        raise QualityGateError("手工稿语义审查不是PASS")

    config = load_config(project_root)
    audit_profile = load_audit_profile(project_root)
    reader_enabled, state_enabled, expected_limit = (
        _manual_review_budget_contract(config)
    )
    expected_call_counts = {"manual_semantic_reviewer": 1}
    if reader_enabled:
        reader_stage = manifest.require_stage_outputs(
            "manual_blind_reader_review"
        )
        reader_call_count = reader_stage.get("call_count")
        if (
            isinstance(reader_call_count, bool)
            or not isinstance(reader_call_count, int)
            or reader_call_count not in {1, 2}
        ):
            raise ArtifactValidationError("手工稿盲读阶段调用数无效")
        reader_review = workspace.read_json("reader_review.json")
        reader_packet = build_blind_reader_packet(
            state, plan["chapter_number"], draft, project_root
        )
        if reader_stage.get("input_hash") != fingerprint(reader_packet):
            raise ArtifactValidationError(
                "手工稿盲读审查输入与当前正文或上下文不一致"
            )
        required_reader_outputs = {
            "reader_review.first.raw.txt",
            "reader_review.first.canonical.json",
            "reader_review.validation.json",
            "reader_review.final.canonical.json",
            "reader_review.json",
        }
        if reader_call_count == 2:
            required_reader_outputs.add("reader_review.retry.raw.txt")
        if not required_reader_outputs.issubset(reader_stage["outputs"]):
            raise ArtifactValidationError("手工稿盲读阶段缺少反馈审计工件")
        diagnostic = workspace.read_json("reader_review.validation.json")
        if diagnostic.get("draft_sha256") != hashlib.sha256(
            draft.encode("utf-8")
        ).hexdigest():
            raise ArtifactValidationError("手工稿盲读诊断未绑定当前正文")
        first_canonical = workspace.read_json(
            "reader_review.first.canonical.json"
        )
        rebuilt_first, recomputed_normalization = _canonical_reader_model_output(
            workspace.read_text("reader_review.first.raw.txt"),
            draft,
            "manual-blind-reader-first-raw",
            audit_profile,
        )
        if rebuilt_first != first_canonical:
            raise ArtifactValidationError(
                "手工稿盲读首报原始输出与规范化报告不一致"
            )
        recomputed_issues = validate_blind_reader_review(
            first_canonical, draft, plan["chapter_number"], audit_profile
        )
        recomputed_diagnostic = build_reader_validation_diagnostic(
            first_canonical,
            draft,
            recomputed_issues,
            recomputed_normalization,
            audit_profile,
        )
        expected_reader_call_count = (
            2 if recomputed_diagnostic.get("retry_eligible") else 1
        )
        if reader_call_count != expected_reader_call_count:
            raise ArtifactValidationError("手工稿盲读反馈调用数与首报诊断不一致")
        if reader_call_count == 2:
            (
                feedback_prompt_version,
                feedback_reason_code,
                _,
            ) = _reader_feedback_identifiers(recomputed_diagnostic)
            recomputed_diagnostic["retry_performed"] = True
            recomputed_diagnostic["feedback_prompt_version"] = (
                feedback_prompt_version
            )
        if diagnostic != recomputed_diagnostic:
            raise ArtifactValidationError("手工稿盲读诊断无法由首报重现")
        final_canonical = workspace.read_json(
            "reader_review.final.canonical.json"
        )
        if final_canonical != reader_review:
            raise ArtifactValidationError("手工稿最终盲读报告与审计副本不一致")
        if reader_call_count == 1 and first_canonical != final_canonical:
            raise ArtifactValidationError("未反馈的手工稿盲读首报与终报不一致")
        if reader_call_count == 2:
            rebuilt_final, final_normalization = _canonical_reader_model_output(
                workspace.read_text("reader_review.retry.raw.txt"),
                draft,
                "manual-blind-reader-feedback-raw",
                audit_profile,
            )
            if rebuilt_final != final_canonical:
                raise ArtifactValidationError(
                    "手工稿盲读反馈原始输出与最终报告不一致"
                )
            require_no_p1(
                _reader_feedback_contract_issues(
                    first_canonical,
                    final_canonical,
                    draft,
                    diagnostic,
                    final_normalization,
                ),
                "手工稿接受前盲读反馈绑定",
            )
        reader_calls = sorted(
            (
                record
                for record in manifest.data.get("budget", {})
                .get("calls", {})
                .values()
                if isinstance(record, dict)
                and record.get("stage") == "blind_reader_reviewer"
            ),
            key=lambda record: record.get("call_id", ""),
        )
        expected_reasons = ["MANUAL_BLIND_READER_REVIEW"]
        if reader_call_count == 2:
            expected_reasons.append(feedback_reason_code)
        if [record.get("reason_code") for record in reader_calls] != expected_reasons:
            raise ArtifactValidationError("手工稿盲读调用原因与反馈流程不一致")
        require_no_p1(
            validate_blind_reader_review(
                reader_review, draft, plan["chapter_number"], audit_profile
            ),
            "手工稿接受前盲读绑定",
        )
        if reader_review.get("verdict") != "PASS":
            raise QualityGateError("手工稿盲读审查不是PASS")
        expected_call_counts["blind_reader_reviewer"] = reader_call_count
    if state_enabled:
        settlement_stage = manifest.require_stage_outputs(
            "manual_state_settlement"
        )
        settlement_call_count = settlement_stage.get("call_count")
        if (
            isinstance(settlement_call_count, bool)
            or not isinstance(settlement_call_count, int)
            or settlement_call_count not in {1, 2}
        ):
            raise ArtifactValidationError("手工稿状态结算阶段调用数无效")
        state_settlement = workspace.read_json("state_settlement.json")
        settlement_packet = build_state_settlement_packet(
            state,
            plan,
            draft,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
        )
        if settlement_stage.get("input_hash") != fingerprint(settlement_packet):
            raise ArtifactValidationError(
                "手工稿状态结算输入与当前正文或上下文不一致"
            )
        required_settlement_outputs = {
            "state_settlement.first.raw.txt",
            "state_settlement.first.canonical.json",
            "state_settlement.validation.json",
            "state_settlement.final.canonical.json",
            "state_settlement.json",
        }
        if settlement_call_count == 2:
            required_settlement_outputs.add("state_settlement.retry.raw.txt")
        if not required_settlement_outputs.issubset(
            settlement_stage["outputs"]
        ):
            raise ArtifactValidationError("手工稿状态结算阶段缺少反馈审计工件")
        settlement_expected = expected_state_changes(
            state,
            plan,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
        )
        settlement_diagnostic = workspace.read_json(
            "state_settlement.validation.json"
        )
        if settlement_diagnostic.get("draft_sha256") != hashlib.sha256(
            draft.encode("utf-8")
        ).hexdigest():
            raise ArtifactValidationError("手工稿状态结算诊断未绑定当前正文")
        first_state_settlement = workspace.read_json(
            "state_settlement.first.canonical.json"
        )
        rebuilt_first_settlement = _canonical_state_settlement_model_output(
            workspace.read_text("state_settlement.first.raw.txt"),
            draft,
            settlement_expected,
            "manual-state-settlement-first-raw",
        )
        if rebuilt_first_settlement != first_state_settlement:
            raise ArtifactValidationError(
                "手工稿状态结算首报原始输出与规范化报告不一致"
            )
        recomputed_settlement_issues = validate_state_settlement(
            first_state_settlement,
            state,
            plan,
            draft,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
            audit_profile,
        )
        recomputed_settlement_diagnostic = (
            build_state_settlement_validation_diagnostic(
                first_state_settlement,
                state,
                plan,
                draft,
                recomputed_settlement_issues,
                planner_context.get("foreshadow_registry"),
                planner_context.get("arc_registry"),
            )
        )
        expected_settlement_call_count = (
            2
            if recomputed_settlement_diagnostic.get("retry_eligible")
            else 1
        )
        if settlement_call_count != expected_settlement_call_count:
            raise ArtifactValidationError(
                "手工稿状态结算反馈调用数与首报诊断不一致"
            )
        if settlement_call_count == 2:
            recomputed_settlement_diagnostic["retry_performed"] = True
            recomputed_settlement_diagnostic["feedback_prompt_version"] = (
                "manual-state-settlement-missing-evidence-feedback"
            )
        if settlement_diagnostic != recomputed_settlement_diagnostic:
            raise ArtifactValidationError("手工稿状态结算诊断无法由首报重现")
        final_state_settlement = workspace.read_json(
            "state_settlement.final.canonical.json"
        )
        if final_state_settlement != state_settlement:
            raise ArtifactValidationError(
                "手工稿最终状态结算与审计副本不一致"
            )
        if (
            settlement_call_count == 1
            and first_state_settlement != final_state_settlement
        ):
            raise ArtifactValidationError(
                "未反馈的手工稿状态结算首报与终报不一致"
            )
        if settlement_call_count == 2:
            rebuilt_final_settlement = _canonical_state_settlement_model_output(
                workspace.read_text("state_settlement.retry.raw.txt"),
                draft,
                settlement_expected,
                "manual-state-settlement-feedback-raw",
            )
            if rebuilt_final_settlement != final_state_settlement:
                raise ArtifactValidationError(
                    "手工稿状态结算反馈原始输出与最终报告不一致"
                )
            require_no_p1(
                validate_state_settlement_feedback_contract(
                    final_state_settlement,
                    draft,
                    settlement_diagnostic,
                ),
                "手工稿接受前状态结算反馈绑定",
            )
        settlement_calls = sorted(
            (
                record
                for record in manifest.data.get("budget", {})
                .get("calls", {})
                .values()
                if isinstance(record, dict)
                and record.get("stage") == "state_settler"
            ),
            key=lambda record: record.get("call_id", ""),
        )
        expected_settlement_reasons = ["MANUAL_STATE_SETTLEMENT"]
        if settlement_call_count == 2:
            expected_settlement_reasons.append(
                "MANUAL_STATE_SETTLEMENT_MISSING_EVIDENCE_FEEDBACK"
            )
        if [
            record.get("reason_code") for record in settlement_calls
        ] != expected_settlement_reasons:
            raise ArtifactValidationError(
                "手工稿状态结算调用原因与反馈流程不一致"
            )
        require_no_p1(
            validate_state_settlement(
                state_settlement,
                state,
                plan,
                draft,
                planner_context.get("foreshadow_registry"),
                planner_context.get("arc_registry"),
                audit_profile,
            ),
            "手工稿接受前状态结算绑定",
        )
        if state_settlement.get("verdict") != "PASS":
            raise QualityGateError("手工稿状态结算不是PASS")
        expected_call_counts["state_settler"] = settlement_call_count
    if manifest.data.get("budget", {}).get("limit") != expected_limit:
        raise ArtifactValidationError("手工导入运行预算上限与反馈合同不一致")
    _require_manual_budget_integrity(manifest, expected_call_counts)
    return manifest


def _plan_committed_by_settlement(
    state: dict[str, Any],
    plan: dict[str, Any],
    settlement: dict[str, Any] | None,
) -> dict[str, Any]:
    """Filter planned mutations down to changes evidenced by the final prose."""
    committed = copy.deepcopy(plan)
    if settlement is None:
        return committed
    evidenced = {
        item.get("path")
        for item in settlement.get("change_evidence", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    original_changes = plan.get("state_changes", {})
    committed_changes = committed.setdefault("state_changes", {})
    list_fields = (
        "protagonist_known_info_add",
        "protagonist_inventory_add",
        "protagonist_inventory_remove",
        "protagonist_body_updates",
        "ability_updates",
        "character_updates",
        "world_confirmed_add",
        "world_hypotheses_add",
    )
    for field in list_fields:
        committed_changes[field] = [
            value
            for index, value in enumerate(original_changes.get(field, []))
            if f"state_changes.{field}[{index}]" in evidenced
        ]
    committed_changes["protagonist_location"] = (
        original_changes.get("protagonist_location")
        if "state_changes.protagonist_location" in evidenced
        else None
    )
    committed_changes["timeline_year"] = (
        original_changes.get("timeline_year")
        if "state_changes.timeline_year" in evidenced
        else state.get("timeline", {}).get("current_year")
    )
    committed_changes["timeline_elapsed_days"] = (
        original_changes.get("timeline_elapsed_days")
        if "state_changes.timeline_elapsed_days" in evidenced
        else state.get("timeline", {}).get("elapsed_days")
    )
    original_foreshadows = plan.get("foreshadow_operations", {})
    committed_foreshadows = committed.setdefault("foreshadow_operations", {})
    for operation in ("plant", "recover"):
        committed_foreshadows[operation] = [
            value
            for index, value in enumerate(original_foreshadows.get(operation, []))
            if f"foreshadow_operations.{operation}[{index}]" in evidenced
        ]
    original_milestones = plan.get("milestone_operations", {}).get("complete", [])
    committed.setdefault("milestone_operations", {})["complete"] = [
        value
        for index, value in enumerate(original_milestones)
        if f"milestone_operations.complete[{index}]" in evidenced
    ]
    return committed


def _new_state_after_chapter(
    state: dict[str, Any],
    plan: dict[str, Any],
    review: dict[str, Any],
    settlement: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    committed_plan = _plan_committed_by_settlement(state, plan, settlement)
    number = plan["chapter_number"]
    updated["machine_state"] = "IDLE"
    updated["last_updated"] = datetime.now().isoformat(timespec="seconds")
    updated["chapter"].update(
        {
            "last_chapter": number,
            "next_chapter": number + 1,
            "current_phase": plan["phase"],
            "last_title": plan["title"],
        }
    )
    changes = committed_plan.get("state_changes", {})
    known = updated["protagonist"].setdefault("known_info", [])
    known.extend(item for item in changes.get("protagonist_known_info_add", []) if item not in known)
    inventory = updated["protagonist"].setdefault("inventory", [])
    inventory.extend(item for item in changes.get("protagonist_inventory_add", []) if item not in inventory)
    removals = set(changes.get("protagonist_inventory_remove", []))
    updated["protagonist"]["inventory"] = [item for item in inventory if item not in removals]
    if changes.get("protagonist_location") is not None:
        updated["protagonist"]["location"] = changes["protagonist_location"]
    for item in changes.get("protagonist_body_updates", []):
        updated["protagonist"].setdefault("body", {})[item["key"]] = item["value"]
    for item in changes.get("ability_updates", []):
        if item["name"] in updated["protagonist"]["abilities"]:
            updated["protagonist"]["abilities"][item["name"]]["status"] = item["status"]
    updated["timeline"]["current_year"] = changes["timeline_year"]
    updated["timeline"]["elapsed_days"] = changes["timeline_elapsed_days"]
    for item in changes.get("character_updates", []):
        updated["characters"]["active"][item["name"]] = {
            "status": item["status"],
            "note": item["note"],
        }
    for source, target in (
        ("world_confirmed_add", "confirmed"),
        ("world_hypotheses_add", "hypotheses"),
    ):
        current = updated["world_lore"].setdefault(target, [])
        current.extend(item for item in changes.get(source, []) if item not in current)
    summary_core = (
        settlement["reader_visible_summary"]["core"]
        if settlement is not None
        else plan["chapter_purpose"][:120]
    )
    settled_hook = settlement.get("hook") if settlement is not None else None
    hook = (
        {"type": settled_hook["type"], "content": settled_hook["content"]}
        if isinstance(settled_hook, dict)
        else plan["hook"]
    )
    updated["chapter_summaries"]["summaries"][str(number)] = {
        "title": plan["title"],
        "core": summary_core,
        "irreversible_changes": _material_change_keys(changes, state),
    }
    updated["recent_hooks"].append({"chapter": number, **hook})
    updated["recent_hooks"] = updated["recent_hooks"][-5:]
    foreshadows = committed_plan.get("foreshadow_operations", {})
    for item in foreshadows.get("plant", []):
        item_id = _foreshadow_id(item)
        updated["active_foreshadows"][item_id] = {"status": "已播种", "plant_chapter": number}
    for item in foreshadows.get("recover", []):
        item_id = _foreshadow_id(item)
        if item_id in updated["active_foreshadows"]:
            updated["active_foreshadows"][item_id].update({"status": "已回收", "recover_chapter": number})
    completed = updated.setdefault("completed_milestones", [])
    for item in committed_plan.get("milestone_operations", {}).get("complete", []):
        if item not in completed:
            completed.append(item)
    if config is not None:
        volume_structure = config.get("volume_structure", [])
        current_volume = updated["chapter"].get("current_volume")
        current_entry = next(
            (
                item
                for item in volume_structure
                if isinstance(item, dict) and item.get("volume") == current_volume
            ),
            None,
        )
        completed_now = set(
            committed_plan.get("milestone_operations", {}).get("complete", [])
        )
        if (
            isinstance(current_entry, dict)
            and current_entry.get("exit_milestone") in completed_now
        ):
            next_entry = next(
                (
                    item
                    for item in volume_structure
                    if isinstance(item, dict)
                    and item.get("volume") == current_volume + 1
                ),
                None,
            )
            if isinstance(next_entry, dict):
                updated["chapter"]["current_volume"] = next_entry["volume"]
                updated["chapter"]["current_volume_name"] = next_entry["name"]
                if next_entry.get("entry_event"):
                    updated["chapter"]["current_event"] = next_entry["entry_event"]
                if next_entry.get("entry_event_name"):
                    updated["chapter"]["current_event_name"] = next_entry[
                        "entry_event_name"
                    ]
        reveal_layer = updated.get("world_lore", {}).get("reveal_layer", 1)
        for layer in config.get("world_reveal_layers", []):
            if (
                isinstance(layer, dict)
                and layer.get("after") in completed
                and isinstance(layer.get("layer"), int)
            ):
                reveal_layer = max(reveal_layer, layer["layer"])
        updated.setdefault("world_lore", {})["reveal_layer"] = reveal_layer
    updated["last_review"] = {
        "chapter": number,
        "grade": review["grade"],
        "verdict": review["verdict"],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    errors = validate_state(updated)
    if errors:
        raise QualityGateError("章节后的状态无效: " + "；".join(errors))
    return updated


def _serialize_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _chapter_meta(
    state: dict[str, Any],
    plan: dict[str, Any],
    static_review: dict[str, Any],
    review: dict[str, Any],
    settlement: dict[str, Any] | None = None,
) -> str:
    memory_plan = _plan_committed_by_settlement(state, plan, settlement)
    changes = "\n".join(
        f"- {key}: {value}"
        for key, value in memory_plan["state_changes"].items()
    )
    evidence = "\n".join(f"- {item['finding']}：{item['quote']}" for item in review["evidence"])
    settlement_section = (
        "\n\n## 正文状态结算\n```json\n"
        + json.dumps(settlement, ensure_ascii=False, indent=2)
        + "\n```"
        if settlement is not None
        else ""
    )
    if settlement is not None:
        memory_plan["chapter_purpose"] = settlement["reader_visible_summary"]["core"]
        if isinstance(settlement.get("hook"), dict):
            memory_plan["hook"] = {
                "type": settlement["hook"]["type"],
                "content": settlement["hook"]["content"],
            }
    return (
        f"# 第{plan['chapter_number']}章元数据\n\n"
        f"- 标题: {plan['title']}\n"
        f"- 事件: {plan['event_id']}\n"
        f"- 阶段: {plan['phase']}\n"
        f"- 审查: {review['verdict']} / {review['grade']}\n\n"
        f"## 不可逆变化\n{changes}\n\n"
        f"## 审查证据\n{evidence}\n\n"
        f"## 静态指标\n```json\n{json.dumps(static_review['metrics'], ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Memory Record\n```json\n{json.dumps(memory_record(memory_plan), ensure_ascii=False, indent=2)}\n```"
        f"{settlement_section}\n"
    )


def _write_temp(target: Path, content: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def merge_formal_chapters(project_root: Path) -> tuple[Path, int]:
    """Build a continuous reading copy from the exact contiguous formal chapter set."""
    run_preflight(project_root, require_voice_ready=False)
    paths = formal_chapter_paths(project_root)
    target = project_path(project_root, "full_novel")
    # Formal chapter sources retain blank lines for convenient editing. The public
    # TXT is a continuous reading copy, so it intentionally contains no empty
    # paragraph lines, including at chapter boundaries.
    content = "\n".join(
        "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        for path in paths
    )
    if content:
        content += "\n"
    temporary = _write_temp(target, content.encode("utf-8"))
    try:
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise AtomicWriteError(f"合订本写入失败: {exc}") from exc
    return target, len(paths)


def accept_dry_run(
    project_root: Path,
    *,
    attempt: int | None = None,
    candidate: int | None = None,
) -> PipelineResult:
    """Revalidate and promote a previously reviewed dry-run attempt."""
    state = load_state(project_path(project_root, "state"))
    run_preflight(project_root, state)
    audit_profile = load_audit_profile(project_root)
    number = state["chapter"]["next_chapter"]
    chapter_work = project_path(project_root, "work_dir") / f"chapter_{number:03d}"
    if attempt is None:
        candidates = sorted(chapter_work.glob("attempt_*"), reverse=True)
    else:
        candidates = [chapter_work / f"attempt_{attempt:02d}"]
    workspace_path: Path | None = None
    semantic_review: dict[str, Any] | None = None
    for candidate_path in candidates:
        review_path = candidate_path / "semantic_review.json"
        decision_path = candidate_path / "decision.json"
        if candidate is None and not review_path.exists():
            continue
        if candidate is not None and not decision_path.exists():
            continue
        try:
            candidate_review = (
                json.loads(review_path.read_text(encoding="utf-8"))
                if candidate is None
                else {"verdict": "PASS"}
            )
        except (OSError, json.JSONDecodeError):
            continue
        if candidate_review.get("verdict") == "PASS":
            workspace_path = candidate_path
            semantic_review = candidate_review
            break
    if workspace_path is None or semantic_review is None:
        label = f"attempt_{attempt:02d}" if attempt is not None else "任何尝试"
        raise ArtifactValidationError(f"第{number}章{label}没有可接受的PASS审查")

    workspace = ChapterWorkspace(workspace_path, number)
    plan = workspace.read_json("plan.json")
    workspace_manifest = (
        RunManifest.load(workspace)
        if workspace.exists("run_manifest.json")
        else None
    )
    manual_mode = bool(
        workspace_manifest is not None
        and workspace_manifest.data.get("mode") == "manual_import"
    )
    if manual_mode and candidate is not None:
        raise ArtifactValidationError(
            "手工导入尝试必须使用 accept --attempt，不接受 --candidate"
        )
    if candidate is not None:
        identifier = f"candidate_{candidate:02d}"
        if workspace_manifest is not None:
            workspace_manifest.require_stage_outputs(f"generate_{identifier}")
            workspace_manifest.require_stage_outputs(f"triage_{identifier}")
        decision = workspace.read_json("decision.json")
        candidate_record = decision.get("candidates", {}).get(identifier)
        card = (
            candidate_record.get("scorecard", candidate_record)
            if isinstance(candidate_record, dict)
            else None
        )
        floors = load_config(project_root).get("quality_evolution", {}).get(
            "candidate_floors"
        )
        prefix = f"candidates/{identifier}"
        if workspace_manifest is not None:
            card = workspace.read_json(f"{prefix}/scorecard.json")
        draft = workspace.read_text(f"{prefix}/draft.txt")
        static = workspace.read_json(f"{prefix}/static_review.json")
        if isinstance(card, dict) and eligible(card, floors):
            reviews: dict[str, dict[str, Any]] = {}
            integrated_path = f"{prefix}/integrated_review.json"
            if workspace.exists(integrated_path):
                reviews["integrated"] = workspace.read_json(integrated_path)
            for dimension in DIMENSIONS:
                review_path = f"{prefix}/reviews/{dimension}.json"
                if workspace.exists(review_path):
                    reviews[dimension] = workspace.read_json(review_path)
            selected_result = EvolutionResult(
                status="WAITING_USER",
                selected_id=identifier,
                draft=draft,
                static_review=static,
                reviews=reviews,
                scorecard=card,
                decision=decision,
            )
            semantic_review = WritingPipeline._semantic_from_evolution(
                number, selected_result
            )
        else:
            manual_path = f"{prefix}/manual_review.json"
            if not workspace.exists(manual_path):
                raise QualityGateError(
                    f"{identifier}未通过自动候选门禁；需提供逐字可验证的 {manual_path} 才能人工接受"
                )
            semantic_review = workspace.read_json(manual_path)
            require_no_p1(
                validate_review(
                    semantic_review,
                    static,
                    expected_chapter=number,
                    draft=draft,
                ),
                "人工复核",
            )
        workspace.write_text("draft.txt", draft)
        workspace.write_json("static_review.json", static)
        workspace.write_json("semantic_review.json", semantic_review)
    try:
        draft = (workspace_path / "draft.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取待接受正文: {exc}") from exc
    planner_context = build_planner_context(project_root, state)
    allowed_canon_ids = {fact["id"] for fact in planner_context["canon_facts"]}
    allowed_foreshadow_ids = set(planner_context.get("foreshadow_registry", {}).get("entries", {}))
    allowed_milestone_ids = set(planner_context.get("arc_registry", {}).get("milestones", {}))
    require_no_p1(
        validate_plan(
            plan,
            state,
            allowed_canon_ids,
            allowed_foreshadow_ids,
            allowed_milestone_ids,
            planner_context.get("foreshadow_registry"),
            planner_context.get("arc_registry"),
        ),
        "待接受规划",
    )
    recent_limit = load_config(project_root).get("quality_gates", {}).get(
        "recent_chapters_for_repetition", 5
    )
    recent = recent_chapters(project_root, state, recent_limit)
    static_review = scan_draft(
        draft,
        recent,
        planner_context["era_bans"],
        plan,
        length_policy=load_config(project_root).get("chapter_length"),
        taste_contract=planner_context.get("user_taste_contract"),
    )
    if not static_review["passed"]:
        hard_failures = [
            Issue(**item)
            for item in static_review["issues"]
            if item["severity"] == "P1"
        ]
        raise QualityGateError(
            "待接受正文重新检查未通过: " + _format_issues(hard_failures)
        )
    manual_manifest = _validate_manual_attempt_for_accept(
        project_root,
        workspace,
        state,
        plan,
        draft,
        static_review,
        semantic_review,
        planner_context,
    )
    workspace.write_json("static_review.json", static_review)
    require_no_p1(
        validate_review(
            semantic_review,
            static_review,
            expected_chapter=number,
            draft=draft,
            require_draft_hash=manual_manifest is not None,
        ),
        "待接受审查",
    )
    if semantic_review.get("verdict") != "PASS":
        raise QualityGateError("待接受语义审查不是PASS")
    acceptance_config = load_config(project_root)
    reader_gate = acceptance_config.get("quality_gates", {}).get(
        "blind_reader_gate", {}
    )
    if reader_gate.get("enabled", False):
        reader_review: dict[str, Any] | None = None
        existing_reader_path = workspace_path / "reader_review.json"
        if existing_reader_path.exists():
            try:
                existing_reader = json.loads(existing_reader_path.read_text(encoding="utf-8"))
                canonicalize_artifact_quotes(existing_reader, draft)
                canonicalize_scene_audit_anchor_quotes(
                    existing_reader.get("mechanism_audit"),
                    draft,
                    audit_profile,
                )
                canonicalize_pacing_diagnostics(existing_reader, draft)
                existing_issues = validate_blind_reader_review(
                    existing_reader, draft, number, audit_profile
                )
                if (
                    not any(issue.severity == "P1" for issue in existing_issues)
                    and existing_reader.get("verdict") == "PASS"
                ):
                    reader_review = existing_reader
            except (OSError, json.JSONDecodeError):
                reader_review = None
        if reader_review is None:
            raise ArtifactValidationError(
                "待接受尝试缺少有效盲读绑定；请由当前 Agent 按 "
                "workflows/accept-candidate.md 生成并验证工件，"
                "accept 不会现场启动模型"
            )
        else:
            workspace.write_json("reader_review.json", reader_review)
        require_no_p1(
            validate_blind_reader_review(
                reader_review, draft, number, audit_profile
            ),
            "接受前盲读者审查",
        )
        if reader_review.get("verdict") != "PASS":
            raise QualityGateError("接受前盲读者审查未通过")
    state_settlement: dict[str, Any] | None = None
    state_gate = acceptance_config.get("quality_gates", {}).get(
        "state_evidence_gate", {}
    )
    if state_gate.get("enabled", False):
        if not reader_gate.get("enabled", False):
            raise QualityGateError("state_evidence_gate要求同时启用blind_reader_gate")
        existing_settlement_path = workspace_path / "state_settlement.json"
        if existing_settlement_path.exists():
            try:
                existing_settlement = json.loads(
                    existing_settlement_path.read_text(encoding="utf-8")
                )
                canonicalize_artifact_quotes(existing_settlement, draft)
                canonicalize_missing_change_paths(
                    existing_settlement,
                    expected_state_changes(
                        state,
                        plan,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    ),
                )
                settlement_issues = validate_state_settlement(
                    existing_settlement,
                    state,
                    plan,
                    draft,
                    planner_context.get("foreshadow_registry"),
                    planner_context.get("arc_registry"),
                    audit_profile,
                )
                if (
                    not any(issue.severity == "P1" for issue in settlement_issues)
                    and existing_settlement.get("verdict") == "PASS"
                ):
                    state_settlement = existing_settlement
            except (OSError, json.JSONDecodeError):
                state_settlement = None
        if state_settlement is None:
            raise ArtifactValidationError(
                "待接受尝试缺少有效状态结算绑定；请由当前 Agent 按 "
                "workflows/accept-candidate.md 生成并验证工件，"
                "accept 不会现场启动模型"
            )
        else:
            workspace.write_json("state_settlement.json", state_settlement)
        require_no_p1(
            validate_state_settlement(
                state_settlement,
                state,
                plan,
                draft,
                planner_context.get("foreshadow_registry"),
                planner_context.get("arc_registry"),
                audit_profile,
            ),
            "接受前正文状态结算",
        )
        if state_settlement.get("verdict") != "PASS":
            raise QualityGateError("接受前正文状态证据不足")
    promote_atomically(
        project_root,
        state,
        plan,
        draft,
        static_review,
        semantic_review,
        workspace,
        state_settlement,
    )
    if manual_manifest is not None:
        manual_manifest.set_status("COMPLETED", valid_candidates=1)
    mark_due_audits(project_root, load_config(project_root), number, workspace)
    return PipelineResult(
        number, workspace.path, True, static_review, semantic_review
    )


def promote_atomically(
    project_root: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    static_review: dict[str, Any],
    semantic_review: dict[str, Any],
    workspace: ChapterWorkspace,
    settlement: dict[str, Any] | None = None,
) -> None:
    number = plan["chapter_number"]
    volume = state["chapter"]["current_volume"]
    chapter_target = project_path(project_root, "chapters_dir") / f"vol_{volume:02d}" / f"chapter_{number:03d}.txt"
    meta_target = project_path(project_root, "chapter_meta_dir") / f"chapter_{number:03d}.md"
    state_target = project_path(project_root, "state")
    if chapter_target.exists() or meta_target.exists():
        raise AtomicWriteError(f"拒绝覆盖已存在的正式第{number}章；请先归档或使用专用重整流程")
    new_state = _new_state_after_chapter(
        state,
        plan,
        semantic_review,
        settlement,
        load_config(project_root),
    )
    persisted_draft = draft.rstrip() + "\n"
    active_contract = load_taste_contract(project_root)
    reader_report = (
        workspace.read_json("reader_review.json")
        if workspace.exists("reader_review.json")
        else None
    )
    new_state["last_review"].update(
        {
            "draft_sha256": hashlib.sha256(
                persisted_draft.encode("utf-8")
            ).hexdigest(),
            "reader_verdict": (
                reader_report.get("verdict")
                if isinstance(reader_report, dict)
                else "DISABLED"
            ),
            "mechanism_verdict": (
                reader_report.get("mechanism_audit", {}).get("verdict")
                if isinstance(reader_report, dict)
                else "DISABLED"
            ),
            "taste_contract_sha256": taste_contract_sha256(active_contract),
        }
    )
    new_state.setdefault("formal_review_bindings", {})[str(number)] = {
        "draft_sha256": new_state["last_review"]["draft_sha256"],
        "reader_verdict": new_state["last_review"]["reader_verdict"],
        "mechanism_verdict": new_state["last_review"]["mechanism_verdict"],
        "taste_contract_sha256": new_state["last_review"]["taste_contract_sha256"],
        "reviewed_at": new_state["last_review"]["timestamp"],
    }
    payloads = {
        chapter_target: persisted_draft.encode("utf-8"),
        meta_target: _chapter_meta(
            state, plan, static_review, semantic_review, settlement
        ).encode("utf-8"),
        state_target: _serialize_json(new_state),
    }
    temporary = {target: _write_temp(target, data) for target, data in payloads.items()}
    state_backup = state_target.read_bytes()
    backup_target = state_target.with_suffix(".json.bak")
    backup_temporary = _write_temp(backup_target, state_backup)
    completed: list[Path] = []
    try:
        os.replace(backup_temporary, backup_target)
        os.replace(temporary[chapter_target], chapter_target)
        completed.append(chapter_target)
        os.replace(temporary[meta_target], meta_target)
        completed.append(meta_target)
        os.replace(temporary[state_target], state_target)
        completed.append(state_target)
    except OSError as exc:
        for path in completed:
            if path == state_target:
                path.write_bytes(state_backup)
            else:
                path.unlink(missing_ok=True)
        for path in temporary.values():
            path.unlink(missing_ok=True)
        backup_temporary.unlink(missing_ok=True)
        raise AtomicWriteError(f"正式工件提升失败，已回滚: {exc}") from exc
    manifest = {
        "chapter": number,
        "promoted_at": datetime.now().isoformat(timespec="seconds"),
        "files": [
            {"path": str(path.relative_to(project_root)), "sha256": hashlib.sha256(payloads[path]).hexdigest()}
            for path in payloads
        ],
    }
    derived_warnings: list[str] = []
    try:
        memory = MemoryStore(project_root)
        memory_plan = _plan_committed_by_settlement(state, plan, settlement)
        if settlement is not None:
            memory_plan["chapter_purpose"] = settlement["reader_visible_summary"]["core"]
            if isinstance(settlement.get("hook"), dict):
                memory_plan["hook"] = {
                    "type": settlement["hook"]["type"],
                    "content": settlement["hook"]["content"],
                }
        memory.record_promoted_chapter(number, chapter_target, memory_plan)
        findings: list[dict[str, Any]] = []
        if workspace.exists("decision.json"):
            decision = workspace.read_json("decision.json")
            selected = decision.get("selected_id") or ""
            prefix = (
                f"candidates/{selected}"
                if selected.startswith("candidate_")
                else f"revisions/round_{int(selected.split('_')[-1]):02d}"
                if selected.startswith("revision_")
                else None
            )
            if prefix:
                for dimension in ("continuity", "character", "craft", "anti_slop"):
                    review_path = f"{prefix}/reviews/{dimension}.json"
                    if not workspace.exists(review_path):
                        continue
                    specialist = workspace.read_json(review_path)
                    for item in specialist.get("required_revisions", []) + specialist.get("warnings", []):
                        findings.append(
                            {
                                "code": item.get("code"),
                                "scope": {"event_id": plan.get("event_id")},
                                "instruction": item.get("instruction")
                                or item.get("explanation")
                                or "避免同类问题复发",
                                "quote": item.get("quote"),
                            }
                        )
        memory.update_lessons(number, findings)
        memory.retire_lessons(number)
    except (OSError, ValueError, ArtifactValidationError, json.JSONDecodeError) as exc:
        derived_warnings.append(f"长期记忆派生更新失败: {exc}")
    if derived_warnings:
        manifest["derived_warnings"] = derived_warnings
    workspace.write_json("promotion_manifest.json", manifest)


def mark_due_audits(
    project_root: Path,
    config: dict[str, Any],
    chapter: int,
    workspace: ChapterWorkspace,
) -> list[str]:
    audit_config = config.get("audits", {})
    due = due_audits(
        chapter,
        audit_config.get("health_interval", 10),
        audit_config.get("arc_interval", 20),
    )
    names = [name for name, is_due in due.items() if is_due]
    if workspace.exists("decision.json"):
        decision = workspace.read_json("decision.json")
        decision["audits_due"] = names
        workspace.write_json("decision.json", decision)
    return names


class WritingPipeline:
    def __init__(
        self,
        project_root: Path,
        provider: ModelProvider | None = None,
        providers: StageModelRouter | None = None,
    ):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        self.audit_profile = load_audit_profile(self.project_root)
        self._provider_injected = providers is not None or provider is not None
        if providers is not None:
            self.router = providers
            self.provider = providers.provider_for("planner")
        elif provider is not None:
            self.provider = provider
            self.router = StageModelRouter.single(provider)
        else:
            raise ProviderError(
                "仓库不再提供 Agent 执行后端；请由当前 Agent 按 WORKFLOW.md "
                "执行 prompt-native 工作流。WritingPipeline 仅接受宿主显式注入的"
                "进程内测试/嵌入 Provider。"
            )

    def run_next(
        self,
        *,
        dry_run: bool = False,
        resume: bool = False,
        mode: str = "balanced",
        shadow_review: str | None = None,
        progress: ProgressSink | None = None,
    ) -> PipelineResult:
        if "quality_evolution" in self.config:
            return self._run_evolution(
                dry_run=dry_run,
                resume=resume,
                mode=mode,
                shadow_review=shadow_review,
                progress=progress,
            )
        return self._run_legacy(dry_run=dry_run)

    @staticmethod
    def _semantic_from_evolution(
        number: int, result: EvolutionResult
    ) -> dict[str, Any]:
        if result.draft is None or result.scorecard is None:
            raise QualityGateError("质量演进没有产生可接受正文")
        integrated = result.reviews.get("integrated", {})
        summaries = dict(integrated.get("summaries", {}))
        evidence_by_dimension = integrated.get("evidence", {})
        warnings_source = list(integrated.get("warnings", []))
        for dimension in DIMENSIONS:
            specialist = result.reviews.get(dimension)
            if specialist:
                summaries[dimension] = specialist.get(
                    "summary", summaries.get(dimension, "")
                )
                evidence_by_dimension = {
                    **evidence_by_dimension,
                    dimension: specialist.get(
                        "evidence", evidence_by_dimension.get(dimension, [])
                    ),
                }
                warnings_source.extend(specialist.get("warnings", []))
        evidence: list[dict[str, str]] = []
        seen_quotes: set[str] = set()
        for dimension in DIMENSIONS:
            for item in evidence_by_dimension.get(dimension, []):
                quote = item.get("quote")
                if quote and quote in result.draft and quote not in seen_quotes:
                    seen_quotes.add(quote)
                    evidence.append(
                        {
                            "quote": quote,
                            "finding": f"{dimension}: {item.get('finding', '')}",
                        }
                    )
                if len(evidence) >= 3:
                    break
            if len(evidence) >= 3:
                break
        if len(evidence) < 3:
            raise QualityGateError("质量演进结论缺少三条不重复正文证据")
        score = result.scorecard["weighted_score"]
        grade = "A" if score >= 90 else "B"
        warnings = [
            item.get("explanation", str(item))
            for item in warnings_source
        ]
        return {
            "chapter_number": number,
            "verdict": "PASS",
            "grade": grade,
            "p1_failures": [],
            "p2_warnings": warnings,
            "evidence": evidence,
            "character_assessment": summaries.get("character", "专项审查通过"),
            "canon_assessment": summaries.get("continuity", "连续性审查通过"),
            "style_assessment": "；".join(
                filter(
                    None,
                    [summaries.get("craft"), summaries.get("anti_slop")],
                )
            ),
            "revision_instructions": [],
        }

    def _attempt_number(self, number: int, resume: bool, state_hash: str) -> int:
        chapter_work = project_path(self.project_root, "work_dir") / f"chapter_{number:03d}"
        attempts = sorted(chapter_work.glob("attempt_*"))
        if resume:
            for path in reversed(attempts):
                manifest_path = path / "run_manifest.json"
                if not manifest_path.exists():
                    continue
                workspace = ChapterWorkspace(path, number)
                manifest = RunManifest.load(workspace)
                if manifest.data.get("state_hash") != state_hash:
                    raise QualityGateError("恢复运行失败：正式状态已变化，不能复用旧候选")
                if manifest.data.get("status") == "REPLAN" or "budget" not in manifest.data:
                    raise LegacyRunNotResumable(
                        "[旧流程] REPLAN 工作区只读；旧调用数不符合新版预算账本，请显式创建新运行"
                    )
                if manifest.data.get("status") in {
                    "RUNNING",
                    "WAITING_USER",
                    "AUTO_PROMOTE",
                    "BUDGET_EXHAUSTED",
                }:
                    return int(path.name.split("_")[-1])
        return (
            max((int(path.name.split("_")[-1]) for path in attempts), default=0)
            + 1
        )

    def _run_evolution(
        self,
        *,
        dry_run: bool,
        resume: bool,
        mode: str,
        shadow_review: str | None,
        progress: ProgressSink | None,
    ) -> PipelineResult:
        state = load_state(project_path(self.project_root, "state"))
        run_preflight(self.project_root, state)
        number = state["chapter"]["next_chapter"]
        state_hash = fingerprint(state)
        attempt = self._attempt_number(number, resume, state_hash)
        recent_limit = self.config.get("quality_gates", {}).get(
            "recent_chapters_for_repetition", 5
        )
        recent = recent_chapters(self.project_root, state, recent_limit)
        memory = MemoryStore(self.project_root)
        base_context = build_planner_context(
            self.project_root, state, memory.context_for_state(state)
        )
        allowed_canon_ids = {fact["id"] for fact in base_context["canon_facts"]}
        allowed_foreshadow_ids = set(base_context.get("foreshadow_registry", {}).get("entries", {}))
        allowed_milestone_ids = set(base_context.get("arc_registry", {}).get("milestones", {}))
        workspace = ChapterWorkspace.create(
            project_path(self.project_root, "work_dir"), number, attempt
        )
        resuming_workspace = resume and workspace.exists("run_manifest.json")
        reader_gate_enabled = bool(
            self.config.get("quality_gates", {})
            .get("blind_reader_gate", {})
            .get("enabled", False)
        )
        state_gate_enabled = bool(
            self.config.get("quality_gates", {})
            .get("state_evidence_gate", {})
            .get("enabled", False)
        )
        if state_gate_enabled and not reader_gate_enabled:
            raise QualityGateError("state_evidence_gate要求同时启用blind_reader_gate")
        call_limit = (
            5 if mode == "fast" and state_gate_enabled
            else 4 if mode == "fast"
            else self.config.get("quality_evolution", {}).get("call_limit", 11)
        )
        if workspace.exists("run_manifest.json"):
            manifest = RunManifest.load(workspace)
            if manifest.data.get("status") == "REPLAN" or "budget" not in manifest.data:
                raise LegacyRunNotResumable(
                    "[旧流程] REPLAN 工作区只读；不支持按新版 --resume 继续"
                )
            if manifest.data.get("state_hash") != state_hash:
                raise QualityGateError("运行清单与当前正式状态不匹配")
            if manifest.data.get("mode", "balanced") != mode:
                raise QualityGateError("恢复运行必须使用原来的创作模式")
        else:
            manifest = RunManifest.create(
                workspace, number, state_hash, call_limit=call_limit, mode=mode
            )
        caller = ModelCallExecutor(self.router, manifest, progress)
        if resuming_workspace:
            caller.budget.recover_interrupted()
        if resuming_workspace and workspace.exists("context.json"):
            planner_context = workspace.read_json("context.json")
        else:
            planner_context = copy.deepcopy(base_context)
            workspace.write_json("context.json", planner_context)
            context_pack = build_chapter_context_pack(
                state,
                planner_context,
                recent,
                self.config.get("context_limits"),
            )
            workspace.write_json("context_metrics.json", context_pack["metrics"])
            manifest.mutate(
                lambda data: data.__setitem__("context_metrics", context_pack["metrics"])
            )
        plan_input_hash = fingerprint(planner_context)
        settings = caller._settings_for("planner")
        route_fingerprint = fingerprint(
            {
                "profile": settings.profile,
                "model": settings.model,
                "reasoning_effort": settings.reasoning_effort,
                "prompt_version": "budgeted-adaptive-initial",
            }
        )
        reader_reservation = None
        state_reservation = None

        def cancel_final_gates() -> None:
            for reservation in (reader_reservation, state_reservation):
                if reservation is None:
                    continue
                try:
                    caller.cancel_before_provider(reservation)
                except ArtifactValidationError:
                    pass

        try:
            if manifest.can_reuse("plan", plan_input_hash, route_fingerprint):
                caller.stage_reused("plan")
                plan = workspace.read_json("plan.json")
            else:
                if manifest.stage_failed("plan"):
                    raise ArtifactValidationError(
                        "Planner已在本预算运行中失败；系统不会自动重试"
                    )
                manifest.begin("plan")
                plan = parse_json_artifact(
                    caller.call(
                        "planner",
                        _agent_prompt(
                            self.project_root,
                            "planner",
                            planner_context,
                            "为当前下一章生成规划。只输出满足plan schema的JSON。",
                        ),
                        self.project_root / "schemas/plan.schema.json",
                        "PLAN_CHAPTER",
                    ),
                    "plan",
                )
                plan_issues = validate_plan(
                    plan,
                    state,
                    allowed_canon_ids,
                    allowed_foreshadow_ids,
                    allowed_milestone_ids,
                    planner_context.get("foreshadow_registry"),
                    planner_context.get("arc_registry"),
                )
                if any(issue.severity == "P1" for issue in plan_issues):
                    workspace.write_json("plan.invalid.json", plan)
                require_no_p1(plan_issues, "规划")
                workspace.write_json("plan.json", plan)
                manifest.complete(
                    "plan",
                    plan_input_hash,
                    ["plan.json"],
                    {
                        "model_profile": settings.profile,
                        "prompt_version": "budgeted-adaptive-initial",
                        "call_count": 1,
                        "route_fingerprint": route_fingerprint,
                    },
                )
            # Reserve mandatory final gates before candidate evolution so the
            # adaptive generation budget cannot consume them.
            if reader_gate_enabled:
                gate_requests = [("blind_reader_reviewer", "BLIND_READER_GATE")]
                if state_gate_enabled:
                    gate_requests.append(("state_settler", "STATE_EVIDENCE_GATE"))
                gate_reservations = caller.reserve_many(gate_requests)
                reader_reservation = gate_reservations[0]
                if state_gate_enabled:
                    state_reservation = gate_reservations[1]
            engine_result = QualityEvolutionEngine(
                self.project_root,
                self.router,
                self.config,
                caller,
                mode=mode,
                shadow_dimension=shadow_review,
            ).run(
                state=state,
                plan=plan,
                recent=recent,
                planner_context=planner_context,
                workspace=workspace,
                manifest=manifest,
            )
        except (ArtifactValidationError, ProviderError, QualityGateError, CallBudgetExceeded) as exc:
            cancel_final_gates()
            stage = manifest.data.get("current_stage", "pipeline")
            manifest.fail(stage, str(exc))
            status = (
                "BUDGET_EXHAUSTED"
                if manifest.data["budget"]["remaining"] == 0
                else "WAITING_USER"
            )
            decision = {
                "chapter_number": number,
                "status": status,
                "reasons": [str(exc)],
                "calls_spent": manifest.data["budget"]["spent"],
                "calls_remaining": manifest.data["budget"]["remaining"],
                "best_available_artifact": None,
                "safe_actions": ["查看已有规划和运行清单"],
                "new_budget_actions": ["显式创建新的预算化运行"],
                "resume_warning": "--resume只恢复现有预算，不会突破运行清单的调用上限",
                "exhausted_stage": stage if status == "BUDGET_EXHAUSTED" else None,
            }
            workspace.write_json("decision.json", decision)
            manifest.set_status(status, valid_candidates=0, waiting_reason=str(exc))
            return PipelineResult(number, workspace.path, False, None, None, status)

        if engine_result.draft is None or engine_result.static_review is None:
            cancel_final_gates()
            return PipelineResult(
                number, workspace.path, False, None, None, engine_result.status
            )
        try:
            semantic_review = self._semantic_from_evolution(number, engine_result)
            workspace.write_text("draft.txt", engine_result.draft)
            workspace.write_json("static_review.json", engine_result.static_review)
            workspace.write_json("semantic_review.json", semantic_review)
            require_no_p1(
                validate_review(
                    semantic_review,
                    engine_result.static_review,
                    expected_chapter=number,
                    draft=engine_result.draft,
                ),
                "质量演进汇总审查",
            )
        except (ArtifactValidationError, QualityGateError) as exc:
            cancel_final_gates()
            manifest.set_status("WAITING_USER", valid_candidates=manifest.data["valid_candidates"], waiting_reason=str(exc))
            return PipelineResult(
                number,
                workspace.path,
                False,
                engine_result.static_review,
                None,
                "WAITING_USER",
            )
        if engine_result.status != "AUTO_PROMOTE":
            cancel_final_gates()
            return PipelineResult(
                number,
                workspace.path,
                False,
                engine_result.static_review,
                semantic_review,
                engine_result.status,
            )

        if not reader_gate_enabled:
            promoted = not dry_run
            status = engine_result.status
            if promoted:
                promote_atomically(
                    self.project_root,
                    state,
                    plan,
                    engine_result.draft,
                    engine_result.static_review,
                    semantic_review,
                    workspace,
                )
                manifest.set_status(
                    "COMPLETED", valid_candidates=manifest.data["valid_candidates"]
                )
                mark_due_audits(self.project_root, self.config, number, workspace)
                status = "COMPLETED"
            return PipelineResult(
                number,
                workspace.path,
                promoted,
                engine_result.static_review,
                semantic_review,
                status,
            )

        if reader_reservation is None:
            raise QualityGateError("盲读者门禁未预留调用")
        reader_packet = build_blind_reader_packet(
            state, number, engine_result.draft, self.project_root
        )
        reader_input_hash = fingerprint(reader_packet)
        reader_settings = caller._settings_for("blind_reader_reviewer")
        reader_route_fingerprint = fingerprint(
            {
                "profile": reader_settings.profile,
                "model": reader_settings.model,
                "reasoning_effort": reader_settings.reasoning_effort,
                "prompt_version": "blind-reader-gate",
            }
        )
        reader_raw: str | None = None
        try:
            manifest.begin("blind_reader_review")
            reader_raw = caller.call_reserved(
                reader_reservation,
                build_blind_reader_prompt(self.project_root, reader_packet),
                self.project_root / "schemas/reader_review.schema.json",
            )
            reader_review = parse_json_artifact(
                reader_raw,
                "blind-reader-review",
            )
            canonicalize_artifact_quotes(reader_review, engine_result.draft)
            canonicalize_scene_audit_anchor_quotes(
                reader_review.get("mechanism_audit"),
                engine_result.draft,
                self.audit_profile,
            )
            canonicalize_pacing_diagnostics(reader_review, engine_result.draft)
            require_no_p1(
                validate_blind_reader_review(
                    reader_review,
                    engine_result.draft,
                    number,
                    self.audit_profile,
                ),
                "盲读者审查结构",
            )
            workspace.write_json("reader_review.json", reader_review)
            manifest.complete(
                "blind_reader_review",
                reader_input_hash,
                ["reader_review.json"],
                {
                    "model_profile": reader_settings.profile,
                    "prompt_version": "blind-reader-gate",
                    "call_count": 1,
                    "route_fingerprint": reader_route_fingerprint,
                },
            )
        except (ArtifactValidationError, ProviderError, QualityGateError) as exc:
            if state_reservation is not None:
                try:
                    caller.cancel_before_provider(state_reservation)
                except ArtifactValidationError:
                    pass
            if reader_raw:
                workspace.write_raw_text("reader_review.invalid.txt", reader_raw)
            decision = {
                "chapter_number": number,
                "status": "WAITING_USER",
                "reasons": [f"盲读者门禁无效: {exc}"],
                "reader_report": "reader_review.invalid.txt" if reader_raw else None,
                "safe_actions": ["查看无效盲读原始报告和既有候选"],
                "new_budget_actions": ["修复盲读输出合同后发起新的预算化运行"],
                "resume_warning": "当前候选不会自动提升；有效PASS盲读仍是硬门禁。",
            }
            workspace.write_json("decision.json", decision)
            manifest.fail("blind_reader_review", str(exc))
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=manifest.data["valid_candidates"],
                waiting_reason=f"盲读者门禁无效: {exc}",
            )
            return PipelineResult(
                number,
                workspace.path,
                False,
                engine_result.static_review,
                semantic_review,
                "WAITING_USER",
            )
        if reader_review["verdict"] != "PASS":
            if state_reservation is not None:
                caller.cancel_before_provider(state_reservation)
            decision = {
                "chapter_number": number,
                "status": "WAITING_USER",
                "reasons": ["盲读者审查未通过"],
                "reader_verdict": reader_review["verdict"],
                "reader_report": "reader_review.json",
                "safe_actions": ["查看盲读者报告和既有候选"],
                "new_budget_actions": ["以报告中的定向问题发起新的生成运行"],
                "resume_warning": "当前候选不会自动提升；盲读者问题必须先修复。",
            }
            workspace.write_json("decision.json", decision)
            manifest.set_status(
                "WAITING_USER",
                valid_candidates=manifest.data["valid_candidates"],
                waiting_reason=f"盲读者审查要求{reader_review['verdict']}",
            )
            return PipelineResult(
                number,
                workspace.path,
                False,
                engine_result.static_review,
                semantic_review,
                "WAITING_USER",
            )

        state_settlement: dict[str, Any] | None = None
        if state_gate_enabled:
            if state_reservation is None:
                raise QualityGateError("状态证据门禁未预留调用")
            settlement_packet = build_state_settlement_packet(
                state,
                plan,
                engine_result.draft,
                planner_context.get("foreshadow_registry"),
                planner_context.get("arc_registry"),
            )
            settlement_input_hash = fingerprint(settlement_packet)
            settlement_settings = caller._settings_for("state_settler")
            settlement_route_fingerprint = fingerprint(
                {
                    "profile": settlement_settings.profile,
                    "model": settlement_settings.model,
                    "reasoning_effort": settlement_settings.reasoning_effort,
                    "prompt_version": "text-grounded-state",
                }
            )
            settlement_raw: str | None = None
            try:
                manifest.begin("state_settlement")
                settlement_raw = caller.call_reserved(
                    state_reservation,
                    build_state_settlement_prompt(
                        self.project_root, settlement_packet
                    ),
                    self.project_root / "schemas/state_settlement.schema.json",
                )
                state_settlement = parse_json_artifact(
                    settlement_raw, "state-settlement"
                )
                canonicalize_artifact_quotes(
                    state_settlement, engine_result.draft
                )
                canonicalize_missing_change_paths(
                    state_settlement,
                    expected_state_changes(
                        state,
                        plan,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    ),
                )
                require_no_p1(
                    validate_state_settlement(
                        state_settlement,
                        state,
                        plan,
                        engine_result.draft,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                        self.audit_profile,
                    ),
                    "正文状态结算",
                )
                workspace.write_json("state_settlement.json", state_settlement)
                manifest.complete(
                    "state_settlement",
                    settlement_input_hash,
                    ["state_settlement.json"],
                    {
                        "model_profile": settlement_settings.profile,
                        "prompt_version": "text-grounded-state",
                        "call_count": 1,
                        "route_fingerprint": settlement_route_fingerprint,
                    },
                )
            except (ArtifactValidationError, ProviderError, QualityGateError) as exc:
                if settlement_raw:
                    workspace.write_raw_text(
                        "state_settlement.invalid.txt", settlement_raw
                    )
                manifest.fail("state_settlement", str(exc))
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=manifest.data["valid_candidates"],
                    waiting_reason=f"正文状态结算无效: {exc}",
                )
                return PipelineResult(
                    number,
                    workspace.path,
                    False,
                    engine_result.static_review,
                    semantic_review,
                    "WAITING_USER",
                )
            if state_settlement["verdict"] != "PASS":
                decision = {
                    "chapter_number": number,
                    "status": "WAITING_USER",
                    "reasons": ["最终正文没有充分演出全部待提交状态变化"],
                    "state_settlement": "state_settlement.json",
                    "missing_changes": state_settlement["missing_changes"],
                    "safe_actions": ["查看缺失变化及其正文证据"],
                    "new_budget_actions": ["定向修订正文后重新进行盲读和状态结算"],
                }
                workspace.write_json("decision.json", decision)
                manifest.set_status(
                    "WAITING_USER",
                    valid_candidates=manifest.data["valid_candidates"],
                    waiting_reason="最终正文状态证据不足",
                )
                return PipelineResult(
                    number,
                    workspace.path,
                    False,
                    engine_result.static_review,
                    semantic_review,
                    "WAITING_USER",
                )

        promoted = not dry_run
        status = engine_result.status
        if promoted:
            promote_atomically(
                self.project_root,
                state,
                plan,
                engine_result.draft,
                engine_result.static_review,
                semantic_review,
                workspace,
                state_settlement,
            )
            manifest.set_status(
                "COMPLETED", valid_candidates=manifest.data["valid_candidates"]
            )
            mark_due_audits(self.project_root, self.config, number, workspace)
            status = "COMPLETED"
        return PipelineResult(
            number,
            workspace.path,
            promoted,
            engine_result.static_review,
            semantic_review,
            status,
        )

    def _run_legacy(self, *, dry_run: bool = False) -> PipelineResult:
        state = load_state(project_path(self.project_root, "state"))
        run_preflight(self.project_root, state)
        number = state["chapter"]["next_chapter"]
        quality_config = self.config.get("quality_gates", {})
        max_attempts = quality_config.get("max_retries", 3)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise ArtifactValidationError("quality_gates.max_retries 必须是正整数")
        recent_limit = quality_config.get("recent_chapters_for_repetition", 5)
        recent = recent_chapters(self.project_root, state, recent_limit)
        base_context = build_planner_context(self.project_root, state)
        allowed_canon_ids = {fact["id"] for fact in base_context["canon_facts"]}
        allowed_foreshadow_ids = set(base_context.get("foreshadow_registry", {}).get("entries", {}))
        allowed_milestone_ids = set(base_context.get("arc_registry", {}).get("milestones", {}))
        failures: list[str] = []
        saved_plan: dict[str, Any] | None = None
        revision_context: dict[str, Any] | None = None

        for attempt in range(1, max_attempts + 1):
            workspace = ChapterWorkspace.create(
                project_path(self.project_root, "work_dir"), number, attempt
            )
            planner_context = copy.deepcopy(base_context)
            if failures:
                planner_context["previous_attempt_failures"] = failures[-2:]
            workspace.write_json("context.json", planner_context)

            try:
                if saved_plan is None:
                    plan_prompt = _agent_prompt(
                        self.project_root,
                        "planner",
                        planner_context,
                        "为当前下一章生成规划。修复输入中列出的前次失败。只输出满足plan schema的JSON，不读写项目文件。",
                    )
                    plan = parse_json_artifact(
                        self.provider.generate(
                            plan_prompt, self.project_root / "schemas/plan.schema.json"
                        ),
                        "plan",
                    )
                    plan_issues = validate_plan(
                        plan,
                        state,
                        allowed_canon_ids,
                        allowed_foreshadow_ids,
                        allowed_milestone_ids,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    )
                    if any(issue.severity == "P1" for issue in plan_issues):
                        workspace.write_json("plan.invalid.json", plan)
                    require_no_p1(plan_issues, "规划")
                    saved_plan = copy.deepcopy(plan)
                else:
                    plan = copy.deepcopy(saved_plan)
                workspace.write_json("plan.json", plan)

                writer_packet = build_writer_packet(
                    state,
                    plan,
                    recent,
                    planner_context,
                    revision_context,
                    project_root=self.project_root,
                )
                workspace.write_json(
                    "writer_context.json",
                    {
                        key: writer_packet[key]
                        for key in (
                            "story_brief",
                            "hard_constraints",
                            "authoritative_context",
                            "context_trace",
                        )
                        if key in writer_packet
                    },
                )
                writer_instruction = (
                    "按revision_context定向修订上一稿并输出完整章节纯文本。只输出正文，不读写项目文件。"
                    if revision_context
                    else "生成完整章节纯文本。只输出正文，不读写项目文件。"
                )
                draft_prompt = _agent_prompt(
                    self.project_root,
                    "writer",
                    writer_packet,
                    writer_instruction,
                )
                draft = canonical_text(self.provider.generate(draft_prompt, None))
                workspace.write_text("draft.txt", draft)

                static_review = scan_draft(
                    draft,
                    recent,
                    planner_context["era_bans"],
                    plan,
                    length_policy=self.config.get("chapter_length"),
                    taste_contract=planner_context.get("user_taste_contract"),
                )
                workspace.write_json("static_review.json", static_review)
                if not static_review["passed"]:
                    hard_failures = [
                        Issue(**item)
                        for item in static_review["issues"]
                        if item["severity"] == "P1"
                    ]
                    revision_context = {
                        "previous_draft": draft,
                        "instructions": [item.message for item in hard_failures],
                        "source": "static_review",
                    }
                    raise QualityGateError(
                        "正文硬检查未通过: " + _format_issues(hard_failures)
                    )

                reviewer_packet = build_reviewer_packet(
                    state, plan, draft, static_review, planner_context
                )
                review_prompt = _agent_prompt(
                    self.project_root,
                    "reviewer",
                    reviewer_packet,
                    "独立审查正文。只输出满足review schema的JSON，不修改正文或项目文件。",
                )
                semantic_review = parse_json_artifact(
                    self.provider.generate(
                        review_prompt, self.project_root / "schemas/review.schema.json"
                    ),
                    "semantic_review",
                )
                workspace.write_json("semantic_review.json", semantic_review)
                require_no_p1(
                    validate_review(
                        semantic_review,
                        static_review,
                        expected_chapter=number,
                        draft=draft,
                    ),
                    "语义审查结构",
                )
                if semantic_review["verdict"] != "PASS":
                    directions = "；".join(
                        semantic_review["revision_instructions"]
                    ) or "审查未提供修订指令"
                    if semantic_review["verdict"] == "REVISE":
                        revision_context = {
                            "previous_draft": draft,
                            "instructions": semantic_review["revision_instructions"],
                            "evidence": semantic_review["evidence"],
                            "source": "semantic_review",
                        }
                    else:
                        saved_plan = None
                        revision_context = None
                    raise QualityGateError(
                        f"语义审查要求{semantic_review['verdict']}: {directions}"
                    )
            except (ArtifactValidationError, ProviderError, QualityGateError) as exc:
                failures.append(f"第{attempt}次: {exc}")
                if attempt == max_attempts:
                    detail = "；".join(failures)
                    raise QualityGateError(
                        f"第{number}章连续{max_attempts}次未通过，正式文件未改变: {detail}"
                    ) from exc
                continue

            if not dry_run:
                promote_atomically(
                    self.project_root,
                    state,
                    plan,
                    draft,
                    static_review,
                    semantic_review,
                    workspace,
                )
            return PipelineResult(
                number,
                workspace.path,
                not dry_run,
                static_review,
                semantic_review,
            )

        raise QualityGateError(f"第{number}章没有产生可发布结果")
