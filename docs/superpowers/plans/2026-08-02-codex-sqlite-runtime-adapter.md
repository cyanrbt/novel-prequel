# Codex SQLite Runtime Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让受管环境中的嵌套 Codex CLI 使用项目内可写的 SQLite 状态目录，并安全完成一次新的真实 dry-run。

**Architecture:** 在现有 Codex CLI 基础命令中增加官方 `sqlite_home` 配置，路径固定为已被 Git 忽略的 `novel/work/.codex-runtime`。不改变 `CODEX_HOME`、认证、模型路由或预算；配置与测试通过后只创建新的 `attempt_06`。

**Tech Stack:** JSON 配置、Python `unittest`、Codex CLI `0.146.0`、现有预算化章节管线。

## Global Constraints

- 不修改 `CODEX_HOME` 或复制任何认证材料。
- 不恢复或重试 `attempt_05`。
- SQLite 目录必须位于 `novel/work/` 且被现有 `.gitignore` 覆盖。
- 不修改模型、思考强度、提示词、沙箱模式或单章 10 次调用上限。
- 无模型检查通过后，只运行一次 `attempt_06`，不自动开始第 2 次 benchmark。
- 若 Planner 仍在请求前失败，立即停止，不尝试第三个目录。
- `.git` 在当前环境只读，不把无法提交视为失败。

---

### Task 1: 固定可写 SQLite 运行目录

**Files:**
- Modify: `config/prequel_config.json`
- Modify: `tests/test_repository_hygiene.py`

**Interfaces:**
- Consumes: `provider.command: list[str]`，由 `provider_from_spec()` 原样保留非模型配置参数。
- Produces: Codex CLI 参数 `--config sqlite_home="novel/work/.codex-runtime"`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_repository_hygiene.py` 增加：

```python
def test_codex_sqlite_runtime_stays_in_ignored_work_area(self):
    import json

    config = json.loads(
        (ROOT / "config/prequel_config.json").read_text(encoding="utf-8")
    )
    command = config["provider"]["command"]
    index = command.index("--config")
    self.assertEqual(
        command[index + 1],
        'sqlite_home="novel/work/.codex-runtime"',
    )
    self.assertTrue(self.is_ignored("novel/work/.codex-runtime/state.sqlite"))
```

- [ ] **Step 2: 运行测试并确认失败**

Run:

```bash
python -m unittest tests.test_repository_hygiene.RepositoryHygieneTests.test_codex_sqlite_runtime_stays_in_ignored_work_area -v
```

Expected: FAIL，因为 `provider.command` 尚无 `sqlite_home` 参数。

- [ ] **Step 3: 最小配置修改**

将 `config/prequel_config.json` 的 Provider 命令改为：

```json
"command": [
  "codex", "exec", "--ephemeral",
  "--sandbox", "read-only",
  "--color", "never",
  "--skip-git-repo-check",
  "--config", "sqlite_home=\"novel/work/.codex-runtime\""
]
```

- [ ] **Step 4: 运行相关测试**

Run:

```bash
python -m unittest tests.test_repository_hygiene tests.test_provider tests.test_model_router -v
```

Expected: PASS；不得启动真实 Codex 模型请求。

- [ ] **Step 5: 无模型 CLI 参数验证**

Run:

```bash
mkdir -p novel/work/.codex-runtime
test -w novel/work/.codex-runtime
codex exec --strict-config \
  --config 'sqlite_home="novel/work/.codex-runtime"' \
  --model gpt-5.6-terra \
  --config 'model_reasoning_effort="medium"' \
  --help >/dev/null
```

Expected: 全部退出成功；可能仍显示 PATH alias 只读警告，但不得出现配置解析错误。本步骤不发送提示词。

- [ ] **Step 6: 提交（仅 `.git` 可写时）**

```bash
git add config/prequel_config.json tests/test_repository_hygiene.py
git commit -m "fix: use writable codex sqlite runtime directory"
```

---

### Task 2: 全量回归并执行替代试运行

**Files:**
- Runtime output: `novel/work/chapter_003/attempt_06/`
- Modify: `docs/superpowers/reports/2026-08-02-chapter-generation-budget-optimization-implementation.md`

**Interfaces:**
- Consumes: `WritingPipeline.run_next(dry_run=True, mode="balanced", shadow_review="continuity")`。
- Produces: 一套不提升正式章节的预算化运行工件和调用清单。

- [ ] **Step 1: 全量自动回归**

Run:

```bash
python -m unittest discover -v
```

Expected: 现有 94 项加新增 1 项全部通过；不启动真实模型。

- [ ] **Step 2: 记录正式状态哈希**

Run:

```bash
sha256sum novel/state/current.json \
  novel/chapters/vol_01/chapter_001.txt \
  novel/chapters/vol_01/chapter_002.txt
```

Expected: 输出三个基线哈希，供试运行后比较。

- [ ] **Step 3: 执行一次真实替代试运行**

Run:

```bash
python scripts/orchestrator.py next \
  --dry-run \
  --mode balanced \
  --shadow-review continuity
```

Expected: 新建 `attempt_06`；状态为 `AUTO_PROMOTE`、`WAITING_USER` 或 `BUDGET_EXHAUSTED`；不得超过 10 次调用，不得启动下一次运行。

- [ ] **Step 4: 检查调用、模型和工件**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

path = Path("novel/work/chapter_003/attempt_06")
manifest = json.loads((path / "run_manifest.json").read_text(encoding="utf-8"))
decision = json.loads((path / "decision.json").read_text(encoding="utf-8"))
assert manifest["budget"]["spent"] <= 10
assert manifest["budget"]["calls"]["call_001"]["stage"] == "planner"
assert decision["status"] in {"AUTO_PROMOTE", "WAITING_USER", "BUDGET_EXHAUSTED"}
print(json.dumps({"budget": manifest["budget"], "decision": decision}, ensure_ascii=False, indent=2))
PY
```

Expected: 断言通过；输出实际路由、耗时、失败和决策详情。

- [ ] **Step 5: 确认正式内容未变**

Run:

```bash
sha256sum novel/state/current.json \
  novel/chapters/vol_01/chapter_001.txt \
  novel/chapters/vol_01/chapter_002.txt
```

Expected: 与 Step 2 完全一致。

- [ ] **Step 6: 更新实施报告**

在实施报告追加 `attempt_05` 的基础设施失败和 `attempt_06` 的实际状态、调用数、模型构成、总耗时与正式状态哈希核对结果。不得把失败的 `attempt_05` 算入批准的十次质量 benchmark。

- [ ] **Step 7: 提交报告（仅 `.git` 可写时）**

```bash
git add docs/superpowers/reports/2026-08-02-chapter-generation-budget-optimization-implementation.md
git commit -m "docs: record first budgeted chapter canary"
```
