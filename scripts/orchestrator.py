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
from scripts.prequel.pipeline import (
    WritingPipeline,
    accept_dry_run,
    formal_chapter_paths,
    merge_formal_chapters,
    run_preflight,
)
from scripts.prequel.quality import scan_draft
from scripts.prequel.state_store import load_state


STATE_FILE = PROJECT_ROOT / "novel/state/current.json"
REGISTRY_FILE = PROJECT_ROOT / "novel/knowledge/canon_registry.json"


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
    return 0


def command_preflight(args) -> int:
    for check in run_preflight(PROJECT_ROOT):
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
    return 2 if failed else 0


def command_next(args) -> int:
    result = WritingPipeline(PROJECT_ROOT).run_next(dry_run=args.dry_run)
    mode = "dry-run完成，未提升" if args.dry_run else "已通过门禁并提升"
    print(f"[OK] 第{result.chapter_number}章{mode}")
    print(f"工作区: {result.workspace}")
    return 0


def command_merge(args) -> int:
    target, count = merge_formal_chapters(PROJECT_ROOT)
    print(f"[OK] 已原子生成合订本，共{count}章: {target}")
    return 0


def command_accept(args) -> int:
    result = accept_dry_run(PROJECT_ROOT, attempt=args.attempt)
    print(f"[OK] 已重新校验并提升第{result.chapter_number}章")
    print(f"来源工作区: {result.workspace}")
    return 0


def command_recover(args) -> int:
    backup = STATE_FILE.with_suffix(".json.bak")
    if not backup.exists():
        raise StateValidationError("没有可用的current.json.bak")
    load_state(backup)
    shutil.copy2(backup, STATE_FILE)
    print("[OK] 已从current.json.bak恢复状态")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="《神秘复苏》前传事务型创作管道")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="查看当前进度")
    status.set_defaults(handler=command_status)

    preflight = sub.add_parser("preflight", help="写作前完整预检")
    preflight.set_defaults(handler=command_preflight)

    next_parser = sub.add_parser("next", help="规划、生成、审查并提升下一章")
    next_parser.add_argument("--dry-run", action="store_true", help="生成全部工件但不提升正式章节")
    next_parser.set_defaults(handler=command_next)

    merge = sub.add_parser("merge", help="从已校验的正式章节生成合订本")
    merge.set_defaults(handler=command_merge)

    accept = sub.add_parser("accept", help="重新校验并提升已通过的dry-run")
    accept.add_argument("--attempt", type=int, help="指定尝试序号；默认选择最新PASS")
    accept.set_defaults(handler=command_accept)

    lint = sub.add_parser("lint", help="对指定正文执行确定性检查")
    lint.add_argument("path", type=Path)
    lint.add_argument("--year", type=int, required=True)
    lint.add_argument("--chapter", type=int, required=True)
    lint.set_defaults(handler=command_lint)

    review = sub.add_parser("review", help="批量静态审查正式章节")
    review.add_argument("--last", type=int, default=5)
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
