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

from .artifacts import ChapterWorkspace
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
from .evaluation import DIMENSIONS, eligible
from .evolution import EvolutionResult, QualityEvolutionEngine
from .memory import MemoryStore, memory_record
from .model_calls import ModelCallExecutor
from .progress import ProgressSink
from .model_router import StageModelRouter
from .provider import ModelProvider, provider_from_config
from .quality import Issue, scan_draft, validate_plan, validate_review
from .run_manifest import RunManifest, fingerprint
from .state_store import load_state, validate_state


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


def _new_state_after_chapter(
    state: dict[str, Any], plan: dict[str, Any], review: dict[str, Any]
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
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
    changes = plan.get("state_changes", {})
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
    updated["chapter_summaries"]["summaries"][str(number)] = {
        "title": plan["title"],
        "core": plan["chapter_purpose"][:120],
        "irreversible_changes": _material_change_keys(changes, state),
    }
    updated["recent_hooks"].append({"chapter": number, **plan["hook"]})
    updated["recent_hooks"] = updated["recent_hooks"][-5:]
    foreshadows = plan.get("foreshadow_operations", {})
    for item in foreshadows.get("plant", []):
        item_id = _foreshadow_id(item)
        updated["active_foreshadows"][item_id] = {"status": "已播种", "plant_chapter": number}
    for item in foreshadows.get("recover", []):
        item_id = _foreshadow_id(item)
        if item_id in updated["active_foreshadows"]:
            updated["active_foreshadows"][item_id].update({"status": "已回收", "recover_chapter": number})
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


def _chapter_meta(plan: dict[str, Any], static_review: dict[str, Any], review: dict[str, Any]) -> str:
    changes = "\n".join(f"- {key}: {value}" for key, value in plan["state_changes"].items())
    evidence = "\n".join(f"- {item['finding']}：{item['quote']}" for item in review["evidence"])
    return (
        f"# 第{plan['chapter_number']}章元数据\n\n"
        f"- 标题: {plan['title']}\n"
        f"- 事件: {plan['event_id']}\n"
        f"- 阶段: {plan['phase']}\n"
        f"- 审查: {review['verdict']} / {review['grade']}\n\n"
        f"## 不可逆变化\n{changes}\n\n"
        f"## 审查证据\n{evidence}\n\n"
        f"## 静态指标\n```json\n{json.dumps(static_review['metrics'], ensure_ascii=False, indent=2)}\n```\n\n"
        f"## Memory Record\n```json\n{json.dumps(memory_record(plan), ensure_ascii=False, indent=2)}\n```\n"
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
        if not isinstance(card, dict) or not eligible(card, floors):
            raise QualityGateError(f"{identifier}未通过全部候选硬门禁，不能人工接受")
        prefix = f"candidates/{identifier}"
        draft = workspace.read_text(f"{prefix}/draft.txt")
        static = workspace.read_json(f"{prefix}/static_review.json")
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
        workspace.write_text("draft.txt", draft)
        workspace.write_json("static_review.json", static)
        workspace.write_json("semantic_review.json", semantic_review)
    try:
        draft = (workspace_path / "draft.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取待接受正文: {exc}") from exc
    planner_context = build_planner_context(project_root, state)
    allowed_canon_ids = {fact["id"] for fact in planner_context["canon_facts"]}
    require_no_p1(validate_plan(plan, state, allowed_canon_ids), "待接受规划")
    recent_limit = load_config(project_root).get("quality_gates", {}).get(
        "recent_chapters_for_repetition", 5
    )
    recent = recent_chapters(project_root, state, recent_limit)
    static_review = scan_draft(draft, recent, planner_context["era_bans"], plan)
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
    promote_atomically(
        project_root,
        state,
        plan,
        draft,
        static_review,
        semantic_review,
        workspace,
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
) -> None:
    number = plan["chapter_number"]
    volume = state["chapter"]["current_volume"]
    chapter_target = project_root / f"novel/chapters/vol_{volume:02d}/chapter_{number:03d}.txt"
    meta_target = project_root / f"novel/chapters/meta/chapter_{number:03d}.md"
    state_target = project_root / "novel/state/current.json"
    if chapter_target.exists() or meta_target.exists():
        raise AtomicWriteError(f"拒绝覆盖已存在的正式第{number}章；请先归档或使用专用重整流程")
    new_state = _new_state_after_chapter(state, plan, semantic_review)
    payloads = {
        chapter_target: (draft.rstrip() + "\n").encode("utf-8"),
        meta_target: _chapter_meta(plan, static_review, semantic_review).encode("utf-8"),
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
        memory.record_promoted_chapter(number, chapter_target, plan)
        findings: list[dict[str, Any]] = []
        if workspace.exists("decision.json"):
            decision = workspace.read_json("decision.json")
            selected = decision.get("selected_id", "")
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
        workspace = ChapterWorkspace.create(
            self.project_root / "novel/work", number, attempt
        )
        resuming_workspace = resume and workspace.exists("run_manifest.json")
        call_limit = 3 if mode == "fast" else self.config.get(
            "quality_evolution", {}
        ).get("call_limit", 10)
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
                require_no_p1(validate_plan(plan, state, allowed_canon_ids), "规划")
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
                "resume_warning": "--resume只恢复现有预算，不会把上限扩展到第11次",
                "exhausted_stage": stage if status == "BUDGET_EXHAUSTED" else None,
            }
            workspace.write_json("decision.json", decision)
            manifest.set_status(status, valid_candidates=0, waiting_reason=str(exc))
            return PipelineResult(number, workspace.path, False, None, None, status)

        if engine_result.draft is None or engine_result.static_review is None:
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
            manifest.set_status("WAITING_USER", valid_candidates=manifest.data["valid_candidates"], waiting_reason=str(exc))
            return PipelineResult(
                number,
                workspace.path,
                False,
                engine_result.static_review,
                None,
                "WAITING_USER",
            )
        promoted = engine_result.status == "AUTO_PROMOTE" and not dry_run
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
                    require_no_p1(
                        validate_plan(plan, state, allowed_canon_ids), "规划"
                    )
                    saved_plan = copy.deepcopy(plan)
                else:
                    plan = copy.deepcopy(saved_plan)
                workspace.write_json("plan.json", plan)

                writer_packet = build_writer_packet(
                    state, plan, recent, planner_context, revision_context
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
                draft = self.provider.generate(draft_prompt, None)
                workspace.write_text("draft.txt", draft)

                static_review = scan_draft(
                    draft, recent, planner_context["era_bans"], plan
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
