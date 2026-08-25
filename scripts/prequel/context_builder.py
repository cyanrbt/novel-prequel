from __future__ import annotations

import json
import hashlib
import re
from difflib import unified_diff
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .project import load_project_spec, project_path
from .taste_contract import load_taste_contract


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"JSON根节点必须是object: {path}")
    return value


def _recent_signatures(texts: list[str]) -> dict[str, Any]:
    endings: list[str] = []
    frequent_actions: dict[str, int] = {}
    action_patterns = {
        "转身离开": r"转身.{0,8}(?:走|离开)",
        "停步不回头": r"停下来.{0,8}没有回头",
        "折纸入怀": r"纸折好.{0,8}塞进怀里",
        "守灯到天亮": r"看着.{0,8}(?:油灯|火苗).{0,20}天亮",
        "渡口扔石": r"(?:渡口|江边).{0,40}扔.{0,8}石",
    }
    for text in texts:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if paragraphs:
            endings.append(paragraphs[-1][-120:])
        for name, pattern in action_patterns.items():
            frequent_actions[name] = frequent_actions.get(name, 0) + len(re.findall(pattern, text))
    return {"recent_endings": endings, "action_counts": frequent_actions}


def _load_chapter_blueprint(
    project_root: Path, event_id: str, chapter_number: int
) -> str:
    """Load the active card from the story-configured opening outline."""
    config = load_project_spec(project_root).load_config()
    policy = config.get("context_policy", {}).get("opening_blueprint", {})
    if not isinstance(policy, dict) or not policy:
        return ""
    if (
        event_id != policy.get("event_id")
        or not int(policy.get("first_chapter", 1))
        <= chapter_number
        <= int(policy.get("last_chapter", 0))
    ):
        return ""
    path = project_path(project_root, "opening_blueprint")
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    first_chapter = re.search(r"(?m)^## 第1章", text)
    intro = text[: first_chapter.start()].strip() if first_chapter else ""
    current = re.search(
        rf"(?ms)^## 第{chapter_number}章.*?(?=^## 第\d+章|^## 前16章结算边界|\Z)",
        text,
    )
    redlines = re.search(r"(?ms)^## 写作执行红线.*\Z", text)
    parts = [intro]
    if current:
        parts.append(current.group(0).strip())
    if redlines:
        parts.append(redlines.group(0).strip())
    return "\n\n".join(part for part in parts if part)


def build_planner_context(
    project_root: Path,
    state: dict[str, Any],
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    spec = load_project_spec(project_root)
    config = spec.load_config()
    registry = _read_json(spec.path("canon_registry"))
    event_id = state["chapter"]["current_event"]
    event_path = spec.path("plots_dir") / f"{event_id}.md"
    if not event_path.exists():
        raise ArtifactValidationError(f"当前事件大纲不存在: {event_path}")
    chapter_blueprint = _load_chapter_blueprint(
        project_root, event_id, state["chapter"]["next_chapter"]
    )
    year = state["timeline"]["current_year"]
    era_key = next(
        (key for key in registry.get("era_bans", {}) if int(key.split("-")[0]) <= year <= int(key.split("-")[1])),
        None,
    )
    era_bans = registry.get("era_bans", {}).get(era_key, {"characters": [], "terms": []})
    selected_fact_ids = set(
        config.get("context_policy", {}).get("planner_canon_fact_ids", [])
    )
    facts = [
        {
            "id": fact["id"],
            "level": fact["level"],
            "claim": fact["claim"],
            "allowed_use": fact["allowed_use"],
            "forbidden_overclaim": fact["forbidden_overclaim"],
        }
        for fact in registry.get("facts", [])
        if fact.get("id") in selected_fact_ids
    ]
    architecture_path = spec.path("series_architecture")
    registry_path = spec.path("arc_registry")
    foreshadow_registry_path = spec.path("foreshadow_registry")
    architecture = architecture_path.read_text(encoding="utf-8") if architecture_path.exists() else ""
    arc_registry = _read_json(registry_path) if registry_path.exists() else {"milestones": {}}
    foreshadow_registry = (
        _read_json(foreshadow_registry_path)
        if foreshadow_registry_path.exists()
        else {"entries": {}}
    )
    user_taste_contract = load_taste_contract(project_root)
    result = {
        "chapter": state["chapter"],
        "timeline": state["timeline"],
        "protagonist": state["protagonist"],
        "characters": state["characters"],
        "world_lore": state["world_lore"],
        "active_foreshadows": state["active_foreshadows"],
        "recent_hooks": state["recent_hooks"][-5:],
        "recent_summaries": dict(list(state["chapter_summaries"]["summaries"].items())[-5:]),
        "event_outline": event_path.read_text(encoding="utf-8"),
        "chapter_blueprint": chapter_blueprint,
        "series_architecture": architecture,
        "arc_registry": arc_registry,
        "foreshadow_registry": foreshadow_registry,
        "completed_milestones": state.get("completed_milestones", []),
        "canon_facts": facts,
        "era_bans": {
            "characters": era_bans.get("characters", []),
            "terms": era_bans.get("terms", []),
            "reason": era_bans.get("reason", ""),
        },
        "user_taste_contract": user_taste_contract,
    }
    if memory_context is not None:
        result["memory_context"] = memory_context
    return result


def _character_voice_fallbacks(project_root: Path) -> dict[str, str]:
    path = project_path(project_root, "character_voice_fallbacks")
    if not path.is_file():
        return {}
    payload = _read_json(path)
    voices = payload.get("voices", {})
    if not isinstance(voices, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in voices.items()
    ):
        raise ArtifactValidationError(f"人物运行声纹格式无效: {path}")
    return dict(voices)


CANDIDATE_FOCUSES = (
    {
        "name": "causal_tension",
        "instruction": "优先强化场景因果和规则试错；每次异常升级都必须改变人物下一步选择。",
    },
    {
        "name": "character_pressure",
        "instruction": "优先让读者先喜欢、心疼或理解一个活人，再让异常伤到这段关系；主角的欲望与缺点必须同时推动选择。",
    },
    {
        "name": "atmospheric_precision",
        "instruction": "优先强化克制的感官证据、空间变化和节奏；避免解释未知与模板化惊吓。",
    },
    {
        "name": "serial_compulsion",
        "instruction": "优先强化主动威胁、揭示变形和情绪余震；结尾要让读者惦记活人的命运，而不只是知道人物下一项调查任务。",
    },
    {
        "name": "lived_voice",
        "instruction": "优先保留人物有限认知造成的声纹、跑题、改口、局部重复和暂时不回收的生活细节；多人发言不能连续压成‘有人说—另一个问—还有人答’的转述清单，关键声音直接呈现，重复噪声才概括为争论走势或可见反应；不要让每句话都显露作者用途，也不得用随机错字、无动机矛盾或大段填充伪造现场感。",
    },
)


FOCUS_PAIRS = (
    ("serial_compulsion", "lived_voice"),
    ("character_pressure", "lived_voice"),
    ("causal_tension", "lived_voice"),
    ("atmospheric_precision", "lived_voice"),
)


def select_candidate_focuses(
    plan: dict[str, Any], chapter_number: int
) -> tuple[dict[str, str], dict[str, str]]:
    rendered = json.dumps(
        {
            key: plan.get(key)
            for key in (
                "chapter_purpose",
                "reader_investment",
                "dramatic_spine",
                "phase",
                "scenes",
                "rule_hypotheses",
                "new_information",
                "hook",
            )
        },
        ensure_ascii=False,
    )
    structural_groups = {
        "causal_tension": ("调查", "规则", "证据", "试错", "异常", "因果", "线索"),
        "character_pressure": ("关系", "人物", "争执", "对话", "利益", "亲缘", "选择"),
        "atmospheric_precision": ("空间", "声音", "气味", "黑暗", "夜", "门", "压迫", "恐惧"),
        "serial_compulsion": ("选择", "代价", "问题", "立刻", "失去", "风险", "不得不", "下一步"),
    }
    scores = {
        name: sum(rendered.count(keyword) for keyword in keywords)
        for name, keywords in structural_groups.items()
    }
    ordered = sorted(scores, key=lambda name: (-scores[name], name))
    if scores[ordered[0]] == 0 or scores[ordered[0]] == scores[ordered[1]]:
        selected_names = FOCUS_PAIRS[(chapter_number - 1) % len(FOCUS_PAIRS)]
        reason = "chapter_rotation"
    else:
        selected_names = (ordered[0], "lived_voice")
        reason = "plan_keywords"
    by_name = {item["name"]: item for item in CANDIDATE_FOCUSES}
    return tuple(
        {**by_name[name], "selection_reason": reason} for name in selected_names
    )  # type: ignore[return-value]


def _bounded_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit]


WRITER_SCENE_FIELDS = (
    "location",
    "characters",
    "goal",
    "conflict",
    "irreversible_change",
    "human_turn",
)


def _build_writer_narrative_engine(investment: dict[str, Any]) -> dict[str, Any]:
    """Expose story intent without handing the Writer the full beat solution."""

    engine = {
        key: investment.get(key)
        for key in (
            "attachment_anchor",
            "protagonist_contradiction",
            "threat_in_motion",
            "emotional_afterimage",
        )
        if investment.get(key)
    }
    nested_fields = {
        "core_threat_continuation": (
            "prior_hook",
            "current_effect",
            "local_answer",
            "forced_change",
        ),
        "revelation_shift": (
            "from",
            "on_page_answer",
            "to",
            "changes",
            "executed_change",
        ),
    }
    for section, fields in nested_fields.items():
        source = investment.get(section)
        if not isinstance(source, dict):
            continue
        reduced = {key: source.get(key) for key in fields if source.get(key)}
        if reduced:
            engine[section] = reduced
    return engine


def build_story_brief(plan: dict[str, Any]) -> dict[str, Any]:
    """Create the intent-level brief shown to the Writer.

    Discovery path, remaining explanations and choice reason stay in the plan
    for Planner/auditor files; they are not copied into the Writer packet.
    """
    spine = plan.get("dramatic_spine", {})
    investment = plan.get("reader_investment", {})
    return {
        key: plan.get(key)
        for key in (
            "chapter_number",
            "title",
            "event_id",
            "phase",
            "chapter_purpose",
            "serial_continuity",
            "new_information",
            "rule_hypotheses",
            "hook",
        )
    } | {
        "narrative_engine": _build_writer_narrative_engine(investment),
        "dramatic_contract": {
            key: spine.get(key)
            for key in (
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
                "ending_leverage",
            )
            if spine.get(key)
        },
        "scenes": [
            {key: scene.get(key) for key in WRITER_SCENE_FIELDS}
            for scene in plan.get("scenes", [])
            if isinstance(scene, dict)
        ]
    }


def _ordinary_explanations_remaining(plan: dict[str, Any]) -> list[dict[str, Any]]:
    remaining: list[dict[str, Any]] = []
    for index, scene in enumerate(plan.get("scenes", [])):
        if not isinstance(scene, dict):
            continue
        explanations = scene.get("ordinary_explanations")
        leftover = (
            explanations.get("remaining")
            if isinstance(explanations, dict)
            else None
        )
        if leftover:
            remaining.append({"scene": index + 1, "remaining": leftover})
    return remaining


def _build_hard_constraints(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_number": plan.get("chapter_number"),
        "prohibited_elements": plan.get("prohibited_elements", []),
        "canon_evidence_ids": plan.get("canon_evidence_ids", []),
        "knowledge_boundaries": [
            {
                "scene": index + 1,
                "constraint": scene.get("knowledge_limits", ""),
            }
            for index, scene in enumerate(plan.get("scenes", []))
            if isinstance(scene, dict) and scene.get("knowledge_limits")
        ],
    }


def build_constraint_ledger(plan: dict[str, Any]) -> dict[str, Any]:
    """Give reviewers binding constraints and story targets, not scene choreography."""
    hard_constraints = dict(_build_hard_constraints(plan))
    hard_constraints["ordinary_explanations_remaining"] = (
        _ordinary_explanations_remaining(plan)
    )
    return {
        "contract_version": 2,
        "hard_constraints": hard_constraints,
        "narrative_targets": build_story_brief(plan),
        "state_change_candidates": plan.get("state_changes", []),
        "operations": plan.get("operations", {}),
        "audit_policy": {
            "state_change_candidates_are_provisional": True,
            "coherent_alternatives_are_allowed": True,
            "narrative_target_deviation_default": "warning_or_revision",
            "hard_failure_requires": [
                "正文违反hard_constraints、continuity_before、canon_facts或event_outline",
                "正文内部出现无法由可见动作解释的时间、空间、知识或物件矛盾",
                "章节核心目的或必要不可逆结果完全缺失",
                "正文把仍未排除的普通解释直接宣布为超自然定论",
            ],
            "quote_source": "draft_only",
        },
    }


def _source_entry(
    project_root: Path,
    label: str,
    relative_path: str | Path,
    *,
    limit: int,
) -> tuple[str, dict[str, Any]] | None:
    path = Path(relative_path)
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取权威上下文 {path}: {exc}") from exc
    content = raw[:limit]
    return content, {
        "label": label,
        "path": str(relative_path),
        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "source_chars": len(raw),
        "included_chars": len(content),
        "included": True,
    }


def _plan_characters(state: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    names = {
        name
        for scene in plan.get("scenes", [])
        if isinstance(scene, dict)
        for name in scene.get("characters", [])
        if isinstance(name, str) and name.strip()
    }
    protagonist = state.get("protagonist", {}).get("name")
    if isinstance(protagonist, str) and protagonist.strip():
        names.add(protagonist)
    return sorted(names)


def _materialize_memory_excerpts(
    project_root: Path, memory_context: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    excerpts: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    for item in memory_context.get("archive", [])[:5]:
        if not isinstance(item, dict) or not isinstance(item.get("source_path"), str):
            continue
        source_path = item["source_path"]
        entry = _source_entry(
            project_root,
            f"memory_chapter_{item.get('chapter', 'unknown')}",
            source_path,
            limit=1800,
        )
        if entry is None:
            continue
        content, source_trace = entry
        excerpts.append(
            {
                "chapter": item.get("chapter"),
                "summary": item.get("summary", ""),
                "excerpt": content,
            }
        )
        trace.append(source_trace)
    return excerpts, trace


def build_authoritative_writer_context(
    project_root: Path,
    state: dict[str, Any],
    plan: dict[str, Any],
    recent_texts: list[str],
    planner_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compile only sources that the Writer actually receives, with provenance."""
    sources: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    spec = load_project_spec(project_root)
    configured = {
        "voice_profile": (spec.path("reference_voice_profile"), 7000),
        "setting_whitelist": (spec.path("setting_whitelist"), 7000),
        "setting_blacklist": (spec.path("setting_blacklist"), 7000),
    }
    for label, (relative_path, limit) in configured.items():
        entry = _source_entry(
            project_root, label, relative_path, limit=limit
        )
        if entry is None:
            continue
        sources[label], source_trace = entry
        trace.append(source_trace)

    excluded_sources = {
        "compact_style": (
            spec.path("compact_style"),
            "由精简的正向文风画像替代；完整风格规则保留给审查",
        ),
        "user_taste_contract_duplicate": (
            spec.path("user_taste_contract"),
            "偏好合同已作为结构化顶层工件注入，不重复附带原文",
        ),
        "rulebook": (
            spec.path("rulebook"),
            "当前任务只注入相关正典、事件约束和禁入项；全量规则书保留给审查",
        ),
    }
    for label, (relative_path, reason) in excluded_sources.items():
        path = Path(relative_path)
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        trace.append(
            {
                "label": label,
                "path": str(relative_path),
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "source_chars": len(raw),
                "included_chars": 0,
                "included": False,
                "reason": reason,
            }
        )

    character_cards: dict[str, str] = {}
    voice_fallbacks: dict[str, str] = {}
    configured_voices = _character_voice_fallbacks(project_root)
    protagonist = state.get("protagonist", {}).get("name")
    for name in _plan_characters(state, plan):
        if name == protagonist:
            # protagonist.md spans the whole series and contains late-stage
            # powers/voice anchors.  The live state is the authoritative,
            # milestone-safe profile for the current chapter.
            voice_fallbacks[name] = configured_voices.get(
                name, "只依据当前状态行动，不预演未来能力或人格"
            )
            protagonist_path = spec.path("protagonist_card")
            if protagonist_path.is_file():
                raw = protagonist_path.read_text(encoding="utf-8")
                trace.append(
                    {
                        "label": f"character:{name}",
                        "path": spec.relative_path("protagonist_card"),
                        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                        "source_chars": len(raw),
                        "included_chars": 0,
                        "included": False,
                        "reason": "跨全书人物卡包含未解锁阶段；当前章改用运行状态",
                    }
                )
            continue
        relative_path = spec.path("characters_dir") / f"{name}.md"
        entry = _source_entry(
            project_root, f"character:{name}", relative_path, limit=8000
        )
        if entry is not None:
            character_cards[name], source_trace = entry
            trace.append(source_trace)
        elif name in configured_voices:
            voice_fallbacks[name] = configured_voices[name]
    sources["character_cards"] = character_cards
    sources["character_voice_fallbacks"] = voice_fallbacks
    sources["protagonist_runtime_profile"] = state.get("protagonist", {})
    runtime_profile = json.dumps(
        sources["protagonist_runtime_profile"], ensure_ascii=False, sort_keys=True
    )
    trace.append(
        {
            "label": "protagonist_runtime_profile",
            "path": f"runtime:{spec.relative_path('state')}#protagonist",
            "sha256": hashlib.sha256(runtime_profile.encode("utf-8")).hexdigest(),
            "source_chars": len(runtime_profile),
            "included_chars": len(runtime_profile),
            "included": True,
        }
    )

    recent_prose = [text[-2400:] for text in recent_texts[-3:] if text.strip()]
    sources["recent_prose"] = recent_prose
    if recent_prose:
        trace.append(
            {
                "label": "recent_prose",
                "path": "runtime:recent_promoted_chapters",
                "sha256": hashlib.sha256(
                    "\n\n".join(recent_prose).encode("utf-8")
                ).hexdigest(),
                "source_chars": sum(len(text) for text in recent_texts[-3:]),
                "included_chars": sum(len(text) for text in recent_prose),
                "included": True,
            }
        )

    memory_context = planner_context.get("memory_context", {})
    excerpts, memory_trace = _materialize_memory_excerpts(
        project_root, memory_context if isinstance(memory_context, dict) else {}
    )
    sources["memory_excerpts"] = excerpts
    trace.extend(memory_trace)
    sources["quality_lessons"] = (
        memory_context.get("lessons", [])[:8]
        if isinstance(memory_context, dict)
        else []
    )
    sources["creative_debts"] = (
        memory_context.get("debts", [])
        if isinstance(memory_context, dict)
        else []
    )

    anchors = spec.path("style_anchors")
    if anchors.is_file():
        raw = anchors.read_text(encoding="utf-8")
        trace.append(
            {
                "label": "style_anchors",
                "path": spec.relative_path("style_anchors"),
                "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                "source_chars": len(raw),
                "included_chars": 0,
                "included": False,
                "reason": "未完成人工逐段核准；避免把旧模板句式当作模仿样本",
            }
        )
    return sources, trace


def build_chapter_context_pack(
    state: dict[str, Any],
    planner_context: dict[str, Any],
    recent_texts: list[str],
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    limits = limits or {}
    core = {
        "chapter": state["chapter"],
        "timeline": state["timeline"],
        "protagonist": state["protagonist"],
        "active_characters": state["characters"].get("active", {}),
        "world_lore": state["world_lore"],
        "active_foreshadows": state["active_foreshadows"],
        "foreshadow_registry": planner_context.get("foreshadow_registry", {"entries": {}}),
        "completed_milestones": planner_context.get("completed_milestones", []),
        "canon_facts": planner_context.get("canon_facts", []),
        "era_bans": planner_context.get("era_bans", {}),
        "event_outline": planner_context.get("event_outline", ""),
        "chapter_blueprint": planner_context.get("chapter_blueprint", ""),
    }
    memory = planner_context.get("memory_context", {})
    retrieved = {
        "archive": memory.get("archive", []),
        "lessons": memory.get("lessons", [])[:8],
        "debts": memory.get("debts", []),
    }
    recent_limit = limits.get("recent_chars", 10000)
    recent = [_bounded_text(text, recent_limit) for text in recent_texts[-5:]]
    pack = {"core": core, "retrieved": retrieved, "recent": recent}
    pack["metrics"] = context_metrics(pack)
    return pack


def context_metrics(packet: dict[str, Any]) -> dict[str, int]:
    return {
        key: len(json.dumps(value, ensure_ascii=False, sort_keys=True))
        for key, value in packet.items()
        if key != "metrics"
    }


def build_writer_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    recent_texts: list[str],
    planner_context: dict[str, Any] | None = None,
    revision_context: dict[str, Any] | None = None,
    project_root: Path | None = None,
) -> dict[str, Any]:
    context = planner_context or {}
    canon_claims = [
        {k: fact[k] for k in ("id", "level", "claim", "allowed_use", "forbidden_overclaim") if k in fact}
        for fact in context.get("canon_facts", [])
        if fact.get("id") in set(plan.get("canon_evidence_ids", []))
    ]
    taste_contract = context.get("user_taste_contract")
    if taste_contract is None and project_root is not None:
        taste_contract = load_taste_contract(project_root)
    packet = {
        "story_brief": build_story_brief(plan),
        "hard_constraints": _build_hard_constraints(plan),
        "continuity": {
            "timeline": state["timeline"],
            "protagonist": state["protagonist"],
            "active_characters": state["characters"].get("active", {}),
            "confirmed_lore": state["world_lore"].get("confirmed", []),
            "hypotheses": state["world_lore"].get("hypotheses", []),
        },
        "canon_claims": canon_claims,
        "era_bans": context.get("era_bans", {"characters": [], "terms": []}),
        "event_guardrails": context.get("event_outline", ""),
        "chapter_guardrails": context.get("chapter_blueprint", ""),
        "user_taste_contract": taste_contract or {},
        "recent_repetition_signatures": _recent_signatures(recent_texts),
    }
    if project_root is not None:
        authoritative, trace = build_authoritative_writer_context(
            project_root, state, plan, recent_texts, context
        )
        packet["voice_profile"] = authoritative.pop("voice_profile", "")
        packet["authoritative_context"] = authoritative
        packet["context_trace"] = trace
    else:
        packet["character_voice_fallbacks"] = {}
    if revision_context:
        packet["revision_context"] = revision_context
    return packet


def build_candidate_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    recent_texts: list[str],
    planner_context: dict[str, Any],
    candidate_index: int,
    project_root: Path | None = None,
) -> dict[str, Any]:
    if candidate_index < 0 or candidate_index >= 2:
        raise ArtifactValidationError("候选创作焦点索引无效")
    packet = build_writer_packet(
        state, plan, recent_texts, planner_context, project_root=project_root
    )
    packet["candidate_focus"] = select_candidate_focuses(
        plan, plan["chapter_number"]
    )[candidate_index]
    packet["candidate_constraints"] = {
        "independent_draft": True,
        "do_not_reference_other_candidates": True,
    }
    return packet


def build_integrated_review_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    static_review: dict[str, Any],
    planner_context: dict[str, Any],
) -> dict[str, Any]:
    packet = build_reviewer_packet(
        state, plan, draft, static_review, planner_context
    )
    packet["allowed_fact_ids"] = [
        item["id"]
        for item in planner_context.get("canon_facts", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    packet["review_dimensions"] = list(("continuity", "character", "craft", "anti_slop"))
    packet["evidence_rule"] = "每个quote必须逐字连续存在于draft"
    return packet


def build_specialist_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    static_review: dict[str, Any],
    planner_context: dict[str, Any],
    dimension: str,
) -> dict[str, Any]:
    return {
        **build_reviewer_packet(
            state, plan, draft, static_review, planner_context
        ),
        "dimension": dimension,
        "evidence_rule": "所有 quote 必须逐字出现在 draft 中",
    }


def build_ballot_packet(
    plan: dict[str, Any], draft_a: str, draft_b: str
) -> dict[str, Any]:
    return {
        "story_brief": build_story_brief(plan),
        "candidate_A": draft_a,
        "candidate_B": draft_b,
        "blind_rule": "只使用 A/B 标签，不推测候选来源或创作焦点",
    }


def build_revision_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    recent_texts: list[str],
    planner_context: dict[str, Any],
    previous_draft: str,
    instructions: list[dict[str, Any]],
    project_root: Path | None = None,
) -> dict[str, Any]:
    packet = build_writer_packet(
        state,
        plan,
        recent_texts,
        planner_context,
        {
            "previous_draft": previous_draft,
            "instructions": instructions,
            "source": "consensus_specialist_reviews",
        },
        project_root,
    )
    packet["revision_guardrails"] = {
        "preserve_uncriticized_strengths": True,
        "return_complete_chapter": True,
    }
    return packet


def build_verification_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    planner_context: dict[str, Any],
    previous_draft: str,
    revised_draft: str,
    target_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    diff = "\n".join(
        unified_diff(
            previous_draft.splitlines(),
            revised_draft.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    return {
        "chapter_number": plan["chapter_number"],
        "constraint_ledger": build_constraint_ledger(plan),
        "revised_draft": revised_draft,
        "diff": diff,
        "target_issues": target_issues,
        "continuity_anchors": {
            "timeline": state["timeline"],
            "protagonist": state["protagonist"],
            "canon_facts": planner_context.get("canon_facts", []),
            "era_bans": planner_context.get("era_bans", {}),
        },
        "user_taste_contract": planner_context.get("user_taste_contract", {}),
    }


def build_reviewer_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    draft: str,
    static_review: dict[str, Any],
    planner_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "chapter_number": plan["chapter_number"],
        "draft_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "constraint_ledger": build_constraint_ledger(plan),
        "draft": draft,
        "chapter_blueprint": planner_context.get("chapter_blueprint", ""),
        "static_review": static_review,
        "continuity_before": state,
        "canon_facts": planner_context.get("canon_facts", []),
        "era_bans": planner_context.get("era_bans", {}),
        "event_outline": planner_context.get("event_outline", ""),
        "user_taste_contract": planner_context.get("user_taste_contract", {}),
    }
