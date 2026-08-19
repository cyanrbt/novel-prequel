from __future__ import annotations

import re
from typing import Any, Iterable


BOUNDARY_GROUPS: dict[str, tuple[str, ...]] = {
    "outside": ("门缝外", "门外", "院外", "屋外", "窗外", "外面"),
    "inside": ("正屋里", "正屋内", "门内", "院内", "屋里", "屋内", "里面"),
}

_QUOTED_RE = re.compile(r"“([^”]*)”")
_OBJECT = r"[\u4e00-\u9fffA-Za-z0-9·]{1,24}"
_REQUEST_PATTERNS = (
    re.compile(
        rf"(?:先|请|再|要|让|必须|准备|打算|想)?(?:把|将)"
        rf"(?P<object>{_OBJECT}?)(?:递进来|递入|交进来|送进来|拿进来|"
        rf"放进来|递给|交给|送给|转交|移交)"
    ),
    re.compile(
        rf"(?:先|请|再)?(?P<object>{_OBJECT}?)(?:递|交|送|拿)(?:进来|入|给我)"
    ),
)
_CLAIM_PATTERNS = (
    re.compile(
        rf"^(?P<object>{_OBJECT}?)(?:就|也)?(?:放)?在(?:我)?"
        rf"(?:包|手|箱|袋|兜)(?:里|中)?$"
    ),
    re.compile(
        rf"^(?:我)?(?:包|手|箱|袋|兜)(?:里|中)?(?:还|也)?(?:有|装着)"
        rf"(?P<object>{_OBJECT})$"
    ),
    re.compile(rf"^我把(?P<object>{_OBJECT}?)(?:带来|带来了|拿来|拿来了)$"),
)
_OBJECT_STOPWORDS = {"谁", "什么", "哪里", "哪儿", "怎么", "为何", "人", "话"}
_ATTRIBUTION_RE = re.compile(
    r"声称|自称|据称|宣称|口头称|口头说|表示|所称|说法|说自己|"
    r"听见[^，。！？!?；;\n]{0,12}说"
)
_MODAL_OR_NEGATED_RE = re.compile(
    r"可能|疑似|或许|也许|尚未|还未|还没|没有|并未|未曾|未能|"
    r"待核|待验|请求|要求|拟|准备|试图|能否|是否|是不是|若|如果|"
    r"将要|打算|必须|需要|下一步|不曾|不是|不在|没看见|未看见|会"
    r"|(?:先|再)(?:完成|核验|验证|递入|递进|取得|收到)"
    r"|将(?:下一步|在|由|把)"
    r"|不(?:落槽|插入|封|关|锁|递|收|核验|验证)"
    r"|未(?:落|插|封|关|锁|递|收|见|核|验)"
    r"|(?:没有|并非|不是|不被|未被|没有被|不能被)"
    r"[^，。！？!?；;\n]{0,16}(?:写成|概括成|视为|认作)"
    r"|(?:不|未|不能|不可)(?:被)?(?:等同于|视为|认作)"
    r"|(?:没有|并未|未|不曾)(?:误)?"
    r"(?:称|说|写|表述|断言|认定|概括|视为|认作)"
    r"|(?:仍)?(?:未|没有|并未)(?:被)?"
    r"(?:递入|递进|交付|转移|接收|收到|取得|核验|验证)"
)
_INTERROGATIVE_SCOPE_RE = re.compile(
    r"能否|是否|是不是|可否|要不要|难道|"
    r"怎么|为何|为什么|谁|何人|何时|何处|哪里|哪儿|[吗么]$"
)
_CONDITIONAL_SCOPE_RE = re.compile(r"^(?:若|如果|假如)")
_ATTRIBUTION_SCOPE_RE = re.compile(
    r"声称|自称|据称|宣称|口头称|口头说|表示|说自己|"
    r"听见[^，。！？!?；;\n]{0,12}说"
)
_MODAL_SCOPE_RESET_RE = re.compile(
    r"^(?:但|不过|然而|却|后来|随后|最终|结果|事实上|实际(?:上)?)"
)
_AFFIRMATIVE_COMPLETION_RESET_RE = re.compile(
    r"已经|已|终于|成功(?:地)?|事实上已|实际(?:上)?已"
    r"|(?:递入|递进|交付|转移|接收|收到|取得|核验|验证)(?:了|完成|成功)"
)
_RELATION_SCOPE_RESET_RE = re.compile(
    r"但|不过|然而|却|后来|随后|最终|结果|事实上|实际(?:上)?"
)
_POSSESSION_VERBS = r"持有|手持|持|拿着|握着|带着|带有|拥有|携带"
_DOCUMENT_SUFFIXES = ("凭条", "收据", "票据", "存根", "单据")


def _nearest_boundary_group(text: str, offset: int) -> str | None:
    before = text[max(0, offset - 180) : offset]
    candidates: list[tuple[int, str]] = []
    for group, aliases in BOUNDARY_GROUPS.items():
        for alias in aliases:
            found = before.rfind(alias)
            if found >= 0:
                candidates.append((found, group))
    return max(candidates)[1] if candidates else None


def _without_dialogue(text: str) -> str:
    return _QUOTED_RE.sub("", text)


def _normalize_object(value: str) -> str:
    value = value.strip(" ，。！？!?；;：:")
    value = re.sub(r"^(?:这|那|哪|一)(?:个|只|枚|把|张|件|份|块|本)", "", value)
    return value


def _object_pattern(object_name: str) -> str:
    escaped = re.escape(object_name)
    # The pending cash/object must not silently match a document whose name
    # merely starts with it (e.g. 押钱凭条 is not 押钱 cash).
    suffix_guard = "(?!" + "|".join(map(re.escape, _DOCUMENT_SUFFIXES)) + ")"
    return escaped + suffix_guard


def _is_modal_or_attributed(text: str) -> bool:
    return bool(_MODAL_OR_NEGATED_RE.search(text) or _ATTRIBUTION_RE.search(text))


def _positive_narrative_boundary_observation(
    draft: str, group: str, object_name: str
) -> bool:
    prose = _without_dialogue(draft)
    aliases = "(?:" + "|".join(map(re.escape, BOUNDARY_GROUPS[group])) + ")"
    obj = _object_pattern(object_name)
    for sentence in re.split(r"[。！？!?\n]", prose):
        if not re.search(aliases, sentence) or not re.search(obj, sentence):
            continue
        if _is_modal_or_attributed(sentence):
            continue
        if re.search(
            rf"(?:看见|看到|瞧见|发现)[^，；;]{{0,36}}{aliases}[^，；;]{{0,36}}{obj}"
            rf"|{aliases}[^，；;]{{0,36}}(?:夹着|攥着|握着|拿着|持有|手里|手上)"
            rf"[^，；;]{{0,24}}{obj}"
            rf"|{aliases}[^，；;]{{0,24}}{obj}[^，；;]{{0,12}}(?:在手里|在手上)",
            sentence,
        ):
            return True
    return False


def _positive_narrative_completion(draft: str, object_name: str) -> bool:
    prose = _without_dialogue(draft)
    obj = _object_pattern(object_name)
    for sentence in re.split(r"[。！？!?\n]", prose):
        if not re.search(obj, sentence) or _is_modal_or_attributed(sentence):
            continue
        if re.search(
            rf"(?:把|将)?{obj}[^，；;]{{0,12}}(?:递入|递进|交给|交付|转交)"
            rf"|(?:收到|接到|拿到|取得)[^，；;]{{0,12}}{obj}"
            rf"|(?:确认|证实|核验|验证)[^，；;]{{0,16}}{obj}[^，；;]{{0,8}}(?:为真|属实|无误|一致)",
            sentence,
        ):
            return True
    return False


def extract_pending_propositions(draft: str) -> list[dict[str, Any]]:
    """Extract uncompleted dialogue claims/requests as an evidence ceiling."""
    raw: list[dict[str, Any]] = []
    for quoted in _QUOTED_RE.finditer(draft):
        group = _nearest_boundary_group(draft, quoted.start())
        for clause in re.split(r"[，。！？!?；;]", quoted.group(1)):
            clause = clause.strip()
            if not clause:
                continue
            for pattern in _REQUEST_PATTERNS:
                for match in pattern.finditer(clause):
                    object_name = _normalize_object(match.group("object"))
                    if group and object_name and object_name not in _OBJECT_STOPWORDS:
                        raw.append(
                            {
                                "relation": "BOUNDARY_OBJECT",
                                "subject_group": group,
                                "object": object_name,
                                "source_kind": "PENDING_REQUEST",
                                "source_quote": match.group(0),
                                "source_offset": quoted.start(),
                            }
                        )
            for pattern in _CLAIM_PATTERNS:
                match = pattern.fullmatch(clause)
                if not match or not group:
                    continue
                object_name = _normalize_object(match.group("object"))
                if object_name and object_name not in _OBJECT_STOPWORDS:
                    raw.append(
                        {
                            "relation": "BOUNDARY_OBJECT",
                            "subject_group": group,
                            "object": object_name,
                            "source_kind": "SPOKEN_CLAIM",
                            "source_quote": clause,
                            "source_offset": quoted.start(),
                        }
                    )
    merged: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    for item in raw:
        completed = (
            _positive_narrative_boundary_observation(
                draft, item["subject_group"], item["object"]
            )
            or _positive_narrative_completion(draft, item["object"])
        )
        if completed:
            continue
        key = (item["relation"], item["subject_group"], item["object"])
        row = merged.setdefault(
            key,
            {
                "relation": item["relation"],
                "subject_group": item["subject_group"],
                "object": item["object"],
                "sources": [],
            },
        )
        source = {
            "source_kind": item["source_kind"],
            "source_quote": item["source_quote"],
            "source_offset": item["source_offset"],
        }
        if source not in row["sources"]:
            row["sources"].append(source)
    # Promotion P1 is deliberately narrower than the parser: require both a
    # spoken boundary claim and a later uncompleted request for the same
    # boundary/object.  A single ambiguous utterance or refund idiom is not
    # strong enough for deterministic rejection.
    return [
        row
        for row in merged.values()
        if row["relation"] == "BOUNDARY_OBJECT"
        and {source["source_kind"] for source in row["sources"]}
        >= {"SPOKEN_CLAIM", "PENDING_REQUEST"}
    ]


def _claim_clauses(claim: str) -> list[tuple[str, bool]]:
    """Return report clauses plus narrowly inherited modal scope.

    Commas often separate the setup of a Chinese question from its action
    complement (``能否……，把某物递入``).  Treating the latter as a fresh,
    affirmative clause upgrades a question into a completed action.  Carry
    only explicit interrogative/conditional scope across comma boundaries,
    and stop carrying it at an explicit result/contrast transition.  Sentence
    boundaries always reset the scope.
    """
    clauses: list[tuple[str, bool]] = []
    for sentence_match in re.finditer(
        r"([^。！？!?\n]+)([。！？!?]|\n|$)", claim
    ):
        sentence = sentence_match.group(1)
        terminal = sentence_match.group(2)
        # A question can contain an affirmative-looking complement such as
        # ``能否确认，银簪已经递入？``.  Question scope begins where
        # its marker appears and flows forward; it must not flow backwards
        # over an independent assertion before that marker.  Conditional and
        # attributed clauses remain non-assertive until an explicit
        # result/contrast transition.
        sentence_is_question = terminal in {"？", "?"}
        scope_kind: str | None = None
        parts = [
            (match.group(1).strip(), match.group(2))
            for match in re.finditer(r"([^，,；;]+)([，,；;]|$)", sentence)
            if match.group(1).strip()
        ]
        previous_separator = ""
        for index, (part, separator_after) in enumerate(parts):
            if previous_separator in {"；", ";"}:
                scope_kind = None
            if _MODAL_SCOPE_RESET_RE.search(part):
                scope_kind = None
            local_conditional = bool(_CONDITIONAL_SCOPE_RE.search(part))
            local_interrogative = bool(
                _INTERROGATIVE_SCOPE_RE.search(part)
                or (sentence_is_question and index == len(parts) - 1)
            )
            local_attribution = bool(_ATTRIBUTION_SCOPE_RE.search(part))
            if local_conditional:
                scope_kind = "conditional"
            elif local_attribution:
                scope_kind = "attribution"
            elif local_interrogative:
                scope_kind = "interrogative"
            elif (
                scope_kind == "interrogative"
                and not sentence_is_question
                and _AFFIRMATIVE_COMPLETION_RESET_RE.search(part)
            ):
                # Do not let a question in an earlier comma clause hide a
                # later declarative clause that explicitly asserts completion.
                scope_kind = None
            clauses.append((part, scope_kind is not None))
            previous_separator = separator_after
    return clauses


def _boundary_object_reason(clause: str, group: str, object_name: str) -> str | None:
    aliases = "(?:" + "|".join(map(re.escape, BOUNDARY_GROUPS[group])) + ")"
    obj = _object_pattern(object_name)
    if not re.search(aliases, clause) or not re.search(obj, clause):
        return None
    possession = re.search(
        rf"{aliases}[^，。！？!?；;]{{0,18}}(?:{_POSSESSION_VERBS})"
        rf"[^，。！？!?；;]{{0,28}}{obj}",
        clause,
    )
    if possession and not _is_modal_or_attributed(possession.group(0)):
        return "把口头声称或待递入物写成已经持有"
    bare_evidence = re.search(
        rf"{aliases}[^，。！？!?；;]{{0,28}}{obj}[^，。！？!?；;]{{0,16}}"
        rf"(?:相冲突|构成证据|成为物证|已经出现|在场)",
        clause,
    )
    if bare_evidence and not _is_modal_or_attributed(bare_evidence.group(0)):
        return "把口头声称或待递入物与边界位置并列成现场事实"
    return None


def _completion_reason(clause: str, object_name: str) -> str | None:
    obj = _object_pattern(object_name)
    patterns = (
        rf"{obj}[^，。！？!?；;]{{0,12}}(?:已经|已)?(?:递入|递进|交付|转移|"
        rf"验证完成|核验完成|得到确认|得到证实)",
        rf"(?:已经|已)?(?:收到|接到|拿到|取得)[^，。！？!?；;]{{0,16}}{obj}",
        rf"(?:已经|已)?(?:完成|通过)[^，。！？!?；;]{{0,12}}{obj}[^，。！？!?；;]{{0,8}}(?:核验|验证)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, clause):
            scope_start = 0
            for reset in _RELATION_SCOPE_RESET_RE.finditer(
                clause, 0, match.start()
            ):
                scope_start = reset.start()
            relation_context = clause[scope_start : match.end()]
            if not _is_modal_or_attributed(relation_context):
                return "把声称、请求或拟议动作写成已取得、已交付或已验证"
    return None


def _draft_open_boundary_kinds(draft: str) -> set[str]:
    tail = _without_dialogue(draft[-1000:])
    explicit_open = bool(
        re.search(r"(?:仍|还|只)留(?:着)?[^。！？!?\n]{0,12}(?:门)?缝", tail)
        or re.search(r"(?:没有|并未|未)[^。！？!?\n]{0,16}(?:插回|落入|落进)[^。！？!?\n]{0,8}槽", tail)
    )
    if not explicit_open:
        return set()
    kinds = {
        kind
        for kind in ("院门", "房门")
        if kind in tail
    }
    if not kinds:
        return set()
    for sentence in re.split(r"[。！？!?\n]", tail):
        if _is_modal_or_attributed(sentence):
            continue
        if re.search(
            r"(?:封住|封死|关死|关闭|关上|合拢|堵死|锁死)(?:了)?(?:院门|房门|门缝|门口)"
            r"|(?:院门|房门|门缝|门口)(?:已经|已|被|给)?(?:封住|封死|关死|关闭|关上|合拢|堵死|锁死)"
            r"|(?:木栓|门栓)[^，；;]{0,10}(?:落槽|插入|落下|上栓)",
            sentence,
        ):
            explicit_open = False
    return kinds if explicit_open else set()


def _boundary_state_reason(clause: str, draft: str) -> str | None:
    open_kinds = _draft_open_boundary_kinds(draft)
    if not open_kinds:
        return None
    match = re.search(
        r"(?:封住|封死|关死|关闭|关上|合拢|堵死|锁死)(?:了)?(?:院门|房门|门缝|门口)"
        r"|(?:院门|房门|门缝|门口)(?:已经|已|被|给)?(?:封住|封死|关死|关闭|关上|合拢|堵死|锁死)"
        r"|(?:木栓|门栓)[^，。！？!?；;]{0,10}(?:落槽|插入|落下|上栓)",
        clause,
    )
    if not match:
        return None
    claimed_kind = next(
        (kind for kind in ("院门", "房门") if kind in match.group(0)),
        None,
    )
    if claimed_kind is not None and claimed_kind not in open_kinds:
        return None
    if "门缝" in match.group(0) and len(open_kinds) != 1:
        return None
    local = clause[max(0, match.start() - 8) : match.end()]
    if _is_modal_or_attributed(local) or re.search(r"(?:将|要)$", local[: match.start() - max(0, match.start() - 8)]):
        return None
    # “木栓落下便会……” describes the consequence of a possible future
    # action.  Look immediately after the matched relation, but do not let a
    # consequence clause erase an explicitly completed state such as
    # “院门已关死，会……”.
    after = clause[match.end() : match.end() + 10]
    if re.match(
        r"后(?:的)?[^，。！？!?；;\n]{0,12}(?:后果|结果|风险|影响|可能性)",
        after,
    ):
        # A report may discuss the consequence *of* a hypothetical action
        # without asserting that the action occurred.  Keep the genuinely
        # completed form fail-closed (e.g. ``木栓已经落槽`` or
        # ``木栓落槽后院门已经关死``).
        return None
    if (
        not re.search(r"(?:已经|已|了|被|给)", match.group(0))
        and re.match(
            r"[^，。！？!?；;]{0,2}(?:便|就|才|则)?(?:会|将|可能|要(?!求))",
            after,
        )
    ):
        return None
    return "把仍被维持的开口或准备动作写成已经关闭、封死或执行完毕"


def detect_evidence_hierarchy_escalations(
    draft: str, claims: Iterable[dict[str, str]]
) -> list[dict[str, Any]]:
    pending = extract_pending_propositions(draft)
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in claims:
        path, claim = item.get("field_path"), item.get("claim")
        if not isinstance(path, str) or not isinstance(claim, str):
            continue
        for clause, inherited_modal in _claim_clauses(claim):
            boundary_reason = (
                None
                if inherited_modal
                else _boundary_state_reason(clause, draft)
            )
            if boundary_reason:
                key = ("REPORT_BOUNDARY_STATE_ESCALATION", path, "", clause)
                if key not in seen:
                    seen.add(key)
                    findings.append(
                        {
                            "code": key[0], "field_path": path, "claim": claim,
                            "clause": clause, "reason": boundary_reason,
                            "subject_group": None, "object": None, "draft_sources": [],
                        }
                    )
            for proposition in pending:
                reason = None
                if not inherited_modal:
                    reason = _boundary_object_reason(
                        clause,
                        proposition["subject_group"],
                        proposition["object"],
                    ) or _completion_reason(clause, proposition["object"])
                if not reason:
                    continue
                key = (
                    "REPORT_EVIDENCE_LEVEL_ESCALATION", path,
                    proposition["object"], clause,
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {
                        "code": key[0], "field_path": path, "claim": claim,
                        "clause": clause, "reason": reason,
                        "subject_group": proposition["subject_group"],
                        "object": proposition["object"],
                        "draft_sources": proposition["sources"],
                    }
                )
    return findings


def reader_factual_claims(review: dict[str, Any]) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            claims.append({"field_path": path, "claim": value})

    recap = review.get("reader_recap")
    if isinstance(recap, dict):
        for field, value in recap.items():
            add(f"reader_recap.{field}", value)
    adversarial = review.get("adversarial_checks")
    if isinstance(adversarial, dict):
        for field, values in adversarial.items():
            if isinstance(values, list):
                for index, value in enumerate(values):
                    add(f"adversarial_checks.{field}[{index}]", value)
    audit = review.get("mechanism_audit")
    if isinstance(audit, dict):
        reconstruction = audit.get("first_read_reconstruction")
        if isinstance(reconstruction, dict):
            for field, value in reconstruction.items():
                add(f"mechanism_audit.first_read_reconstruction.{field}", value)
        for ledger_name in ("pov_source_ledger", "boundary_action_ledger", "shock_response_ledger", "dialogue_register_ledger"):
            rows = audit.get(ledger_name)
            if isinstance(rows, list):
                for index, row in enumerate(rows):
                    if isinstance(row, dict):
                        for field in ("information_source", "explanation", "goal", "response_window"):
                            add(f"mechanism_audit.{ledger_name}[{index}].{field}", row.get(field))
    pacing = review.get("pacing_diagnostics")
    if isinstance(pacing, dict):
        add("pacing_diagnostics.first_1000_chars_result", pacing.get("first_1000_chars_result"))
        for field in ("first_active_pressure", "core_threat_activation", "first_costly_choice"):
            row = pacing.get(field)
            if isinstance(row, dict):
                add(f"pacing_diagnostics.{field}.effect", row.get("effect"))
        turns = pacing.get("pressure_turns")
        if isinstance(turns, list):
            for index, row in enumerate(turns):
                if isinstance(row, dict):
                    add(f"pacing_diagnostics.pressure_turns[{index}].effect", row.get("effect"))
    experience = review.get("reading_experience")
    if isinstance(experience, dict):
        add("reading_experience.next_click_reason", experience.get("next_click_reason"))
        reasons = experience.get("friction_reasons")
        if isinstance(reasons, list):
            for index, value in enumerate(reasons):
                add(f"reading_experience.friction_reasons[{index}]", value)
    benchmark = review.get("benchmark_comparison")
    if isinstance(benchmark, dict):
        for field, row in benchmark.items():
            if isinstance(row, dict):
                add(f"benchmark_comparison.{field}.assessment", row.get("assessment"))
    for list_field, claim_field in (("warnings", "explanation"), ("evidence", "finding")):
        rows = review.get(list_field)
        if isinstance(rows, list):
            for index, row in enumerate(rows):
                if isinstance(row, dict):
                    add(f"{list_field}[{index}].{claim_field}", row.get(claim_field))
    return claims


def settlement_factual_claims(settlement: dict[str, Any]) -> list[dict[str, str]]:
    claims: list[dict[str, str]] = []

    def add(path: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            claims.append({"field_path": path, "claim": value})

    summary = settlement.get("reader_visible_summary")
    if isinstance(summary, dict):
        add("reader_visible_summary.core", summary.get("core"))
    hook = settlement.get("hook")
    if isinstance(hook, dict):
        add("hook.content", hook.get("content"))
    evidence = settlement.get("change_evidence")
    if isinstance(evidence, list):
        for index, item in enumerate(evidence):
            if isinstance(item, dict):
                add(f"change_evidence[{index}].value", item.get("value"))
                add(f"change_evidence[{index}].finding", item.get("finding"))
    return claims
