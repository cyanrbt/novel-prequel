from __future__ import annotations

import json
import re
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


def build_planner_context(project_root: Path, state: dict[str, Any]) -> dict[str, Any]:
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
    return {
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


DEFAULT_CHARACTER_VOICES = {
    "张洞": "短句、少解释；只陈述观察、风险和决定，不用完整理论替代试错",
    "张家叔公": "少用疑问句；用整理、修补或收起物件结束谈话；目标是让张洞退出旧仪式",
    "张洞父亲": "句子相对完整，会追问证据；目标是打断家族以人填棺的循环",
    "张洞母亲": "关注粮食、债务、船费、名声与活人的去处；不承担神秘导师功能",
    "李二": "话多，习惯用俗例判断危险，容易把临时经验当成保命法",
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
    if revision_context:
        packet["revision_context"] = revision_context
    return packet


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
