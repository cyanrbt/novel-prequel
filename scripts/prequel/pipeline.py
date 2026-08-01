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
from .context_builder import (
    build_planner_context,
    build_reviewer_packet,
    build_writer_packet,
)
from .errors import (
    ArtifactValidationError,
    AtomicWriteError,
    ProviderError,
    QualityGateError,
)
from .provider import ModelProvider, provider_from_config
from .quality import Issue, scan_draft, validate_plan, validate_review
from .state_store import load_state, validate_state


@dataclass(frozen=True)
class PipelineResult:
    chapter_number: int
    workspace: Path
    promoted: bool
    static_review: dict[str, Any]
    semantic_review: dict[str, Any]


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


def run_preflight(project_root: Path, state: dict[str, Any] | None = None) -> list[str]:
    checks: list[str] = []
    state = state or load_state(project_root / "novel/state/current.json")
    errors = validate_state(state)
    if errors:
        raise QualityGateError("状态预检失败: " + "；".join(errors))
    checks.append("state schema validated")

    config = load_config(project_root)
    provider_from_config(config, project_root)
    checks.append("model provider configured")

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
        f"## 静态指标\n```json\n{json.dumps(static_review['metrics'], ensure_ascii=False, indent=2)}\n```\n"
    )


def _write_temp(target: Path, content: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        return Path(handle.name)


def merge_formal_chapters(project_root: Path) -> tuple[Path, int]:
    """Build the reading copy from the exact contiguous formal chapter set."""
    run_preflight(project_root)
    paths = formal_chapter_paths(project_root)
    target = project_root / "novel/full_novel.txt"
    content = "\n\n".join(
        path.read_text(encoding="utf-8").rstrip() for path in paths
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
    for candidate in candidates:
        review_path = candidate / "semantic_review.json"
        if not review_path.exists():
            continue
        try:
            candidate_review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if candidate_review.get("verdict") == "PASS":
            workspace_path = candidate
            semantic_review = candidate_review
            break
    if workspace_path is None or semantic_review is None:
        label = f"attempt_{attempt:02d}" if attempt is not None else "任何尝试"
        raise ArtifactValidationError(f"第{number}章{label}没有可接受的PASS审查")

    workspace = ChapterWorkspace(workspace_path, number)
    plan = workspace.read_json("plan.json")
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
    workspace.write_json("promotion_manifest.json", manifest)


class WritingPipeline:
    def __init__(self, project_root: Path, provider: ModelProvider | None = None):
        self.project_root = project_root.resolve()
        self.config = load_config(self.project_root)
        self.provider = provider or provider_from_config(self.config, self.project_root)

    def run_next(self, *, dry_run: bool = False) -> PipelineResult:
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
