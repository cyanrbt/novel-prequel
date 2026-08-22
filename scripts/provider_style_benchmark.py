#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prequel.provider import (
    AgyCliProvider,
    CodexCliProvider,
    GrokCliProvider,
    OpenCodeCliProvider,
)


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "novel" / "benchmarks" / "provider_style_eval_2026-08-22"
PROMPT_PATH = BENCHMARK_DIR / "trial_prompt.md"
RUBRIC_PATH = BENCHMARK_DIR / "scoring_rubric.md"
CONFIG_PATH = BENCHMARK_DIR / "candidates.json"
RAW_DIR = BENCHMARK_DIR / "raw"
BLIND_DIR = BENCHMARK_DIR / "blind"
JUDGE_DIR = BENCHMARK_DIR / "judges"
ISOLATION_ROOT = Path("/tmp/novel-provider-style-eval-2026-08-22")
BLIND_SEED = "provider-style-eval-2026-08-22-frozen-order-v1"

DIMENSIONS = {
    "plot_fidelity": ("剧情忠实与信息边界", 20),
    "character_voice": ("人物声音与群像真实性", 15),
    "horror_pacing": ("恐怖递进与节奏", 15),
    "spatial_clarity": ("空间、动作与因果清晰度", 10),
    "serial_readability": ("商业网文可读性与追读力", 15),
    "web_vitality": ("未经加工的网络生命力", 15),
    "anti_template": ("反模板与语言独立性", 10),
}


def read_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def find_spec(kind: str, item_id: str) -> dict[str, Any]:
    config = read_config()
    plural = "candidates" if kind == "candidate" else "judges"
    for spec in config[plural]:
        if spec["id"] == item_id:
            return spec
    raise SystemExit(f"未知{kind}: {item_id}")


def isolated_dir(item_id: str) -> Path:
    path = ISOLATION_ROOT / item_id
    path.mkdir(parents=True, exist_ok=True)
    marker = path / "ISOLATED_BENCHMARK_WORKDIR.txt"
    if not marker.exists():
        marker.write_text(
            "This directory intentionally contains no novel source or peer output.\n",
            encoding="utf-8",
        )
    return path


def provider_for(spec: dict[str, Any]):
    workdir = isolated_dir(spec["id"])
    provider_type = spec["provider"]
    model = spec["model"]
    effort = spec["effort"]
    timeout = 1800

    if provider_type == "codex_cli":
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
            timeout_seconds=timeout,
            project_root=workdir,
            model=model,
            reasoning_effort=effort,
        )
    if provider_type == "agy_cli":
        return AgyCliProvider(
            command=[
                "agy",
                "--dangerously-skip-permissions",
                "--disable-slash-commands",
            ],
            timeout_seconds=timeout,
            project_root=workdir,
            model=model,
            reasoning_effort=effort,
        )
    if provider_type == "opencode_cli":
        return OpenCodeCliProvider(
            command=["opencode", "run", "--pure", "--format", "json"],
            timeout_seconds=timeout,
            project_root=workdir,
            model=model,
            reasoning_effort=effort,
        )
    if provider_type == "grok_cli":
        return GrokCliProvider(
            command=["grok"],
            timeout_seconds=timeout,
            project_root=workdir,
            model=model,
            reasoning_effort=effort,
        )
    raise SystemExit(f"不支持的 provider: {provider_type}")


def cjk_count(value: str) -> int:
    return len(re.findall(r"[\u3400-\u9fff]", value))


def run_once(spec: dict[str, Any], prompt: str, output_path: Path) -> None:
    if output_path.exists():
        raise SystemExit(f"拒绝覆盖既有结果: {output_path}")

    meta_path = output_path.with_suffix(".meta.json")
    attempt = 1
    if meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        if previous.get("status") != "failed":
            raise SystemExit(f"发现非失败状态的既有元数据，拒绝重试: {meta_path}")
        previous_attempt = int(previous.get("attempt", 1))
        if previous_attempt >= 2:
            raise SystemExit(f"已发生两次技术失败，拒绝继续自动重试: {meta_path}")
        archived_meta = output_path.with_name(
            f"{output_path.stem}.attempt_{previous_attempt:02d}.failed.json"
        )
        if not archived_meta.exists():
            write_json(archived_meta, previous)
        attempt = previous_attempt + 1

    started = time.monotonic()
    meta: dict[str, Any] = {
        "id": spec["id"],
        "provider": spec["provider"],
        "model": spec["model"],
        "effort": spec["effort"],
        "prompt_sha256": sha256_text(prompt),
        "started_at": now_iso(),
        "status": "running",
        "attempt": attempt,
        "isolated_workdir": str(isolated_dir(spec["id"])),
    }
    write_json(meta_path, meta)
    print(f"START {spec['id']} prompt={meta['prompt_sha256']}", flush=True)
    try:
        output = provider_for(spec).generate(prompt)
    except Exception as exc:
        meta.update(
            {
                "status": "failed",
                "finished_at": now_iso(),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        write_json(meta_path, meta)
        print(f"FAILED {spec['id']}: {exc}", flush=True)
        raise

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output.rstrip() + "\n", encoding="utf-8")
    meta.update(
        {
            "status": "complete",
            "finished_at": now_iso(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "output_sha256": sha256_text(output.rstrip() + "\n"),
            "output_characters": len(output),
            "output_cjk_characters": cjk_count(output),
        }
    )
    write_json(meta_path, meta)
    print(
        f"DONE {spec['id']} chars={len(output)} cjk={cjk_count(output)} "
        f"seconds={meta['elapsed_seconds']}",
        flush=True,
    )


def freeze_hashes() -> None:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    (BENCHMARK_DIR / "prompt.sha256").write_text(
        f"{sha256_text(prompt)}  trial_prompt.md\n", encoding="utf-8"
    )
    (BENCHMARK_DIR / "rubric.sha256").write_text(
        f"{sha256_text(rubric)}  scoring_rubric.md\n", encoding="utf-8"
    )


def prepare_blind() -> None:
    config = read_config()
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for spec in config["candidates"]:
        raw_path = RAW_DIR / f"{spec['id']}.txt"
        if raw_path.exists():
            candidates.append(spec)
            continue
        meta_path = RAW_DIR / f"{spec['id']}.meta.json"
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        if meta.get("status") != "failed" or int(meta.get("attempt", 0)) < 2:
            raise SystemExit(f"候选尚未完成且未达到连续两次技术失败: {spec['id']}")
        excluded.append(
            {
                **spec,
                "status": "excluded_after_two_technical_failures",
                "attempts": int(meta.get("attempt", 2)),
                "last_error": meta.get("error", "unknown technical failure"),
            }
        )
    if len(candidates) < 2:
        raise SystemExit("有效候选不足两个，无法盲评")

    shuffled = list(candidates)
    random.Random(BLIND_SEED).shuffle(shuffled)
    mapping: dict[str, Any] = {
        "benchmark_id": config["benchmark_id"],
        "seed_sha256": sha256_text(BLIND_SEED),
        "created_at": now_iso(),
        "mapping": {},
        "excluded_candidates": excluded,
    }
    for index, spec in enumerate(shuffled):
        label = chr(ord("A") + index)
        text = (RAW_DIR / f"{spec['id']}.txt").read_text(encoding="utf-8")
        blind_path = BLIND_DIR / f"candidate_{label}.txt"
        blind_path.write_text(text, encoding="utf-8")
        mapping["mapping"][label] = {
            **spec,
            "source_sha256": sha256_text(text),
            "blind_sha256": sha256_text(blind_path.read_text(encoding="utf-8")),
        }
    write_json(BENCHMARK_DIR / "blind_mapping.json", mapping)
    build_judge_prompt()
    print("Blind set and judge prompt prepared.", flush=True)


def build_judge_prompt() -> None:
    rubric = RUBRIC_PATH.read_text(encoding="utf-8").rstrip()
    mapping_doc = json.loads(
        (BENCHMARK_DIR / "blind_mapping.json").read_text(encoding="utf-8")
    )
    labels = list(mapping_doc["mapping"])
    label_range = f"{labels[0]}—{labels[-1]}"
    parts = [
        "你是匿名中文网文试写的独立评审。以下候选均使用同一剧情要求生成，身份已隐藏。",
        f"请完整阅读 {label_range} 后再评分。不得猜测或讨论模型身份。严格依照评分规程，不能按个人文风偏好随意改权重。",
        "\n【评分规程】\n" + rubric,
        "\n【输出要求】\n只输出一个合法 JSON 对象，不要 Markdown 代码块或其他文字。",
        f"JSON 顶层必须包含 ranking、candidates、overall_observations。ranking 是 {label_range} 从优到劣且不重复的数组。",
        "candidates 的每项必须含七个整数分：plot_fidelity、character_voice、horror_pacing、spatial_clarity、serial_readability、web_vitality、anti_template；total 必须为七项之和；另含 hard_failures（只用规程标签的数组）、strengths（最多3条短句）、weaknesses（最多3条短句）、reader_verdict（一句）。",
        "overall_observations 是最多五条短句的数组，说明本组最能区分模型的现象。",
    ]
    for label in labels:
        candidate_path = BLIND_DIR / f"candidate_{label}.txt"
        if not candidate_path.exists():
            raise SystemExit(f"缺少盲评候选: {candidate_path}")
        candidate_text = candidate_path.read_text(encoding="utf-8").rstrip()
        parts.append(f"\n========== 候选 {label} ==========\n{candidate_text}")
    prompt = "\n".join(parts).rstrip() + "\n"
    (BENCHMARK_DIR / "judge_prompt.md").write_text(prompt, encoding="utf-8")
    (BENCHMARK_DIR / "judge_prompt.sha256").write_text(
        f"{sha256_text(prompt)}  judge_prompt.md\n", encoding="utf-8"
    )


def extract_json(value: str) -> dict[str, Any]:
    cleaned = value.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("judge output is not a JSON object")
    return parsed


def normalize_judgment(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize an unambiguous list-form candidates payload without changing scores."""
    candidates = value.get("candidates")
    result = dict(value)
    normalizations: list[str] = []
    if isinstance(candidates, list):
        normalized: dict[str, Any] = {}
        for entry in candidates:
            if not isinstance(entry, dict):
                return value
            label = entry.get("candidate")
            if not isinstance(label, str) or label in normalized:
                return value
            normalized_entry = dict(entry)
            normalized_entry.pop("candidate", None)
            normalized[label] = normalized_entry
        candidates = normalized
        result["candidates"] = normalized
        normalizations.append("candidates_list_to_label_object")
    if isinstance(candidates, dict):
        for label, entry in candidates.items():
            if not isinstance(entry, dict):
                continue
            scores = [entry.get(key) for key in DIMENSIONS]
            if not all(
                isinstance(score, (int, float)) and not isinstance(score, bool)
                for score in scores
            ):
                continue
            computed = sum(scores)
            if entry.get("total") != computed:
                entry["total"] = computed
                normalizations.append(f"recomputed_total:{label}")
    if normalizations:
        result["normalizations"] = normalizations
    return result


def validate_judgment(value: dict[str, Any], labels: list[str]) -> list[str]:
    errors: list[str] = []
    ranking = value.get("ranking")
    if not isinstance(ranking, list) or sorted(ranking) != sorted(labels):
        errors.append(f"ranking 必须恰好包含 {labels[0]}-{labels[-1]}")
    candidates = value.get("candidates")
    if not isinstance(candidates, dict):
        return errors + ["缺少 candidates 对象"]
    for label in labels:
        entry = candidates.get(label)
        if not isinstance(entry, dict):
            errors.append(f"缺少候选 {label}")
            continue
        computed = 0
        for key, (_, maximum) in DIMENSIONS.items():
            score = entry.get(key)
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                errors.append(f"{label}.{key} 不是数字")
                continue
            if score < 0 or score > maximum:
                errors.append(f"{label}.{key} 超出 0-{maximum}")
            computed += score
        if entry.get("total") != computed:
            errors.append(f"{label}.total={entry.get('total')}，应为 {computed}")
    return errors


def parse_judge_output(spec: dict[str, Any], prompt: str) -> None:
    output_path = JUDGE_DIR / f"{spec['id']}.txt"
    raw = output_path.read_text(encoding="utf-8")
    parsed_path = JUDGE_DIR / f"{spec['id']}.json"
    mapping_doc = json.loads(
        (BENCHMARK_DIR / "blind_mapping.json").read_text(encoding="utf-8")
    )
    labels = list(mapping_doc["mapping"])
    try:
        parsed = normalize_judgment(extract_json(raw))
        errors = validate_judgment(parsed, labels)
    except Exception as exc:
        errors = [f"无法解析 JSON: {type(exc).__name__}: {exc}"]
        parsed = {"parse_error": errors[0]}
    write_json(parsed_path, parsed)
    validation = {
        "judge_id": spec["id"],
        "judge_prompt_sha256": sha256_text(prompt),
        "valid": not errors,
        "errors": errors,
    }
    write_json(JUDGE_DIR / f"{spec['id']}.validation.json", validation)
    if errors:
        print(f"JUDGE INVALID {spec['id']}: {'; '.join(errors)}", flush=True)
    else:
        print(f"JUDGE VALID {spec['id']}", flush=True)


def judge_once(spec: dict[str, Any]) -> None:
    prompt_path = BENCHMARK_DIR / "judge_prompt.md"
    if not prompt_path.exists():
        raise SystemExit("尚未 prepare-blind")
    prompt = prompt_path.read_text(encoding="utf-8")
    output_path = JUDGE_DIR / f"{spec['id']}.txt"
    run_once(spec, prompt, output_path)
    parse_judge_output(spec, prompt)


def revalidate_judges() -> None:
    prompt_path = BENCHMARK_DIR / "judge_prompt.md"
    if not prompt_path.exists():
        raise SystemExit("尚未 prepare-blind")
    prompt = prompt_path.read_text(encoding="utf-8")
    for spec in read_config()["judges"]:
        output_path = JUDGE_DIR / f"{spec['id']}.txt"
        if output_path.exists():
            parse_judge_output(spec, prompt)


def aggregate() -> None:
    config = read_config()
    mapping_doc = json.loads(
        (BENCHMARK_DIR / "blind_mapping.json").read_text(encoding="utf-8")
    )
    mapping = mapping_doc["mapping"]
    excluded_candidates = mapping_doc.get("excluded_candidates", [])
    judgments: dict[str, dict[str, Any]] = {}
    invalid: dict[str, list[str]] = {}
    for spec in config["judges"]:
        validation_path = JUDGE_DIR / f"{spec['id']}.validation.json"
        parsed_path = JUDGE_DIR / f"{spec['id']}.json"
        if not validation_path.exists() or not parsed_path.exists():
            invalid[spec["id"]] = ["missing"]
            continue
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if not validation.get("valid"):
            invalid[spec["id"]] = validation.get("errors", ["invalid"])
            continue
        judgments[spec["id"]] = json.loads(parsed_path.read_text(encoding="utf-8"))
    if not judgments:
        raise SystemExit("没有有效评审结果")

    rows: list[dict[str, Any]] = []
    for label, spec in mapping.items():
        dimension_values: dict[str, list[float]] = {key: [] for key in DIMENSIONS}
        totals: list[float] = []
        top2_votes = 0
        hard_failures: dict[str, int] = {}
        for judgment in judgments.values():
            entry = judgment["candidates"][label]
            total = 0.0
            for key in DIMENSIONS:
                score = float(entry[key])
                dimension_values[key].append(score)
                total += score
            totals.append(total)
            if label in judgment["ranking"][:2]:
                top2_votes += 1
            for flag in entry.get("hard_failures", []):
                hard_failures[str(flag)] = hard_failures.get(str(flag), 0) + 1
        dimensions = {
            key: round(statistics.mean(values), 2)
            for key, values in dimension_values.items()
        }
        rows.append(
            {
                "label": label,
                "id": spec["id"],
                "provider": spec["provider"],
                "model": spec["model"],
                "effort": spec["effort"],
                "metered": bool(spec.get("metered", False)),
                "mean_total": round(statistics.mean(totals), 2),
                "score_stddev": round(statistics.pstdev(totals), 2),
                "top2_votes": top2_votes,
                "dimensions": dimensions,
                "hard_failures": hard_failures,
            }
        )
    rows.sort(key=lambda row: (-row["mean_total"], row["score_stddev"], row["id"]))

    non_metered = [row for row in rows if not row["metered"]]
    best_non_metered = non_metered[0]
    for row in rows:
        if not row["metered"]:
            row["metered_adoption_gate"] = None
            continue
        vitality_lead = row["dimensions"]["web_vitality"] - best_non_metered["dimensions"]["web_vitality"]
        anti_lead = row["dimensions"]["anti_template"] - best_non_metered["dimensions"]["anti_template"]
        majority_hard_failure = any(
            count > len(judgments) / 2 for count in row["hard_failures"].values()
        )
        checks = {
            "total_lead_at_least_3": row["mean_total"] >= best_non_metered["mean_total"] + 3.0,
            "vitality_or_anti_template_lead_at_least_2": max(vitality_lead, anti_lead) >= 2.0,
            "top2_votes_at_least_3": row["top2_votes"] >= 3,
            "no_majority_same_hard_failure": not majority_hard_failure,
        }
        row["metered_adoption_gate"] = {
            "passed": all(checks.values()),
            "checks": checks,
            "compared_to": best_non_metered["id"],
        }

    result = {
        "benchmark_id": config["benchmark_id"],
        "generated_at": now_iso(),
        "valid_judges": list(judgments),
        "invalid_judges": invalid,
        "excluded_candidates": excluded_candidates,
        "ranking": rows,
    }
    write_json(BENCHMARK_DIR / "aggregate.json", result)

    lines = [
        "# Provider 文风盲测结果",
        "",
        f"有效评审：{len(judgments)}/{len(config['judges'])}。分数为有效评审的七维均值之和；± 为评审总分的总体标准差。",
        "",
        "| 排名 | 模型 | 强度 | 总分 | ± | 前二票 | 网络生命力 | 反模板 | 按量采用门槛 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, row in enumerate(rows, 1):
        gate = row["metered_adoption_gate"]
        gate_text = "—" if gate is None else ("通过" if gate["passed"] else "未通过")
        lines.append(
            f"| {index} | {row['model']} | {row['effort']} | {row['mean_total']:.2f} | "
            f"{row['score_stddev']:.2f} | {row['top2_votes']} | "
            f"{row['dimensions']['web_vitality']:.2f} | {row['dimensions']['anti_template']:.2f} | {gate_text} |"
        )
    lines.extend(["", "## 各维度", ""])
    for row in rows:
        lines.append(f"### {row['model']} / {row['effort']}（匿名标签 {row['label']}）")
        lines.append("")
        for key, (name, maximum) in DIMENSIONS.items():
            lines.append(f"- {name}：{row['dimensions'][key]:.2f}/{maximum}")
        failures = row["hard_failures"] or {"无": 0}
        lines.append(
            "- 硬伤票：" + "、".join(f"{key}×{value}" for key, value in failures.items())
        )
        lines.append("")
    if invalid:
        lines.extend(["## 无效或缺失评审", ""])
        for judge_id, errors in invalid.items():
            lines.append(f"- {judge_id}：{'；'.join(errors)}")
        lines.append("")
    if excluded_candidates:
        lines.extend(["## 因技术失败未进入盲评的候选", ""])
        for candidate in excluded_candidates:
            lines.append(
                f"- {candidate['model']} / {candidate['effort']}：连续 "
                f"{candidate['attempts']} 次未形成正文；{candidate['last_error']}"
            )
        lines.append("")
    lines.extend(
        [
            "## 解释限制",
            "",
            "- 每个模型只有一个样本，排序会受到单次采样波动影响。",
            "- 评审同样是模型；匿名化能减少身份偏见，不能消除审美偏差或模型家族偏差。",
            "- 本轮按预先冻结门槛判断 DeepSeek 与 MiMo 是否证明按量调用价值，不根据结果事后移动标准。",
        ]
    )
    (BENCHMARK_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Aggregated {len(judgments)} valid judges.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the frozen provider prose benchmark")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--candidate", required=True)
    sub.add_parser("freeze")
    sub.add_parser("prepare-blind")
    judge_parser = sub.add_parser("judge")
    judge_parser.add_argument("--judge", required=True)
    sub.add_parser("revalidate-judges")
    sub.add_parser("aggregate")
    args = parser.parse_args()

    if args.command == "list":
        config = read_config()
        for kind in ("candidates", "judges"):
            print(f"[{kind}]")
            for spec in config[kind]:
                print(
                    f"{spec['id']}\t{spec['provider']}\t{spec['model']}\t{spec['effort']}"
                )
    elif args.command == "freeze":
        freeze_hashes()
        print("Prompt and rubric hashes frozen.")
    elif args.command == "generate":
        spec = find_spec("candidate", args.candidate)
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        run_once(spec, prompt, RAW_DIR / f"{spec['id']}.txt")
    elif args.command == "prepare-blind":
        prepare_blind()
    elif args.command == "judge":
        spec = find_spec("judge", args.judge)
        judge_once(spec)
    elif args.command == "revalidate-judges":
        revalidate_judges()
    elif args.command == "aggregate":
        aggregate()


if __name__ == "__main__":
    main()
