#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import provider_style_benchmark as base


ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = ROOT / "novel" / "benchmarks" / "provider_style_eval_2026-08-22"
SUITE_DIR = BASE_DIR / "claude_supplement"
BASE_RAW_DIR = BASE_DIR / "raw"
CLAUDE_IDS = {
    "agy_claude_opus_4_6_thinking",
    "agy_claude_sonnet_4_6_thinking",
}


def configure_base() -> None:
    base.BENCHMARK_DIR = SUITE_DIR
    base.PROMPT_PATH = BASE_DIR / "trial_prompt.md"
    base.RUBRIC_PATH = BASE_DIR / "scoring_rubric.md"
    base.CONFIG_PATH = SUITE_DIR / "candidates.json"
    base.RAW_DIR = SUITE_DIR / "raw"
    base.BLIND_DIR = SUITE_DIR / "blind"
    base.JUDGE_DIR = SUITE_DIR / "judges"
    base.ISOLATION_ROOT = Path(
        "/tmp/novel-provider-style-eval-2026-08-22-claude-supplement"
    )
    base.BLIND_SEED = "provider-style-eval-2026-08-22-claude-supplement-v1"


def copy_reused_candidates() -> None:
    config = base.read_config()
    for spec in config["candidates"]:
        if spec["id"] in CLAUDE_IDS:
            continue
        source_path = BASE_RAW_DIR / f"{spec['id']}.txt"
        if not source_path.exists():
            raise SystemExit(f"首轮原始正文不存在: {source_path}")
        text = source_path.read_text(encoding="utf-8")
        target_path = base.RAW_DIR / f"{spec['id']}.txt"
        if target_path.exists():
            existing = target_path.read_text(encoding="utf-8")
            if existing != text:
                raise SystemExit(f"复用正文已存在但哈希不一致: {target_path}")
        else:
            target_path.write_text(text, encoding="utf-8")

        source_meta_path = BASE_RAW_DIR / f"{spec['id']}.meta.json"
        source_meta: dict[str, Any] = json.loads(
            source_meta_path.read_text(encoding="utf-8")
        )
        reused_meta = {
            "id": spec["id"],
            "provider": spec["provider"],
            "model": spec["model"],
            "effort": spec["effort"],
            "status": "reused_from_base_benchmark",
            "source_path": str(source_path.relative_to(ROOT)),
            "source_prompt_sha256": source_meta["prompt_sha256"],
            "source_output_sha256": source_meta["output_sha256"],
            "output_sha256": base.sha256_text(text),
            "output_characters": len(text.rstrip("\n")),
            "output_cjk_characters": base.cjk_count(text),
        }
        if reused_meta["output_sha256"] != source_meta["output_sha256"]:
            raise SystemExit(f"复用正文与首轮元数据哈希不一致: {source_path}")
        base.write_json(target_path.with_suffix(".meta.json"), reused_meta)


def find_spec(kind: str, item_id: str) -> dict[str, Any]:
    return base.find_spec(kind, item_id)


def list_suite() -> None:
    config = base.read_config()
    for kind in ("candidates", "judges"):
        print(f"[{kind}]")
        for spec in config[kind]:
            print(
                f"{spec['id']}\t{spec['provider']}\t{spec['model']}\t"
                f"{spec['effort']}\t{spec.get('source', 'judge')}"
            )


def main() -> None:
    configure_base()
    parser = argparse.ArgumentParser(description="Run the Claude supplement blind test")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--candidate", required=True)
    sub.add_parser("prepare-blind")
    judge_parser = sub.add_parser("judge")
    judge_parser.add_argument("--judge", required=True)
    sub.add_parser("revalidate-judges")
    sub.add_parser("aggregate")
    args = parser.parse_args()

    if args.command == "list":
        list_suite()
    elif args.command == "generate":
        if args.candidate not in CLAUDE_IDS:
            raise SystemExit("补测只允许生成两款 Claude；其余七篇必须复用首轮输出")
        spec = find_spec("candidate", args.candidate)
        prompt = base.PROMPT_PATH.read_text(encoding="utf-8")
        output_path = base.RAW_DIR / f"{spec['id']}.txt"
        base.run_once(spec, prompt, output_path)
    elif args.command == "prepare-blind":
        copy_reused_candidates()
        base.prepare_blind()
    elif args.command == "judge":
        spec = find_spec("judge", args.judge)
        base.judge_once(spec)
    elif args.command == "revalidate-judges":
        base.revalidate_judges()
    elif args.command == "aggregate":
        base.aggregate()


if __name__ == "__main__":
    main()
