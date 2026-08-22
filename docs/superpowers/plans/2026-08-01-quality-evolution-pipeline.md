# 小说质量进化管线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有单稿 Planner/Writer/Reviewer 管线升级为三候选、四专项审查、盲选择优、最多两轮定向修订、分层长期记忆和阶段复审的可恢复质量进化管线。

**Architecture:** `WritingPipeline` 保留正式状态和原子提升职责；新增模型路由、嵌套工件与运行清单、专项评价与选择策略、候选进化引擎、长期记忆和阶段审计模块。所有模型输出先成为工作区工件，确定性政策决定自动提升或等待人工，正式正文和状态只能沿用现有原子提升入口写入。

**Tech Stack:** Python 3.10+ 标准库、`unittest`、JSON Schema、Codex CLI、Markdown/JSON 本地工件。

## Global Constraints

- 保留现有 Python CLI，不建设网页 IDE、出版套件、封面、有声书、营销或互动故事模式。
- 默认每章生成 3 个候选，专项维度权重依次为连续性 30%、人物 25%、文学性 30%、反 AI 痕迹 15%。
- 候选资格线依次为 85、75、75、80；自动提升总分不低于 85，连续性不低于 90，其余维度不低于 82。
- 单章最多执行 2 轮修订；总分不提高、任一维度下降超过 3 分或修订稿未获 2/3 盲选支持时保留上一版本。
- Writer 每次最多接收 8 条相关质量经验；同类问题最近 10 章出现至少 3 次才激活，连续 10 章未复发则退休。
- 每 10 章生成健康检查，每 20 章生成阶段复审；审计不得自动改写历史正式章节。
- 第一版不引入向量数据库、外部检索服务或云端记忆。
- `novel/state/current.json` 只在原子提升时改变；运行等待状态写入工作区 `run_manifest.json`。
- 旧 `provider` 配置必须继续工作；新模型档案缺省时全部回退到当前 Codex CLI。
- 当前基线为 `python3 -m unittest discover -v` 的 33 项测试全部通过。
- 当前执行环境的 `.git` 为只读；实现中的提交步骤需要在 `.git` 可写的环境中执行，或由用户在外部执行同一提交命令。

---

## File Structure

### 新建文件

- `scripts/prequel/model_router.py`：命名模型档案、阶段路由和单 Provider 兼容适配。
- `scripts/prequel/run_manifest.py`：输入指纹、阶段完成记录、失败记录和恢复判定。
- `scripts/prequel/evaluation.py`：专项审查验证、加权计分、候选资格和提升政策。
- `scripts/prequel/evolution.py`：候选生成、专项审查、盲选和定向修订流程。
- `scripts/prequel/memory.py`：ARCHIVAL 索引、质量经验生命周期和 CORE 检索。
- `scripts/prequel/audits.py`：十章健康检查、二十章阶段复审和创作债务更新。
- `schemas/specialist_review.schema.json`：四类专项 Reviewer 的严格输出契约。
- `schemas/ballot.schema.json`：盲选裁决的严格输出契约。
- `schemas/audit.schema.json`：阶段审计报告的严格输出契约。
- `agents/reviewer_continuity.md`、`agents/reviewer_character.md`、`agents/reviewer_craft.md`、`agents/reviewer_anti_slop.md`：四个独立 Reviewer 指令。
- `agents/selector.md`：匿名成对比较指令。
- `agents/arc_reviewer.md`：十章与二十章审计指令。
- `tests/evolution_fixtures.py`：新管线的稳定计划、正文、审查和选票工件生成器。
- `tests/test_model_router.py`：模型路由测试。
- `tests/test_run_manifest.py`：嵌套工件和恢复测试。
- `tests/test_evaluation.py`：专项验证、计分、盲选和提升政策测试。
- `tests/test_evolution.py`：候选、审查、选择和修订测试。
- `tests/test_memory.py`：索引、检索和经验生命周期测试。
- `tests/test_audits.py`：审计触发、报告和非正式写入测试。
- `novel/knowledge/memory_index.json`：可重建的正式章节索引初始空壳。
- `novel/knowledge/quality_lessons.json`：质量经验初始空壳。
- `novel/knowledge/creative_debts.json`：阶段审计债务初始空壳。

### 修改文件

- `scripts/prequel/provider.py`：从单个配置对象创建 Provider，并保留旧入口。
- `scripts/prequel/artifacts.py`：从根文件白名单升级为安全的嵌套相对路径白名单。
- `scripts/prequel/context_builder.py`：增加候选侧重点、专项 Reviewer、盲选和记忆上下文构建器。
- `scripts/prequel/pipeline.py`：调用进化引擎、恢复运行、决策提升和提升后派生更新。
- `scripts/orchestrator.py`：增加 `--resume`、`accept --candidate`、运行状态显示和 `audit --arc`。
- `config/prequel_config.json`：增加模型档案、阶段路由、评分阈值、候选数和审计间隔。
- `agents/writer.md`：明确候选侧重点和修订简报的边界。
- `README.md`、`init.md`：记录新流程、命令、工件和恢复方式。
- `tests/test_provider.py`、`tests/test_context_builder.py`、`tests/test_pipeline.py`、`tests/test_repository_hygiene.py`：兼容与回归覆盖。

---

### Task 1: 分阶段模型路由

**Files:**
- Create: `scripts/prequel/model_router.py`
- Modify: `scripts/prequel/provider.py`
- Modify: `config/prequel_config.json`
- Create: `tests/test_model_router.py`
- Modify: `tests/test_provider.py`

**Interfaces:**
- Consumes: 现有 `ModelProvider.generate(prompt, output_schema)` 和旧 `config["provider"]`。
- Produces: `provider_from_spec(spec, project_root)`、`StageModelRouter.from_config(config, project_root)`、`StageModelRouter.single(provider)`、`StageModelRouter.profile_for(stage)`、`StageModelRouter.provider_for(stage)`。

- [ ] **Step 1: 写模型路由失败测试**

```python
# tests/test_model_router.py
import unittest
from pathlib import Path

from scripts.prequel.errors import ProviderError
from scripts.prequel.model_router import StageModelRouter


class StubProvider:
    def generate(self, prompt, output_schema=None):
        return prompt


class ModelRouterTests(unittest.TestCase):
    def test_single_provider_serves_every_stage(self):
        provider = StubProvider()
        router = StageModelRouter.single(provider)
        self.assertIs(router.provider_for("planner"), provider)
        self.assertIs(router.provider_for("selector"), provider)

    def test_profile_inherits_legacy_command_and_overrides_timeout(self):
        config = {
            "provider": {"type": "codex_cli", "command": ["codex", "exec"], "timeout_seconds": 900},
            "model_profiles": {"default": {}, "judge": {"timeout_seconds": 1200}},
            "stage_routes": {"planner": "default", "selector": "judge"},
        }
        router = StageModelRouter.from_config(config, Path.cwd())
        self.assertEqual(router.provider_for("planner").timeout_seconds, 900)
        self.assertEqual(router.provider_for("selector").timeout_seconds, 1200)

    def test_unknown_profile_fails_preflight(self):
        config = {
            "provider": {"type": "codex_cli", "command": ["codex", "exec"]},
            "model_profiles": {"default": {}},
            "stage_routes": {"selector": "missing"},
        }
        with self.assertRaises(ProviderError):
            StageModelRouter.from_config(config, Path.cwd())
```

- [ ] **Step 2: 运行测试并确认缺少模块**

Run: `python3 -m unittest tests.test_model_router -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.prequel.model_router'`.

- [ ] **Step 3: 实现 Provider 配置合并与路由**

```python
# scripts/prequel/provider.py：用此函数承接现有 provider_from_config 的构造逻辑
def provider_from_spec(spec: dict, project_root: Path) -> CodexCliProvider:
    if spec.get("type") != "codex_cli":
        raise ProviderError("当前只支持 provider.type=codex_cli")
    command = spec.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ProviderError("provider.command 必须是非空字符串数组")
    if "--dangerously-bypass-approvals-and-sandbox" in command:
        raise ProviderError("provider.command 禁止绕过沙箱")
    timeout = spec.get("timeout_seconds", 900)
    if not isinstance(timeout, int) or timeout <= 0:
        raise ProviderError("provider.timeout_seconds 必须是正整数")
    return CodexCliProvider(command, timeout, project_root)


def provider_from_config(config: dict, project_root: Path) -> CodexCliProvider:
    return provider_from_spec(config.get("provider", {}), project_root)
```

```python
# scripts/prequel/model_router.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ProviderError
from .provider import ModelProvider, provider_from_spec


@dataclass(frozen=True)
class StageModelRouter:
    providers: dict[str, ModelProvider]
    routes: dict[str, str]

    @classmethod
    def single(cls, provider: ModelProvider) -> "StageModelRouter":
        return cls({"default": provider}, {})

    @classmethod
    def from_config(cls, config: dict, project_root: Path) -> "StageModelRouter":
        legacy = config.get("provider", {})
        profiles = config.get("model_profiles") or {"default": {}}
        if "default" not in profiles:
            profiles = {"default": {}, **profiles}
        providers = {
            name: provider_from_spec({**legacy, **spec}, project_root)
            for name, spec in profiles.items()
        }
        routes = config.get("stage_routes", {})
        missing = sorted(set(routes.values()) - set(providers))
        if missing:
            raise ProviderError("阶段路由引用未知模型档案: " + ", ".join(missing))
        return cls(providers, routes)

    def provider_for(self, stage: str) -> ModelProvider:
        profile = self.profile_for(stage)
        try:
            return self.providers[profile]
        except KeyError as exc:
            raise ProviderError(f"阶段 {stage} 没有可用模型档案") from exc

    def profile_for(self, stage: str) -> str:
        return self.routes.get(stage, "default")
```

在 `config/prequel_config.json` 中加入设计规格第 8 节的 `model_profiles` 和 `stage_routes`，档案只覆盖 `timeout_seconds`，命令继承现有 `provider`；同时加入后续任务共同使用的精确配置：

```json
{
  "quality_evolution": {
    "candidate_count": 3,
    "candidate_retries": 3,
    "review_retries": 2,
    "revision_rounds": 2,
    "weights": {"continuity": 0.3, "character": 0.25, "craft": 0.3, "anti_slop": 0.15},
    "candidate_floors": {"continuity": 85, "character": 75, "craft": 75, "anti_slop": 80},
    "auto_promote": {"weighted_score": 85, "continuity": 90, "character": 82, "craft": 82, "anti_slop": 82, "ballot_votes": 2},
    "manual_floor": 78,
    "max_dimension_regression": 3
  },
  "memory_management": {
    "chapter_summary_max_chars": 50,
    "summary_compress_interval": 20,
    "compress_target_chars": 10,
    "foreshadow_check_interval": 5,
    "style_drift_check_interval": 10,
    "max_context_size_kb": 80,
    "max_revealed_rules": 50,
    "max_retries": 3,
    "max_active_lessons": 8,
    "lesson_window_chapters": 10,
    "lesson_activation_count": 3,
    "lesson_retire_after_clean_chapters": 10
  },
  "audits": {"health_interval": 10, "arc_interval": 20}
}
```

更新现有 `memory_management` object，而不是写入第二个同名键。

- [ ] **Step 4: 运行路由与 Provider 回归测试**

Run: `python3 -m unittest tests.test_model_router tests.test_provider -v`

Expected: PASS.

- [ ] **Step 5: 提交模型路由**

```bash
git add scripts/prequel/model_router.py scripts/prequel/provider.py config/prequel_config.json tests/test_model_router.py tests/test_provider.py
git commit -m "feat: add stage model routing"
```

---

### Task 2: 安全嵌套工件与可恢复运行清单

**Files:**
- Modify: `scripts/prequel/artifacts.py`
- Create: `scripts/prequel/run_manifest.py`
- Create: `tests/test_run_manifest.py`
- Modify: `tests/test_provider.py`

**Interfaces:**
- Consumes: `atomic_save_json(path, value)`。
- Produces: `ChapterWorkspace.write_text(relative, content)`、`write_json`、`read_json`、`digest`、`exists`；`RunManifest.create`、`load`、`begin`、`can_reuse`、`complete`、`fail`、`set_status`。

- [ ] **Step 1: 写路径安全、哈希和恢复失败测试**

```python
# tests/test_run_manifest.py
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.errors import ArtifactValidationError
from scripts.prequel.run_manifest import RunManifest, fingerprint


class RunManifestTests(unittest.TestCase):
    def test_nested_candidate_artifact_is_allowed_and_hashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            workspace.write_text("candidates/candidate_01/draft.txt", "第3章：试门")
            self.assertEqual(len(workspace.digest("candidates/candidate_01/draft.txt")), 64)

    def test_parent_escape_and_unknown_leaf_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            for name in ("../chapter_003.txt", "candidates/candidate_01/formal.txt"):
                with self.assertRaises(ArtifactValidationError):
                    workspace.write_text(name, "越界")

    def test_completed_stage_is_reusable_only_when_inputs_and_outputs_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = ChapterWorkspace.create(Path(tmp), 3, 1)
            manifest = RunManifest.create(workspace, 3, "state-hash")
            workspace.write_text("candidates/candidate_01/draft.txt", "正文")
            manifest.complete("candidate.01.generate", "input-hash", ["candidates/candidate_01/draft.txt"], {"model_profile": "prose", "prompt_version": "writer-sha", "call_count": 1})
            self.assertTrue(manifest.can_reuse("candidate.01.generate", "input-hash"))
            workspace.write_text("candidates/candidate_01/draft.txt", "被修改")
            self.assertFalse(manifest.can_reuse("candidate.01.generate", "input-hash"))

    def test_fingerprint_is_stable_for_key_order(self):
        self.assertEqual(fingerprint({"b": 2, "a": 1}), fingerprint({"a": 1, "b": 2}))
```

- [ ] **Step 2: 运行测试并确认当前白名单不支持嵌套工件**

Run: `python3 -m unittest tests.test_run_manifest -v`

Expected: FAIL because nested paths are rejected and `run_manifest` does not exist.

- [ ] **Step 3: 实现安全相对路径工件仓库**

```python
# scripts/prequel/artifacts.py：替换 ALLOWED_ARTIFACTS 与 _target
import hashlib
from pathlib import PurePosixPath

ROOT_ARTIFACTS = {
    "context.json", "plan.json", "run_manifest.json", "decision.json",
    "decision.md", "promotion_manifest.json",
}
NESTED_PATTERNS = (
    re.compile(r"^candidates/candidate_\d{2}/(?:draft\.txt|generation\.json|static_review\.json|scorecard\.json)$"),
    re.compile(r"^candidates/candidate_\d{2}/reviews/(?:continuity|character|craft|anti_slop)\.json$"),
    re.compile(r"^comparisons/(?:initial|revision_\d{2})/ballot_\d{2}\.json$"),
    re.compile(r"^revisions/round_\d{2}/(?:brief\.json|draft\.txt|static_review\.json|scorecard\.json)$"),
    re.compile(r"^revisions/round_\d{2}/reviews/(?:continuity|character|craft|anti_slop)\.json$"),
)

def _safe_relative(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ArtifactValidationError(f"不安全的章节工件路径: {name}")
    if name not in ROOT_ARTIFACTS and not any(pattern.fullmatch(name) for pattern in NESTED_PATTERNS):
        raise ArtifactValidationError(f"不允许的章节工件: {name}")
    return name

def _target(self, name: str) -> Path:
    target = self.path / _safe_relative(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target

def read_text(self, name: str) -> str:
    target = self._target(name)
    try:
        return target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactValidationError(f"无法读取工件 {name}: {exc}") from exc

def exists(self, name: str) -> bool:
    return self._target(name).is_file()

def digest(self, name: str) -> str:
    target = self._target(name)
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError as exc:
        raise ArtifactValidationError(f"无法计算工件哈希 {name}: {exc}") from exc
```

- [ ] **Step 4: 实现原子运行清单**

```python
# scripts/prequel/run_manifest.py
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .artifacts import ChapterWorkspace
from .errors import ArtifactValidationError


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class RunManifest:
    workspace: ChapterWorkspace
    data: dict[str, Any]

    @classmethod
    def create(cls, workspace: ChapterWorkspace, chapter: int, state_hash: str) -> "RunManifest":
        value = {"chapter": chapter, "state_hash": state_hash, "status": "RUNNING", "current_stage": "init", "valid_candidates": 0, "waiting_reason": None, "stages": {}, "failures": []}
        workspace.write_json("run_manifest.json", value)
        return cls(workspace, value)

    @classmethod
    def load(cls, workspace: ChapterWorkspace) -> "RunManifest":
        value = workspace.read_json("run_manifest.json")
        if value.get("chapter") != workspace.chapter_number or not isinstance(value.get("stages"), dict):
            raise ArtifactValidationError("run_manifest 与工作区不匹配")
        return cls(workspace, value)

    def _save(self) -> None:
        self.workspace.write_json("run_manifest.json", self.data)

    def can_reuse(self, stage: str, input_hash: str) -> bool:
        record = self.data["stages"].get(stage)
        if not record or record.get("status") != "COMPLETED" or record.get("input_hash") != input_hash:
            return False
        return all(self.workspace.exists(path) and self.workspace.digest(path) == digest for path, digest in record["outputs"].items())

    def begin(self, stage: str) -> None:
        self.data["current_stage"] = stage
        self._save()

    def complete(self, stage: str, input_hash: str, outputs: list[str], metadata: dict[str, Any]) -> None:
        required = {"model_profile", "prompt_version", "call_count"}
        if set(metadata) != required or not isinstance(metadata["call_count"], int) or metadata["call_count"] < 0:
            raise ArtifactValidationError("阶段 metadata 必须包含模型档案、提示词版本和非负调用数")
        self.data["stages"][stage] = {
            "status": "COMPLETED", "input_hash": input_hash,
            "outputs": {path: self.workspace.digest(path) for path in outputs},
            **metadata,
        }
        self._save()

    def fail(self, stage: str, message: str) -> None:
        self.data["failures"].append({"stage": stage, "message": message})
        self.data["stages"][stage] = {"status": "FAILED"}
        self._save()

    def set_status(self, status: str, *, valid_candidates: int, waiting_reason: str | None = None) -> None:
        if status not in {"RUNNING", "WAITING_USER", "AUTO_PROMOTE", "REPLAN", "COMPLETED"}:
            raise ArtifactValidationError(f"无效运行状态: {status}")
        self.data.update({"status": status, "valid_candidates": valid_candidates, "waiting_reason": waiting_reason})
        self._save()
```

- [ ] **Step 5: 运行工件与清单测试**

Run: `python3 -m unittest tests.test_run_manifest tests.test_provider -v`

Expected: PASS.

- [ ] **Step 6: 提交可恢复工件基础**

```bash
git add scripts/prequel/artifacts.py scripts/prequel/run_manifest.py tests/test_run_manifest.py tests/test_provider.py
git commit -m "feat: add resumable chapter artifacts"
```

---

### Task 3: 专项审查、计分与提升政策

**Files:**
- Create: `scripts/prequel/evaluation.py`
- Create: `schemas/specialist_review.schema.json`
- Create: `schemas/ballot.schema.json`
- Create: `agents/reviewer_continuity.md`
- Create: `agents/reviewer_character.md`
- Create: `agents/reviewer_craft.md`
- Create: `agents/reviewer_anti_slop.md`
- Create: `agents/selector.md`
- Create: `tests/test_evaluation.py`
- Modify: `tests/test_provider.py`

**Interfaces:**
- Consumes: 静态检查结果和专项 Reviewer JSON。
- Produces: `DIMENSIONS`、`validate_specialist_review`、`build_scorecard`、`eligible`、`tally_ballots`、`revision_improved`、`promotion_decision`。

- [ ] **Step 1: 写专项验证和政策失败测试**

```python
# tests/test_evaluation.py
import unittest

from scripts.prequel.evaluation import (
    build_scorecard, eligible, promotion_decision, revision_improved,
    tally_ballots, validate_specialist_review,
)


def review(dimension, score, hard=False):
    return {
        "chapter_number": 3, "dimension": dimension, "score": score,
        "hard_failures": [{"code": "FACT", "quote": "门内", "explanation": "冲突"}] if hard else [],
        "warnings": [],
        "evidence": [{"quote": "门内", "finding": "证据"}, {"quote": "铁栓", "finding": "证据"}, {"quote": "纸灰", "finding": "证据"}],
        "required_revisions": [], "summary": "完成审查",
    }


class EvaluationTests(unittest.TestCase):
    def test_false_quote_invalidates_specialist_review(self):
        issues = validate_specialist_review(review("continuity", 90), "只有铁栓和纸灰", 3, "continuity")
        self.assertIn("REVIEW_FALSE_EVIDENCE", {issue.code for issue in issues})

    def test_dimension_floor_cannot_be_hidden_by_weighted_score(self):
        reviews = {
            "continuity": review("continuity", 95), "character": review("character", 74),
            "craft": review("craft", 95), "anti_slop": review("anti_slop", 95),
        }
        card = build_scorecard(reviews)
        self.assertFalse(eligible(card, {"continuity": 85, "character": 75, "craft": 75, "anti_slop": 80}))

    def test_ballot_requires_two_votes(self):
        self.assertEqual(tally_ballots(["candidate_01", "candidate_01", "candidate_02"]), "candidate_01")
        self.assertIsNone(tally_ballots(["candidate_01", "candidate_02", None]))

    def test_revision_regression_is_rejected(self):
        previous = {"weighted_score": 86, "scores": {"continuity": 92, "character": 84, "craft": 84, "anti_slop": 84}}
        current = {"weighted_score": 87, "scores": {"continuity": 88, "character": 90, "craft": 86, "anti_slop": 86}}
        self.assertFalse(revision_improved(previous, current, 2))

    def test_high_confidence_result_auto_promotes(self):
        card = {"weighted_score": 86, "scores": {"continuity": 92, "character": 84, "craft": 85, "anti_slop": 83}, "hard_failures": [], "required_revisions": []}
        self.assertEqual(promotion_decision(card, "candidate_01", "candidate_01", 2)["status"], "AUTO_PROMOTE")
```

- [ ] **Step 2: 运行测试并确认评价模块缺失**

Run: `python3 -m unittest tests.test_evaluation -v`

Expected: FAIL with missing `scripts.prequel.evaluation`.

- [ ] **Step 3: 创建严格模型输出 Schema**

`schemas/specialist_review.schema.json` 使用以下完整契约：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "specialist_review.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["chapter_number", "dimension", "score", "hard_failures", "warnings", "evidence", "required_revisions", "summary"],
  "properties": {
    "chapter_number": {"type": "integer", "minimum": 1},
    "dimension": {"type": "string", "enum": ["continuity", "character", "craft", "anti_slop"]},
    "score": {"type": "integer", "minimum": 0, "maximum": 100},
    "hard_failures": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["code", "quote", "explanation"],
        "properties": {"code": {"type": "string"}, "quote": {"type": "string"}, "explanation": {"type": "string"}}
      }
    },
    "warnings": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["code", "quote", "explanation"],
        "properties": {"code": {"type": "string"}, "quote": {"type": "string"}, "explanation": {"type": "string"}}
      }
    },
    "evidence": {
      "type": "array", "minItems": 3,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["quote", "finding"],
        "properties": {"quote": {"type": "string"}, "finding": {"type": "string"}}
      }
    },
    "required_revisions": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["code", "quote", "instruction", "acceptance"],
        "properties": {
          "code": {"type": "string"}, "quote": {"type": "string"},
          "instruction": {"type": "string"}, "acceptance": {"type": "string"}
        }
      }
    },
    "summary": {"type": "string"}
  }
}
```

`schemas/ballot.schema.json` 严格包含：

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "ballot.schema.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["winner", "criteria", "evidence", "rationale"],
  "properties": {
    "winner": {"type": "string", "enum": ["A", "B", "TIE"]},
    "criteria": {
      "type": "object",
      "additionalProperties": false,
      "required": ["plan_fulfillment", "character", "pacing", "anti_slop"],
      "properties": {
        "plan_fulfillment": {"type": "string", "enum": ["A", "B", "TIE"]},
        "character": {"type": "string", "enum": ["A", "B", "TIE"]},
        "pacing": {"type": "string", "enum": ["A", "B", "TIE"]},
        "anti_slop": {"type": "string", "enum": ["A", "B", "TIE"]}
      }
    },
    "evidence": {
      "type": "array",
      "minItems": 4,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["candidate", "quote", "finding"],
        "properties": {
          "candidate": {"type": "string", "enum": ["A", "B"]},
          "quote": {"type": "string"},
          "finding": {"type": "string"}
        }
      }
    },
    "rationale": {"type": "string"}
  }
}
```

- [ ] **Step 4: 实现确定性评价政策**

```python
# scripts/prequel/evaluation.py 的核心公开接口
from collections import Counter

from .quality import Issue

DIMENSIONS = ("continuity", "character", "craft", "anti_slop")
WEIGHTS = {"continuity": 0.30, "character": 0.25, "craft": 0.30, "anti_slop": 0.15}

def validate_specialist_review(review, draft, expected_chapter, expected_dimension):
    issues = []
    required = {"chapter_number", "dimension", "score", "hard_failures", "warnings", "evidence", "required_revisions", "summary"}
    if not isinstance(review, dict):
        return [Issue("SPECIALIST_NOT_OBJECT", "P1", "专项审查不是object", repr(review)[:120])]
    for field in sorted(required - review.keys()):
        issues.append(Issue("SPECIALIST_FIELD_MISSING", "P1", f"专项审查缺失字段: {field}", field))
    if review.get("chapter_number") != expected_chapter:
        issues.append(Issue("SPECIALIST_CHAPTER_MISMATCH", "P1", "专项审查章号不匹配", str(review.get("chapter_number"))))
    if review.get("dimension") != expected_dimension:
        issues.append(Issue("SPECIALIST_DIMENSION_MISMATCH", "P1", "专项审查维度不匹配", str(review.get("dimension"))))
    score = review.get("score")
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        issues.append(Issue("SPECIALIST_BAD_SCORE", "P1", "专项分数必须是0到100的整数", repr(score)))
    evidence_items = list(review.get("evidence", []))
    evidence_items += list(review.get("hard_failures", []))
    evidence_items += list(review.get("warnings", []))
    evidence_items += list(review.get("required_revisions", []))
    for item in evidence_items:
        quote = item.get("quote") if isinstance(item, dict) else None
        if not quote or quote not in draft:
            issues.append(Issue("REVIEW_FALSE_EVIDENCE", "P1", "专项审查引文不在正文", repr(quote)))
    return issues

def validate_ballot(ballot, draft_a, draft_b):
    issues = []
    if ballot.get("winner") not in {"A", "B", "TIE"}:
        issues.append(Issue("BALLOT_BAD_WINNER", "P1", "盲选胜者无效", str(ballot.get("winner"))))
    for item in ballot.get("evidence", []):
        candidate = item.get("candidate")
        quote = item.get("quote")
        source = draft_a if candidate == "A" else draft_b if candidate == "B" else ""
        if not quote or quote not in source:
            issues.append(Issue("BALLOT_FALSE_EVIDENCE", "P1", "盲选引文不在对应候选", repr(quote)))
    return issues

def build_scorecard(reviews, weights=None):
    weights = weights or WEIGHTS
    scores = {name: reviews[name]["score"] for name in DIMENSIONS}
    return {
        "scores": scores,
        "weighted_score": round(sum(scores[name] * weights[name] for name in DIMENSIONS), 2),
        "hard_failures": [item for name in DIMENSIONS for item in reviews[name]["hard_failures"]],
        "required_revisions": [item for name in DIMENSIONS for item in reviews[name]["required_revisions"]],
    }

def eligible(card, floors):
    return not card["hard_failures"] and all(card["scores"][name] >= floors[name] for name in DIMENSIONS)

def tally_ballots(winners):
    counts = Counter(winner for winner in winners if winner is not None)
    if not counts:
        return None
    winner, votes = counts.most_common(1)[0]
    return winner if votes >= 2 else None

def revision_improved(previous, current, supporting_votes, max_regression=3):
    if supporting_votes < 2 or current["weighted_score"] <= previous["weighted_score"]:
        return False
    return all(current["scores"][name] >= previous["scores"][name] - max_regression for name in DIMENSIONS)

def promotion_decision(card, score_winner, ballot_winner, ballot_votes, policy=None):
    policy = policy or {"weighted_score": 85, "continuity": 90, "character": 82, "craft": 82, "anti_slop": 82, "ballot_votes": 2, "manual_floor": 78}
    auto = (
        card["weighted_score"] >= policy["weighted_score"] and card["scores"]["continuity"] >= policy["continuity"]
        and all(card["scores"][name] >= policy[name] for name in ("character", "craft", "anti_slop"))
        and not card["hard_failures"] and not card["required_revisions"]
        and score_winner == ballot_winner and ballot_votes >= policy["ballot_votes"]
    )
    if auto:
        return {"status": "AUTO_PROMOTE", "reasons": []}
    if card["weighted_score"] >= policy["manual_floor"] and not card["hard_failures"]:
        return {"status": "WAITING_USER", "reasons": ["未同时满足全部自动提升条件"]}
    return {"status": "REPLAN", "reasons": ["总分低于78或存在硬失败"]}
```

同时实现 `validate_specialist_review` 和 `validate_ballot`，逐条验证章号、维度、分数类型以及引文是否存在于对应正文。

- [ ] **Step 5: 写六份紧凑、独立的 Agent 指令**

```markdown
<!-- agents/reviewer_continuity.md -->
# 连续性专项 Reviewer
你只审查时间、地点、人物知识边界、能力闸门、事实等级、异常规律、伏笔和规划兑现。你不改稿，不评价文采，不读取或猜测其他 Reviewer 结论。每条判断必须引用正文中原样存在的短句；事实冲突、越知识边界、能力提前和核心不可逆变化缺失写入 hard_failures。只输出 specialist_review schema JSON，dimension 固定为 continuity。
```

```markdown
<!-- agents/reviewer_character.md -->
# 人物专项 Reviewer
你只审查人物目标、利益、恐惧、关系变化、声纹、行为可信度和角色工具化。你不改稿，不替作者补动机，不评价设定正确性。每条判断必须引用正文原句；重大行动没有可见动机或人物知识超限写入 hard_failures，其余可定向修复问题写入 warnings 或 required_revisions。只输出 specialist_review schema JSON，dimension 固定为 character。
```

```markdown
<!-- agents/reviewer_craft.md -->
# 文学性专项 Reviewer
你只审查场景张力、节奏、信息抵达方式、情绪落点、结构重复和章末因果。你不改稿，不因篇幅长短直接判优，不把个人题材偏好当缺陷。每条判断必须引用正文原句；规划核心变化未被场景化写入 hard_failures，解释过多、场景冗余和节奏同构写入 warnings 或 required_revisions。只输出 specialist_review schema JSON，dimension 固定为 craft。
```

```markdown
<!-- agents/reviewer_anti_slop.md -->
# 反 AI 痕迹专项 Reviewer
你只审查解释过度、同义复述、固定身体动作、否定句模板、整齐排比、开头结尾模板和跨章机械重复。你不改稿，不因常用词单次出现而处罚。每条判断必须引用正文原句，并说明重复次数或上下文模式；大段复用写入 hard_failures，其余模式写入 warnings 或 required_revisions。只输出 specialist_review schema JSON，dimension 固定为 anti_slop。
```

```markdown
<!-- agents/selector.md -->
# 匿名候选裁决器
你只比较候选 A 与候选 B 对同一规划的兑现质量。不得推测生成模型、候选编号或作者意图，不以篇幅直接决定胜负。分别判断规划兑现、人物可信度、节奏与信息密度、反 AI 痕迹；证据必须标明 A/B 且引文原样存在。确无优势时选择 TIE。只输出 ballot schema JSON。
```

```markdown
<!-- agents/reviewer 文件共同约束，分别附加到上述四个文件末尾 -->
不得直接给出改写正文。required_revisions 只能说明修改对象、保留内容和可验证验收条件。不得引用规划或输入说明冒充正文证据。
```

- [ ] **Step 6: 运行评价与 Schema 回归测试**

Run: `python3 -m unittest tests.test_evaluation tests.test_provider -v`

Expected: PASS, including strict object contract checks for `specialist_review` and `ballot`.

- [ ] **Step 7: 提交评价协议**

```bash
git add scripts/prequel/evaluation.py schemas/specialist_review.schema.json schemas/ballot.schema.json agents/reviewer_*.md agents/selector.md tests/test_evaluation.py tests/test_provider.py
git commit -m "feat: add specialist quality evaluation"
```

---

### Task 4: 三候选生成与专项审查引擎

**Files:**
- Create: `scripts/prequel/evolution.py`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `agents/writer.md`
- Create: `tests/evolution_fixtures.py`
- Create: `tests/test_evolution.py`
- Modify: `tests/test_context_builder.py`

**Interfaces:**
- Consumes: 核准 plan、写前 state、近期正文、Planner context、`StageModelRouter`、`ChapterWorkspace`、`RunManifest`。
- Produces: `CandidateResult`、`QualityEvolutionEngine.generate_candidates`、`review_candidates`、`eligible_candidates`。

- [ ] **Step 1: 写候选差异、局部失败和独立审查失败测试**

```python
# tests/test_evolution.py：第一组测试
class EvolutionTests(unittest.TestCase):
    def test_three_candidates_receive_distinct_focus(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, router, context = build_engine(Path(tmp), valid_stage_outputs())
            candidates = engine.generate_candidates(context)
            self.assertEqual([item.focus for item in candidates], ["character_conflict", "horror_evidence", "pacing_compression"])
            prompts = "\n".join(router.prompts)
            for focus in ("character_conflict", "horror_evidence", "pacing_compression"):
                self.assertIn(focus, prompts)

    def test_failed_candidate_is_replenished_without_discarding_valid_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = valid_stage_outputs()
            outputs["candidate_writer"].insert(0, ProviderError("第一次生成失败"))
            engine, router, context = build_engine(Path(tmp), outputs)
            candidates = engine.generate_candidates(context)
            self.assertEqual(len(candidates), 3)
            self.assertEqual(router.stage_calls["candidate_writer"], 4)

    def test_invalid_continuity_quote_retries_only_continuity_reviewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = valid_stage_outputs()
            outputs["continuity_reviewer"].insert(0, specialist_review("continuity", 90, quote="不存在的引文"))
            engine, router, context = build_engine(Path(tmp), outputs)
            candidates = engine.generate_candidates(context)
            engine.review_candidates(context, candidates)
            self.assertEqual(router.stage_calls["continuity_reviewer"], 4)
            self.assertEqual(router.stage_calls["character_reviewer"], 3)
```

`tests/evolution_fixtures.py` 提供以下完整通用夹具；后续测试只调整 `outputs` 队列，不再创建未定义的场景辅助函数：

```python
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.context_builder import build_planner_context
from scripts.prequel.evolution import QualityEvolutionEngine
from scripts.prequel.run_manifest import RunManifest
from scripts.prequel.state_store import load_state
from tests.test_pipeline import make_project_fixture, valid_plan_json


class QueueProvider:
    def __init__(self, owner, stage):
        self.owner = owner
        self.stage = stage

    def generate(self, prompt, output_schema=None):
        self.owner.prompts.append(prompt)
        self.owner.stage_calls[self.stage] += 1
        value = self.owner.outputs[self.stage].pop(0)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


class RecordingRouter:
    def __init__(self, outputs):
        self.outputs = {stage: list(values) for stage, values in outputs.items()}
        self.stage_calls = Counter()
        self.prompts = []

    def provider_for(self, stage):
        return QueueProvider(self, stage)

    def profile_for(self, stage):
        return stage


def draft(number):
    sentences = [f"张洞按候选{number}的第{index}处记号核对铁栓时，纸灰已经越过门槛落到门内。" for index in range(1, 80)]
    return f"第1章：候选{number}\n\n" + "".join(sentences)


def specialist_review(dimension, score, quote="门内"):
    return {
        "chapter_number": 1, "dimension": dimension, "score": score,
        "hard_failures": [], "warnings": [],
        "evidence": [
            {"quote": quote, "finding": "异常进入门内"},
            {"quote": "铁栓", "finding": "现实物件承载冲突"},
            {"quote": "纸灰", "finding": "异常证据明确"},
        ],
        "required_revisions": [], "summary": "通过专项审查",
    }


def ballot(winner):
    return {
        "winner": winner,
        "criteria": {"plan_fulfillment": winner, "character": winner, "pacing": winner, "anti_slop": winner},
        "evidence": [
            {"candidate": "A", "quote": "铁栓", "finding": "A的物件证据"},
            {"candidate": "A", "quote": "纸灰", "finding": "A的异常证据"},
            {"candidate": "B", "quote": "铁栓", "finding": "B的物件证据"},
            {"candidate": "B", "quote": "纸灰", "finding": "B的异常证据"},
        ],
        "rationale": "按四项标准比较",
    }


def valid_stage_outputs():
    outputs = {"candidate_writer": [draft(1), draft(2), draft(3)]}
    for dimension, score in (("continuity", 92), ("character", 84), ("craft", 85), ("anti_slop", 83)):
        outputs[f"{dimension}_reviewer"] = [specialist_review(dimension, score) for _ in range(3)]
    outputs["selector"] = [ballot("A"), ballot("A"), ballot("A")]
    outputs["reviser"] = []
    return outputs


def build_engine(tmp_root, outputs, quality_config=None):
    root = make_project_fixture(tmp_root)
    source_root = Path.cwd()
    for relative in (
        "schemas/specialist_review.schema.json", "schemas/ballot.schema.json",
        "agents/reviewer_continuity.md", "agents/reviewer_character.md",
        "agents/reviewer_craft.md", "agents/reviewer_anti_slop.md", "agents/selector.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, target)
    state = load_state(root / "novel/state/current.json")
    plan = json.loads(valid_plan_json())
    planner_context = build_planner_context(root, state)
    workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
    manifest = RunManifest.create(workspace, 1, "state-hash")
    router = RecordingRouter(outputs)
    engine = QualityEvolutionEngine(
        root, router, workspace, manifest,
        quality_config or {"candidate_count": 3, "candidate_retries": 3, "review_retries": 2},
    )
    context = {"state": state, "plan": plan, "recent": [], "planner_context": planner_context, "memory": {}}
    return engine, router, context
```

- [ ] **Step 2: 运行测试并确认候选引擎缺失**

Run: `python3 -m unittest tests.test_evolution -v`

Expected: FAIL with missing `scripts.prequel.evolution`.

- [ ] **Step 3: 增加阶段专用上下文构建器**

```python
# scripts/prequel/context_builder.py
CANDIDATE_FOCUSES = {
    "character_conflict": "强化人物目标冲突、关系变化和对白差异，不改变规划事实",
    "horror_evidence": "强化可观察异常、试错链和现实代价，不增加关键解法",
    "pacing_compression": "减少解释与重复，使场景进入退出更锋利，不删除不可逆变化",
}

def build_candidate_packet(state, plan, recent_texts, planner_context, focus, memory_context=None):
    packet = build_writer_packet(state, plan, recent_texts, planner_context)
    packet["candidate_focus"] = {"id": focus, "instruction": CANDIDATE_FOCUSES[focus]}
    packet["memory"] = memory_context or {"archive": [], "lessons": [], "debts": []}
    return packet

def build_specialist_packet(dimension, state, plan, draft, static_review, planner_context):
    return {
        "dimension": dimension, "chapter_number": plan["chapter_number"], "plan": plan,
        "draft": draft, "static_review": static_review, "continuity_before": state,
        "canon_facts": planner_context.get("canon_facts", []), "era_bans": planner_context.get("era_bans", {}),
    }
```

在 `agents/writer.md` 增加：候选侧重点只决定表现重点，不能改动规划；有 `revision_context` 时必须输出完整修订稿并保留指定内容。

- [ ] **Step 4: 实现候选与专项审查引擎**

```python
# scripts/prequel/evolution.py 的候选与审查实现
import json
import re
from dataclasses import dataclass, field

from .context_builder import build_candidate_packet, build_specialist_packet
from .errors import ArtifactValidationError, ProviderError, QualityGateError
from .evaluation import DIMENSIONS, build_scorecard, validate_specialist_review
from .quality import scan_draft
from .run_manifest import fingerprint


def _parse_model_json(raw, label):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(f"{label}不是合法JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactValidationError(f"{label}根节点必须是object")
    return value


@dataclass
class CandidateResult:
    candidate_id: str
    focus: str
    draft: str
    draft_path: str
    static_review: dict
    version: str
    reviews: dict[str, dict] = field(default_factory=dict)
    scorecard: dict | None = None


class QualityEvolutionEngine:
    FOCUSES = ("character_conflict", "horror_evidence", "pacing_compression")

    def __init__(self, project_root, router, workspace, manifest, quality_config):
        self.project_root = project_root
        self.router = router
        self.workspace = workspace
        self.manifest = manifest
        self.quality_config = quality_config

    def generate_candidates(self, context):
        target = self.quality_config.get("candidate_count", 3)
        retry_limit = self.quality_config.get("candidate_retries", 3)
        candidates = []
        for slot in range(1, target + 1):
            focus = self.FOCUSES[(slot - 1) % len(self.FOCUSES)]
            candidate = self._generate_slot(context, slot, focus, retry_limit, candidates)
            if candidate is not None:
                candidates.append(candidate)
        if len(candidates) < 2:
            raise QualityGateError("有效候选少于两个，无法执行盲选")
        return candidates

    def _generate_slot(self, context, slot, focus, retry_limit, accepted):
        candidate_id = f"candidate_{slot:02d}"
        draft_path = f"candidates/{candidate_id}/draft.txt"
        generation_path = f"candidates/{candidate_id}/generation.json"
        static_path = f"candidates/{candidate_id}/static_review.json"
        for retry in range(1, retry_limit + 1):
            packet = build_candidate_packet(
                context["state"], context["plan"], context["recent"],
                context["planner_context"], focus, context.get("memory"),
            )
            packet["candidate_retry"] = retry
            input_hash = fingerprint(packet)
            stage = f"candidate.{slot:02d}.generate.{retry:02d}"
            try:
                if self.manifest.can_reuse(stage, input_hash):
                    draft = self.workspace.read_text(draft_path)
                    static_review = self.workspace.read_json(static_path)
                else:
                    provider = self.router.provider_for("candidate_writer")
                    role = (self.project_root / "agents/writer.md").read_text(encoding="utf-8")
                    prompt = role + "\n\n# 唯一输入工件\n" + json.dumps(packet, ensure_ascii=False, indent=2)
                    draft = provider.generate(prompt, None)
                    comparison_texts = context["recent"] + [item.draft for item in accepted]
                    static_review = scan_draft(draft, comparison_texts, context["planner_context"]["era_bans"], context["plan"])
                    self.workspace.write_text(draft_path, draft)
                    self.workspace.write_json(generation_path, {"focus": focus, "retry": retry, "input_hash": input_hash})
                    self.workspace.write_json(static_path, static_review)
                    self.manifest.complete(stage, input_hash, [draft_path, generation_path, static_path], {
                        "model_profile": self.router.profile_for("candidate_writer"),
                        "prompt_version": fingerprint(role), "call_count": 1,
                    })
                if static_review["passed"]:
                    return CandidateResult(candidate_id, focus, draft, draft_path, static_review, candidate_id)
            except (ArtifactValidationError, ProviderError) as exc:
                self.manifest.fail(stage, str(exc))
        return None

    def review_candidates(self, context, candidates):
        retry_limit = self.quality_config.get("review_retries", 2)
        for candidate in candidates:
            for dimension in DIMENSIONS:
                candidate.reviews[dimension] = self._review_one(context, candidate, dimension, retry_limit)
            candidate.scorecard = build_scorecard(candidate.reviews)
            self.workspace.write_json(f"candidates/{candidate.candidate_id}/scorecard.json", candidate.scorecard)
        return candidates

    def _review_one(self, context, candidate, dimension, retry_limit):
        path = f"candidates/{candidate.candidate_id}/reviews/{dimension}.json"
        packet = build_specialist_packet(
            dimension, context["state"], context["plan"], candidate.draft,
            candidate.static_review, context["planner_context"],
        )
        for retry in range(1, retry_limit + 1):
            input_hash = fingerprint({"packet": packet, "retry": retry})
            stage = f"{candidate.candidate_id}.review.{dimension}.{retry:02d}"
            if self.manifest.can_reuse(stage, input_hash):
                return self.workspace.read_json(path)
            try:
                role = (self.project_root / "agents" / f"reviewer_{dimension}.md").read_text(encoding="utf-8")
                prompt = role + "\n\n# 唯一输入工件\n" + json.dumps(packet, ensure_ascii=False, indent=2)
                raw = self.router.provider_for(f"{dimension}_reviewer").generate(
                    prompt, self.project_root / "schemas/specialist_review.schema.json"
                )
                review = _parse_model_json(raw, f"{dimension}_review")
                issues = validate_specialist_review(review, candidate.draft, context["plan"]["chapter_number"], dimension)
                if issues:
                    raise ArtifactValidationError("；".join(issue.message for issue in issues))
                self.workspace.write_json(path, review)
                self.manifest.complete(stage, input_hash, [path], {
                    "model_profile": self.router.profile_for(f"{dimension}_reviewer"),
                    "prompt_version": fingerprint(role), "call_count": 1,
                })
                return review
            except (ArtifactValidationError, ProviderError) as exc:
                self.manifest.fail(stage, str(exc))
        raise QualityGateError(f"{candidate.candidate_id} 的 {dimension} 审查连续失败")
```

同时为 `ChapterWorkspace` 增加 `read_text(relative)`，与 `read_json` 使用同一安全路径校验；运行清单调用键固定为 `candidate.01.generate.01` 和 `candidate_01.review.continuity.01` 形式，所有写入完成后再调用 `manifest.complete`。

- [ ] **Step 5: 运行候选、上下文和现有静态检查测试**

Run: `python3 -m unittest tests.test_evolution tests.test_context_builder tests.test_quality -v`

Expected: PASS.

- [ ] **Step 6: 提交候选审查引擎**

```bash
git add scripts/prequel/evolution.py scripts/prequel/context_builder.py agents/writer.md tests/evolution_fixtures.py tests/test_evolution.py tests/test_context_builder.py
git commit -m "feat: generate and review chapter candidates"
```

---

### Task 5: 盲选、定向修订与最终决策

**Files:**
- Modify: `scripts/prequel/evolution.py`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `tests/test_evolution.py`

**Interfaces:**
- Consumes: 合格 `CandidateResult` 列表、`ballot.schema.json`、专项共识问题。
- Produces: `SelectionResult`、`QualityEvolutionEngine.select_candidate`、`revise_winner`、`run`，以及 `decision.json`/`decision.md`。

- [ ] **Step 1: 写循环赛、冲突和修订回退失败测试**

```python
# tests/test_evolution.py：第二组测试
def test_round_robin_selects_two_win_candidate(self):
    with tempfile.TemporaryDirectory() as tmp:
        outputs = valid_stage_outputs()
        engine, router, context = build_engine(Path(tmp), outputs)
        candidates = engine.review_candidates(context, engine.generate_candidates(context))
        result = engine.select_candidate(context, candidates)
        self.assertEqual(result.winner_id, "candidate_01")
        self.assertEqual(result.ballot_votes, 2)

def test_score_winner_and_ballot_winner_conflict_waits_for_user(self):
    with tempfile.TemporaryDirectory() as tmp:
        outputs = valid_stage_outputs()
        outputs["selector"] = [ballot("B"), ballot("A"), ballot("A")]
        engine, router, context = build_engine(Path(tmp), outputs)
        result = engine.run(context)
        self.assertEqual(result.decision["status"], "WAITING_USER")

def test_improving_revision_replaces_winner(self):
    with tempfile.TemporaryDirectory() as tmp:
        outputs = valid_stage_outputs()
        outputs["reviser"] = [draft(4)]
        for dimension, score in (("continuity", 94), ("character", 87), ("craft", 88), ("anti_slop", 86)):
            outputs[f"{dimension}_reviewer"].append(specialist_review(dimension, score))
        outputs["selector"] = [ballot("B"), ballot("B"), ballot("B")]
        engine, router, context = build_engine(Path(tmp), outputs)
        candidates = engine.review_candidates(context, engine.generate_candidates(context))
        candidates[0].reviews["character"]["required_revisions"] = [{"code": "MOTIVE", "quote": "铁栓", "instruction": "明确现实动机", "acceptance": "选择改变"}]
        candidates[0].reviews["craft"]["required_revisions"] = [{"code": "MOTIVE", "quote": "铁栓", "instruction": "用冲突呈现动机", "acceptance": "删除解释"}]
        result = engine.revise_winner(context, candidates[0])
        self.assertEqual(result.version, "revision_01")

def test_regressing_revision_keeps_previous_version(self):
    with tempfile.TemporaryDirectory() as tmp:
        outputs = valid_stage_outputs()
        outputs["reviser"] = [draft(5)]
        for dimension, score in (("continuity", 88), ("character", 87), ("craft", 88), ("anti_slop", 86)):
            outputs[f"{dimension}_reviewer"].append(specialist_review(dimension, score))
        outputs["selector"] = [ballot("B"), ballot("B"), ballot("B")]
        engine, router, context = build_engine(Path(tmp), outputs)
        candidates = engine.review_candidates(context, engine.generate_candidates(context))
        candidates[0].reviews["character"]["required_revisions"] = [{"code": "MOTIVE", "quote": "铁栓", "instruction": "明确现实动机", "acceptance": "选择改变"}]
        candidates[0].reviews["craft"]["required_revisions"] = [{"code": "MOTIVE", "quote": "铁栓", "instruction": "用冲突呈现动机", "acceptance": "删除解释"}]
        result = engine.revise_winner(context, candidates[0])
        self.assertEqual(result.version, "candidate_01")
```

- [ ] **Step 2: 运行选择测试并确认方法缺失**

Run: `python3 -m unittest tests.test_evolution.EvolutionTests -v`

Expected: FAIL with missing selection and revision methods.

- [ ] **Step 3: 增加匿名盲选与修订包**

```python
# scripts/prequel/context_builder.py
def build_ballot_packet(plan, draft_a, draft_b):
    return {"plan": plan, "candidate_A": draft_a, "candidate_B": draft_b}

def build_revision_packet(state, plan, draft, required_revisions, preserve):
    return {
        "plan": plan,
        "continuity": {"timeline": state["timeline"], "protagonist": state["protagonist"], "characters": state["characters"]},
        "revision_context": {
            "previous_draft": draft,
            "instructions": required_revisions,
            "preserve": preserve,
            "acceptance": "总分提高、各维度下降不超过3分、盲选至少2票支持修订稿",
        },
    }
```

- [ ] **Step 4: 实现三票盲选和两轮修订**

`select_candidate` 对三个合格候选执行 `(01,02)`、`(01,03)`、`(02,03)`；只有两个候选时交换 A/B 顺序运行三次。每张模型选票经 `validate_ballot` 后映射回真实候选 ID，并将映射和模型结果一起写入 `comparisons/initial/ballot_NN.json`。

`revise_winner` 从至少两个专项 Reviewer 指向同一问题代码的修订项生成 `brief.json`。每轮修订重新执行静态检查、四专项审查和三张“上一版本 vs 修订稿”盲选。只在 `revision_improved` 返回 true 时替换当前版本。

`run` 最终写入：

```python
decision = {
    "chapter_number": plan["chapter_number"],
    "status": policy["status"],
    "recommended_candidate": final.candidate_id,
    "recommended_version": final.version,
    "score_winner": score_winner,
    "ballot_winner": selection.winner_id,
    "ballot_votes": selection.ballot_votes,
    "scorecard": final.scorecard,
    "reasons": policy["reasons"],
    "final_draft_path": final.draft_path,
}
```

`decision.md` 由确定性模板渲染候选去留、四维分数、选票、修订前后差异和提升条件，不让模型自行概括决策历史。

调用 `promotion_decision` 时传入 `{**quality_config["auto_promote"], "manual_floor": quality_config["manual_floor"]}`；调用 `build_scorecard`、`eligible` 和 `revision_improved` 时分别传入配置中的 weights、candidate_floors 和 max_dimension_regression，禁止在进化引擎重复硬编码阈值。

- [ ] **Step 5: 运行进化引擎完整测试**

Run: `python3 -m unittest tests.test_evolution tests.test_evaluation -v`

Expected: PASS.

- [ ] **Step 6: 提交选择与修订**

```bash
git add scripts/prequel/evolution.py scripts/prequel/context_builder.py tests/test_evolution.py
git commit -m "feat: select and revise winning drafts"
```

---

### Task 6: 接入 WritingPipeline、恢复和人工接受

**Files:**
- Modify: `scripts/prequel/pipeline.py`
- Modify: `scripts/orchestrator.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `QualityEvolutionEngine.run` 的最终决策。
- Produces: `WritingPipeline.run_next(dry_run=False, resume=False)`、`accept_candidate(project_root, attempt=None, candidate=None)`、派生运行状态查询。

- [ ] **Step 1: 写自动提升、等待、恢复和状态竞争测试**

```python
# tests/test_pipeline.py：新增测试
import shutil

from scripts.prequel.artifacts import ChapterWorkspace
from scripts.prequel.run_manifest import RunManifest, fingerprint
from scripts.prequel.state_store import load_state
from tests.evolution_fixtures import RecordingRouter, specialist_review, valid_stage_outputs

def make_evolution_project(root):
    root = make_project_fixture(root)
    source_root = Path.cwd()
    for relative in (
        "schemas/specialist_review.schema.json", "schemas/ballot.schema.json",
        "agents/reviewer_continuity.md", "agents/reviewer_character.md",
        "agents/reviewer_craft.md", "agents/reviewer_anti_slop.md", "agents/selector.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, target)
    config_path = root / "config/prequel_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["quality_evolution"] = {"candidate_count": 3, "candidate_retries": 3, "review_retries": 2, "revision_rounds": 2}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return root

def pipeline_router(scores=(92, 84, 85, 83)):
    outputs = valid_stage_outputs()
    outputs["planner"] = [valid_plan_json()]
    for dimension, score in zip(("continuity", "character", "craft", "anti_slop"), scores):
        outputs[f"{dimension}_reviewer"] = [specialist_review(dimension, score) for _ in range(3)]
    return RecordingRouter(outputs)

def interrupted_project(root):
    root = make_evolution_project(root)
    state = load_state(root / "novel/state/current.json")
    workspace = ChapterWorkspace.create(root / "novel/work", 1, 1)
    workspace.write_json("context.json", {"chapter": 1})
    workspace.write_json("plan.json", json.loads(valid_plan_json()))
    RunManifest.create(workspace, 1, fingerprint(state))
    return root

def test_high_confidence_evolution_result_promotes_atomically(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = make_evolution_project(Path(tmp))
        result = WritingPipeline(root, providers=pipeline_router()).run_next()
        self.assertTrue(result.promoted)
        self.assertTrue((root / "novel/chapters/vol_01/chapter_001.txt").exists())

def test_borderline_result_keeps_formal_state_unchanged(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = make_evolution_project(Path(tmp))
        original = (root / "novel/state/current.json").read_bytes()
        result = WritingPipeline(root, providers=pipeline_router((89, 80, 80, 81))).run_next()
        self.assertFalse(result.promoted)
        self.assertEqual((root / "novel/state/current.json").read_bytes(), original)

def test_resume_rejects_changed_formal_state(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = interrupted_project(Path(tmp))
        state_path = root / "novel/state/current.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_updated"] = "changed-after-run"
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(QualityGateError):
            WritingPipeline(root, providers=RecordingRouter({})).run_next(resume=True)
```

- [ ] **Step 2: 运行管线测试并确认新参数缺失**

Run: `python3 -m unittest tests.test_pipeline -v`

Expected: FAIL on `providers`, `resume`, and candidate decision behavior.

- [ ] **Step 3: 将 WritingPipeline 改为路由和进化引擎编排**

构造器保持旧 `provider` 参数，并增加 `providers: StageModelRouter | None`；优先级为显式 `providers`、`provider` 包装为 single、配置构造 router。

`run_next` 执行：预检 → 加载或创建 attempt → 校验 state hash → 生成/复用 plan → 调用 `QualityEvolutionEngine.run` → 根据 decision 处理 `REPLAN`、`WAITING_USER` 或 `AUTO_PROMOTE`。`dry_run=True` 无条件禁止提升。

`accept_candidate` 读取 `decision.json`；指定候选时只允许选择已通过所有硬门禁的候选，并重新执行该候选四专项审查。提升前重新验证 state hash、next chapter、正式目标不存在和全部工件哈希。

- [ ] **Step 4: 扩展 CLI 参数与状态输出**

```python
# scripts/orchestrator.py 参数增量
next_parser.add_argument("--resume", action="store_true", help="恢复输入仍有效的最近运行")
accept.add_argument("--candidate", type=int, choices=(1, 2, 3), help="选择通过硬门禁的候选")

# command_next
result = WritingPipeline(PROJECT_ROOT).run_next(dry_run=args.dry_run, resume=args.resume)

# command_status
runtime = latest_run_status(PROJECT_ROOT, chapter["next_chapter"])
if runtime:
    print(f"运行: {runtime['status']} / {runtime['stage']}")
    print(f"有效候选: {runtime['valid_candidates']}")
    if runtime.get("waiting_reason"):
        print(f"等待原因: {runtime['waiting_reason']}")
```

- [ ] **Step 5: 运行完整管线回归测试**

Run: `python3 -m unittest tests.test_pipeline tests.test_state_store tests.test_repository_hygiene -v`

Expected: PASS;旧单 Provider 测试仍通过，新进化测试使用显式 router。

- [ ] **Step 6: 提交管线与 CLI**

```bash
git add scripts/prequel/pipeline.py scripts/orchestrator.py tests/test_pipeline.py
git commit -m "feat: integrate resumable quality evolution pipeline"
```

---

### Task 7: 分层记忆与可遗忘质量经验

**Files:**
- Create: `scripts/prequel/memory.py`
- Create: `novel/knowledge/memory_index.json`
- Create: `novel/knowledge/quality_lessons.json`
- Create: `novel/knowledge/creative_debts.json`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `scripts/prequel/pipeline.py`
- Create: `tests/test_memory.py`

**Interfaces:**
- Consumes: 正式章节、plan、最终专项 findings、正式 state 和来源哈希。
- Produces: `MemoryStore.rebuild_index`、`retrieve`、`update_lessons`、`retire_lessons`、`core_context`。

- [ ] **Step 1: 写来源失效、相关检索和经验生命周期失败测试**

```python
# tests/test_memory.py
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.memory import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "novel/knowledge").mkdir(parents=True)
        (self.root / "novel/chapters/vol_01").mkdir(parents=True)
        (self.root / "novel/knowledge/memory_index.json").write_text('{"schema":"novel-memory-index","entries":[]}', encoding="utf-8")
        (self.root / "novel/knowledge/quality_lessons.json").write_text('{"schema":"novel-quality-lessons","lessons":[]}', encoding="utf-8")
        (self.root / "novel/knowledge/creative_debts.json").write_text('{"schema":"novel-creative-debts","debts":[]}', encoding="utf-8")
        self.store = MemoryStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_retrieve_uses_character_event_location_and_foreshadow_ids(self):
        chapter = self.root / "novel/chapters/vol_01/chapter_001.txt"
        chapter.write_text("第1章：纸灰", encoding="utf-8")
        entry = {"chapter": 1, "source_path": "novel/chapters/vol_01/chapter_001.txt", "source_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(), "characters": ["张洞"], "locations": ["祠堂"], "event_id": "event_1", "foreshadows": ["F-A01"], "irreversible_changes": ["known_info"], "hook_type": "安全区崩坏", "summary": "纸灰越界"}
        (self.root / "novel/knowledge/memory_index.json").write_text(json.dumps({"schema": "novel-memory-index", "entries": [entry]}, ensure_ascii=False), encoding="utf-8")
        result = self.store.retrieve({"characters": ["张洞"], "event_id": "event_1", "locations": ["祠堂"], "foreshadows": ["F-A01"]})
        self.assertEqual(result[0]["chapter"], 1)

    def test_changed_source_hash_invalidates_archive_entry(self):
        chapter = self.root / "novel/chapters/vol_01/chapter_001.txt"
        chapter.write_text("原正文", encoding="utf-8")
        entry = {"chapter": 1, "source_path": "novel/chapters/vol_01/chapter_001.txt", "source_sha256": hashlib.sha256(chapter.read_bytes()).hexdigest(), "characters": [], "locations": [], "event_id": "event_1", "foreshadows": [], "irreversible_changes": [], "hook_type": "未解问题", "summary": "摘要"}
        (self.root / "novel/knowledge/memory_index.json").write_text(json.dumps({"schema": "novel-memory-index", "entries": [entry]}), encoding="utf-8")
        chapter.write_text("被修改的正式正文", encoding="utf-8")
        self.assertEqual(self.store.valid_entries(), [])

    def test_problem_activates_after_three_occurrences_in_ten_chapters(self):
        for chapter in (2, 5, 9):
            self.store.update_lessons(chapter, [{"code": "REPEATED_INVESTIGATION", "scope": {"event_id": "event_1"}, "instruction": "改变调查信息抵达方式", "quote": "逐项核对"}])
        self.assertEqual(self.store.active_lessons()[0]["status"], "active")

    def test_active_lesson_retires_after_ten_clean_chapters(self):
        for chapter in (2, 5, 10):
            self.store.update_lessons(chapter, [{"code": "REPEATED_INVESTIGATION", "scope": {}, "instruction": "改变调查结构", "quote": "逐项核对"}])
        self.store.retire_lessons(20)
        self.assertEqual(self.store.all_lessons()[0]["status"], "retired")

    def test_core_context_limits_lessons_to_eight(self):
        findings = [{"code": f"STYLE_{index}", "scope": {"characters": ["张洞"]}, "instruction": f"避免模式{index}", "quote": f"证据{index}"} for index in range(12)]
        for chapter in (1, 2, 3):
            self.store.update_lessons(chapter, findings)
        plan = {"event_id": "event_1", "scenes": [{"characters": ["张洞"], "location": "祠堂"}], "foreshadow_operations": {"plant": [], "recover": []}}
        self.assertEqual(len(self.store.core_context(plan)["lessons"]), 8)
```

- [ ] **Step 2: 运行测试并确认记忆模块缺失**

Run: `python3 -m unittest tests.test_memory -v`

Expected: FAIL with missing `scripts.prequel.memory`.

- [ ] **Step 3: 创建三个稳定空壳文件**

`novel/knowledge/memory_index.json`：

```json
{"schema": "novel-memory-index", "entries": []}
```

`novel/knowledge/quality_lessons.json`：

```json
{"schema": "novel-quality-lessons", "lessons": []}
```

`novel/knowledge/creative_debts.json`：

```json
{"schema": "novel-creative-debts", "debts": []}
```

- [ ] **Step 4: 实现确定性 MemoryStore**

```python
# scripts/prequel/memory.py 的核心实现
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .state_store import atomic_save_json


class MemoryStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self.index_path = self.project_root / "novel/knowledge/memory_index.json"
        self.lessons_path = self.project_root / "novel/knowledge/quality_lessons.json"
        self.debts_path = self.project_root / "novel/knowledge/creative_debts.json"

    def _load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def all_lessons(self):
        return self._load(self.lessons_path)["lessons"]

    def active_lessons(self):
        return [item for item in self.all_lessons() if item["status"] == "active"]

    def valid_entries(self):
        valid = []
        for item in self._load(self.index_path)["entries"]:
            source = self.project_root / item["source_path"]
            if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == item["source_sha256"]:
                valid.append(item)
        return valid

    def retrieve(self, query, limit=20):
        def score(item):
            value = 3 if item["event_id"] == query.get("event_id") else 0
            value += 2 * len(set(item["characters"]) & set(query.get("characters", [])))
            value += 2 * len(set(item["locations"]) & set(query.get("locations", [])))
            value += 3 * len(set(item["foreshadows"]) & set(query.get("foreshadows", [])))
            return value
        ranked = [(score(item), item) for item in self.valid_entries()]
        return [item for value, item in sorted(ranked, key=lambda pair: (pair[0], pair[1]["chapter"]), reverse=True) if value > 0][:limit]

    def record_promoted_chapter(self, chapter_path, plan, summary):
        data = self._load(self.index_path)
        number = plan["chapter_number"]
        characters = sorted({name for scene in plan["scenes"] for name in scene["characters"]})
        locations = sorted({scene["location"] for scene in plan["scenes"]})
        foreshadows = plan["foreshadow_operations"]["plant"] + plan["foreshadow_operations"]["recover"]
        entry = {
            "chapter": number, "source_path": str(chapter_path.relative_to(self.project_root)),
            "source_sha256": hashlib.sha256(chapter_path.read_bytes()).hexdigest(),
            "characters": characters, "locations": locations, "event_id": plan["event_id"],
            "foreshadows": foreshadows, "irreversible_changes": sorted(key for key, value in plan["state_changes"].items() if value not in (None, [], {})),
            "hook_type": plan["hook"]["type"], "summary": summary,
        }
        data["entries"] = [item for item in data["entries"] if item["chapter"] != number] + [entry]
        atomic_save_json(self.index_path, data)

    def rebuild_index(self):
        entries = []
        pattern = re.compile(r"## Memory Record\s*```json\s*(\{.*?\})\s*```", re.S)
        for meta_path in sorted((self.project_root / "novel/chapters/meta").glob("chapter_*.md")):
            match = pattern.search(meta_path.read_text(encoding="utf-8"))
            if not match:
                continue
            item = json.loads(match.group(1))
            chapter_path = self.project_root / item["source_path"]
            if chapter_path.is_file():
                item["source_sha256"] = hashlib.sha256(chapter_path.read_bytes()).hexdigest()
                entries.append(item)
        atomic_save_json(self.index_path, {"schema": "novel-memory-index", "entries": entries})
        return entries

    def update_lessons(self, chapter, findings):
        data = self._load(self.lessons_path)
        by_code = {item["code"]: item for item in data["lessons"]}
        for finding in findings:
            item = by_code.setdefault(finding["code"], {
                "code": finding["code"], "scope": finding["scope"], "instruction": finding["instruction"],
                "first_seen": chapter, "last_seen": chapter, "occurrences": [], "evidence": [], "status": "candidate",
            })
            item["last_seen"] = chapter
            item["occurrences"] = [value for value in item["occurrences"] if value >= chapter - 9] + [chapter]
            item["evidence"] = (item["evidence"] + [{"chapter": chapter, "quote": finding["quote"]}])[-10:]
            if len(set(item["occurrences"])) >= 3:
                item["status"] = "active"
        data["lessons"] = sorted(by_code.values(), key=lambda item: item["code"])
        atomic_save_json(self.lessons_path, data)

    def retire_lessons(self, current_chapter):
        data = self._load(self.lessons_path)
        for item in data["lessons"]:
            if item["status"] == "active" and current_chapter - item["last_seen"] >= 10:
                item["status"] = "retired"
        atomic_save_json(self.lessons_path, data)

    def core_context(self, plan):
        characters = sorted({name for scene in plan.get("scenes", []) for name in scene.get("characters", [])})
        locations = sorted({scene.get("location") for scene in plan.get("scenes", []) if scene.get("location")})
        foreshadows = plan.get("foreshadow_operations", {}).get("plant", []) + plan.get("foreshadow_operations", {}).get("recover", [])
        query = {"characters": characters, "locations": locations, "event_id": plan.get("event_id"), "foreshadows": foreshadows}
        relevant = []
        for item in self.active_lessons():
            scope = item.get("scope", {})
            if not scope or set(scope.get("characters", [])) & set(characters) or scope.get("event_id") == plan.get("event_id"):
                relevant.append(item)
        relevant.sort(key=lambda item: item["last_seen"], reverse=True)
        return {"archive": self.retrieve(query), "lessons": relevant[:8], "debts": self._load(self.debts_path)["debts"]}
```

索引检索只按稳定 ID 完全匹配计分，不进行模糊语义搜索。经验项固定保存最近十章 occurrences，并允许退休经验在再次累计三次后重新激活。

- [ ] **Step 5: 接入 Planner/Writer CORE 和提升后派生更新**

`build_planner_context` 增加 `memory_context` 参数并注入相关 archive、lessons、debts；`build_candidate_packet` 只注入候选所需子集。章节原子提升成功后调用 `MemoryStore.record_promoted_chapter` 和 `update_lessons`；失败只记录警告，不回滚正式章节，下次 `preflight` 重建失效索引。

同时修改 `pipeline._chapter_meta`，在现有可读元数据末尾增加 `## Memory Record` 的 fenced JSON，字段与索引项一致但不写 `source_sha256`。该 Markdown 文件仍与正文、状态一起原子提升，因此 `rebuild_index` 可以在工作区丢失后从正式 meta 和正文恢复 ARCHIVAL。

- [ ] **Step 6: 运行记忆和上下文回归测试**

Run: `python3 -m unittest tests.test_memory tests.test_context_builder tests.test_pipeline -v`

Expected: PASS.

- [ ] **Step 7: 提交长期记忆**

```bash
git add scripts/prequel/memory.py scripts/prequel/context_builder.py scripts/prequel/pipeline.py novel/knowledge/memory_index.json novel/knowledge/quality_lessons.json novel/knowledge/creative_debts.json tests/test_memory.py
git commit -m "feat: add bounded long-book memory"
```

---

### Task 8: 十章健康检查与二十章阶段复审

**Files:**
- Create: `scripts/prequel/audits.py`
- Create: `schemas/audit.schema.json`
- Create: `agents/arc_reviewer.md`
- Modify: `scripts/prequel/pipeline.py`
- Modify: `scripts/orchestrator.py`
- Create: `tests/test_audits.py`
- Modify: `tests/test_provider.py`

**Interfaces:**
- Consumes: 正式章节索引、质量经验、创作债务和 `arc_reviewer` 模型路由。
- Produces: `due_audits`、`AuditRunner.run_health`、`run_arc`、`update_debts`、`audit --arc`。

- [ ] **Step 1: 写触发、债务和历史不改写失败测试**

```python
# tests/test_audits.py
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.prequel.audits import AuditRunner, due_audits
from scripts.prequel.errors import ArtifactValidationError
from tests.evolution_fixtures import RecordingRouter


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "novel/chapters/vol_01").mkdir(parents=True)
        (self.root / "novel/knowledge").mkdir(parents=True)
        (self.root / "agents").mkdir(parents=True)
        (self.root / "schemas").mkdir(parents=True)
        for number in range(1, 21):
            (self.root / f"novel/chapters/vol_01/chapter_{number:03d}.txt").write_text(f"第{number}章：记录\n\n张洞检查第{number}道门。", encoding="utf-8")
        (self.root / "novel/knowledge/memory_index.json").write_text('{"schema":"novel-memory-index","entries":[]}', encoding="utf-8")
        (self.root / "novel/knowledge/quality_lessons.json").write_text('{"schema":"novel-quality-lessons","lessons":[]}', encoding="utf-8")
        (self.root / "novel/knowledge/creative_debts.json").write_text('{"schema":"novel-creative-debts","debts":[]}', encoding="utf-8")
        (self.root / "agents/arc_reviewer.md").write_text("只输出审计JSON", encoding="utf-8")
        (self.root / "schemas/audit.schema.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, quote="张洞检查第20道门"):
        return {"audit_type": "health", "through_chapter": 20, "findings": [{"code": "HOOK_REPEAT", "severity": "P2", "chapters": [20], "evidence": [{"chapter": 20, "quote": quote}], "explanation": "结尾同构"}], "debts": [{"id": "DEBT-HOOK-20", "priority": "P2", "scope": "future", "instruction": "改变钩子类型", "acceptance": "未来三章不重复"}], "summary": "需要改变节奏"}

    def test_due_intervals(self):
        self.assertEqual(due_audits(9), {"health": False, "arc": False})
        self.assertEqual(due_audits(10), {"health": True, "arc": False})
        self.assertEqual(due_audits(20), {"health": True, "arc": True})

    def test_health_report_updates_debts_without_changing_formal_chapter(self):
        paths = sorted((self.root / "novel/chapters").glob("vol_*/chapter_*.txt"))
        original = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        router = RecordingRouter({"arc_reviewer": [self.report()]})
        AuditRunner(self.root, router).run_health(20)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}, original)
        debts = json.loads((self.root / "novel/knowledge/creative_debts.json").read_text(encoding="utf-8"))
        self.assertTrue(debts["debts"])

    def test_false_audit_quote_does_not_change_formal_chapters(self):
        chapter = self.root / "novel/chapters/vol_01/chapter_020.txt"
        original = chapter.read_bytes()
        router = RecordingRouter({"arc_reviewer": [self.report("不存在的引文")]})
        with self.assertRaises(ArtifactValidationError):
            AuditRunner(self.root, router).run_health(20)
        self.assertEqual(chapter.read_bytes(), original)
```

- [ ] **Step 2: 运行测试并确认审计模块缺失**

Run: `python3 -m unittest tests.test_audits -v`

Expected: FAIL with missing `scripts.prequel.audits`.

- [ ] **Step 3: 创建严格 audit schema 和审计指令**

`schemas/audit.schema.json` 严格包含 `audit_type`、`through_chapter`、`findings`、`debts`、`summary`。每条 finding 含 `code`、`severity`、`chapters`、`evidence`、`explanation`；每条 debt 含 `id`、`priority`、`scope`、`instruction`、`acceptance`。`agents/arc_reviewer.md` 明确健康检查只看十章分布，阶段复审看二十章弧线，二者都不得提出直接改写已发布章节的动作。

- [ ] **Step 4: 实现审计执行和债务合并**

```python
# scripts/prequel/audits.py
import json
import re
from pathlib import Path

from .errors import ArtifactValidationError
from .state_store import atomic_save_json


def due_audits(last_chapter: int) -> dict[str, bool]:
    return {"health": last_chapter > 0 and last_chapter % 10 == 0, "arc": last_chapter > 0 and last_chapter % 20 == 0}

class AuditRunner:
    def __init__(self, project_root, router):
        self.project_root = Path(project_root)
        self.router = router

    def run_health(self, through_chapter: int) -> Path:
        return self._run("health", through_chapter, 10)

    def run_arc(self, through_chapter: int) -> Path:
        return self._run("arc", through_chapter, 20)

    def _run(self, audit_type: str, through_chapter: int, window: int) -> Path:
        chapter_paths = sorted(
            (self.project_root / "novel/chapters").glob("vol_*/chapter_*.txt"),
            key=lambda path: int(re.search(r"chapter_(\d+)", path.name).group(1)),
        )
        selected = chapter_paths[-window:]
        chapters = {
            int(re.search(r"chapter_(\d+)", path.name).group(1)): path.read_text(encoding="utf-8")
            for path in selected
        }
        memory = json.loads((self.project_root / "novel/knowledge/memory_index.json").read_text(encoding="utf-8"))
        lessons = json.loads((self.project_root / "novel/knowledge/quality_lessons.json").read_text(encoding="utf-8"))
        debts_path = self.project_root / "novel/knowledge/creative_debts.json"
        debts = json.loads(debts_path.read_text(encoding="utf-8"))
        packet = {
            "audit_type": audit_type,
            "through_chapter": through_chapter,
            "chapters": chapters,
            "memory_entries": [item for item in memory["entries"] if item["chapter"] in chapters],
            "active_lessons": [item for item in lessons["lessons"] if item["status"] == "active"],
            "existing_debts": debts["debts"],
        }
        role = (self.project_root / "agents/arc_reviewer.md").read_text(encoding="utf-8")
        prompt = role + "\n\n# 唯一输入工件\n" + json.dumps(packet, ensure_ascii=False, indent=2)
        raw = self.router.provider_for("arc_reviewer").generate(
            prompt, self.project_root / "schemas/audit.schema.json"
        )
        try:
            report = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError(f"audit不是合法JSON: {exc}") from exc
        if report.get("audit_type") != audit_type or report.get("through_chapter") != through_chapter:
            raise ArtifactValidationError("audit 类型或截止章号不匹配")
        for finding in report.get("findings", []):
            for evidence in finding.get("evidence", []):
                chapter = evidence.get("chapter")
                quote = evidence.get("quote")
                if chapter not in chapters or not quote or quote not in chapters[chapter]:
                    raise ArtifactValidationError("audit 引文无法在正式章节定位")
        report_path = self.project_root / "novel/reviews" / audit_type / f"chapter_{through_chapter:03d}.json"
        atomic_save_json(report_path, report)
        merged = {item["id"]: item for item in debts["debts"]}
        for item in report.get("debts", []):
            merged[item["id"]] = item
        atomic_save_json(debts_path, {"schema": "novel-creative-debts", "debts": list(merged.values())})
        return report_path
```

`atomic_save_json` 会创建审计报告父目录；重复运行同一章审计覆盖同名派生报告，按 debt ID 覆盖旧项而不产生重复记录。

- [ ] **Step 5: 接入提升后审计和 `audit --arc`**

原子提升和记忆更新完成后调用 `due_audits`；审计异常捕获为派生警告并记录到工作区 decision，不改变 `PipelineResult.promoted=True`。CLI `audit --arc` 对当前 `last_chapter` 手动运行阶段复审并打印报告路径和新增债务数量。

- [ ] **Step 6: 运行审计、Schema 和管线测试**

Run: `python3 -m unittest tests.test_audits tests.test_provider tests.test_pipeline -v`

Expected: PASS.

- [ ] **Step 7: 提交阶段审计**

```bash
git add scripts/prequel/audits.py schemas/audit.schema.json agents/arc_reviewer.md scripts/prequel/pipeline.py scripts/orchestrator.py tests/test_audits.py tests/test_provider.py
git commit -m "feat: add periodic story audits"
```

---

### Task 9: 文档、全量回归与第 3 章 dry-run 校准

**Files:**
- Modify: `README.md`
- Modify: `init.md`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `scripts/prequel/evolution.py`
- Modify: `scripts/orchestrator.py`
- Modify: `tests/test_repository_hygiene.py`
- Modify: `tests/test_pipeline.py`
- Modify: `config/prequel_config.json`
- Runtime only: `novel/work/chapter_003/attempt_*/`

**Interfaces:**
- Consumes: 全部新命令、配置和工件协议。
- Produces: 用户可执行手册、仓库链接校验、只读基线数据和完整 dry-run 验收记录。

- [ ] **Step 1: 写文档命令与链接回归测试**

```python
# tests/test_repository_hygiene.py
def test_readme_documents_quality_evolution_commands(self):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for command in ("next --resume", "accept --candidate", "audit --arc"):
        self.assertIn(command, text)

def test_engine_manual_links_to_quality_design(self):
    text = (ROOT / "init.md").read_text(encoding="utf-8")
    self.assertIn("docs/superpowers/specs/2026-08-01-quality-evolution-pipeline-design.md", text)

# tests/test_pipeline.py
def test_review_parser_accepts_specialist_calibration(self):
    from scripts.orchestrator import build_parser
    args = build_parser().parse_args(["review", "--last", "2", "--specialists"])
    self.assertTrue(args.specialists)
```

- [ ] **Step 2: 运行文档测试并确认新命令尚未记录**

Run: `python3 -m unittest tests.test_repository_hygiene -v`

Expected: FAIL because README and manual do not yet contain all commands.

- [ ] **Step 3: 更新 README、引擎手册和默认配置说明**

README 的创作管线图改为“规划 → 三候选 → 四专项审查 → 盲选 → 定向修订 → 自动提升/人工确认”，快速开始增加 `next --resume`、`accept --candidate 2`、`audit --arc`。`init.md` 记录阈值、工件树、状态哈希恢复、质量经验生命周期、审计非回写保证和模型路由回退。

同时给 `review` 增加可选 `--specialists`：`context_builder.build_formal_review_packet` 从章号、正式 meta、截至该章的正文、canon registry 和当前状态构造只读校准包；`evolution.review_formal_chapter` 调用四个专项 Reviewer，但将连续性结果标记为 `calibration_only`，不得用它改写状态或阻断历史章节。报告写入 `novel/work/baselines/chapter_NNN.json`，不进入正式章节目录。

- [ ] **Step 4: 运行全部自动测试和预检**

Run: `python3 -m unittest discover -v`

Expected: PASS with no skipped tests.

Run: `python3 scripts/orchestrator.py preflight`

Expected: all checks print `[OK]`, including model stage routes and memory stores.

- [ ] **Step 5: 对第 1、2 章执行只读质量基线**

Run: `python3 scripts/orchestrator.py review --last 2 --specialists`

Expected: 正式章节字节不变；输出静态结果，并将基线专项分数、调用数和耗时记录在本地工作区报告中。若现有 B 级章节低于软阈值，只调整配置中的软分数线，不降低任何 P1 规则。

- [ ] **Step 6: 完成第 3 章质量优先 dry-run**

Run: `python3 scripts/orchestrator.py next --dry-run`

Expected: `novel/state/current.json` 和正式章节不变；工作区包含 3 个候选、12 份初始专项审查、3 张初始选票、0–2 轮修订、`decision.json`、`decision.md` 和完整 `run_manifest.json`。

- [ ] **Step 7: 验证恢复不重复调用**

Run: `python3 scripts/orchestrator.py next --resume --dry-run`

Expected: 所有输入和工件哈希未变时直接复用已完成阶段；命令输出显示 0 次重复候选生成和 0 次重复专项审查。

- [ ] **Step 8: 检查工作树并提交最终文档**

```bash
git status --short
git diff --check
git add README.md init.md scripts/prequel/context_builder.py scripts/prequel/evolution.py scripts/orchestrator.py config/prequel_config.json tests/test_repository_hygiene.py tests/test_pipeline.py
git commit -m "docs: document quality evolution workflow"
```

- [ ] **Step 9: 最终验收**

Run: `python3 -m unittest discover -v && python3 scripts/orchestrator.py status`

Expected: 全部测试通过；`status` 显示正式进度仍为“第2章完成 → 第3章待写”，并显示最近 dry-run 的决策状态而不修改正式状态。
