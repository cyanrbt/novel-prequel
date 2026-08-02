#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.prequel.metrics import benchmark_summary


def main() -> int:
    parser = argparse.ArgumentParser(description="只读汇总十次章节管线试运行")
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(benchmark_summary(args.manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
