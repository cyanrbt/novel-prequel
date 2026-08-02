from __future__ import annotations

import json
import re
from difflib import unified_diff
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError


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


def build_planner_context(
    project_root: Path,
    state: dict[str, Any],
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = _read_json(project_root / "novel/knowledge/canon_registry.json")
    event_id = state["chapter"]["current_event"]
    event_path = project_root / "novel/plots" / f"{event_id}.md"
    if not event_path.exists():
        raise ArtifactValidationError(f"当前事件大纲不存在: {event_path}")
    year = state["timeline"]["current_year"]
    era_key = next(
        (key for key in registry.get("era_bans", {}) if int(key.split("-")[0]) <= year <= int(key.split("-")[1])),
        None,
    )
    era_bans = registry.get("era_bans", {}).get(era_key, {"characters": [], "terms": []})
    facts = [
        {
            "id": fact["id"],
            "level": fact["level"],
            "claim": fact["claim"],
            "allowed_use": fact["allowed_use"],
            "forbidden_overclaim": fact["forbidden_overclaim"],
        }
        for fact in registry.get("facts", [])
        if fact.get("id") in {"CANON-RULE-001", "CANON-RULE-002", "PREQUEL-EVENT-001"}
    ]
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
        "canon_facts": facts,
        "era_bans": {
            "characters": era_bans.get("characters", []),
            "terms": era_bans.get("terms", []),
            "reason": era_bans.get("reason", ""),
        },
    }
    if memory_context is not None:
        result["memory_context"] = memory_context
    return result


DEFAULT_CHARACTER_VOICES = {
    "张洞": "短句、少解释；只陈述观察、风险和决定，不用完整理论替代试错",
    "张家叔公": "少用疑问句；用整理、修补或收起物件结束谈话；目标是让张洞退出旧仪式",
    "张洞父亲": "句子相对完整，会追问证据；目标是打断家族以人填棺的循环",
    "张洞母亲": "关注粮食、债务、船费、名声与活人的去处；不承担神秘导师功能",
    "李二": "话多，习惯用俗例判断危险，容易把临时经验当成保命法",
}


CANDIDATE_FOCUSES = (
    {
        "name": "causal_tension",
        "instruction": "优先强化场景因果和规则试错；每次异常升级都必须改变人物下一步选择。",
    },
    {
        "name": "character_pressure",
        "instruction": "优先强化人物利益、关系压力和现实代价；异常必须落到具体生活选择。",
    },
    {
        "name": "atmospheric_precision",
        "instruction": "优先强化克制的感官证据、空间变化和节奏；避免解释未知与模板化惊吓。",
    },
)


FOCUS_PAIRS = (
    ("causal_tension", "character_pressure"),
    ("causal_tension", "atmospheric_precision"),
    ("character_pressure", "atmospheric_precision"),
)


def select_candidate_focuses(
    plan: dict[str, Any], chapter_number: int
) -> tuple[dict[str, str], dict[str, str]]:
    rendered = json.dumps(
        {
            key: plan.get(key)
            for key in (
                "chapter_purpose",
                "phase",
                "scenes",
                "rule_hypotheses",
                "new_information",
                "hook",
            )
        },
        ensure_ascii=False,
    )
    groups = {
        "causal_tension": ("调查", "规则", "证据", "试错", "异常", "因果", "线索"),
        "character_pressure": ("关系", "人物", "争执", "对话", "利益", "亲缘", "选择"),
        "atmospheric_precision": ("空间", "声音", "气味", "黑暗", "夜", "门", "压迫", "恐惧"),
    }
    scores = {
        name: sum(rendered.count(keyword) for keyword in keywords)
        for name, keywords in groups.items()
    }
    ordered = sorted(scores, key=lambda name: (-scores[name], name))
    if scores[ordered[1]] == 0 or scores[ordered[0]] == scores[ordered[2]]:
        selected_names = FOCUS_PAIRS[(chapter_number - 1) % len(FOCUS_PAIRS)]
        reason = "chapter_rotation"
    else:
        selected_names = (ordered[0], ordered[1])
        reason = "plan_keywords"
    by_name = {item["name"]: item for item in CANDIDATE_FOCUSES}
    return tuple(
        {**by_name[name], "selection_reason": reason} for name in selected_names
    )  # type: ignore[return-value]


def _bounded_text(value: Any, limit: int) -> Any:
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[:limit]


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
        "canon_facts": planner_context.get("canon_facts", []),
        "era_bans": planner_context.get("era_bans", {}),
        "event_outline": planner_context.get("event_outline", ""),
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
) -> dict[str, Any]:
    context = planner_context or {}
    canon_claims = [
        {k: fact[k] for k in ("id", "level", "claim", "allowed_use", "forbidden_overclaim") if k in fact}
        for fact in context.get("canon_facts", [])
        if fact.get("id") in set(plan.get("canon_evidence_ids", []))
    ]
    packet = {
        "plan": plan,
        "continuity": {
            "timeline": state["timeline"],
            "protagonist": state["protagonist"],
            "active_characters": state["characters"].get("active", {}),
            "confirmed_lore": state["world_lore"].get("confirmed", []),
            "hypotheses": state["world_lore"].get("hypotheses", []),
        },
        "canon_claims": canon_claims,
        "era_bans": context.get("era_bans", {"characters": [], "terms": []}),
        "character_voices": DEFAULT_CHARACTER_VOICES,
        "style_principles": [
            "冷静记录异常事实，不替读者命名恐惧",
            "规则必须经观察、假说、试错、后果和临时结论显现",
            "使用具体生活代价，不用抽象悲伤动作模板",
            "每个场景必须改变选择、关系、规则假说或生存条件",
            "不复制原著标志性句式、段落或对白",
        ],
        "recent_repetition_signatures": _recent_signatures(recent_texts),
    }
    if context.get("memory_context"):
        packet["quality_memory"] = {
            "archive": context["memory_context"].get("archive", []),
            "lessons": context["memory_context"].get("lessons", [])[:8],
            "debts": context["memory_context"].get("debts", []),
        }
    if revision_context:
        packet["revision_context"] = revision_context
    return packet


def build_candidate_packet(
    state: dict[str, Any],
    plan: dict[str, Any],
    recent_texts: list[str],
    planner_context: dict[str, Any],
    candidate_index: int,
) -> dict[str, Any]:
    if candidate_index < 0 or candidate_index >= 2:
        raise ArtifactValidationError("候选创作焦点索引无效")
    packet = build_writer_packet(state, plan, recent_texts, planner_context)
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
        "plan": plan,
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
        "plan": plan,
        "revised_draft": revised_draft,
        "diff": diff,
        "target_issues": target_issues,
        "continuity_anchors": {
            "timeline": state["timeline"],
            "protagonist": state["protagonist"],
            "canon_facts": planner_context.get("canon_facts", []),
            "era_bans": planner_context.get("era_bans", {}),
        },
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
        "plan": plan,
        "draft": draft,
        "static_review": static_review,
        "continuity_before": state,
        "canon_facts": planner_context.get("canon_facts", []),
        "era_bans": planner_context.get("era_bans", {}),
    }
