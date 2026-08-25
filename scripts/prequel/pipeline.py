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
from .context_builder import build_planner_context
from .errors import (
    ArtifactValidationError,
    AtomicWriteError,
    QualityGateError,
)
from .evaluation import DIMENSIONS, canonicalize_artifact_quotes, eligible
from .memory import MemoryStore, memory_record
from .project import load_project_spec, project_path
from .quality import Issue, scan_draft, validate_plan, validate_review
from .reader_review import (
    canonicalize_pacing_diagnostics,
    validate_blind_reader_review,
)
from .scene_audit import canonicalize_scene_audit_anchor_quotes
from .run_manifest import RunManifest, fingerprint
from .state_store import load_state, validate_state
from .taste_contract import load_taste_contract, taste_contract_sha256
from .state_settlement import (
    canonicalize_missing_change_paths,
    expected_state_changes,
    validate_state_settlement,
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
    if "workflow_policy" in config:
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
    if provenance.get("manual_review_contract") != "prompt-native-artifacts":
        raise ArtifactValidationError(
            "手工导入运行不是当前任务工件合同；请重新导入新尝试"
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
        "manual_review_contract": "prompt-native-artifacts",
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
    )
    manifest.set_status(
        "WAITING_USER",
        valid_candidates=0,
        waiting_reason="手工稿已导入；等待当前 Agent 提交哈希绑定的审查工件",
    )
    return PipelineResult(
        chapter,
        workspace.path,
        False,
        None,
        None,
        status="WAITING_USER",
    )


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
    """Validate imported provenance without executing any semantic task."""
    del project_root, planner_context
    if not workspace.exists("run_manifest.json"):
        return None
    manifest = RunManifest.load(workspace)
    if manifest.data.get("mode") != "manual_import":
        return None
    _require_manual_import_integrity(workspace, state)
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


def _semantic_from_candidate(
    number: int,
    draft: str,
    scorecard: dict[str, Any],
    reviews: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Normalize already-produced review artifacts into the promotion contract."""
    integrated = reviews.get("integrated", {})
    summaries = dict(integrated.get("summaries", {}))
    evidence_by_dimension = dict(integrated.get("evidence", {}))
    warnings_source = list(integrated.get("warnings", []))
    for dimension in DIMENSIONS:
        specialist = reviews.get(dimension)
        if specialist:
            summaries[dimension] = specialist.get(
                "summary", summaries.get(dimension, "")
            )
            evidence_by_dimension[dimension] = specialist.get(
                "evidence", evidence_by_dimension.get(dimension, [])
            )
            warnings_source.extend(specialist.get("warnings", []))
    evidence: list[dict[str, str]] = []
    seen_quotes: set[str] = set()
    for dimension in DIMENSIONS:
        for item in evidence_by_dimension.get(dimension, []):
            quote = item.get("quote")
            if quote and quote in draft and quote not in seen_quotes:
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
        raise QualityGateError("候选审查结论缺少三条不重复正文证据")
    score = scorecard["weighted_score"]
    return {
        "chapter_number": number,
        "verdict": "PASS",
        "grade": "A" if score >= 90 else "B",
        "p1_failures": [],
        "p2_warnings": [
            item.get("explanation", str(item))
            for item in warnings_source
        ],
        "evidence": evidence,
        "character_assessment": summaries.get("character", "专项审查通过"),
        "canon_assessment": summaries.get("continuity", "连续性审查通过"),
        "style_assessment": "；".join(
            filter(None, [summaries.get("craft"), summaries.get("anti_slop")])
        ),
        "revision_instructions": [],
    }


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
        floors = load_config(project_root).get("workflow_policy", {}).get(
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
            semantic_review = _semantic_from_candidate(
                number, draft, card, reviews
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
