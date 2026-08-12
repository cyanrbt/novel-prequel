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
from .model_router import StageModelRouter
from .provider import ModelProvider, provider_from_config
from .quality import Issue, scan_draft, validate_plan, validate_review
from .reader_review import (
    build_blind_reader_packet,
    build_blind_reader_prompt,
    validate_blind_reader_review,
)
from .run_manifest import RunManifest, fingerprint
from .state_store import load_state, validate_state
from .state_settlement import (
    build_state_settlement_packet,
    build_state_settlement_prompt,
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
    path = project_root / "config/prequel_config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"配置文件无效: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError("配置根节点必须是object")
    return value


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
        (project_root / "novel/chapters").glob("vol_*/chapter_*.txt"),
        key=lambda path: int(re.search(r"chapter_(\d+)", path.name).group(1)),
    )


def formal_chapter_numbers(project_root: Path) -> list[int]:
    return [int(re.search(r"chapter_(\d+)", path.name).group(1)) for path in formal_chapter_paths(project_root)]


def run_preflight(
    project_root: Path,
    state: dict[str, Any] | None = None,
    *,
    check_cli_capabilities: bool = False,
) -> list[str]:
    checks: list[str] = []
    state = state or load_state(project_root / "novel/state/current.json")
    errors = validate_state(state)
    if errors:
        raise QualityGateError("状态预检失败: " + "；".join(errors))
    checks.append("state schema validated")

    config = load_config(project_root)
    router = StageModelRouter.from_config(config, project_root)
    checks.append("model provider and stage routes configured")
    if check_cli_capabilities:
        from .cli_capabilities import (
            bundled_model_catalog,
            codex_version,
            validate_requested_routes,
        )

        command = config.get("provider", {}).get("command", ["codex"])
        executable = command[0] if isinstance(command, list) and command else "codex"
        catalog = bundled_model_catalog(executable)
        requested = {
            stage: (
                router.settings_for(stage).model,
                router.settings_for(stage).reasoning_effort,
            )
            for stage in sorted(config.get("stage_routes", {}))
        }
        errors = validate_requested_routes(catalog, requested)
        if errors:
            raise QualityGateError("Codex模型能力预检失败: " + "；".join(errors))
        checks.append(f"Codex CLI: {codex_version(executable)}")
        checks.extend(
            f"route {stage}: {model}/{effort}"
            for stage, (model, effort) in requested.items()
        )
        checks.append("Codex model and reasoning capabilities validated")

    if "quality_evolution" in config:
        for filename, field in (
            ("memory_index.json", "entries"),
            ("quality_lessons.json", "lessons"),
            ("creative_debts.json", "debts"),
        ):
            path = project_root / "novel/knowledge" / filename
            try:
                store = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualityGateError(f"长期记忆文件无效 {filename}: {exc}") from exc
            if not isinstance(store, dict) or not isinstance(store.get(field), list):
                raise QualityGateError(f"长期记忆文件缺少数组 {field}: {filename}")
        checks.append("long-book memory stores validated")

    registry_path = project_root / "novel/knowledge/canon_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if set(registry.get("confidence_levels", {})) != {"A", "B", "C"}:
        raise QualityGateError("canon registry缺少A/B/C三级")
    checks.append("canon registry and era bans loaded")

    architecture_path = project_root / "novel/plots/series_architecture.md"
    arc_registry_path = project_root / "novel/knowledge/arc_registry.json"
    foreshadow_registry_path = project_root / "novel/knowledge/foreshadow_registry.json"
    if not architecture_path.exists():
        raise QualityGateError("总架构文件不存在: novel/plots/series_architecture.md")
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

    event_path = project_root / "novel/plots" / f"{state['chapter']['current_event']}.md"
    if not event_path.exists():
        raise QualityGateError(f"当前事件大纲不存在: {event_path}")
    checks.append("event outline exists")

    numbers = formal_chapter_numbers(project_root)
    expected = list(range(1, state["chapter"]["last_chapter"] + 1))
    if numbers != expected:
        raise QualityGateError(f"正式章节与状态不一致: 文件{numbers}，状态应为{expected}")
    label = f"1-{numbers[-1]}" if numbers else "empty baseline"
    checks.append(f"formal chapters contiguous: {label}")
    checks.append(f"next chapter: {state['chapter']['next_chapter']}")
    return checks


def recent_chapters(project_root: Path, state: dict[str, Any], limit: int = 5) -> list[str]:
    paths = formal_chapter_paths(project_root)
    return [path.read_text(encoding="utf-8") for path in paths[-limit:]]


def _agent_prompt(project_root: Path, agent: str, packet: dict[str, Any], instruction: str) -> str:
    agent_file = project_root / "agents" / f"{agent}.md"
    try:
        role = agent_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取{agent}指令: {exc}") from exc
    return (
        role.rstrip()
        + "\n\n# 本次任务\n"
        + instruction
        + "\n\n# 唯一输入工件\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )


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
    run_preflight(project_root)
    paths = formal_chapter_paths(project_root)
    target = project_root / "novel/full_novel.txt"
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
    state = load_state(project_root / "novel/state/current.json")
    run_preflight(project_root, state)
    number = state["chapter"]["next_chapter"]
    chapter_work = project_root / "novel/work" / f"chapter_{number:03d}"
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
    if candidate is not None:
        identifier = f"candidate_{candidate:02d}"
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
    )
    workspace.write_json("static_review.json", static_review)
    if not static_review["passed"]:
        hard_failures = [
            Issue(**item)
            for item in static_review["issues"]
            if item["severity"] == "P1"
        ]
        raise QualityGateError(
            "待接受正文重新检查未通过: " + _format_issues(hard_failures)
        )
    require_no_p1(
        validate_review(
            semantic_review,
            static_review,
            expected_chapter=number,
            draft=draft,
        ),
        "待接受审查",
    )
    acceptance_config = load_config(project_root)
    reader_gate = acceptance_config.get("quality_gates", {}).get(
        "blind_reader_gate", {}
    )
    router: StageModelRouter | None = None
    if reader_gate.get("enabled", False):
        reader_review: dict[str, Any] | None = None
        existing_reader_path = workspace_path / "reader_review.json"
        if existing_reader_path.exists():
            try:
                existing_reader = json.loads(existing_reader_path.read_text(encoding="utf-8"))
                canonicalize_artifact_quotes(existing_reader, draft)
                existing_issues = validate_blind_reader_review(existing_reader, draft, number)
                if (
                    not any(issue.severity == "P1" for issue in existing_issues)
                    and existing_reader.get("verdict") == "PASS"
                ):
                    reader_review = existing_reader
            except (OSError, json.JSONDecodeError):
                reader_review = None
        if reader_review is None:
            # A targeted edit changes the draft hash, so reports for earlier
            # text fail validation and a fresh blind read is mandatory.
            router = StageModelRouter.from_config(acceptance_config, project_root)
            raw = router.provider_for("blind_reader_reviewer").generate(
                build_blind_reader_prompt(
                    project_root,
                    build_blind_reader_packet(state, number, draft, project_root),
                ),
                project_root / "schemas/reader_review.schema.json",
            )
            try:
                reader_review = parse_json_artifact(raw, "accept-blind-reader")
                canonicalize_artifact_quotes(reader_review, draft)
                workspace.write_json("reader_review.json", reader_review)
            except ArtifactValidationError:
                workspace.write_raw_text("reader_review.invalid.txt", raw)
                raise
        else:
            workspace.write_json("reader_review.json", reader_review)
        require_no_p1(
            validate_blind_reader_review(reader_review, draft, number),
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
                )
                if (
                    not any(issue.severity == "P1" for issue in settlement_issues)
                    and existing_settlement.get("verdict") == "PASS"
                ):
                    state_settlement = existing_settlement
            except (OSError, json.JSONDecodeError):
                state_settlement = None
        if state_settlement is None:
            router = router or StageModelRouter.from_config(
                acceptance_config, project_root
            )
            settlement_raw = router.provider_for("state_settler").generate(
                build_state_settlement_prompt(
                    project_root,
                    build_state_settlement_packet(
                        state,
                        plan,
                        draft,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    ),
                ),
                project_root / "schemas/state_settlement.schema.json",
            )
            try:
                state_settlement = parse_json_artifact(
                    settlement_raw, "accept-state-settlement"
                )
                canonicalize_artifact_quotes(state_settlement, draft)
                canonicalize_missing_change_paths(
                    state_settlement,
                    expected_state_changes(
                        state,
                        plan,
                        planner_context.get("foreshadow_registry"),
                        planner_context.get("arc_registry"),
                    ),
                )
                workspace.write_json("state_settlement.json", state_settlement)
            except ArtifactValidationError:
                workspace.write_raw_text(
                    "state_settlement.invalid.txt", settlement_raw
                )
                raise
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
    chapter_target = project_root / f"novel/chapters/vol_{volume:02d}/chapter_{number:03d}.txt"
    meta_target = project_root / f"novel/chapters/meta/chapter_{number:03d}.md"
    state_target = project_root / "novel/state/current.json"
    if chapter_target.exists() or meta_target.exists():
        raise AtomicWriteError(f"拒绝覆盖已存在的正式第{number}章；请先归档或使用专用重整流程")
    new_state = _new_state_after_chapter(
        state,
        plan,
        semantic_review,
        settlement,
        load_config(project_root),
    )
    payloads = {
        chapter_target: (draft.rstrip() + "\n").encode("utf-8"),
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
        self._provider_injected = providers is not None or provider is not None
        if providers is not None:
            self.router = providers
            self.provider = providers.provider_for("planner")
        elif provider is not None:
            self.provider = provider
            self.router = StageModelRouter.single(provider)
        else:
            self.provider = provider_from_config(self.config, self.project_root)
            self.router = StageModelRouter.from_config(self.config, self.project_root)

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
        chapter_work = self.project_root / "novel/work" / f"chapter_{number:03d}"
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
        state = load_state(self.project_root / "novel/state/current.json")
        run_preflight(
            self.project_root,
            state,
            check_cli_capabilities=not self._provider_injected,
        )
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
            self.project_root / "novel/work", number, attempt
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
            require_no_p1(
                validate_blind_reader_review(reader_review, engine_result.draft, number),
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
        state = load_state(self.project_root / "novel/state/current.json")
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
                self.project_root / "novel/work", number, attempt
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
