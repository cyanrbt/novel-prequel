from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .evaluation import canonicalize_quote
from .quality import Issue, _taste_contract_issues


AUDIT_FIELDS = {
    "artifact_sha256",
    "verdict",
    "pov_source_ledger",
    "boundary_action_ledger",
    "shock_response_ledger",
    "dialogue_register_ledger",
    "first_read_reconstruction",
    "blocking_issues",
    "revision_instructions",
}

POV_PATTERN = re.compile(
    r"看见|看清|看出|认出|听出|知道|确定|明白|意识到|察觉|发现|记得|想起|断定|猜到|以为|觉得"
)
NARRATOR_IDENTITY_PATTERN = re.compile(
    r"(?:门外|门后|窗外|来人|那人|那道声音|脚步声)[^。！？!?\n]{0,20}(?:就是|正是|原来是|是)[^。！？!?\n]{1,20}"
)
BOUNDARY_NOUN_PATTERN = re.compile(
    r"门|窗|帘|门缝|门闩|门栓|门槛|院墙|墙头|屋里|屋外|门内|门外"
)
BOUNDARY_ACTION_PATTERN = re.compile(
    r"开|关|推|拉|闩|栓|跨|迈|进|出|探|伸|退|站|看|认|绕|堵|跑|走|过来|过去"
)
SHOCK_PATTERN = re.compile(
    r"死了|死人|死者|尸体|尸身|亡人|咽气|断气|下葬|出殡|入殓|灵堂|棺材|棺木|不可能回来"
)
DIALOGUE_PATTERN = re.compile(r"“[^”\n]{1,240}”")


def _sentence_spans(text: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        quote = raw.strip()
        if not quote or re.fullmatch(r"第\s*\d+\s*章(?:[：:].*)?", quote):
            continue
        start = match.start() + leading
        spans.append({"quote": quote, "start": start, "end": start + len(quote)})
    return spans


def _with_ids(prefix: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"anchor_id": f"{prefix}-{index:03d}", **row}
        for index, row in enumerate(rows, start=1)
    ]


def _even_dialogue_sample(rows: list[dict[str, Any]], limit: int = 24) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    indexes = {
        round(index * (len(rows) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [row for index, row in enumerate(rows) if index in indexes]


def extract_scene_audit_anchors(draft: str) -> dict[str, list[dict[str, Any]]]:
    sentences = _sentence_spans(draft)
    pov = [
        {**item, "kind": "perception_or_knowledge"}
        for item in sentences
        if POV_PATTERN.search(item["quote"])
    ]
    for item in sentences:
        if NARRATOR_IDENTITY_PATTERN.search(item["quote"]) and item not in pov:
            pov.append({**item, "kind": "narrator_identity_claim"})
    pov.sort(key=lambda item: item["start"])

    boundaries = [
        item
        for item in sentences
        if BOUNDARY_NOUN_PATTERN.search(item["quote"])
        and BOUNDARY_ACTION_PATTERN.search(item["quote"])
    ]
    shocks = [item for item in sentences if SHOCK_PATTERN.search(item["quote"])]
    dialogues = [
        {"quote": match.group(0), "start": match.start(), "end": match.end()}
        for match in DIALOGUE_PATTERN.finditer(draft)
    ]
    return {
        "pov_claims": _with_ids("POV", pov),
        "boundary_actions": _with_ids("BOUNDARY", boundaries),
        "shock_triggers": _with_ids("SHOCK", shocks),
        "dialogue_samples": _with_ids(
            "DIALOGUE", _even_dialogue_sample(dialogues)
        ),
    }


def build_scene_audit_packet(
    draft: str,
    taste_contract: dict[str, Any],
    *,
    artifact_label: str = "demo",
    prior_reader_facts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "artifact_label": artifact_label,
        "artifact_sha256": hashlib.sha256(draft.encode("utf-8")).hexdigest(),
        "prior_reader_facts": prior_reader_facts or [],
        "user_taste_contract": taste_contract,
        "audit_anchors": extract_scene_audit_anchors(draft),
        "draft": draft,
        "coverage_rule": "四本账必须逐项覆盖输入中的每个anchor_id；不得合并、遗漏或自造anchor。",
    }


def build_scene_audit_prompt(project_root: Path, packet: dict[str, Any]) -> str:
    try:
        role = (project_root / "agents/demo_scene_reviewer.md").read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取片段场景审查指令: {exc}") from exc
    return role.rstrip() + "\n\n# 唯一输入工件\n" + json.dumps(
        packet, ensure_ascii=False, indent=2
    )


def canonicalize_scene_audit_anchor_quotes(
    audit: Any, draft: str
) -> None:
    """Restore model-copied anchor quotes from their deterministic IDs."""
    if not isinstance(audit, dict):
        return
    anchors = extract_scene_audit_anchors(draft)
    specs = (
        ("pov_source_ledger", "pov_claims", "claim_quote"),
        ("boundary_action_ledger", "boundary_actions", "action_quote"),
        ("shock_response_ledger", "shock_triggers", "trigger_quote"),
        ("dialogue_register_ledger", "dialogue_samples", "dialogue_quote"),
    )
    for ledger_field, anchor_field, quote_field in specs:
        expected = {
            item["anchor_id"]: item["quote"]
            for item in anchors[anchor_field]
        }
        rows = audit.get(ledger_field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            anchor_id = row.get("anchor_id")
            if anchor_id in expected:
                row[quote_field] = expected[anchor_id]

    auxiliary_fields = (
        ("pov_source_ledger", ("source_quote",)),
        ("boundary_action_ledger", ("before_quote", "after_quote")),
        ("shock_response_ledger", ("response_quote",)),
    )
    for ledger_field, quote_fields in auxiliary_fields:
        rows = audit.get(ledger_field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for quote_field in quote_fields:
                quote = row.get(quote_field)
                if not isinstance(quote, str) or not quote or quote in draft:
                    continue
                canonical = canonicalize_quote(quote, draft)
                if canonical == quote:
                    # Preserve the legacy auxiliary-only punctuation repair;
                    # anchor context makes a unique short stem safe here.
                    stem = quote.strip().rstrip("。！？!?；;，,")
                    if stem and draft.count(stem) == 1:
                        canonical = stem
                if canonical != quote:
                    row[quote_field] = canonical


def _validate_ledger_coverage(
    *,
    audit: dict[str, Any],
    field: str,
    anchors: list[dict[str, Any]],
    quote_field: str,
) -> tuple[list[Issue], dict[str, dict[str, Any]]]:
    issues: list[Issue] = []
    rows = audit.get(field)
    if not isinstance(rows, list):
        return [Issue("SCENE_BAD_LEDGER", "P1", f"{field}必须是数组", repr(rows))], {}
    expected = {item["anchor_id"]: item for item in anchors}
    actual: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("anchor_id"), str):
            issues.append(Issue("SCENE_BAD_LEDGER_ROW", "P1", f"{field}条目无anchor_id", repr(row)))
            continue
        anchor_id = row["anchor_id"]
        if anchor_id in actual:
            issues.append(Issue("SCENE_DUPLICATE_ANCHOR", "P1", f"{field}重复anchor_id", anchor_id))
            continue
        actual[anchor_id] = row
        anchor = expected.get(anchor_id)
        if anchor is None:
            issues.append(Issue("SCENE_UNKNOWN_ANCHOR", "P1", f"{field}包含自造anchor", anchor_id))
        elif row.get(quote_field) != anchor["quote"]:
            issues.append(
                Issue(
                    "SCENE_ANCHOR_QUOTE_MISMATCH",
                    "P1",
                    f"{field}没有原样复制anchor引文",
                    repr({"id": anchor_id, "expected": anchor["quote"], "actual": row.get(quote_field)}),
                )
            )
    missing = sorted(set(expected) - set(actual))
    if missing:
        issues.append(
            Issue(
                "SCENE_ANCHOR_COVERAGE_MISSING",
                "P1",
                f"{field}没有覆盖全部高风险锚点",
                repr(missing),
            )
        )
    return issues, actual


def _quote_exists(draft: str, quote: Any) -> bool:
    return isinstance(quote, str) and bool(quote) and quote in draft


def validate_scene_mechanism_audit(
    audit: Any,
    draft: str,
    *,
    taste_contract: dict[str, Any] | None = None,
) -> list[Issue]:
    if not isinstance(audit, dict):
        return [Issue("SCENE_AUDIT_NOT_OBJECT", "P1", "场景审计不是object", repr(audit))]
    issues: list[Issue] = []
    if set(audit) != AUDIT_FIELDS:
        issues.append(
            Issue(
                "SCENE_AUDIT_FIELDS",
                "P1",
                "场景审计字段不完整或包含额外字段",
                repr(sorted(audit)),
            )
        )
    expected_hash = hashlib.sha256(draft.encode("utf-8")).hexdigest()
    if audit.get("artifact_sha256") != expected_hash:
        issues.append(Issue("SCENE_AUDIT_HASH_MISMATCH", "P1", "场景审计未绑定当前文本", repr(audit.get("artifact_sha256"))))
    verdict = audit.get("verdict")
    if verdict not in {"PASS", "REVISE"}:
        issues.append(Issue("SCENE_AUDIT_BAD_VERDICT", "P1", "场景审计结论无效", repr(verdict)))

    anchors = extract_scene_audit_anchors(draft)
    ledger_specs = (
        ("pov_source_ledger", "pov_claims", "claim_quote"),
        ("boundary_action_ledger", "boundary_actions", "action_quote"),
        ("shock_response_ledger", "shock_triggers", "trigger_quote"),
        ("dialogue_register_ledger", "dialogue_samples", "dialogue_quote"),
    )
    ledgers: dict[str, dict[str, dict[str, Any]]] = {}
    for field, anchor_field, quote_field in ledger_specs:
        found, rows = _validate_ledger_coverage(
            audit=audit,
            field=field,
            anchors=anchors[anchor_field],
            quote_field=quote_field,
        )
        issues.extend(found)
        ledgers[field] = rows

    bad_findings: list[dict[str, str]] = []
    pov_by_id = {item["anchor_id"]: item for item in anchors["pov_claims"]}
    for anchor_id, row in ledgers.get("pov_source_ledger", {}).items():
        if set(row) != {"anchor_id", "claim_quote", "information_source", "source_quote", "verdict", "explanation"}:
            issues.append(Issue("SCENE_BAD_POV_ROW", "P1", "认知来源账条目结构无效", repr(row)))
            continue
        if row.get("verdict") not in {"SUPPORTED", "UNSUPPORTED", "AMBIGUOUS"}:
            issues.append(Issue("SCENE_BAD_POV_ROW", "P1", "认知来源结论无效", repr(row)))
        source_quote = row.get("source_quote")
        if source_quote is not None and not _quote_exists(draft, source_quote):
            issues.append(Issue("SCENE_FALSE_SOURCE_QUOTE", "P1", "认知来源引文不在正文", repr(source_quote)))
        anchor = pov_by_id.get(anchor_id)
        if anchor and _quote_exists(draft, source_quote):
            source_start = draft.find(source_quote)
            source_end = source_start + len(source_quote)
            if source_end > anchor["end"]:
                issues.append(Issue("SCENE_RETROACTIVE_POV_SOURCE", "P1", "认知来源出现在结论之后", repr(source_quote)))
        if row.get("verdict") != "SUPPORTED":
            bad_findings.append({"field": "pov", "id": anchor_id})

    for row in ledgers.get("boundary_action_ledger", {}).values():
        if set(row) != {"anchor_id", "action_quote", "before_quote", "after_quote", "visible_to_pov", "verdict", "explanation"}:
            issues.append(Issue("SCENE_BAD_BOUNDARY_ROW", "P1", "空间边界账条目结构无效", repr(row)))
            continue
        if row.get("verdict") not in {"COHERENT", "INCOHERENT", "UNCLEAR"} or not isinstance(row.get("visible_to_pov"), bool):
            issues.append(Issue("SCENE_BAD_BOUNDARY_ROW", "P1", "空间边界结论无效", repr(row)))
        for field in ("before_quote", "after_quote"):
            if row.get(field) is not None and not _quote_exists(draft, row[field]):
                issues.append(Issue("SCENE_FALSE_BOUNDARY_QUOTE", "P1", f"{field}引文不在正文", repr(row.get(field))))
        if row.get("verdict") != "COHERENT":
            bad_findings.append({"field": "boundary", "id": str(row.get("anchor_id"))})

    shock_by_id = {item["anchor_id"]: item for item in anchors["shock_triggers"]}
    for anchor_id, row in ledgers.get("shock_response_ledger", {}).items():
        if set(row) != {"anchor_id", "trigger_quote", "response_quote", "response_window", "verdict", "explanation"}:
            issues.append(Issue("SCENE_BAD_SHOCK_ROW", "P1", "受惊反应账条目结构无效", repr(row)))
            continue
        if row.get("verdict") not in {"CREDIBLE", "UNDERREACTION", "OVERSTAGED", "NOT_NEW_INFORMATION"}:
            issues.append(Issue("SCENE_BAD_SHOCK_ROW", "P1", "受惊反应结论无效", repr(row)))
        response_quote = row.get("response_quote")
        if response_quote is not None and not _quote_exists(draft, response_quote):
            issues.append(Issue("SCENE_FALSE_RESPONSE_QUOTE", "P1", "受惊反应引文不在正文", repr(response_quote)))
        anchor = shock_by_id.get(anchor_id)
        if anchor and row.get("verdict") == "CREDIBLE":
            if not _quote_exists(draft, response_quote):
                issues.append(Issue("SCENE_MISSING_SHOCK_RESPONSE", "P1", "CREDIBLE必须引用刺激后的即时反应", anchor_id))
            else:
                response_at = draft.find(response_quote, anchor["start"])
                compact_gap = len(re.sub(r"\s+", "", draft[anchor["end"]:response_at])) if response_at >= 0 else 10**9
                if response_at < anchor["start"] or compact_gap > 500:
                    issues.append(Issue("SCENE_LATE_SHOCK_RESPONSE", "P1", "受惊反应不在刺激后的即时窗口", repr(response_quote)))
        if row.get("verdict") not in {"CREDIBLE", "NOT_NEW_INFORMATION"}:
            bad_findings.append({"field": "shock", "id": anchor_id})

    for row in ledgers.get("dialogue_register_ledger", {}).values():
        if set(row) != {"anchor_id", "dialogue_quote", "speaker", "goal", "verdict", "explanation"}:
            issues.append(Issue("SCENE_BAD_DIALOGUE_ROW", "P1", "对白口吻账条目结构无效", repr(row)))
            continue
        if row.get("verdict") not in {"NATURAL", "STIFF", "ARCHAIC", "EXPOSITORY", "UNCLEAR"}:
            issues.append(Issue("SCENE_BAD_DIALOGUE_ROW", "P1", "对白口吻结论无效", repr(row)))
        if row.get("verdict") != "NATURAL":
            bad_findings.append({"field": "dialogue", "id": str(row.get("anchor_id"))})

    reconstruction = audit.get("first_read_reconstruction")
    reconstruction_ok = False
    if (
        isinstance(reconstruction, dict)
        and set(reconstruction) == {
            "reader_can_reconstruct",
            "required_rereads",
            "character_positions",
            "visibility_limits",
            "action_chain",
            "confusing_quotes",
        }
        and isinstance(reconstruction.get("reader_can_reconstruct"), bool)
        and isinstance(reconstruction.get("required_rereads"), int)
        and not isinstance(reconstruction.get("required_rereads"), bool)
        and reconstruction["required_rereads"] >= 0
        and all(isinstance(reconstruction.get(field), str) and reconstruction[field].strip() for field in ("character_positions", "visibility_limits", "action_chain"))
        and isinstance(reconstruction.get("confusing_quotes"), list)
        and all(_quote_exists(draft, quote) for quote in reconstruction["confusing_quotes"])
    ):
        reconstruction_ok = (
            reconstruction["reader_can_reconstruct"]
            and reconstruction["required_rereads"] == 0
            and not reconstruction["confusing_quotes"]
        )
    else:
        issues.append(Issue("SCENE_BAD_RECONSTRUCTION", "P1", "首次阅读空间复原字段无效", repr(reconstruction)))

    blockers = audit.get("blocking_issues")
    if not isinstance(blockers, list):
        issues.append(Issue("SCENE_BAD_BLOCKERS", "P1", "blocking_issues必须是数组", repr(blockers)))
        blockers = []
    else:
        for item in blockers:
            if (
                not isinstance(item, dict)
                or set(item) != {"code", "quote", "explanation"}
                or not _quote_exists(draft, item.get("quote"))
                or not isinstance(item.get("code"), str)
                or not isinstance(item.get("explanation"), str)
            ):
                issues.append(Issue("SCENE_BAD_BLOCKER", "P1", "场景阻断问题结构或引文无效", repr(item)))
    instructions = audit.get("revision_instructions")
    if not isinstance(instructions, list) or not all(isinstance(item, str) and item.strip() for item in instructions):
        issues.append(Issue("SCENE_BAD_INSTRUCTIONS", "P1", "场景修订指令必须是字符串数组", repr(instructions)))
        instructions = []

    deterministic, _ = _taste_contract_issues(draft, taste_contract)
    deterministic_p1 = [item for item in deterministic if item.severity == "P1"]
    if verdict == "PASS" and (
        bad_findings
        or not reconstruction_ok
        or blockers
        or instructions
        or deterministic_p1
    ):
        issues.append(
            Issue(
                "SCENE_PASS_CONFLICT",
                "P1",
                "PASS不得伴随视角、空间、受惊反应、对白、首次理解或偏好合同问题",
                repr({
                    "bad_findings": bad_findings,
                    "reconstruction_ok": reconstruction_ok,
                    "blocking_count": len(blockers),
                    "instruction_count": len(instructions),
                    "deterministic_codes": [item.code for item in deterministic_p1],
                }),
            )
        )
    if verdict == "REVISE" and (not blockers or not instructions):
        issues.append(Issue("SCENE_REVISE_WITHOUT_ACTION", "P1", "REVISE必须同时给出阻断引文和修订指令", repr(audit)))
    return issues
