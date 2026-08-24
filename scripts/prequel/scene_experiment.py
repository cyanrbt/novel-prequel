from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any

from .errors import ArtifactValidationError
from .prompt_native import validate_schema_instance
from .run_manifest import fingerprint
from .state_store import atomic_save_json, atomic_save_text


CONDITIONS = (
    "contract_first",
    "simulation_fixed",
    "simulation_rolling",
)
BLIND_LABELS = ("A", "B", "C")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError("实验候选正文不能为空")
    return value.rstrip() + "\n"


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取实验来源文件 {path}: {exc}") from exc


def cjk_count(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", value))


def scene_packet_fingerprint(packet: dict[str, Any]) -> str:
    return fingerprint(packet)


def _source_path(project_root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ArtifactValidationError(f"{label}路径必须是非空字符串")
    requested = Path(relative)
    if requested.is_absolute():
        raise ArtifactValidationError(f"{label}必须使用仓库内相对路径")
    root = project_root.resolve()
    path = (root / requested).resolve()
    if not path.is_relative_to(root):
        raise ArtifactValidationError(f"{label}越出仓库边界")
    if not path.is_file():
        raise ArtifactValidationError(f"{label}不存在: {relative}")
    return path


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ArtifactValidationError(f"{label}不得重复")


def _cards_by_id(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {card["actor_id"]: card for card in packet["character_cards"]}


def _pov_actor_id(packet: dict[str, Any]) -> str:
    pov = packet["pov_character"]
    matches = [
        card["actor_id"]
        for card in packet["character_cards"]
        if pov in {card["actor_id"], card["display_name"]}
    ]
    if len(matches) != 1:
        raise ArtifactValidationError("POV 人物必须唯一对应一张角色卡")
    return matches[0]


def validate_scene_packet(
    project_root: Path, packet: dict[str, Any]
) -> list[str]:
    checks = validate_schema_instance(
        project_root,
        "schemas/scene_experiment.schema.json",
        packet,
        label="场景实验输入",
    )
    bindings = packet["source_bindings"]
    source_fields = (
        ("state_path", "state_sha256", "状态来源"),
        ("source_chapter_path", "source_chapter_sha256", "章节来源"),
        ("voice_profile_path", "voice_profile_sha256", "文风画像来源"),
        ("taste_contract_path", "taste_contract_sha256", "偏好合同来源"),
    )
    for path_key, hash_key, label in source_fields:
        path = _source_path(project_root, bindings[path_key], label)
        actual = file_sha256(path)
        if bindings[hash_key] != actual:
            raise ArtifactValidationError(
                f"{label} SHA-256 已变化: expected={bindings[hash_key]}, actual={actual}"
            )

    cards = packet["character_cards"]
    actor_ids = [card["actor_id"] for card in cards]
    display_names = [card["display_name"] for card in cards]
    _unique(actor_ids, "角色 actor_id")
    _unique(display_names, "角色 display_name")
    _pov_actor_id(packet)

    public = packet["public_seed"]
    public_fact_ids = [
        item["id"] for group in (public["fixed_facts"], public["world_rules"])
        for item in group
    ]
    _unique(public_fact_ids, "公开事实与世界规则 ID")
    horizon_ids = [item["beat_id"] for item in public["initial_horizon"]]
    _unique(horizon_ids, "暂定节拍 ID")
    for card in cards:
        fact_ids = [
            item["id"]
            for group in (card["known_facts"], card["false_beliefs"])
            for item in group
        ]
        _unique(fact_ids, f"角色 {card['actor_id']} 的事实 ID")

    prose = packet["prose_contract"]
    if prose["min_cjk_characters"] >= prose["max_cjk_characters"]:
        raise ArtifactValidationError("正文最小长度必须小于最大长度")
    packet_hash = scene_packet_fingerprint(packet)
    return checks + [
        "scene experiment source hashes validated",
        "scene experiment actors and POV validated",
        f"scene experiment packet fingerprint: {packet_hash}",
    ]


def available_fact_ids(packet: dict[str, Any], actor_id: str) -> set[str]:
    cards = _cards_by_id(packet)
    if actor_id not in cards:
        raise ArtifactValidationError(f"实验中不存在角色: {actor_id}")
    card = cards[actor_id]
    return {
        item["id"]
        for group in (
            card["known_facts"],
            card["false_beliefs"],
        )
        for item in group
    }


def validate_character_intention(
    project_root: Path,
    packet: dict[str, Any],
    intention: dict[str, Any],
    *,
    actor_id: str,
    tick: int,
) -> list[str]:
    validate_scene_packet(project_root, packet)
    checks = validate_schema_instance(
        project_root,
        "schemas/character_intention.schema.json",
        intention,
        label="角色意图",
    )
    if intention["experiment_id"] != packet["experiment_id"]:
        raise ArtifactValidationError("角色意图 experiment_id 与输入不一致")
    expected_source = scene_packet_fingerprint(packet)
    if intention["source_fingerprint"] != expected_source:
        raise ArtifactValidationError("角色意图引用了不同的实验输入")
    if intention["actor_id"] != actor_id or intention["tick"] != tick:
        raise ArtifactValidationError("角色意图的 actor_id 或 tick 与任务不一致")
    unknown = sorted(set(intention["used_fact_ids"]) - available_fact_ids(packet, actor_id))
    if unknown:
        raise ArtifactValidationError(
            f"角色 {actor_id} 使用了越界事实 ID: {', '.join(unknown)}"
        )
    return checks + [
        f"character intention bound: {actor_id}/tick-{tick}",
        "character intention fact references validated",
    ]


def validate_contract_scene_plan(
    project_root: Path,
    packet: dict[str, Any],
    plan: dict[str, Any],
) -> list[str]:
    validate_scene_packet(project_root, packet)
    checks = validate_schema_instance(
        project_root,
        "schemas/contract_scene_plan.schema.json",
        plan,
        label="控制组场景计划",
    )
    if (
        plan["experiment_id"] != packet["experiment_id"]
        or plan["source_fingerprint"] != scene_packet_fingerprint(packet)
        or plan["pov_character"] != packet["pov_character"]
    ):
        raise ArtifactValidationError("控制组场景计划没有绑定当前实验输入")
    event_ids = [event["event_id"] for event in plan["fixed_event_chain"]]
    _unique(event_ids, "控制组事件 ID")
    return checks + ["contract-first scene plan binding validated"]


def build_actor_observations(
    resolution: dict[str, Any], actor_id: str
) -> list[dict[str, Any]]:
    observations = []
    for event in sorted(resolution["events"], key=lambda item: item["order"]):
        if actor_id not in event["observable_by"]:
            continue
        observations.append(
            {
                "event_id": event["event_id"],
                "order": event["order"],
                "visible_actor": event["visible_actor"],
                "action": event["action"],
                "observable_result": event["observable_result"],
                "state_changes": list(event["state_changes"]),
            }
        )
    return observations


def validate_world_resolution(
    project_root: Path,
    packet: dict[str, Any],
    resolution: dict[str, Any],
    intentions: dict[str, dict[str, Any]],
    *,
    tick: int,
) -> list[str]:
    validate_scene_packet(project_root, packet)
    checks = validate_schema_instance(
        project_root,
        "schemas/world_resolution.schema.json",
        resolution,
        label="世界结算",
    )
    expected_source = scene_packet_fingerprint(packet)
    if (
        resolution["experiment_id"] != packet["experiment_id"]
        or resolution["source_fingerprint"] != expected_source
        or resolution["tick"] != tick
    ):
        raise ArtifactValidationError("世界结算没有绑定当前实验输入或 tick")

    cards = _cards_by_id(packet)
    if set(intentions) != set(cards):
        missing = sorted(set(cards) - set(intentions))
        extra = sorted(set(intentions) - set(cards))
        raise ArtifactValidationError(
            "世界结算必须接收每个角色恰好一份意图"
            f"; missing={missing}; extra={extra}"
        )
    action_to_actor: dict[str, str] = {}
    expected_intent_hashes: dict[str, str] = {}
    for actor_id, intention in intentions.items():
        validate_character_intention(
            project_root, packet, intention, actor_id=actor_id, tick=tick
        )
        action_id = intention["action_id"]
        if action_id in action_to_actor:
            raise ArtifactValidationError(f"角色行动 ID 重复: {action_id}")
        action_to_actor[action_id] = actor_id
        expected_intent_hashes[actor_id] = fingerprint(intention)
    if resolution["intent_fingerprints"] != expected_intent_hashes:
        raise ArtifactValidationError("世界结算引用的角色意图指纹不一致")

    events = resolution["events"]
    event_ids = [event["event_id"] for event in events]
    _unique(event_ids, "世界结算事件 ID")
    orders = [event["order"] for event in events]
    if sorted(orders) != list(range(1, len(events) + 1)):
        raise ArtifactValidationError("世界结算事件 order 必须从1连续递增")
    allowed_observers = set(cards)
    for event in events:
        observers = set(event["observable_by"])
        if not observers.issubset(allowed_observers):
            raise ArtifactValidationError(
                f"事件 {event['event_id']} 含未知观察者: "
                + ", ".join(sorted(observers - allowed_observers))
            )
        intent_ref = event["intent_ref"]
        if intent_ref == "WORLD_PRESSURE":
            if event["actor_id"] != "world":
                raise ArtifactValidationError("WORLD_PRESSURE 事件 actor_id 必须为 world")
        else:
            if intent_ref not in action_to_actor:
                raise ArtifactValidationError(
                    f"事件 {event['event_id']} 引用了未知行动: {intent_ref}"
                )
            if event["actor_id"] != action_to_actor[intent_ref]:
                raise ArtifactValidationError(
                    f"事件 {event['event_id']} 的 actor_id 与行动来源不一致"
                )
    rule_ids = {item["id"] for item in packet["public_seed"]["world_rules"]}
    unknown_rules = sorted(set(resolution["rule_ids_used"]) - rule_ids)
    if unknown_rules:
        raise ArtifactValidationError(
            "世界结算使用了未登记规则: " + ", ".join(unknown_rules)
        )
    return checks + [
        f"world resolution bound: tick-{tick}",
        "world resolution intent and observer references validated",
    ]


def build_pov_event_trace(
    project_root: Path,
    packet: dict[str, Any],
    resolution: dict[str, Any],
    intentions: dict[str, dict[str, Any]],
    *,
    tick: int,
    opening_state: str | None = None,
) -> dict[str, Any]:
    validate_world_resolution(
        project_root, packet, resolution, intentions, tick=tick
    )
    pov_actor = _pov_actor_id(packet)
    visible = []
    visible_states: list[str] = []
    for event in sorted(resolution["events"], key=lambda item: item["order"]):
        if pov_actor not in event["observable_by"]:
            continue
        visible.append(
            {
                "event_id": event["event_id"],
                "order": event["order"],
                "visible_actor": event["visible_actor"],
                "action": event["action"],
                "observable_result": event["observable_result"],
                "pov_may_infer": event["pov_may_infer"],
                "state_changes": list(event["state_changes"]),
            }
        )
        for state in event["state_changes"]:
            if state not in visible_states:
                visible_states.append(state)
    if not visible:
        raise ArtifactValidationError("当前世界结算没有 POV 可见事件")
    if not visible_states:
        visible_states = [visible[-1]["observable_result"]]
    trace = {
        "schema": "novel-pov-event-trace",
        "experiment_id": packet["experiment_id"],
        "source_fingerprint": scene_packet_fingerprint(packet),
        "resolution_fingerprint": fingerprint(resolution),
        "tick": tick,
        "pov_character": packet["pov_character"],
        "opening_state": opening_state or packet["public_seed"]["opening_state"],
        "visible_events": visible,
        "world_state_visible_after": visible_states,
        "unresolved_pressure": resolution["unresolved_pressure"],
        "hidden_fields_removed": True,
    }
    validate_schema_instance(
        project_root,
        "schemas/pov_event_trace.schema.json",
        trace,
        label="POV事件轨迹",
    )
    return trace


def validate_rolling_horizon(
    project_root: Path,
    packet: dict[str, Any],
    resolution: dict[str, Any],
    horizon: dict[str, Any],
) -> list[str]:
    validate_scene_packet(project_root, packet)
    checks = validate_schema_instance(
        project_root,
        "schemas/rolling_horizon.schema.json",
        horizon,
        label="滚动节拍",
    )
    if (
        horizon["experiment_id"] != packet["experiment_id"]
        or horizon["source_fingerprint"] != scene_packet_fingerprint(packet)
        or horizon["after_resolution_fingerprint"] != fingerprint(resolution)
    ):
        raise ArtifactValidationError("滚动节拍没有绑定当前输入与世界结算")
    if horizon["retained_far_milestone"] != packet["public_seed"]["far_milestone"]:
        raise ArtifactValidationError("滚动节拍擅自改变了远期里程碑")
    expected_old = {
        beat["beat_id"] for beat in packet["public_seed"]["initial_horizon"][1:]
    }
    if set(horizon["old_beat_ids"]) != expected_old:
        raise ArtifactValidationError("滚动节拍未准确引用冻结的后续节拍")
    valid_events = {event["event_id"] for event in resolution["events"]}
    if not set(horizon["forcing_event_ids"]).issubset(valid_events):
        raise ArtifactValidationError("滚动节拍引用了不存在的结算事件")
    revised_ids = [beat["beat_id"] for beat in horizon["revised_beats"]]
    _unique(revised_ids, "修订节拍 ID")
    return checks + [
        "rolling horizon source and resolution bindings validated",
        "rolling horizon retained far milestone",
    ]


def validate_candidate_texts(
    packet: dict[str, Any], candidates: dict[str, str]
) -> dict[str, str]:
    if set(candidates) != set(CONDITIONS):
        raise ArtifactValidationError(
            "实验候选必须恰好包含 contract_first、simulation_fixed、simulation_rolling"
        )
    normalized = {name: canonical_text(text) for name, text in candidates.items()}
    minimum = packet["prose_contract"]["min_cjk_characters"]
    maximum = packet["prose_contract"]["max_cjk_characters"]
    leak_markers = (
        "contract_first",
        "simulation_fixed",
        "simulation_rolling",
        "novel-character-intention",
        "novel-world-resolution",
        "novel-pov-event-trace",
    )
    for name, text in normalized.items():
        length = cjk_count(text)
        if length < minimum or length > maximum:
            raise ArtifactValidationError(
                f"候选 {name} 中文字符数 {length} 越出共同边界 {minimum}-{maximum}"
            )
        leaked = [marker for marker in leak_markers if marker in text]
        if leaked:
            raise ArtifactValidationError(
                f"候选 {name} 泄漏实验提示标记: {', '.join(leaked)}"
            )
    return normalized


def prepare_blind_bundle(
    project_root: Path,
    packet: dict[str, Any],
    candidates: dict[str, str],
    output_dir: Path,
    *,
    seed: str | None = None,
) -> dict[str, Any]:
    validate_scene_packet(project_root, packet)
    normalized = validate_candidate_texts(packet, candidates)
    output_dir = output_dir.resolve()
    work_boundary = (project_root / "novel/work").resolve()
    if not output_dir.is_relative_to(work_boundary):
        raise ArtifactValidationError("盲评输出必须位于 novel/work/ 内")
    blind_dir = output_dir / "blind"
    mapping_path = output_dir / "blind_mapping.json"
    packet_path = blind_dir / "blind_packet.json"
    occupied = [
        path
        for path in (mapping_path, packet_path, *(blind_dir / f"candidate_{x}.txt" for x in BLIND_LABELS))
        if path.exists()
    ]
    if occupied:
        raise ArtifactValidationError(
            "拒绝覆盖既有盲评工件: " + ", ".join(str(path) for path in occupied)
        )
    blind_dir.mkdir(parents=True, exist_ok=True)
    blind_seed = seed or f"{packet['experiment_id']}:blind-v1"
    shuffled = list(CONDITIONS)
    random.Random(blind_seed).shuffle(shuffled)
    mapping: dict[str, Any] = {
        "schema": "novel-scene-experiment-blind-mapping",
        "experiment_id": packet["experiment_id"],
        "source_fingerprint": scene_packet_fingerprint(packet),
        "seed_sha256": text_sha256(blind_seed),
        "mapping": {},
    }
    blind_candidates: dict[str, str] = {}
    candidate_fingerprints: dict[str, str] = {}
    for label, condition in zip(BLIND_LABELS, shuffled):
        text = normalized[condition]
        digest = text_sha256(text)
        target = blind_dir / f"candidate_{label}.txt"
        atomic_save_text(target, text)
        mapping["mapping"][label] = {
            "condition": condition,
            "candidate_sha256": digest,
        }
        blind_candidates[label] = text
        candidate_fingerprints[label] = digest
    blind_packet = {
        "schema": "novel-scene-experiment-blind-packet",
        "experiment_id": packet["experiment_id"],
        "source_fingerprint": scene_packet_fingerprint(packet),
        "candidate_fingerprints": candidate_fingerprints,
        "candidates": blind_candidates,
        "questions": [
            "最愿意继续读哪一版？",
            "从哪一句开始明显像 AI？",
            "哪个关键行动最像只能由这个人物作出？",
            "哪一版最像事情真的发生了，而不是作者兑现提纲？",
        ],
        "workflow_state": "WAITING_USER",
    }
    atomic_save_json(mapping_path, mapping)
    atomic_save_json(packet_path, blind_packet)
    return {"mapping": mapping, "blind_packet": blind_packet}


def validate_scene_experiment_comparison(
    project_root: Path,
    packet: dict[str, Any],
    comparison: dict[str, Any],
    candidates: dict[str, str],
) -> list[str]:
    validate_scene_packet(project_root, packet)
    checks = validate_schema_instance(
        project_root,
        "schemas/scene_experiment_comparison.schema.json",
        comparison,
        label="场景机制盲评",
    )
    if set(candidates) != set(BLIND_LABELS):
        raise ArtifactValidationError("盲评候选必须恰好包含 A、B、C")
    normalized = {label: canonical_text(text) for label, text in candidates.items()}
    expected_hashes = {label: text_sha256(text) for label, text in normalized.items()}
    if (
        comparison["experiment_id"] != packet["experiment_id"]
        or comparison["source_fingerprint"] != scene_packet_fingerprint(packet)
        or comparison["candidate_fingerprints"] != expected_hashes
    ):
        raise ArtifactValidationError("盲评报告没有绑定当前实验与候选正文")
    if set(comparison["ranking"]) != set(BLIND_LABELS):
        raise ArtifactValidationError("盲评 ranking 必须恰好覆盖 A、B、C")
    if comparison["preferred_candidate"] != comparison["ranking"][0]:
        raise ArtifactValidationError("preferred_candidate 必须等于 ranking 第一项")
    findings = comparison["candidate_findings"]
    if {item["candidate"] for item in findings} != set(BLIND_LABELS):
        raise ArtifactValidationError("candidate_findings 必须恰好覆盖 A、B、C")
    for item in findings:
        label = item["candidate"]
        observations = item["strengths"] + item["gaps"]
        if not observations:
            raise ArtifactValidationError(f"候选 {label} 至少需要一项带引用观察")
        for observation in observations:
            if observation["quote"] not in normalized[label]:
                raise ArtifactValidationError(
                    f"候选 {label} 的观察引用不在正文中: {observation['quote']!r}"
                )
    return checks + [
        "scene experiment comparison covers A/B/C",
        "scene experiment comparison fingerprints and quotes validated",
    ]


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError(f"无法读取{label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label}根节点必须是object")
    return value
