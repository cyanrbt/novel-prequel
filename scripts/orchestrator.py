#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# 允许用户从项目根目录直接执行 ``python3 scripts/orchestrator.py``。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prequel.errors import PrequelError, StateValidationError
from scripts.prequel.audits import AuditRunner
from scripts.prequel.model_router import StageModelRouter
from scripts.prequel.metrics import chapter_metrics
from scripts.prequel.pipeline import (
    WritingPipeline,
    accept_dry_run,
    formal_chapter_paths,
    load_config,
    merge_formal_chapters,
    run_preflight,
)
from scripts.prequel.quality import scan_draft
from scripts.prequel.evaluation import DIMENSIONS, build_scorecard, validate_specialist_review
from scripts.prequel.pipeline import parse_json_artifact
from scripts.prequel.state_store import atomic_save_json, load_state


STATE_FILE = PROJECT_ROOT / "novel/state/current.json"
REGISTRY_FILE = PROJECT_ROOT / "novel/knowledge/canon_registry.json"


def format_progress_event(event: dict) -> str:
    """Render only the safe event fields defined by the progress contract."""
    kind = str(event.get("kind") or "UNKNOWN")
    stage = str(event.get("stage") or "unknown")
    call_id = str(event.get("call_id") or "call_???")
    model = str(event.get("model") or "unknown")
    effort = str(event.get("reasoning_effort") or "unknown")
    duration = (event.get("duration_ms") or 0) / 1000
    if kind == "CALL_STARTED":
        return f"[{call_id}] 开始 {stage} · {model}/{effort}"
    if kind == "CALL_COMPLETED":
        return (
            f"[{call_id}] 模型调用完成 {stage} · {duration:.1f}秒；"
            "正在校验工件"
        )
    if kind == "CALL_FAILED":
        return (
            f"[{call_id}] 模型调用失败 {stage} · "
            f"{event.get('error_code') or 'UNKNOWN'} · {duration:.1f}秒"
        )
    if kind == "ARTIFACT_INVALID":
        diagnostic = event.get("diagnostic_artifact") or "无原始输出"
        return (
            f"[审查无效] {stage} · {event.get('failure_kind') or 'UNKNOWN'} · "
            f"诊断: {diagnostic}"
        )
    if kind == "STAGE_REUSED":
        return f"[复用] {stage} · 已验证现有工件"
    return f"[{kind}] {stage}"


def _cli_progress(event: dict) -> None:
    print(format_progress_event(event), flush=True)


def _era_bans(year: int) -> dict:
    registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    for interval, bans in registry.get("era_bans", {}).items():
        start, end = (int(value) for value in interval.split("-", 1))
        if start <= year <= end:
            return bans
    return {"characters": [], "terms": []}


def command_status(args) -> int:
    state = load_state(STATE_FILE)
    chapter = state["chapter"]
    print("《神秘复苏前传》创作状态")
    print(f"状态: {state['machine_state']}")
    print(f"进度: 第{chapter['last_chapter']}章完成 → 第{chapter['next_chapter']}章待写")
    print(f"当前事件: {chapter['current_event_name']} / {chapter['current_phase']}")
    print(f"时间地点: {state['timeline']['current_year']}年 / {state['protagonist']['location']}")
    chapter_work = PROJECT_ROOT / "novel/work" / f"chapter_{chapter['next_chapter']:03d}"
    for attempt in sorted(chapter_work.glob("attempt_*"), reverse=True):
        manifest = attempt / "run_manifest.json"
        if not manifest.exists():
            continue
        runtime = json.loads(manifest.read_text(encoding="utf-8"))
        if runtime.get("status") == "REPLAN":
            print("运行: [旧流程] REPLAN / 只读")
            print("恢复: 不支持按新版 --resume 继续；请显式创建新运行")
        else:
            print(f"运行: {runtime.get('status')} / {runtime.get('current_stage')}")
        print(f"有效候选: {runtime.get('valid_candidates', 0)}")
        budget = runtime.get("budget")
        if isinstance(budget, dict):
            print(f"调用: {budget.get('spent', 0)}/{budget.get('limit', '?')}")
            routes = sorted(
                {
                    (item.get("model"), item.get("reasoning_effort"))
                    for item in budget.get("calls", {}).values()
                    if item.get("model")
                }
            )
            if routes:
                print(
                    "实际路由: "
                    + ", ".join(f"{model}/{effort}" for model, effort in routes)
                )
        if runtime.get("waiting_reason"):
            print(f"等待原因: {runtime['waiting_reason']}")
        break
    return 0


def command_preflight(args) -> int:
    for check in run_preflight(PROJECT_ROOT, check_cli_capabilities=True):
        print(f"[OK] {check}")
    return 0


def _print_review(result: dict) -> None:
    for issue in result["issues"]:
        print(f"[{issue['severity']}] {issue['code']}: {issue['message']} — {issue['evidence']}")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


def command_lint(args) -> int:
    text = args.path.read_text(encoding="utf-8")
    result = scan_draft(
        text,
        [],
        _era_bans(args.year),
        {"chapter_number": args.chapter, "prohibited_elements": []},
    )
    _print_review(result)
    return 0 if result["passed"] else 2


def command_review(args) -> int:
    state = load_state(STATE_FILE)
    paths = formal_chapter_paths(PROJECT_ROOT)
    selected = paths[-args.last :] if args.last else paths
    previous: list[str] = []
    failed = False
    for path in selected:
        number = int(path.stem.split("_")[1])
        result = scan_draft(
            path.read_text(encoding="utf-8"),
            previous[-5:],
            _era_bans(state["timeline"]["current_year"]),
            {"chapter_number": number, "prohibited_elements": []},
        )
        print(f"\n第{number}章: {'PASS' if result['passed'] else 'FAIL'}")
        _print_review(result)
        failed = failed or not result["passed"]
        previous.append(path.read_text(encoding="utf-8"))
        if args.specialists:
            config = load_config(PROJECT_ROOT)
            router = StageModelRouter.from_config(config, PROJECT_ROOT)
            draft = path.read_text(encoding="utf-8")
            reviews = {}
            for dimension in DIMENSIONS:
                role = (PROJECT_ROOT / f"agents/reviewer_{dimension}.md").read_text(
                    encoding="utf-8"
                )
                packet = {
                    "chapter_number": number,
                    "dimension": dimension,
                    "draft": draft,
                    "static_review": result,
                    "continuity_snapshot": state,
                    "calibration_only": True,
                }
                raw = router.provider_for(f"{dimension}_reviewer").generate(
                    role
                    + "\n\n# 本次任务\n这是只读正式章校准，不得提出回写历史状态。"
                    + "\n\n# 唯一输入工件\n"
                    + json.dumps(packet, ensure_ascii=False, indent=2),
                    PROJECT_ROOT / "schemas/specialist_review.schema.json",
                )
                review = parse_json_artifact(raw, f"formal-{number}-{dimension}")
                failures = [
                    issue.message
                    for issue in validate_specialist_review(
                        review, draft, number, dimension
                    )
                    if issue.severity == "P1"
                ]
                if failures:
                    raise StateValidationError("专项校准证据无效: " + "；".join(failures))
                reviews[dimension] = review
            report = {
                "chapter_number": number,
                "calibration_only": True,
                "reviews": reviews,
                "scorecard": build_scorecard(
                    reviews,
                    config.get("quality_evolution", {}).get("weights"),
                ),
            }
            target = PROJECT_ROOT / f"novel/work/baselines/chapter_{number:03d}.json"
            atomic_save_json(target, report)
            print(f"专项校准: {target}")
    return 2 if failed else 0


def command_next(args) -> int:
    result = WritingPipeline(PROJECT_ROOT).run_next(
        dry_run=args.dry_run,
        resume=args.resume,
        mode=args.mode,
        shadow_review=args.shadow_review,
        progress=_cli_progress,
    )
    mode = (
        "已通过门禁并提升"
        if result.promoted
        else "dry-run完成，未提升"
        if args.dry_run
        else "已完成评估，等待人工确认"
    )
    print(f"[OK] 第{result.chapter_number}章{mode}")
    print(f"状态: {result.status}")
    print(f"工作区: {result.workspace}")
    manifest_path = result.workspace / "run_manifest.json"
    decision_path = result.workspace / "decision.json"
    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        budget = manifest.get("budget", {})
        print(f"调用: {budget.get('spent', 0)}/{budget.get('limit', '?')}")
        calls = budget.get("calls", {}).values()
        models: dict[str, int] = {}
        for call in calls:
            model = call.get("model", "unknown")
            models[model] = models.get(model, 0) + 1
        print("模型构成: " + ", ".join(f"{name}×{count}" for name, count in sorted(models.items())))
        metrics = chapter_metrics(manifest_path)
        wall = metrics["wall_time_seconds"]
        print(
            "实际墙钟耗时: 未知"
            if wall is None
            else f"实际墙钟耗时: {wall:.1f}秒"
        )
        print(
            "并发调用耗时合计: "
            f"{metrics['model_call_time_seconds']:.1f}秒"
        )
    if decision_path.exists():
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        if decision.get("degraded"):
            print(f"失败候选: {decision.get('failed_candidate') or '未知'}")
            print("系统未自动重试: " + str(decision.get("automatic_retry_skipped_reason")))
            print("当前最佳有效工件: " + str(decision.get("best_available_artifact")))
        failures = decision.get("failures", [])
        if failures:
            print("失败明细:")
            for failure in failures:
                print(
                    f"- {failure.get('stage') or '未知阶段'} / "
                    f"{failure.get('failure_kind') or 'UNKNOWN'}: "
                    f"{failure.get('message') or '无说明'}"
                )
                if failure.get("diagnostic_artifact"):
                    print(f"  诊断: {failure['diagnostic_artifact']}")
        if result.status in {"WAITING_USER", "BUDGET_EXHAUSTED"}:
            for reason in decision.get("reasons", []):
                print(f"等待原因: {reason}")
            print("无需新增调用的安全操作:")
            for item in decision.get("safe_actions", []):
                print(f"- {item}")
            print("会建立新预算的额外消耗操作:")
            for item in decision.get("new_budget_actions", []):
                print(f"- {item}")
            print(decision.get("resume_warning", "--resume 不会扩展预算"))
        if result.status == "BUDGET_EXHAUSTED":
            spent = decision.get("calls_spent", "?")
            limit = manifest.get("budget", {}).get("limit", "?")
            print(f"BUDGET_EXHAUSTED（{spent}/{limit}）")
    return 0


def command_merge(args) -> int:
    target, count = merge_formal_chapters(PROJECT_ROOT)
    print(f"[OK] 已原子生成合订本，共{count}章: {target}")
    return 0


def command_accept(args) -> int:
    result = accept_dry_run(
        PROJECT_ROOT, attempt=args.attempt, candidate=args.candidate
    )
    print(f"[OK] 已重新校验并提升第{result.chapter_number}章")
    print(f"来源工作区: {result.workspace}")
    return 0


def command_audit(args) -> int:
    state = load_state(STATE_FILE)
    through = state["chapter"]["last_chapter"]
    if through < 1:
        raise StateValidationError("没有可审计的正式章节")
    config = load_config(PROJECT_ROOT)
    runner = AuditRunner(
        PROJECT_ROOT, StageModelRouter.from_config(config, PROJECT_ROOT)
    )
    path = runner.run_arc(through) if args.arc else runner.run_health(through)
    print(f"[OK] 审计报告: {path}")
    return 0


def command_recover(args) -> int:
    backup = STATE_FILE.with_suffix(".json.bak")
    if not backup.exists():
        raise StateValidationError("没有可用的current.json.bak")
    load_state(backup)
    shutil.copy2(backup, STATE_FILE)
    print("[OK] 已从current.json.bak恢复状态")
    return 0


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="《神秘复苏》前传事务型创作管道")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="查看当前进度")
    status.set_defaults(handler=command_status)

    preflight = sub.add_parser("preflight", help="写作前完整预检")
    preflight.set_defaults(handler=command_preflight)

    next_parser = sub.add_parser("next", help="规划、生成、审查并提升下一章")
    next_parser.add_argument("--dry-run", action="store_true", help="生成全部工件但不提升正式章节")
    next_parser.add_argument("--resume", action="store_true", help="恢复输入仍有效的最近运行")
    next_parser.add_argument(
        "--mode",
        choices=("balanced", "fast"),
        default="balanced",
        help="balanced最多10次调用；fast最多3次调用",
    )
    next_parser.add_argument(
        "--shadow-review",
        choices=("continuity", "character", "craft", "anti_slop"),
        help="仅用于获批试运行的预算内影子专项复核",
    )
    next_parser.set_defaults(handler=command_next)

    merge = sub.add_parser("merge", help="从已校验的正式章节生成合订本")
    merge.set_defaults(handler=command_merge)

    accept = sub.add_parser("accept", help="重新校验并提升已通过的dry-run")
    accept.add_argument("--attempt", type=int, help="指定尝试序号；默认选择最新PASS")
    accept.add_argument("--candidate", type=_positive_int, help="人工选择工作区中已通过硬门禁的候选")
    accept.set_defaults(handler=command_accept)

    audit = sub.add_parser("audit", help="对正式章节执行阶段审计，不改写历史正文")
    audit.add_argument("--arc", action="store_true", help="执行二十章级阶段复审；默认十章健康检查")
    audit.set_defaults(handler=command_audit)

    lint = sub.add_parser("lint", help="对指定正文执行确定性检查")
    lint.add_argument("path", type=Path)
    lint.add_argument("--year", type=int, required=True)
    lint.add_argument("--chapter", type=int, required=True)
    lint.set_defaults(handler=command_lint)

    review = sub.add_parser("review", help="批量静态审查正式章节")
    review.add_argument("--last", type=int, default=5)
    review.add_argument("--specialists", action="store_true", help="生成四维只读专项校准报告")
    review.set_defaults(handler=command_review)

    recover = sub.add_parser("recover", help="从已验证的状态备份恢复")
    recover.set_defaults(handler=command_recover)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except PrequelError as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[STOP] 项目文件无法读取: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
