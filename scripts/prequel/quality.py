from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str
    evidence: str


PLAN_REQUIRED = {
    "chapter_number", "title", "event_id", "phase", "chapter_purpose", "dramatic_spine", "scenes",
    "new_information", "state_changes", "rule_hypotheses", "canon_evidence_ids",
    "foreshadow_operations", "milestone_operations", "hook", "prohibited_elements",
    "reader_investment", "serial_continuity",
}

REVIEW_REQUIRED = {
    "chapter_number", "verdict", "grade", "p1_failures", "p2_warnings", "evidence",
    "character_assessment", "canon_assessment", "style_assessment", "revision_instructions",
}

STATE_CHANGE_REQUIRED = {
    "protagonist_known_info_add", "protagonist_inventory_add",
    "protagonist_inventory_remove", "protagonist_location",
    "protagonist_body_updates", "ability_updates", "timeline_year",
    "timeline_elapsed_days", "character_updates", "world_confirmed_add",
    "world_hypotheses_add",
}

SCENE_MODEL_REQUIRED = {
    "initial_state",
    "discovery_path",
    "knowledge_limits",
    "ordinary_explanations",
    "choice_reason",
    "end_state",
    "threat_action",
    "human_turn",
    "payoff_type",
}

READER_INVESTMENT_REQUIRED = {
    "attachment_anchor",
    "protagonist_contradiction",
    "threat_in_motion",
    "core_threat_continuation",
    "revelation_shift",
    "emotional_afterimage",
    "clue_delivery",
}

REVELATION_SHIFT_REQUIRED = {"from", "to", "changes"}
CORE_THREAT_CONTINUATION_REQUIRED = {
    "prior_hook",
    "current_effect",
    "local_answer",
    "old_defense",
    "defense_failure",
    "replacement_rule",
    "forced_change",
    "human_pressure_link",
}
CLUE_DELIVERY_REQUIRED = {"method", "resistance", "coincidence_risk"}
ATTACHMENT_ANCHOR_REQUIRED = {
    "focus",
    "on_page_moment",
    "private_meaning",
    "lived_value",
    "threatened_loss",
    "loss_carrier",
}
EMOTIONAL_AFTERIMAGE_REQUIRED = {
    "person",
    "immediate_wound",
    "material_aftereffect",
    "relationship_aftereffect",
    "unresolved_choice",
    "mystery_subordinate_to",
}
SERIAL_CONTINUITY_REQUIRED = {
    "prior_human_wound",
    "opening_consequence",
    "carried_object_state",
    "pressure_novelty",
}

DRAMATIC_SPINE_REQUIRED = {
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
}

FORBIDDEN_POV = ["他不知道的是", "她不知道的是", "与此同时", "在另一边", "另一边却"]
ACTION_PATTERNS = {
    "停步不回头": r"停下来.{0,10}没有回头",
    "折纸入怀": r"纸折好.{0,10}塞进怀里",
    "守灯到天亮": r"看着.{0,10}(?:油灯|火苗).{0,30}天亮",
    "渡口扔石": r"(?:渡口|江边).{0,60}扔.{0,10}石",
    "眼光变暗": r"眼睛里的光变了.{0,20}更暗",
}

DEFAULT_LENGTH_POLICY = {
    "safe_min": 1,
    "target_min": 3500,
    "target_max": 5000,
    "safe_max": 8000,
}


def _latest_chapter_context(state: dict[str, Any]) -> str:
    """Render only the immediately preceding chapter's durable consequences."""
    last_chapter = state.get("chapter", {}).get("last_chapter")
    parts: list[str] = []
    for hook in state.get("recent_hooks", []):
        if isinstance(hook, dict) and hook.get("chapter") == last_chapter:
            parts.extend(str(hook.get(key, "")) for key in ("type", "content"))
    summaries = state.get("chapter_summaries", {}).get("summaries", {})
    summary = summaries.get(str(last_chapter), {}) if isinstance(summaries, dict) else {}
    if isinstance(summary, dict):
        parts.extend(str(summary.get(key, "")) for key in ("title", "core"))
        changes = summary.get("irreversible_changes", [])
        if isinstance(changes, list):
            parts.extend(str(item) for item in changes)
    return "\n".join(part for part in parts if part)


def _planned_serial_engine(plan: dict[str, Any]) -> str:
    """Render fields that define the new chapter's main pressure and payment."""
    spine = plan.get("dramatic_spine", {})
    investment = plan.get("reader_investment", {})
    if not isinstance(spine, dict):
        spine = {}
    if not isinstance(investment, dict):
        investment = {}
    attachment = investment.get("attachment_anchor", {}) if isinstance(investment, dict) else {}
    afterimage = investment.get("emotional_afterimage", {}) if isinstance(investment, dict) else {}
    delivery = investment.get("clue_delivery", {}) if isinstance(investment, dict) else {}
    parts = [
        spine.get("opening_genre_signal", ""),
        spine.get("destabilizing_event", ""),
        spine.get("protagonist_choice", ""),
        spine.get("choice_cost", ""),
        spine.get("cost_realization", ""),
        investment.get("threat_in_motion", "") if isinstance(investment, dict) else "",
        attachment.get("focus", "") if isinstance(attachment, dict) else "",
        attachment.get("on_page_moment", "") if isinstance(attachment, dict) else "",
        attachment.get("private_meaning", "") if isinstance(attachment, dict) else "",
        attachment.get("lived_value", "") if isinstance(attachment, dict) else "",
        attachment.get("threatened_loss", "") if isinstance(attachment, dict) else "",
        attachment.get("loss_carrier", "") if isinstance(attachment, dict) else "",
        afterimage.get("immediate_wound", "") if isinstance(afterimage, dict) else "",
        afterimage.get("material_aftereffect", "") if isinstance(afterimage, dict) else "",
        afterimage.get("relationship_aftereffect", "") if isinstance(afterimage, dict) else "",
        delivery.get("method", "") if isinstance(delivery, dict) else "",
    ]
    scenes = plan.get("scenes", [])
    if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
        parts.extend(scenes[0].get(key, "") for key in ("goal", "conflict", "threat_action"))
    return "\n".join(part for part in parts if isinstance(part, str) and part)


def _validate_immediate_serial_progression(
    plan: dict[str, Any],
    state: dict[str, Any],
) -> list[Issue]:
    """Block a new chapter from disguising the previous chapter's engine as escalation."""
    last_chapter = state.get("chapter", {}).get("last_chapter")
    next_chapter = state.get("chapter", {}).get("next_chapter")
    if not isinstance(last_chapter, int) or next_chapter != last_chapter + 1:
        return []

    prior = _latest_chapter_context(state)
    planned = _planned_serial_engine(plan)
    issues: list[Issue] = []

    prior_exit_loss = re.search(
        r"(?:失去|错过|发尽|未领到).{0,20}(?:客位|客牌|船牌|渡船|离镇|出镇|学徒)"
        r"|(?:客位|客牌|船牌|渡船|离镇|出镇).{0,20}(?:失去|错过|发尽|未领到)",
        prior,
    )
    planned_exit_loss = re.search(
        r"(?:不去|错过|失去|放弃|无法|不能|不再).{0,24}(?:车行|车位|客位|船牌|客牌|渡船|离镇|出镇|陆路)"
        r"|(?:车行|车位|客位|船牌|客牌|渡船|离镇|出镇|陆路).{0,24}(?:不去|错过|失去|放弃|无法|不能|不再)",
        planned,
    )
    if prior_exit_loss and planned_exit_loss:
        issues.append(
            Issue(
                "REPEATED_EXIT_LOSS",
                "P1",
                "上一章已经兑现失去离镇机会；下一章必须消费该后果并换一个压力领域，不能改换交通方式再失去一次",
                planned_exit_loss.group(0),
            )
        )

    prior_career_loss = re.search(
        r"(?:失去|错过|错失|毁坏).{0,24}(?:学徒|木行|引荐|客位|客牌|木样)"
        r"|(?:学徒|木行|引荐|客位|客牌|木样).{0,24}(?:失去|错过|错失|毁坏)",
        prior,
    )
    planned_career_loss = re.search(
        r"(?:失去|放弃|撤回|不再给).{0,24}(?:试工|木工活|用工|木匠铺|手艺机会)"
        r"|(?:试工|木工活|用工|木匠铺|手艺机会).{0,24}(?:失去|放弃|撤回|不再给)",
        planned,
    )
    if prior_career_loss and planned_career_loss:
        issues.append(
            Issue(
                "REPEATED_CAREER_LOSS",
                "P1",
                "上一章已经毁掉木样并失去学徒客位；下一章不能再以失去本地试工或木工机会重复支付同一身份代价",
                planned_career_loss.group(0),
            )
        )

    active_foreshadows = state.get("active_foreshadows", {})
    paper_ash_just_planted = any(
        item_id == "F-A01"
        and isinstance(runtime, dict)
        and runtime.get("status") == "已播种"
        and runtime.get("plant_chapter") == last_chapter
        for item_id, runtime in active_foreshadows.items()
    ) if isinstance(active_foreshadows, dict) else False
    if paper_ash_just_planted and "纸灰" in planned:
        issues.append(
            Issue(
                "IMMEDIATE_CLUE_REPETITION",
                "P1",
                "纸灰刚在上一章完成播种，不能立刻换一个缝隙或容器继续充当本章开篇、关键发现或主要威胁",
                next((line for line in planned.splitlines() if "纸灰" in line), "纸灰"),
            )
        )

    prior_damaged_sample = re.search(
        r"(?:毁坏|折断|掰断).{0,10}(?:木样|样榫)|(?:木样|样榫).{0,10}(?:毁坏|折断|掰断)",
        prior,
    )
    sample_state_preserved = re.search(
        r"(?:断|毁|两截|半截|残).{0,10}(?:木样|样榫)|(?:木样|样榫).{0,10}(?:断|毁|两截|半截|残)",
        planned,
    )
    if prior_damaged_sample and "样榫" in planned and not sample_state_preserved:
        issues.append(
            Issue(
                "DAMAGED_OBJECT_STATE_ERASED",
                "P1",
                "上一章已毁坏的样榫不能以完好物件重新入场；规划必须明确保留其断裂状态或不再使用",
                next((line for line in planned.splitlines() if "样榫" in line), "样榫"),
            )
        )
    return issues


def _validate_scene_spatial_triggers(plan: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    scenes = plan.get("scenes", [])
    if not isinstance(scenes, list):
        return issues
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        location = scene.get("location", "")
        rendered = "\n".join(
            str(scene.get(key, ""))
            for key in ("discovery_path", "choice_reason", "threat_action", "human_turn", "end_state")
        )
        if (
            isinstance(location, str)
            and "孙家" not in location
            and re.search(
                r"(?:听见|听到).{0,10}孙家.{0,8}(?:内屋|外院|争执|呼叫)"
                r"|孙家.{0,8}(?:内屋|外院).{0,12}(?:叫|喊|呼叫|争执).{0,10}(?:张洞|他)",
                rendered,
            )
        ):
            issues.append(Issue(
                "IMPOSSIBLE_REMOTE_TRIGGER",
                "P1",
                "张家或木匠铺距孙家步行约一刻钟；张洞不能直接听见孙家内屋争执或母亲呼叫，必须已在现场或有可见传话链",
                f"scene {index}: {location}",
            ))
    return issues


def _validate_ending_leverage_support(plan: dict[str, Any]) -> list[Issue]:
    spine = plan.get("dramatic_spine", {})
    if not isinstance(spine, dict):
        return []
    ending = spine.get("ending_leverage", "")
    if not isinstance(ending, str) or not ending:
        return []
    claims = set(re.findall(
        r"(?:孙家|张家|祠堂|木匠铺)[\u4e00-\u9fff]{0,3}(?:铁栓|钥匙|账簿|名单|木样|行囊)",
        ending,
    ))
    if not claims:
        return []
    rendered = repr({
        "scenes": plan.get("scenes", []),
        "state_changes": plan.get("state_changes", {}),
    })
    unsupported = sorted(claim for claim in claims if claim not in rendered)
    if not unsupported:
        return []
    return [Issue(
        "UNSUPPORTED_ENDING_LEVERAGE",
        "P1",
        "ending_leverage不能凭空改变资源归属或行动身份；相关物件与关系必须先在场景中建立",
        "、".join(unsupported),
    )]


def _validate_event_specific_authority(plan: dict[str, Any]) -> list[Issue]:
    if plan.get("event_id") != "event_1":
        return []
    rendered = repr({
        "reader_investment": plan.get("reader_investment"),
        "dramatic_spine": plan.get("dramatic_spine"),
        "scenes": plan.get("scenes"),
    })
    issues: list[Issue] = []
    if plan.get("chapter_number") == 2:
        spine = plan.get("dramatic_spine", {})
        cost_rendered = "\n".join(
            str(spine.get(field, ""))
            for field in ("choice_cost", "cost_realization")
        ) if isinstance(spine, dict) else ""
        if not re.search(
            r"张洞(?:当场|公开|亲自|因此|也|将)?(?:失去|交出|承担|接下|背上|负责|被拒|被逐|受损|受牵连)"
            r"|张洞.{0,40}(?:责任|追责|风险).{0,16}(?:自己|名下)"
            r"|张洞.{0,24}(?:收下|接过).{0,16}(?:殓衣|亡者衣物)"
            r"|(?:责任|代价|追责|风险).{0,12}(?:落在|记在|压到|由).{0,8}张洞",
            cost_rendered,
        ):
            issues.append(Issue(
                "PROTAGONIST_COST_NOT_REALIZED",
                "P1",
                "第二章关键选择必须让张洞本人新承担一项已发生的责任、损失或关系风险；母亲失去针线钱不能独自替代主角代价",
                cost_rendered[:480],
            ))
        scene_threats = "\n".join(
            str(scene.get("threat_action", ""))
            for scene in plan.get("scenes", [])
            if isinstance(scene, dict)
        )
        investment = plan.get("reader_investment", {})
        core_thread = investment.get("core_threat_continuation", {}) if isinstance(investment, dict) else {}
        threat_parts = [
            str(investment.get("threat_in_motion", "")) if isinstance(investment, dict) else "",
            scene_threats,
        ]
        if isinstance(core_thread, dict):
            threat_parts.extend(str(value) for value in core_thread.values())
        threat_context = "\n".join(threat_parts)
        dead_voice_identified = bool(re.search(
            r"(?:死者|死人|已死|亡父|亡母|亡妻).{0,20}(?:声音|称呼|叫门)"
            r"|(?:声音|称呼).{0,20}(?:死者|死人|已死|亡父|亡母|亡妻)",
            threat_context,
        ))
        current_boundary_action = bool(re.search(
            r"(?:街门|院门|侧门|门外|关闭边界).{0,40}(?:三次|三下|敲击|敲门|借声|声音|之声|私称|称呼|叫门|响起|诱.{0,6}(?:开门|开街门|开院门|开侧门|拔栓))"
            r"|(?:门外客|借声|声音|之声|私称|称呼).{0,40}(?:街门|院门|侧门|门外|关闭边界|开门|开街门|开院门|开侧门|拔栓|诱.{0,6}开)"
            r"|(?:三次|三下|敲击|敲门|叫门|响起).{0,40}(?:街门|院门|侧门|门外|关闭边界)",
            scene_threats,
        ))
        immediate_human_effect = bool(re.search(
            r"(?:当场|立即|随即).{0,20}(?:退出|撤走|离开|拒绝|改口|放下|失去)"
            r"|(?:帮丧|抬棺|邻里|家人).{0,20}(?:退出|撤走|离开|拒绝|改口|失去)",
            scene_threats,
        ))
        if not (dead_voice_identified and current_boundary_action and immediate_human_effect):
            issues.append(Issue(
                "CORE_ANOMALY_NOT_ACTING_ON_PAGE",
                "P1",
                "第二章不能只转述另一户证词；借用死者声音的威胁必须在本章当前场景短暂行动，并立即改变一个活人的选择或孙家丧事资源",
                scene_threats[:640],
            ))
    if (
        "孙有田" in rendered
        and re.search(
            r"孙有田.{0,100}(?:向米铺说明旧债|追讨旧债|核认.{0,12}赊米|以.{0,20}赊米.{0,20}要求)",
            rendered,
        )
    ):
        issues.append(Issue(
            "UNSUPPORTED_DEBT_AUTHORITY",
            "P1",
            "孙有田可以索取丧事人情，但不能把亡妻在米铺的担保当成自己可追讨、撤销或强制核认的债权",
            "孙有田 / 赊米担保",
        ))
    hook_content = plan.get("hook", {}).get("content", "")
    if (
        plan.get("chapter_number") == 2
        and isinstance(hook_content, str)
        and re.search(r"(?:进入|留在|走进).{0,12}孙家.{0,8}(?:内屋|屋内)|孙家.{0,8}(?:内屋|屋内)", hook_content)
        and not re.search(
            r"(?:求救|呼喊|拒绝放行|不许.{0,8}离开|锁住|落锁|上闩|封住|改口|逼迫|隐瞒|争执|失踪|受伤)",
            hook_content,
        )
    ):
        issues.append(Issue(
            "STATIC_INTERIOR_WAIT_HOOK",
            "P1",
            "母亲进入孙家内屋或张洞被留在外院只是位置变化；章末必须出现无法被正常入殓私密流程解释的主动变化",
            hook_content,
        ))
    afterimage = plan.get("reader_investment", {}).get("emotional_afterimage", {})
    person = afterimage.get("person", "") if isinstance(afterimage, dict) else ""
    if (
        plan.get("chapter_number", 0) > 1
        and isinstance(person, str)
        and ("母亲" in person or "张母" in person)
        and isinstance(hook_content, str)
        and not any(alias in hook_content for alias in ("母亲", "张母", "娘"))
    ):
        issues.append(Issue(
            "ENDING_ABANDONS_AFTERIMAGE",
            "P1",
            "章末钩子必须继续作用于情绪余震中的母亲，不能在她刚付出代价后切换成配角的新差事或下一项调查",
            hook_content,
        ))
    return issues


def _result(issues: list[Issue], metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": not any(issue.severity == "P1" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "metrics": metrics,
    }


def validate_plan(
    plan: dict[str, Any],
    state: dict[str, Any],
    allowed_canon_ids: set[str] | None = None,
    allowed_foreshadow_ids: set[str] | None = None,
    allowed_milestone_ids: set[str] | None = None,
    foreshadow_registry: dict[str, Any] | None = None,
    arc_registry: dict[str, Any] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(plan, dict):
        return [Issue("PLAN_NOT_OBJECT", "P1", "规划不是JSON object", repr(plan)[:120])]
    for field in sorted(PLAN_REQUIRED - plan.keys()):
        issues.append(Issue("PLAN_FIELD_MISSING", "P1", f"规划缺失字段: {field}", field))
    expected = state.get("chapter", {}).get("next_chapter")
    if plan.get("chapter_number") != expected:
        issues.append(Issue("PLAN_CHAPTER_MISMATCH", "P1", f"规划章号应为{expected}", str(plan.get("chapter_number"))))
    if plan.get("event_id") != state.get("chapter", {}).get("current_event"):
        issues.append(Issue("PLAN_EVENT_MISMATCH", "P1", "规划事件与状态不一致", str(plan.get("event_id"))))
    issues.extend(_validate_immediate_serial_progression(plan, state))
    issues.extend(_validate_scene_spatial_triggers(plan))
    issues.extend(_validate_ending_leverage_support(plan))
    issues.extend(_validate_event_specific_authority(plan))
    continuity = plan.get("serial_continuity")
    if (
        not isinstance(continuity, dict)
        or set(continuity) != SERIAL_CONTINUITY_REQUIRED
        or not all(
            isinstance(continuity.get(field), str) and continuity[field].strip()
            for field in SERIAL_CONTINUITY_REQUIRED
        )
    ):
        issues.append(
            Issue(
                "BAD_SERIAL_CONTINUITY",
                "P1",
                "规划必须明确消费上一章人物伤口、开场后果、关键物件状态，并说明本章压力为何不是换皮重复",
                repr(continuity)[:320],
            )
        )
    investment = plan.get("reader_investment")
    if not isinstance(investment, dict) or set(investment) != READER_INVESTMENT_REQUIRED:
        issues.append(
            Issue(
                "BAD_READER_INVESTMENT",
                "P1",
                "规划必须定义人物依恋、主角矛盾、主动威胁、揭示变形、情绪余震和线索阻力",
                repr(investment)[:280],
            )
        )
    else:
        for field in ("protagonist_contradiction", "threat_in_motion"):
            if not isinstance(investment.get(field), str) or not investment[field].strip():
                issues.append(Issue("EMPTY_READER_INVESTMENT", "P1", f"reader_investment.{field}不得为空", repr(investment.get(field))))
        threat_in_motion = investment.get("threat_in_motion", "")
        if isinstance(threat_in_motion, str) and re.search(
            r"(?:异常|危险|对手|门外(?:客|之物)?).{0,10}(?:尚未|并未|没有|未曾|暂未).{0,6}(?:再现|出现|行动|逼近|到来)",
            threat_in_motion,
        ):
            issues.append(
                Issue(
                    "INACTIVE_THREAT_ADMITTED",
                    "P1",
                    "threat_in_motion不能承认核心威胁本章没有行动；请让异常、对手或错误选择在本章内主动改变活人处境",
                    threat_in_motion,
                )
            )
        core_thread = investment.get("core_threat_continuation")
        if (
            not isinstance(core_thread, dict)
            or set(core_thread) != CORE_THREAT_CONTINUATION_REQUIRED
            or not all(
                isinstance(core_thread.get(field), str) and core_thread[field].strip()
                for field in CORE_THREAT_CONTINUATION_REQUIRED
            )
        ):
            issues.append(Issue(
                "BAD_CORE_THREAT_CONTINUATION",
                "P1",
                "非首章必须说明上一章核心威胁怎样在本章继续作用、产生局部答案、迫使当前改变，并与活人压力合流；首章也须声明核心威胁的当场闭环",
                repr(core_thread)[:480],
            ))
        else:
            local_answer = core_thread["local_answer"]
            if re.search(
                r"(?:只是|仅|仍|尚|无法|不能).{0,12}(?:矛盾|不一|不明|不清|未知|没说清|未确认)"
                r"|(?:矛盾说法|说法不一|各执一词)(?:。|，|；|$)",
                local_answer,
            ):
                issues.append(Issue(
                    "STALLED_CORE_REVELATION",
                    "P1",
                    "核心局部答案不能只把同一未知改写成证词矛盾或仍待确认；它必须排除一种理解、改变问题种类或形成新的行动边界",
                    local_answer,
                ))
            if core_thread["old_defense"].strip() == core_thread["replacement_rule"].strip():
                issues.append(Issue(
                    "STALLED_ACTION_RULE",
                    "P1",
                    "局部答案必须使旧防法失效、受限或产生新代价；替代规则不能原样重复旧防法",
                    core_thread["old_defense"],
                ))
            if plan.get("chapter_number") == 2 and plan.get("event_id") == "event_1":
                core_rendered = "\n".join(core_thread.values())
                if not re.search(r"(?:死者|死人|借声|声音|叫门)", core_rendered):
                    issues.append(Issue(
                        "CORE_HOOK_RECEDES_IN_PLAN",
                        "P1",
                        "第二章不能只用孙家名声冲突承接第一章；借用死者声音的核心疑问必须与本章局部答案和现实压力直接相连",
                        core_rendered[:480],
                    ))
        attachment = investment.get("attachment_anchor")
        if (
            not isinstance(attachment, dict)
            or set(attachment) != ATTACHMENT_ANCHOR_REQUIRED
            or not all(
                isinstance(attachment.get(field), str) and attachment[field].strip()
                for field in ATTACHMENT_ANCHOR_REQUIRED
            )
        ):
            issues.append(Issue(
                "BAD_ATTACHMENT_ANCHOR",
                "P1",
                "依恋锚点必须分别说明对象、危险前的当场体验、私人意义和将被夺走的内容",
                repr(attachment),
            ))
        afterimage = investment.get("emotional_afterimage")
        if (
            not isinstance(afterimage, dict)
            or set(afterimage) != EMOTIONAL_AFTERIMAGE_REQUIRED
            or not all(
                isinstance(afterimage.get(field), str) and afterimage[field].strip()
                for field in EMOTIONAL_AFTERIMAGE_REQUIRED
            )
        ):
            issues.append(Issue(
                "BAD_EMOTIONAL_AFTERIMAGE",
                "P1",
                "情绪余震必须落到具体活人、已发生的伤口、未决选择，并让谜题服从人物处境",
                repr(afterimage),
            ))
        shift = investment.get("revelation_shift")
        if (
            not isinstance(shift, dict)
            or set(shift) != REVELATION_SHIFT_REQUIRED
            or not all(isinstance(shift.get(field), str) and shift[field].strip() for field in REVELATION_SHIFT_REQUIRED)
            or shift.get("from") == shift.get("to")
            or shift.get("changes") not in {"IDENTITY", "RELATIONSHIP", "SAFETY", "ACTION_RULE", "MORAL_CHOICE"}
        ):
            issues.append(Issue("BAD_REVELATION_SHIFT", "P1", "揭示必须把问题变成不同种类的人物或行动问题", repr(shift)))
        delivery = investment.get("clue_delivery")
        if (
            not isinstance(delivery, dict)
            or set(delivery) != CLUE_DELIVERY_REQUIRED
            or not all(isinstance(delivery.get(field), str) and delivery[field].strip() for field in CLUE_DELIVERY_REQUIRED)
            or delivery.get("coincidence_risk") not in {"LOW", "MEDIUM", "HIGH"}
        ):
            issues.append(Issue("BAD_CLUE_DELIVERY", "P1", "线索取得必须说明方法、阻力和巧合风险", repr(delivery)))
        elif delivery["coincidence_risk"] == "HIGH":
            issues.append(Issue("HIGH_COINCIDENCE_CLUE", "P1", "关键线索不能依靠规划已识别的高风险巧合抵达", repr(delivery)))
    spine = plan.get("dramatic_spine")
    if not isinstance(spine, dict) or set(spine) != DRAMATIC_SPINE_REQUIRED:
        issues.append(
            Issue(
                "BAD_DRAMATIC_SPINE",
                "P1",
                "规划必须完整定义人物欲望、选择、代价与问题升级",
                repr(spine)[:240],
            )
        )
    else:
        for field in DRAMATIC_SPINE_REQUIRED - {"question_progression"}:
            if not isinstance(spine.get(field), str) or not spine[field].strip():
                issues.append(
                    Issue(
                        "EMPTY_DRAMATIC_SPINE",
                        "P1",
                        f"dramatic_spine.{field}不得为空",
                        repr(spine.get(field)),
                    )
                )
        progression = spine.get("question_progression")
        if (
            not isinstance(progression, list)
            or not 3 <= len(progression) <= 4
            or not all(isinstance(item, str) and item.strip() for item in progression)
            or len(set(progression)) != len(progression)
        ):
            issues.append(
                Issue(
                    "BAD_QUESTION_PROGRESSION",
                    "P1",
                    "问题链必须包含3到4个互不重复的具体问题",
                    repr(progression),
                )
            )
    changes = plan.get("state_changes")
    if not isinstance(changes, dict):
        issues.append(Issue("NO_STATE_CHANGE", "P1", "本章没有不可逆状态变化", "state_changes"))
    else:
        for field in sorted(STATE_CHANGE_REQUIRED - changes.keys()):
            issues.append(Issue("STATE_CHANGE_FIELD_MISSING", "P1", f"状态变化缺失字段: {field}", field))
        material_lists = (
            "protagonist_known_info_add", "protagonist_inventory_add",
            "protagonist_inventory_remove", "protagonist_body_updates",
            "ability_updates", "character_updates", "world_confirmed_add",
            "world_hypotheses_add",
        )
        material = any(changes.get(field) for field in material_lists)
        material = material or (
            changes.get("protagonist_location") not in {None, state.get("protagonist", {}).get("location")}
        )
        material = material or changes.get("timeline_year") != state.get("timeline", {}).get("current_year")
        material = material or changes.get("timeline_elapsed_days") != state.get("timeline", {}).get("elapsed_days")
        if not material:
            issues.append(Issue("NO_STATE_CHANGE", "P1", "本章状态字段没有任何真实变化", repr(changes)[:200]))
        inventory = set(state.get("protagonist", {}).get("inventory", []))
        unknown_removals = [
            item
            for item in changes.get("protagonist_inventory_remove", [])
            if item not in inventory
        ]
        if unknown_removals:
            issues.append(
                Issue(
                    "INVENTORY_REMOVE_MISSING",
                    "P1",
                    "规划不能移除当前状态中不存在的物品；请改为可结算的新信息或先登记资源",
                    repr(unknown_removals),
                )
            )
        existing_known = set(state.get("protagonist", {}).get("known_info", []))
        duplicate_known = [
            item
            for item in changes.get("protagonist_known_info_add", [])
            if item in existing_known
        ]
        if duplicate_known:
            issues.append(
                Issue(
                    "KNOWN_INFO_ALREADY_PRESENT",
                    "P1",
                    "规划不能把当前状态已知事实重新当作本章新进展",
                    repr(duplicate_known),
                )
            )
        existing_rendered = "\n".join(existing_known)
        semantic_duplicates: list[str] = []
        for item in changes.get("protagonist_known_info_add", []):
            if not isinstance(item, str) or item in existing_known:
                continue
            if (
                "张家正门" in item
                and re.search(r"(?:外力|门外|由外向内)", item)
                and re.search(r"(?:撞|撬)", item)
                and "张家正门" in existing_rendered
                and re.search(r"(?:外力|门外|由外向内)", existing_rendered)
                and re.search(r"(?:撞|撬)", existing_rendered)
            ):
                semantic_duplicates.append(item)
        if semantic_duplicates:
            issues.append(
                Issue(
                    "KNOWN_INFO_SEMANTIC_DUPLICATE",
                    "P1",
                    "规划不能把状态中已有事实换成同义措辞再次结算；可在场景中简短复核，但不得当作新认知或章节奖励",
                    repr(semantic_duplicates),
                )
            )
    evidence_ids = plan.get("canon_evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        issues.append(Issue("NO_CANON_EVIDENCE", "P1", "规划没有声明原著/前传依据", repr(evidence_ids)))
    elif allowed_canon_ids is not None:
        unknown = sorted(set(evidence_ids) - allowed_canon_ids)
        if unknown:
            issues.append(Issue("UNKNOWN_CANON_EVIDENCE", "P1", "规划引用了未注册依据", ", ".join(unknown)))
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        issues.append(Issue("NO_SCENES", "P1", "规划没有场景", "scenes"))
    else:
        payoff_types: list[str] = []
        for index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict):
                issues.append(Issue("SCENE_NOT_OBJECT", "P1", f"场景{index}不是object", repr(scene)[:160]))
                continue
            if not scene.get("goal") or not scene.get("conflict"):
                issues.append(Issue("SCENE_NO_DRAMATIC_PRESSURE", "P1", f"场景{index}缺少目标或阻力", repr(scene)[:160]))
            if not scene.get("function") and not scene.get("irreversible_change"):
                issues.append(Issue("SCENE_NO_FUNCTION", "P1", f"场景{index}缺少场景功能", repr(scene)[:160]))
            if not scene.get("pressure_change") and not scene.get("irreversible_change"):
                issues.append(Issue("SCENE_NO_PRESSURE_CHANGE", "P1", f"场景{index}没有压力变化", repr(scene)[:160]))
            missing_model = sorted(SCENE_MODEL_REQUIRED - scene.keys())
            if missing_model:
                issues.append(Issue(
                    "SCENE_MODEL_MISSING",
                    "P1",
                    f"场景{index}缺少可复原场景字段",
                    ", ".join(missing_model),
                ))
                continue
            for field in (
                "initial_state",
                "discovery_path",
                "knowledge_limits",
                "choice_reason",
                "end_state",
                "threat_action",
                "human_turn",
            ):
                if not isinstance(scene.get(field), str) or not scene[field].strip():
                    issues.append(Issue(
                        "SCENE_MODEL_EMPTY",
                        "P1",
                        f"场景{index}的{field}不得为空",
                        repr(scene.get(field)),
                    ))
            payoff_type = scene.get("payoff_type")
            if payoff_type not in {"HUMAN_CHANGE", "MIXED", "EVIDENCE_ONLY"}:
                issues.append(Issue("SCENE_BAD_PAYOFF", "P1", f"场景{index}的payoff_type无效", repr(payoff_type)))
            else:
                payoff_types.append(payoff_type)
            explanations = scene.get("ordinary_explanations")
            if not isinstance(explanations, dict):
                issues.append(Issue(
                    "SCENE_BAD_ALTERNATIVES",
                    "P1",
                    f"场景{index}的普通解释必须是object",
                    repr(explanations),
                ))
            else:
                required_alternatives = {"considered", "excluded", "remaining"}
                if set(explanations) != required_alternatives:
                    issues.append(Issue(
                        "SCENE_BAD_ALTERNATIVES",
                        "P1",
                        f"场景{index}的普通解释字段不完整",
                        repr(explanations),
                    ))
                elif not all(
                    isinstance(explanations[field], list)
                    and all(isinstance(item, str) and item.strip() for item in explanations[field])
                    for field in required_alternatives
                ):
                    issues.append(Issue(
                        "SCENE_BAD_ALTERNATIVES",
                        "P1",
                        f"场景{index}的普通解释必须是字符串数组",
                        repr(explanations),
                    ))
        if payoff_types:
            evidence_only_count = payoff_types.count("EVIDENCE_ONLY")
            if evidence_only_count * 2 > len(payoff_types):
                issues.append(Issue("EVIDENCE_DOMINATED_PLAN", "P1", "超过半数场景只奖励证据变可靠，人物处境没有改变", repr(payoff_types)))
            if payoff_types[-1] == "EVIDENCE_ONLY":
                issues.append(Issue("EVIDENCE_ONLY_ENDING", "P1", "末场不能只留下更可靠的证据或下一项调查", repr(payoff_types)))
    foreshadows = plan.get("foreshadow_operations")
    planned_foreshadows: dict[str, set[str]] = {"plant": set(), "recover": set()}
    if not isinstance(foreshadows, dict):
        issues.append(Issue("BAD_FORESHADOW_OPS", "P1", "伏笔操作必须是object", repr(foreshadows)))
    else:
        for operation in ("plant", "recover"):
            values = foreshadows.get(operation)
            if not isinstance(values, list):
                issues.append(Issue("BAD_FORESHADOW_OPS", "P1", f"伏笔{operation}必须是数组", repr(values)))
                continue
            for value in values:
                if not isinstance(value, str) or not re.match(r"^F-[A-Z]-?\d+", value.strip()):
                    issues.append(Issue("BAD_FORESHADOW_ID", "P1", "伏笔必须以稳定ID开头，如F-A01", repr(value)))
                    continue
                matched = re.match(r"^(F-[A-Z]-?\d+)", value.strip())
                if matched:
                    item_id = matched.group(1)
                    planned_foreshadows[operation].add(item_id)
                    if allowed_foreshadow_ids is not None and item_id not in allowed_foreshadow_ids:
                        issues.append(Issue("UNKNOWN_FORESHADOW", "P1", "伏笔未在登记表中定义", item_id))
    milestones = plan.get("milestone_operations")
    if not isinstance(milestones, dict) or not isinstance(milestones.get("complete"), list):
        issues.append(Issue("BAD_MILESTONE_OPS", "P1", "里程碑操作必须包含complete数组", repr(milestones)))
        planned_milestones: set[str] = set()
    else:
        planned_milestones = {item for item in milestones["complete"] if isinstance(item, str)}
        if allowed_milestone_ids is not None:
            for item in sorted(planned_milestones - allowed_milestone_ids):
                issues.append(Issue("UNKNOWN_MILESTONE", "P1", "里程碑未在登记表中定义", item))
    completed_before = set(state.get("completed_milestones", []))
    active_foreshadows = state.get("active_foreshadows", {})
    overlap = planned_foreshadows["plant"] & planned_foreshadows["recover"]
    for item_id in sorted(overlap):
        issues.append(Issue("FORESHADOW_SAME_CHAPTER_RECOVERY", "P1", "伏笔不得在同章播种并回收", item_id))
    for item_id in sorted(planned_foreshadows["plant"]):
        if item_id in active_foreshadows:
            issues.append(Issue("FORESHADOW_ALREADY_PLANTED", "P1", "伏笔已经播种，不得重复播种", item_id))
    for item_id in sorted(planned_foreshadows["recover"]):
        runtime = active_foreshadows.get(item_id)
        if not isinstance(runtime, dict) or runtime.get("status") != "已播种":
            issues.append(Issue("FORESHADOW_NOT_PLANTED", "P1", "伏笔必须在更早章节播种后才能回收", item_id))
        elif runtime.get("plant_chapter", expected) >= expected:
            issues.append(Issue("FORESHADOW_NOT_MATURE", "P1", "伏笔不能在播种章立即回收", item_id))
    registry_entries = (foreshadow_registry or {}).get("entries", {})
    for item_id in sorted(planned_foreshadows["plant"]):
        entry = registry_entries.get(item_id, {})
        prerequisites = entry.get("plant_after", []) if isinstance(entry, dict) else []
        missing = [item for item in prerequisites if item not in completed_before]
        if missing:
            issues.append(Issue("FORESHADOW_PREREQUISITE_MISSING", "P1", "伏笔播种前置里程碑未完成", f"{item_id}: {', '.join(missing)}"))
    for milestone in planned_milestones:
        for item_id, runtime in active_foreshadows.items():
            entry = registry_entries.get(item_id, {})
            if (
                isinstance(entry, dict)
                and entry.get("recover_by") == milestone
                and isinstance(runtime, dict)
                and runtime.get("status") == "已播种"
                and item_id not in planned_foreshadows["recover"]
            ):
                issues.append(Issue("FORESHADOW_RECOVERY_OVERDUE", "P1", "完成该里程碑前必须回收已到期伏笔", f"{item_id} -> {milestone}"))

    milestone_entries = (arc_registry or {}).get("milestones", {})
    current_volume = state.get("chapter", {}).get("current_volume")
    for milestone in sorted(planned_milestones):
        if milestone in completed_before:
            issues.append(Issue("MILESTONE_ALREADY_COMPLETED", "P1", "里程碑已经完成，不得重复提交", milestone))
        entry = milestone_entries.get(milestone, {})
        if not isinstance(entry, dict):
            continue
        missing = [item for item in entry.get("after", []) if item not in completed_before]
        if missing:
            issues.append(Issue("MILESTONE_PREREQUISITE_MISSING", "P1", "里程碑前置条件未完成", f"{milestone}: {', '.join(missing)}"))
        if entry.get("volume") is not None and entry.get("volume") != current_volume:
            issues.append(Issue("MILESTONE_WRONG_VOLUME", "P1", "里程碑不属于当前卷", f"{milestone}: volume {entry.get('volume')}"))
    abilities = state.get("protagonist", {}).get("abilities", {})
    rendered = repr({
        key: plan.get(key)
        for key in ("scenes", "new_information", "state_changes", "rule_hypotheses")
    })
    completed_milestones = completed_before | planned_milestones
    for name, ability in abilities.items():
        unlock_after = ability.get("unlock_after")
        if isinstance(unlock_after, list) and name in rendered:
            missing = [item for item in unlock_after if item not in completed_milestones]
            if missing:
                issues.append(Issue("ABILITY_GATE", "P1", f"能力前置里程碑未完成: {name}", ", ".join(missing)))
        elif expected and expected < ability.get("unlock_chapter", 0) and name in rendered:
            issues.append(Issue("ABILITY_GATE", "P1", f"能力提前出现: {name}", name))
    return issues


def validate_review(
    review: dict[str, Any],
    static_review: dict[str, Any],
    *,
    expected_chapter: int | None = None,
    draft: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(review, dict):
        return [Issue("REVIEW_NOT_OBJECT", "P1", "审查不是JSON object", repr(review)[:120])]
    for field in sorted(REVIEW_REQUIRED - review.keys()):
        issues.append(Issue("REVIEW_FIELD_MISSING", "P1", f"审查缺失字段: {field}", field))
    if review.get("verdict") not in {"PASS", "REVISE", "REPLAN"}:
        issues.append(Issue("REVIEW_BAD_VERDICT", "P1", "审查结论无效", str(review.get("verdict"))))
    if expected_chapter is not None and review.get("chapter_number") != expected_chapter:
        issues.append(Issue("REVIEW_CHAPTER_MISMATCH", "P1", f"审查章号应为{expected_chapter}", str(review.get("chapter_number"))))
    evidence = review.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 3:
        issues.append(Issue("REVIEW_NO_EVIDENCE", "P1", "审查必须提供至少3条原文定位证据", repr(evidence)))
    elif draft is not None:
        for item in evidence:
            quote = item.get("quote") if isinstance(item, dict) else None
            if not quote or quote not in draft:
                issues.append(Issue("REVIEW_FALSE_EVIDENCE", "P1", "审查证据未在正文中找到", repr(quote)))
    if not static_review.get("passed") and review.get("verdict") == "PASS":
        issues.append(Issue("REVIEW_CONTRADICTS_STATIC", "P1", "语义审查不得推翻P1硬检查", "verdict=PASS"))
    if review.get("verdict") == "PASS" and review.get("p1_failures"):
        issues.append(Issue("REVIEW_SELF_CONTRADICTION", "P1", "PASS审查仍包含P1失败", repr(review.get("p1_failures"))[:160]))
    if review.get("verdict") == "PASS" and review.get("grade") not in {"A", "B"}:
        issues.append(Issue("REVIEW_PASS_LOW_GRADE", "P1", "PASS审查等级只能是A或B", str(review.get("grade"))))
    if review.get("verdict") == "PASS" and review.get("revision_instructions"):
        issues.append(Issue("REVIEW_PASS_WITH_REVISIONS", "P1", "PASS审查不得再要求修订", repr(review.get("revision_instructions"))[:160]))
    return issues


def _paragraphs(text: str, minimum: int = 30) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= minimum]


def _long_sentences(text: str, minimum: int = 24) -> list[str]:
    sentences = re.split(r"[。！？!?\n]+", text)
    return [re.sub(r"\s+", "", sentence) for sentence in sentences if len(re.sub(r"\s+", "", sentence)) >= minimum]


def scan_draft(
    text: str,
    recent_texts: list[str],
    era_bans: dict[str, Any],
    plan: dict[str, Any],
    *,
    length_policy: dict[str, int] | None = None,
) -> dict[str, Any]:
    issues: list[Issue] = []
    stripped = text.strip() if isinstance(text, str) else ""
    if not stripped or stripped in {"[PLACEHOLDER]", "PLACEHOLDER", "待生成"}:
        issues.append(Issue("PLACEHOLDER_TEXT", "P1", "正文为空或为占位内容", stripped[:80]))
        return _result(issues, {"char_count": len(stripped)})

    for name in era_bans.get("characters", []):
        if name and name in stripped:
            issues.append(Issue("ERA_BANNED_CHARACTER", "P1", f"当前年代禁入人物: {name}", name))
    for term in era_bans.get("terms", []):
        if term and term in stripped:
            issues.append(Issue("ERA_BANNED_TERM", "P1", f"当前年代禁入概念: {term}", term))
    for item in plan.get("prohibited_elements", []):
        if item and item in stripped and not any(x.evidence == item for x in issues):
            issues.append(Issue("PLAN_PROHIBITED_ELEMENT", "P1", f"规划明确禁入: {item}", item))

    header_match = re.match(r"^第\s*(\d+)\s*章", stripped)
    expected = plan.get("chapter_number")
    if not header_match or int(header_match.group(1)) != expected:
        evidence = stripped.splitlines()[0][:100] if stripped else ""
        issues.append(Issue("CHAPTER_NUMBER_MISMATCH", "P1", f"正文标题章号应为{expected}", evidence))

    for phrase in FORBIDDEN_POV:
        if phrase in stripped:
            issues.append(Issue("FORBIDDEN_POV", "P1", f"出现越视角句式: {phrase}", phrase))

    recent_paragraphs = set()
    recent_sentences: Counter[str] = Counter()
    for recent in recent_texts:
        recent_paragraphs.update(_paragraphs(recent))
        recent_sentences.update(_long_sentences(recent))
    for paragraph in _paragraphs(stripped):
        if paragraph in recent_paragraphs:
            issues.append(Issue("EXACT_PARAGRAPH_REUSE", "P1", "与最近章节重复完整段落", paragraph[:120]))
    current_sentences = Counter(_long_sentences(stripped))
    for sentence, count in current_sentences.items():
        if count + recent_sentences[sentence] >= 3:
            issues.append(Issue("CROSS_CHAPTER_REPETITION", "P1", "长句或动作句在近期累计至少3次", sentence[:120]))

    action_counts: dict[str, int] = {}
    combined_recent = "\n".join(recent_texts)
    for name, pattern in ACTION_PATTERNS.items():
        current_count = len(re.findall(pattern, stripped))
        recent_count = len(re.findall(pattern, combined_recent))
        action_counts[name] = current_count
        if current_count >= 2 or current_count + recent_count >= 3:
            issues.append(Issue("REPEATED_ACTION", "P2", f"动作模板重复: {name}", f"本章{current_count}，近期{recent_count}"))

    char_count = len(re.sub(r"\s+", "", stripped))
    dash_count = stripped.count("——")
    dash_per_thousand = round(dash_count / max(char_count, 1) * 1000, 2)
    if dash_per_thousand > 8:
        issues.append(Issue("DASH_OVERUSE", "P2", "破折号密度过高", str(dash_per_thousand)))
    negation_count = len(re.findall(r"不是.{0,24}是", stripped))
    if negation_count > 3:
        issues.append(Issue("NEGATION_TEMPLATE_OVERUSE", "P2", "‘不是…是…’句式过多", str(negation_count)))
    policy = {**DEFAULT_LENGTH_POLICY, **(length_policy or {})}
    safe_min = int(policy["safe_min"])
    target_min = int(policy["target_min"])
    target_max = int(policy["target_max"])
    safe_max = int(policy["safe_max"])
    if char_count < safe_min or char_count > safe_max:
        issues.append(
            Issue(
                "WORD_COUNT_HARD_FAIL",
                "P1",
                f"正文长度超出{safe_min}-{safe_max}字安全范围",
                str(char_count),
            )
        )
    elif char_count < target_min or char_count > target_max:
        issues.append(
            Issue(
                "WORD_COUNT_TARGET_MISS",
                "P2",
                f"正文未达到{target_min}-{target_max}字目标范围，需审查是否充分演出",
                str(char_count),
            )
        )

    return _result(
        issues,
        {
            "char_count": char_count,
            "dash_count": dash_count,
            "dash_per_thousand": dash_per_thousand,
            "negation_template_count": negation_count,
            "action_counts": action_counts,
            "length_policy": policy,
        },
    )
