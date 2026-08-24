#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.prequel.errors import ArtifactValidationError, PrequelError
from scripts.prequel.pipeline import parse_json_artifact
from scripts.prequel.provider import CodexCliProvider
from scripts.prequel.run_manifest import fingerprint
from scripts.prequel.scene_experiment import (
    build_actor_observations,
    build_pov_event_trace,
    cjk_count,
    file_sha256,
    load_json_object,
    prepare_blind_bundle,
    scene_packet_fingerprint,
    text_sha256,
    validate_candidate_texts,
    validate_character_intention,
    validate_contract_scene_plan,
    validate_rolling_horizon,
    validate_scene_packet,
    validate_world_resolution,
)
from scripts.prequel.state_store import atomic_save_json, atomic_save_text


DEFAULT_PACKET = (
    ROOT
    / "novel/benchmarks/scene_generation_mechanism_2026-08-24/scene_packet.json"
)
ISOLATION_ROOT = Path("/tmp/novel-scene-generation-mechanism")
STRUCTURE_MODEL = ("gpt-5.6-terra", "medium")
ACTOR_MODEL = ("gpt-5.6-luna", "high")
WRITER_MODEL = ("gpt-5.6-sol", "medium")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取实验文件 {path}: {exc}") from exc


def _write_once(path: Path, value: str) -> None:
    if path.exists():
        raise ArtifactValidationError(f"拒绝覆盖既有实验工件: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_text(path, value)


def _write_frozen_input(
    path: Path, value: str, *, resume: bool
) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_text(path, value)
        return
    if not resume:
        raise ArtifactValidationError(f"拒绝覆盖既有实验输入: {path}")
    if _read(path) != value.rstrip() + "\n":
        raise ArtifactValidationError(f"既有实验输入已变化: {path}")


def _provider(model: str, effort: str, stage: str) -> CodexCliProvider:
    isolated = ISOLATION_ROOT / stage
    isolated.mkdir(parents=True, exist_ok=True)
    marker = isolated / "ISOLATED_WORKDIR.txt"
    if not marker.exists():
        marker.write_text(
            "This workdir intentionally contains no novel source or peer output.\n",
            encoding="utf-8",
        )
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{effort}"',
    ]
    return CodexCliProvider(
        command=command,
        timeout_seconds=1200,
        project_root=isolated,
        model=model,
        reasoning_effort=effort,
    )


def _stage_prompt(role_path: Path, instruction: str, inputs: dict[str, Any]) -> str:
    return (
        _read(role_path).rstrip()
        + "\n\n# 本次冻结任务\n"
        + instruction.strip()
        + "\n\n# 唯一输入工件\n"
        + json.dumps(inputs, ensure_ascii=False, indent=2)
        + "\n"
    )


def _reuse_guard(
    workdir: Path,
    stage: str,
    prompt: str,
    output_path: Path,
    *,
    resume: bool,
) -> bool:
    if not output_path.exists():
        return False
    if not resume:
        raise ArtifactValidationError(f"阶段 {stage} 已存在；使用 --resume 或新工作区")
    meta_path = workdir / "metadata" / f"{stage}.json"
    meta = load_json_object(meta_path, f"阶段 {stage} 元数据")
    if (
        meta.get("status") != "complete"
        or meta.get("prompt_sha256") != text_sha256(prompt)
        or meta.get("output_sha256") != file_sha256(output_path)
    ):
        raise ArtifactValidationError(f"阶段 {stage} 既有工件无法安全复用")
    print(f"REUSE {stage}", flush=True)
    return True


def _call_json(
    workdir: Path,
    stage: str,
    role_file: str,
    instruction: str,
    inputs: dict[str, Any],
    schema_file: str,
    model_spec: tuple[str, str],
    *,
    resume: bool,
) -> dict[str, Any]:
    prompt = _stage_prompt(ROOT / role_file, instruction, inputs)
    output_path = workdir / "artifacts" / f"{stage}.json"
    if _reuse_guard(workdir, stage, prompt, output_path, resume=resume):
        return load_json_object(output_path, f"阶段 {stage} 工件")
    prompt_path = workdir / "prompts" / f"{stage}.md"
    raw_path = workdir / "raw" / f"{stage}.txt"
    meta_path = workdir / "metadata" / f"{stage}.json"
    _write_frozen_input(prompt_path, prompt, resume=resume)
    model, effort = model_spec
    started = time.monotonic()
    metadata = {
        "stage": stage,
        "status": "running",
        "model": model,
        "reasoning_effort": effort,
        "prompt_sha256": text_sha256(prompt),
        "started_at": now_iso(),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_json(meta_path, metadata)
    print(f"START {stage} · {model}/{effort}", flush=True)
    try:
        raw = _provider(model, effort, stage).generate(
            prompt, ROOT / schema_file
        )
        artifact = parse_json_artifact(raw, stage)
        _write_once(raw_path, raw)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_json(output_path, artifact)
    except BaseException as exc:
        metadata.update(
            {
                "status": "failed",
                "finished_at": now_iso(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        atomic_save_json(meta_path, metadata)
        raise
    metadata.update(
        {
            "status": "complete",
            "finished_at": now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "output_sha256": file_sha256(output_path),
        }
    )
    atomic_save_json(meta_path, metadata)
    print(f"DONE {stage} · {metadata['elapsed_seconds']}秒", flush=True)
    return artifact


def _call_text(
    workdir: Path,
    stage: str,
    role_file: str,
    instruction: str,
    inputs: dict[str, Any],
    model_spec: tuple[str, str],
    *,
    resume: bool,
) -> str:
    prompt = _stage_prompt(ROOT / role_file, instruction, inputs)
    output_path = workdir / "candidates" / f"{stage}.txt"
    if _reuse_guard(workdir, stage, prompt, output_path, resume=resume):
        return _read(output_path)
    prompt_path = workdir / "prompts" / f"{stage}.md"
    raw_path = workdir / "raw" / f"{stage}.txt"
    meta_path = workdir / "metadata" / f"{stage}.json"
    _write_frozen_input(prompt_path, prompt, resume=resume)
    model, effort = model_spec
    started = time.monotonic()
    metadata = {
        "stage": stage,
        "status": "running",
        "model": model,
        "reasoning_effort": effort,
        "prompt_sha256": text_sha256(prompt),
        "started_at": now_iso(),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_save_json(meta_path, metadata)
    print(f"START {stage} · {model}/{effort}", flush=True)
    try:
        raw = _provider(model, effort, stage).generate(prompt)
        text = raw.rstrip() + "\n"
        _write_once(raw_path, raw)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_text(output_path, text)
    except BaseException as exc:
        metadata.update(
            {
                "status": "failed",
                "finished_at": now_iso(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        atomic_save_json(meta_path, metadata)
        raise
    metadata.update(
        {
            "status": "complete",
            "finished_at": now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "output_sha256": file_sha256(output_path),
            "cjk_characters": cjk_count(text),
        }
    )
    atomic_save_json(meta_path, metadata)
    print(
        f"DONE {stage} · {metadata['cjk_characters']}汉字 · "
        f"{metadata['elapsed_seconds']}秒",
        flush=True,
    )
    return text


def _actor_inputs(
    packet: dict[str, Any],
    card: dict[str, Any],
    *,
    tick: int,
    pressure: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "artifact_constants": {
            "schema": "novel-character-intention",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": scene_packet_fingerprint(packet),
            "tick": tick,
            "actor_id": card["actor_id"],
            "uses_forbidden_author_knowledge": False,
        },
        "shared_public_state": {
            "timeline": packet["public_seed"]["timeline"],
            "location": packet["public_seed"]["location"],
            "opening_state": packet["public_seed"]["opening_state"],
        },
        "current_pressure": pressure,
        "character_card": card,
        "current_observations": observations,
    }


def _generate_actor_intentions(
    packet: dict[str, Any],
    workdir: Path,
    *,
    branch: str,
    tick: int,
    pressure: dict[str, Any],
    observations: dict[str, list[dict[str, Any]]],
    resume: bool,
    max_workers: int,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}

    def generate(card: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        actor_id = card["actor_id"]
        stage = f"{branch}_tick_{tick}_actor_{actor_id}"
        artifact = _call_json(
            workdir,
            stage,
            "agents/character_actor.md",
            (
                "只代表 character_card 对应角色作出一个下一步行动。"
                "逐字复制 artifact_constants；action_id 使用大写字母、数字或下划线且在本 tick 唯一。"
            ),
            _actor_inputs(
                packet,
                card,
                tick=tick,
                pressure=pressure,
                observations=observations.get(actor_id, []),
            ),
            "schemas/character_intention.schema.json",
            ACTOR_MODEL,
            resume=resume,
        )
        validate_character_intention(
            ROOT, packet, artifact, actor_id=actor_id, tick=tick
        )
        return actor_id, artifact

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(generate, card): card["actor_id"]
            for card in packet["character_cards"]
        }
        for future in as_completed(futures):
            actor_id, artifact = future.result()
            results[actor_id] = artifact
    return {card["actor_id"]: results[card["actor_id"]] for card in packet["character_cards"]}


def _resolver_inputs(
    packet: dict[str, Any],
    intentions: dict[str, dict[str, Any]],
    *,
    tick: int,
    pressure: dict[str, Any],
    prior_world_state: list[str],
) -> dict[str, Any]:
    public_roles = [
        {
            "actor_id": card["actor_id"],
            "display_name": card["display_name"],
            "public_role": card["public_role"],
        }
        for card in packet["character_cards"]
    ]
    return {
        "artifact_constants": {
            "schema": "novel-world-resolution",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": scene_packet_fingerprint(packet),
            "tick": tick,
            "intent_fingerprints": {
                actor_id: fingerprint(value)
                for actor_id, value in intentions.items()
            },
            "unconfirmed_truths_preserved": True,
            "no_prose": True,
        },
        "public_seed": packet["public_seed"],
        "public_roles": public_roles,
        "current_pressure": pressure,
        "prior_world_state": prior_world_state,
        "intentions": intentions,
    }


def _resolve_tick(
    packet: dict[str, Any],
    workdir: Path,
    intentions: dict[str, dict[str, Any]],
    *,
    branch: str,
    tick: int,
    pressure: dict[str, Any],
    prior_world_state: list[str],
    resume: bool,
) -> dict[str, Any]:
    stage = f"{branch}_tick_{tick}_resolver"
    artifact = _call_json(
        workdir,
        stage,
        "agents/world_resolver.md",
        (
            "结算当前 tick。逐字复制 artifact_constants。observable_by 只能使用 public_roles 的 actor_id；"
            "若事件来自角色行动，intent_ref 必须复制对应 action_id，actor_id 必须是该行动角色；"
            "只有已经启动的环境变化才可使用 WORLD_PRESSURE，且其 actor_id 必须为 world。"
        ),
        _resolver_inputs(
            packet,
            intentions,
            tick=tick,
            pressure=pressure,
            prior_world_state=prior_world_state,
        ),
        "schemas/world_resolution.schema.json",
        STRUCTURE_MODEL,
        resume=resume,
    )
    validate_world_resolution(
        ROOT, packet, artifact, intentions, tick=tick
    )
    return artifact


def _observations_for_all(
    packet: dict[str, Any], resolution: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    return {
        card["actor_id"]: build_actor_observations(
            resolution, card["actor_id"]
        )
        for card in packet["character_cards"]
    }


def _sanitized_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        "tick": resolution["tick"],
        "events": [
            {
                "event_id": event["event_id"],
                "order": event["order"],
                "visible_actor": event["visible_actor"],
                "action": event["action"],
                "observable_result": event["observable_result"],
                "observable_by": event["observable_by"],
                "state_changes": event["state_changes"],
            }
            for event in resolution["events"]
        ],
        "world_state_after": resolution["world_state_after"],
        "unresolved_pressure": resolution["unresolved_pressure"],
    }


def run_experiment(
    packet_path: Path,
    workdir: Path,
    *,
    resume: bool,
    max_workers: int,
) -> dict[str, Any]:
    packet = load_json_object(packet_path, "场景实验输入")
    validate_scene_packet(ROOT, packet)
    source_hash = scene_packet_fingerprint(packet)
    workdir = workdir.resolve()
    boundary = (ROOT / "novel/work").resolve()
    if not workdir.is_relative_to(boundary):
        raise ArtifactValidationError("实验运行目录必须位于 novel/work/ 内")
    workdir.mkdir(parents=True, exist_ok=True)

    manifest_path = workdir / "experiment_manifest.json"
    if manifest_path.exists() and not resume:
        raise ArtifactValidationError("实验工作区已存在；使用 --resume 或选择新目录")
    if manifest_path.exists():
        manifest = load_json_object(manifest_path, "实验运行清单")
        if manifest.get("source_fingerprint") != source_hash:
            raise ArtifactValidationError("既有实验运行绑定了不同输入")
    else:
        manifest = {
            "schema": "novel-scene-experiment-run",
            "experiment_id": packet["experiment_id"],
            "source_fingerprint": source_hash,
            "status": "RUNNING",
            "started_at": now_iso(),
            "models": {
                "structure": {"model": STRUCTURE_MODEL[0], "effort": STRUCTURE_MODEL[1]},
                "actor": {"model": ACTOR_MODEL[0], "effort": ACTOR_MODEL[1]},
                "writer": {"model": WRITER_MODEL[0], "effort": WRITER_MODEL[1]},
            },
            "formal_content_modified": False,
        }
        atomic_save_json(manifest_path, manifest)
        atomic_save_json(workdir / "scene_packet.json", packet)

    # Control condition: complete contract before prose.
    contract_plan = _call_json(
        workdir,
        "contract_first_plan",
        "agents/scene_contract_planner.md",
        (
            "生成认真优化的控制组计划。逐字复制 artifact_constants，"
            "在写作前把事件链、选择、代价、人物转折、局部答案和终点全部确定。"
        ),
        {
            "artifact_constants": {
                "schema": "novel-contract-scene-plan",
                "experiment_id": packet["experiment_id"],
                "source_fingerprint": source_hash,
                "pov_character": packet["pov_character"],
                "no_prose": True,
            },
            "public_seed": packet["public_seed"],
            "prose_contract": packet["prose_contract"],
        },
        "schemas/contract_scene_plan.schema.json",
        STRUCTURE_MODEL,
        resume=resume,
    )
    validate_contract_scene_plan(ROOT, packet, contract_plan)
    contract_text = _call_text(
        workdir,
        "contract_first",
        "agents/scene_contract_writer.md",
        "严格实现完整计划；不得读取或猜测其他实验路线。",
        {
            "public_seed": packet["public_seed"],
            "contract_scene_plan": contract_plan,
            "prose_contract": packet["prose_contract"],
        },
        WRITER_MODEL,
        resume=resume,
    )

    # Shared first tick for both simulation conditions.
    first_pressure = packet["public_seed"]["initial_horizon"][0]
    empty_observations = {
        card["actor_id"]: [] for card in packet["character_cards"]
    }
    tick1_intentions = _generate_actor_intentions(
        packet,
        workdir,
        branch="shared",
        tick=1,
        pressure=first_pressure,
        observations=empty_observations,
        resume=resume,
        max_workers=max_workers,
    )
    tick1_resolution = _resolve_tick(
        packet,
        workdir,
        tick1_intentions,
        branch="shared",
        tick=1,
        pressure=first_pressure,
        prior_world_state=[packet["public_seed"]["opening_state"]],
        resume=resume,
    )
    tick1_trace = build_pov_event_trace(
        ROOT, packet, tick1_resolution, tick1_intentions, tick=1
    )
    atomic_save_json(workdir / "artifacts/shared_tick_1_pov_trace.json", tick1_trace)
    tick1_observations = _observations_for_all(packet, tick1_resolution)

    # Fixed branch: keep the frozen second pressure wording.
    fixed_pressure = packet["public_seed"]["initial_horizon"][1]
    fixed_intentions = _generate_actor_intentions(
        packet,
        workdir,
        branch="fixed",
        tick=2,
        pressure=fixed_pressure,
        observations=tick1_observations,
        resume=resume,
        max_workers=max_workers,
    )
    fixed_resolution = _resolve_tick(
        packet,
        workdir,
        fixed_intentions,
        branch="fixed",
        tick=2,
        pressure=fixed_pressure,
        prior_world_state=tick1_resolution["world_state_after"],
        resume=resume,
    )
    visible_after_tick1 = "；".join(tick1_trace["world_state_visible_after"])
    fixed_trace = build_pov_event_trace(
        ROOT,
        packet,
        fixed_resolution,
        fixed_intentions,
        tick=2,
        opening_state=visible_after_tick1,
    )
    atomic_save_json(workdir / "artifacts/fixed_tick_2_pov_trace.json", fixed_trace)

    # Rolling branch: revise pressure from the actual first resolution.
    rolling_horizon = _call_json(
        workdir,
        "rolling_horizon_after_tick_1",
        "agents/rolling_scene_planner.md",
        (
            "逐字复制 artifact_constants。forcing_event_ids 只能引用 sanitized_resolution.events；"
            "old_beat_ids 必须完整复制 artifact_constants；只修订短期压力，不指定角色动作或结果。"
        ),
        {
            "artifact_constants": {
                "schema": "novel-rolling-horizon",
                "experiment_id": packet["experiment_id"],
                "source_fingerprint": source_hash,
                "after_resolution_fingerprint": fingerprint(tick1_resolution),
                "retained_far_milestone": packet["public_seed"]["far_milestone"],
                "old_beat_ids": [
                    beat["beat_id"]
                    for beat in packet["public_seed"]["initial_horizon"][1:]
                ],
                "no_prose": True,
            },
            "old_horizon": packet["public_seed"]["initial_horizon"][1:],
            "sanitized_resolution": _sanitized_resolution(tick1_resolution),
            "pov_trace": tick1_trace,
        },
        "schemas/rolling_horizon.schema.json",
        STRUCTURE_MODEL,
        resume=resume,
    )
    validate_rolling_horizon(
        ROOT, packet, tick1_resolution, rolling_horizon
    )
    rolling_pressure = rolling_horizon["revised_beats"][0]
    rolling_intentions = _generate_actor_intentions(
        packet,
        workdir,
        branch="rolling",
        tick=2,
        pressure=rolling_pressure,
        observations=tick1_observations,
        resume=resume,
        max_workers=max_workers,
    )
    rolling_resolution = _resolve_tick(
        packet,
        workdir,
        rolling_intentions,
        branch="rolling",
        tick=2,
        pressure=rolling_pressure,
        prior_world_state=tick1_resolution["world_state_after"],
        resume=resume,
    )
    rolling_trace = build_pov_event_trace(
        ROOT,
        packet,
        rolling_resolution,
        rolling_intentions,
        tick=2,
        opening_state=visible_after_tick1,
    )
    atomic_save_json(workdir / "artifacts/rolling_tick_2_pov_trace.json", rolling_trace)

    fixed_text = _call_text(
        workdir,
        "simulation_fixed",
        "agents/event_renderer.md",
        "只渲染两份 POV 轨迹中已结算的事件，不补计划、不补隐藏原因。",
        {
            "opening_public_state": packet["public_seed"]["opening_state"],
            "pov_traces": [tick1_trace, fixed_trace],
            "prose_contract": packet["prose_contract"],
        },
        WRITER_MODEL,
        resume=resume,
    )
    rolling_text = _call_text(
        workdir,
        "simulation_rolling",
        "agents/event_renderer.md",
        "只渲染两份 POV 轨迹中已结算的事件，不补计划、不补隐藏原因。",
        {
            "opening_public_state": packet["public_seed"]["opening_state"],
            "pov_traces": [tick1_trace, rolling_trace],
            "prose_contract": packet["prose_contract"],
        },
        WRITER_MODEL,
        resume=resume,
    )

    candidates = validate_candidate_texts(
        packet,
        {
            "contract_first": contract_text,
            "simulation_fixed": fixed_text,
            "simulation_rolling": rolling_text,
        },
    )
    blind_packet_path = workdir / "blind/blind_packet.json"
    if blind_packet_path.exists():
        if not resume:
            raise ArtifactValidationError("既有盲评包拒绝覆盖")
        blind_packet = load_json_object(blind_packet_path, "盲评包")
    else:
        blind_packet = prepare_blind_bundle(
            ROOT, packet, candidates, workdir
        )["blind_packet"]
    manifest.update(
        {
            "status": "WAITING_USER",
            "finished_at": now_iso(),
            "candidate_fingerprints": {
                name: text_sha256(text) for name, text in candidates.items()
            },
            "blind_packet": str(blind_packet_path.relative_to(ROOT)),
            "formal_content_modified": False,
        }
    )
    atomic_save_json(manifest_path, manifest)
    return {"manifest": manifest, "blind_packet": blind_packet}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行预先规划、角色模拟与滚动规划的隔离场景实验"
    )
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-workers", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_workers < 1 or args.max_workers > 5:
        print("[STOP] --max-workers 必须在1到5之间", file=sys.stderr)
        return 2
    try:
        packet = load_json_object(args.packet, "场景实验输入")
        workdir = args.workdir or (
            ROOT
            / "novel/work/scene-experiments"
            / packet["experiment_id"]
        )
        result = run_experiment(
            args.packet,
            workdir,
            resume=args.resume,
            max_workers=args.max_workers,
        )
    except PrequelError as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        return 2
    print("[OK] 三条机制路线已完成并匿名化")
    print(f"状态: {result['manifest']['status']}")
    print(f"盲评包: {result['manifest']['blind_packet']}")
    print("正式正文与状态未修改")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
