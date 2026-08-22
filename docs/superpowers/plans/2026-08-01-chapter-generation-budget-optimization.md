# 小说章节生成预算优化 Implementation Plan

**审查修订：** 2026-08-02，纳入集成审查单点、降级提示、焦点组合、预算终态、CLI 参数能力门和旧 `REPLAN` 兼容策略。

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not start any real Codex chapter generation during implementation or automated tests.

**Goal:** 将当前三候选、全量专项审查、多轮盲选和外层重试流程，收敛为每章最多 10 次调用的两候选自适应管线，同时显式路由 GPT-5.6 Sol/Terra/Luna、支持最大并发 2、一次定向修订、差分验证和幂等恢复。

**Architecture:** `WritingPipeline` 继续负责正式状态和原子晋级；`ModelCallExecutor` 成为章节生成期间唯一允许启动模型的入口；`CallBudget` 和 `RunManifest` 在调用前原子预留、在调用后结算并记录实际模型；`QualityEvolutionEngine` 使用两候选并发生成、并发集成初筛和最多两个条件专项审查，再由确定性决策器决定早停、一次盲选、一次修订或等待人工。十章/二十章审计从章节事务中拆出，避免隐藏的第 11 次调用。

**Tech Stack:** Python 3.10+ 标准库、`unittest`、`concurrent.futures.ThreadPoolExecutor`、JSON Schema、Codex CLI、Markdown/JSON 本地工件。

**Approved design:** `docs/superpowers/specs/2026-08-01-chapter-generation-budget-optimization-design.md`

本计划取代 `docs/superpowers/plans/2026-08-01-quality-evolution-pipeline.md` 中与候选数、评审次数、盲选次数、修订轮数、重试、模型路由和自动审计有关的冲突步骤；旧计划其余已实现的记忆、审计和原子晋级内容继续保留。

---

## Global Constraints

- 当前基线：`python3 -m unittest discover -v` 共 58 项测试通过；实施中每个任务后都必须保持相关测试通过。
- 当前工作树已有用户修改和未跟踪文件。只修改本计划列出的相关文件，不覆盖或回滚无关改动。
- `.git` 当前为只读。计划中的提交步骤仅在 `.git` 可写环境执行；否则记录建议提交信息，不把无法提交视为实现失败。
- 自动测试不得执行真实 `codex exec`。Provider 测试使用 Python 子进程或假 Provider；管线测试使用脚本化输出。
- 单章预算包括 Planner 在内的所有章节模型调用。Provider 进程一旦启动，失败、超时、无效输出和重试均消耗一个槽位。
- 不实现无条件模型重试，不自动重新规划，不在章节晋级后自动运行模型审计。
- 最大并发固定为 2；并发只用于候选生成、集成初筛和相互独立的专项审查。
- 保持正式 `current.json` 和正式章节的原子晋级边界不变。
- 旧工作区应保持可读；新流程不需要继续生成第三候选或旧式三张选票。

---

### Task 0: Codex CLI 能力门（最先执行）

**Files:**
- Create: `scripts/prequel/cli_capabilities.py`
- Create: `tests/test_cli_capabilities.py`
- Modify: `scripts/orchestrator.py`

**Interfaces:**
- `bundled_model_catalog(codex_command) -> dict`
- `validate_requested_routes(catalog, routes) -> list[str]`
- `build_exec_argv(base_command, model, reasoning_effort) -> list[str]`
- 所有检查默认不发起模型请求。

- [ ] **Step 1: 写模型目录与 argv 失败测试**

```python
def test_approved_model_effort_pairs_exist_in_catalog(self):
    catalog = bundled_catalog_fixture()
    errors = validate_requested_routes(catalog, {
        "writer": ("gpt-5.6-sol", "medium"),
        "planner": ("gpt-5.6-terra", "medium"),
        "specialist": ("gpt-5.6-terra", "high"),
        "verifier": ("gpt-5.6-luna", "high"),
    })
    self.assertEqual(errors, [])

def test_missing_effort_fails_before_provider_start(self):
    errors = validate_requested_routes(
        bundled_catalog_fixture(luna_efforts=["low", "medium"]),
        {"verifier": ("gpt-5.6-luna", "high")},
    )
    self.assertIn("gpt-5.6-luna/high", errors[0])

def test_exec_argv_preserves_toml_value_as_one_argument(self):
    argv = build_exec_argv(["codex", "exec"], "gpt-5.6-terra", "medium")
    self.assertEqual(argv[-4:], [
        "--model", "gpt-5.6-terra",
        "--config", 'model_reasoning_effort="medium"',
    ])
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python3 -m unittest tests.test_cli_capabilities -v`

Expected: FAIL，因为能力门尚不存在。

- [ ] **Step 3: 实现无模型调用能力检查**

`bundled_model_catalog()` 使用参数数组执行 `codex debug models --bundled`，解析 `models[].slug`、`default_reasoning_level` 和 `supported_reasoning_levels[].effort`。禁止 `shell=True`。目录命令失败、JSON 无效或目标组合缺失都必须阻止后续模型路由初始化。

本机 2026-08-02 的无模型检查基线为 Codex CLI `0.146.0`，目录包含 `gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-5.6-luna`；本计划使用的 medium/high 组合均存在。实现时仍须重新检查，不能把该观察硬编码成永不过期的事实。

- [ ] **Step 4: 验证 CLI 参数解析但不发请求**

Run:

```bash
codex exec --strict-config --model gpt-5.6-terra --config 'model_reasoning_effort="medium"' --help
codex debug models --bundled
```

Expected: 两条命令退出成功。第一条只证明 CLI 接受参数形式，第二条证明内置目录支持目标组合；两者都不能宣称已经完成真实端到端调用。

- [ ] **Step 5: 将能力门接入 `preflight`**

`python3 scripts/orchestrator.py preflight` 输出 CLI 版本、每个阶段的模型/强度和能力检查结果。任何错误都在启动 Provider 之前失败。

- [ ] **Step 6: 不执行 live canary**

真实 canary 必须由用户在实施代码审查通过后单独批准。建议只执行一次最小结构化请求，并把实际模型/强度写入独立 canary manifest；它不属于自动测试，也不能悄悄计入十次章节试运行。

- [ ] **Step 7: 运行测试**

Run: `python3 -m unittest tests.test_cli_capabilities -v`

Expected: PASS，无真实模型调用。

- [ ] **Step 8: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/cli_capabilities.py tests/test_cli_capabilities.py scripts/orchestrator.py
git commit -m "test: validate codex model and reasoning capabilities"
```

---

### Task 1: 显式模型与思考强度路由

**Files:**
- Modify: `scripts/prequel/provider.py`
- Modify: `scripts/prequel/model_router.py`
- Modify: `config/prequel_config.json`
- Modify: `tests/test_provider.py`
- Modify: `tests/test_model_router.py`

**Interfaces:**
- `CodexCliProvider.model: str | None`
- `CodexCliProvider.reasoning_effort: str | None`
- `StageModelRouter.settings_for(stage) -> ResolvedModelSettings`
- `provider_from_spec()` 必须拒绝重复模型参数、未知模型和禁止的默认强度。

- [ ] **Step 1: 写失败测试，要求命令显式包含模型和推理强度**

```python
# tests/test_provider.py
def test_explicit_model_and_effort_are_added_to_codex_command(self):
    provider = provider_from_spec(
        {
            "type": "codex_cli",
            "command": [sys.executable, "-c", "import sys; print(sys.argv[1:])"],
            "model": "gpt-5.6-terra",
            "reasoning_effort": "medium",
        },
        Path.cwd(),
    )
    self.assertEqual(provider.model, "gpt-5.6-terra")
    self.assertEqual(provider.reasoning_effort, "medium")
    self.assertIn("--model", provider.command)
    self.assertIn("model_reasoning_effort=\"medium\"", provider.command)

def test_ultra_is_rejected_for_budgeted_pipeline(self):
    with self.assertRaises(ProviderError):
        provider_from_spec(
            {
                "type": "codex_cli",
                "command": ["codex", "exec"],
                "model": "gpt-5.6-sol",
                "reasoning_effort": "ultra",
            },
            Path.cwd(),
        )
```

```python
# tests/test_model_router.py
def test_resolved_route_exposes_model_effort_and_profile(self):
    config = {
        "provider": {"type": "codex_cli", "command": ["codex", "exec"]},
        "model_profiles": {
            "terra_medium": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"}
        },
        "stage_routes": {"planner": "terra_medium"},
    }
    route = StageModelRouter.from_config(config, Path.cwd()).settings_for("planner")
    self.assertEqual((route.profile, route.model, route.reasoning_effort),
                     ("terra_medium", "gpt-5.6-terra", "medium"))
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_provider tests.test_model_router -v`

Expected: FAIL，因为 Provider 尚不接受 `model`/`reasoning_effort`，Router 尚无 `settings_for()`。

- [ ] **Step 3: 实现受控模型参数和解析结果**

在 `provider.py` 中增加允许列表和显式命令构造：

```python
CODEX_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}

model = spec.get("model")
effort = spec.get("reasoning_effort")
if model not in CODEX_MODELS:
    raise ProviderError(f"未知或未显式配置的Codex模型: {model}")
if effort not in REASONING_EFFORTS:
    raise ProviderError(f"不允许的思考强度: {effort}")
if any(arg in {"-m", "--model"} for arg in command):
    raise ProviderError("provider.command 不得内嵌模型参数")
if any("model_reasoning_effort" in arg for arg in command):
    raise ProviderError("provider.command 不得内嵌思考强度")
command = [*command, "--model", model, "--config", f'model_reasoning_effort="{effort}"']
```

在 `model_router.py` 新增不可变解析对象：

```python
@dataclass(frozen=True)
class ResolvedModelSettings:
    profile: str
    model: str
    reasoning_effort: str

def settings_for(self, stage: str) -> ResolvedModelSettings:
    profile = self.profile_for(stage)
    provider = self.provider_for(stage)
    if not isinstance(provider, CodexCliProvider) or not provider.model or not provider.reasoning_effort:
        raise ProviderError(f"阶段 {stage} 没有显式模型设置")
    return ResolvedModelSettings(profile, provider.model, provider.reasoning_effort)
```

`StageModelRouter.single()` 只用于测试/兼容注入 Provider；它从 Provider 的 `model`/`reasoning_effort` 属性读取设置，测试替身没有属性时使用明确标记的 `injected-test-provider`/`none`，且该标记不得出现在真实 `from_config()` 路径。真实路径必须显式配置。

- [ ] **Step 4: 写入批准的模型档案和阶段路由**

基础 `provider` 同样增加 `"model": "gpt-5.6-terra"` 和 `"reasoning_effort": "medium"`，保证兼容入口 `provider_from_config()` 也不会依赖隐式默认值。各 profile 在合并后覆盖基础设置。

```json
"model_profiles": {
  "default": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
  "terra_medium": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
  "terra_high": {"model": "gpt-5.6-terra", "reasoning_effort": "high"},
  "sol_medium": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
  "sol_high": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
  "luna_high": {"model": "gpt-5.6-luna", "reasoning_effort": "high"}
},
"stage_routes": {
  "planner": "terra_medium",
  "candidate_writer": "sol_medium",
  "integrated_reviewer": "terra_medium",
  "continuity_reviewer": "terra_high",
  "character_reviewer": "terra_high",
  "craft_reviewer": "terra_high",
  "anti_slop_reviewer": "terra_high",
  "selector": "sol_medium",
  "reviser": "sol_high",
  "verifier": "luna_high",
  "verifier_complex": "terra_high",
  "arc_reviewer": "terra_high"
}
```

- [ ] **Step 5: 运行测试**

Run: `python3 -m unittest tests.test_provider tests.test_model_router -v`

Expected: PASS；测试过程不得启动真实 Codex。

- [ ] **Step 6: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/provider.py scripts/prequel/model_router.py config/prequel_config.json tests/test_provider.py tests/test_model_router.py
git commit -m "feat: pin chapter pipeline models and reasoning"
```

---

### Task 2: 持久化调用预算与线程安全运行清单

**Files:**
- Create: `scripts/prequel/call_budget.py`
- Modify: `scripts/prequel/run_manifest.py`
- Create: `tests/test_call_budget.py`
- Modify: `tests/test_run_manifest.py`

**Interfaces:**
- `CallBudget.reserve(stage, settings, reason_code) -> CallReservation`
- `CallBudget.reserve_many(requests) -> list[CallReservation]`，全有或全无
- `CallBudget.complete(reservation, duration_ms, usage=None)`
- `CallBudget.fail(reservation, duration_ms, error)`
- `CallBudget.cancel_before_provider(reservation)`，仅允许 `RESERVED` 状态且不计调用
- `CallBudget.remaining -> int`
- `RunManifest` 所有读改写操作由同一个 `threading.RLock` 保护。

- [ ] **Step 1: 写预算硬上限和并发失败测试**

```python
# tests/test_call_budget.py
def test_eleventh_reservation_is_rejected(self):
    manifest = make_manifest(limit=10)
    budget = CallBudget(manifest)
    reservations = [budget.reserve("stage", SETTINGS, "TEST") for _ in range(10)]
    with self.assertRaises(CallBudgetExceeded):
        budget.reserve("stage", SETTINGS, "TEST_11")
    self.assertEqual(budget.remaining, 0)

def test_two_threads_cannot_oversubscribe_last_slot(self):
    manifest = make_manifest(limit=1)
    budget = CallBudget(manifest)
    barrier = threading.Barrier(2)
    outcomes = []
    def worker():
        barrier.wait()
        try:
            outcomes.append(budget.reserve("candidate", SETTINGS, "RACE").call_id)
        except CallBudgetExceeded:
            outcomes.append("blocked")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: worker(), range(2)))
    self.assertEqual(len([x for x in outcomes if x != "blocked"]), 1)

def test_stale_active_reservation_becomes_failed_on_resume(self):
    manifest = make_manifest(limit=10)
    reservation = CallBudget(manifest).reserve("planner", SETTINGS, "PLAN")
    reloaded = RunManifest.load(manifest.workspace)
    CallBudget(reloaded).recover_interrupted()
    call = reloaded.data["budget"]["calls"][reservation.call_id]
    self.assertEqual(call["status"], "FAILED")
    self.assertEqual(call["error_code"], "INTERRUPTED")

def test_reserve_many_is_all_or_nothing(self):
    manifest = make_manifest(limit=1)
    budget = CallBudget(manifest)
    with self.assertRaises(CallBudgetExceeded):
        budget.reserve_many([
            ("reviser", SOL_HIGH, "REVISE"),
            ("verifier", LUNA_HIGH, "VERIFY_REVISION"),
        ])
    self.assertEqual(budget.remaining, 1)
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run: `python3 -m unittest tests.test_call_budget tests.test_run_manifest -v`

Expected: FAIL with `ModuleNotFoundError` 或缺少预算字段。

- [ ] **Step 3: 扩展清单初始结构**

`RunManifest.create()` 增加：

```python
"budget": {
    "limit": call_limit,
    "next_call_id": 1,
    "active": [],
    "calls": {},
    "spent": 0,
    "remaining": call_limit,
},
"mode": mode,
"context_metrics": {},
"started_at": utc_now(),
"finished_at": None,
```

新增合法状态 `BUDGET_EXHAUSTED`；删除新流程对 `REPLAN` 的依赖，但 `load()` 继续容忍旧清单中的 `REPLAN`，保证旧工作区可读。

为 `RunManifest` 增加：

```python
_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

def mutate(self, callback: Callable[[dict[str, Any]], T]) -> T:
    with self._lock:
        result = callback(self.data)
        self._save_unlocked()
        return result
```

- [ ] **Step 4: 实现 CallBudget**

预算记录的单次调用字段至少包含：`call_id`、`stage`、`reason_code`、`profile`、`model`、`reasoning_effort`、`reserved_at`、`started_at`、`finished_at`、`duration_ms`、`status`、`error_code`、`error_summary` 和可空 `usage`。

活动预留在完成或失败时从 `active` 删除并更新同一条 call record，不得重复计数。恢复时所有遗留 `RESERVED`/`RUNNING` 调用转为 `FAILED/INTERRUPTED`，仍然占用预算。

`reserve_many()` 必须在一次 `manifest.mutate()` 中检查容量并写入全部预留；容量不足时不能写入任何记录。该接口专门用于 Reviser＋Verifier 成对预留。

`spent` 和 `remaining` 是在同一把锁内由 call records 与 active reservations 重新计算的派生缓存字段；每次预算写入都必须重算，不能由调用者直接递增。运行进入终态时填写 `finished_at`。

- [ ] **Step 5: 运行预算与恢复测试**

Run: `python3 -m unittest tests.test_call_budget tests.test_run_manifest -v`

Expected: PASS。

- [ ] **Step 6: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/call_budget.py scripts/prequel/run_manifest.py tests/test_call_budget.py tests/test_run_manifest.py
git commit -m "feat: add persistent atomic model call budget"
```

---

### Task 3: 建立唯一模型调用入口

**Files:**
- Create: `scripts/prequel/model_calls.py`
- Modify: `scripts/prequel/errors.py`
- Create: `tests/test_model_calls.py`

**Interfaces:**
- `ModelCallExecutor.call(stage, prompt, output_schema, reason_code) -> str`
- `ModelCallExecutor.reserve_many([(stage, reason_code), ...]) -> list[CallReservation]`
- `ModelCallExecutor.call_reserved(reservation, prompt, output_schema) -> str`
- 所有章节生成阶段必须经该接口调用 Provider。

- [ ] **Step 1: 写成功、失败和预算耗尽测试**

```python
class RecordingProvider:
    model = "gpt-5.6-terra"
    reasoning_effort = "medium"
    def __init__(self, output=None, error=None):
        self.output, self.error, self.calls = output, error, 0
    def generate(self, prompt, output_schema=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.output

def test_success_is_recorded_with_resolved_route(self):
    executor, manifest, provider = make_executor(output="{}")
    self.assertEqual(executor.call("planner", "p", None, "PLAN"), "{}")
    record = only_call(manifest)
    self.assertEqual(record["status"], "COMPLETED")
    self.assertEqual(record["model"], "gpt-5.6-terra")

def test_provider_failure_still_spends_slot(self):
    executor, manifest, provider = make_executor(error=ProviderError("boom"))
    with self.assertRaises(ProviderError):
        executor.call("planner", "p", None, "PLAN")
    self.assertEqual(manifest.data["budget"]["remaining"], 9)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_model_calls -v`

Expected: FAIL，因为 `ModelCallExecutor` 尚不存在。

- [ ] **Step 3: 实现调用包装器**

```python
class ModelCallExecutor:
    def __init__(self, router, manifest):
        self.router = router
        self.budget = CallBudget(manifest)

    def call(self, stage, prompt, output_schema, reason_code):
        settings = self.router.settings_for(stage)
        reservation = self.budget.reserve(stage, settings, reason_code)
        return self.call_reserved(reservation, prompt, output_schema)

    def call_reserved(self, reservation, prompt, output_schema):
        started = time.monotonic()
        self.budget.mark_running(reservation)
        try:
            output = self.router.provider_for(reservation.stage).generate(prompt, output_schema)
        except Exception as exc:
            self.budget.fail(reservation, elapsed_ms(started), exc)
            raise
        self.budget.complete(reservation, elapsed_ms(started), usage=None)
        return output
```

`reserve_many()` 先解析每个 stage 的实际设置，再委托 `CallBudget.reserve_many()`；`call_reserved()` 不得再次预留。未使用的成对预留如果因明确的本地验证失败而终止，可由同一进程显式 `cancel_before_provider()` 并释放槽位；恢复时遗留的预留无法证明未启动，仍按 `INTERRUPTED` 保守计费。

错误摘要最多保留 1000 字符，不能保存环境变量、完整命令行认证信息或模型内部推理。未知异常结算后原样重新抛出。

- [ ] **Step 4: 运行测试**

Run: `python3 -m unittest tests.test_model_calls -v`

Expected: PASS。

- [ ] **Step 5: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/model_calls.py scripts/prequel/errors.py tests/test_model_calls.py
git commit -m "feat: meter every chapter model invocation"
```

---

### Task 4: 集成初筛、候选分类和确定性决策

**Files:**
- Modify: `scripts/prequel/evaluation.py`
- Create: `schemas/integrated_review.schema.json`
- Create: `schemas/revision_verification.schema.json`
- Modify: `schemas/ballot.schema.json`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- `validate_integrated_review(review, draft, chapter) -> list[Issue]`
- `scorecard_from_integrated(review, weights) -> dict`
- `merge_specialist_review(card, review) -> dict`
- `classify_candidate(card, floors, near_miss) -> str`
- `selection_policy(candidates, score_gap) -> SelectionAction`
- `promotion_decision(..., selection_confident, verification_passed) -> dict`

- [ ] **Step 1: 写四类候选和选择规则测试**

```python
def test_candidate_classes_are_deterministic(self):
    self.assertEqual(classify_candidate(card(hard=True)), "HARD_FAIL")
    self.assertEqual(classify_candidate(card(scores=(90, 80, 80, 84))), "ELIGIBLE")
    self.assertEqual(classify_candidate(card(scores=(84, 80, 84, 84), weighted=83)), "NEAR_MISS")
    self.assertEqual(classify_candidate(card(scores=(70, 70, 70, 70), weighted=70)), "LOW_SCORE")

def test_large_score_gap_skips_selector(self):
    action = selection_policy([evaluated("a", 90), evaluated("b", 84)], score_gap=4)
    self.assertEqual((action.kind, action.selected_id), ("DIRECT_SELECT", "a"))

def test_close_eligible_scores_request_one_selector(self):
    action = selection_policy([evaluated("a", 87), evaluated("b", 85)], score_gap=4)
    self.assertEqual(action.kind, "SELECTOR")

def test_single_eligible_never_replans(self):
    action = selection_policy([eligible_draft("a"), low_draft("b")], score_gap=4)
    self.assertEqual(action.selected_id, "a")
    self.assertNotEqual(action.kind, "REPLAN")

def test_single_eligible_cannot_auto_promote_without_continuity_guard(self):
    outcome = promotion_decision(
        auto_threshold_card(),
        selection_confident=True,
        selection_mode="SINGLE_ELIGIBLE",
        continuity_guard_passed=False,
        verification_passed=True,
    )
    self.assertEqual(outcome["status"], "WAITING_USER")
```

- [ ] **Step 2: 写集成审查证据和置信度测试**

集成输出必须包含四维整数分、四维 0–1 置信度、逐字正文证据、按维度标注的硬失败/修订项、`specialist_requests`，以及使用规划/事实稳定 ID 表达的 `fact_findings`。两稿对同一稳定 ID 给出互斥结论时，确定性触发连续性复核。虚假引文、缺失维度、未知事实 ID 或无效置信度均产生 P1 校验问题。

- [ ] **Step 3: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_evaluation -v`

Expected: FAIL，因为新契约和分类函数尚不存在。

- [ ] **Step 4: 实现 Schema 与纯函数决策**

`classify_candidate()` 的顺序必须是：硬失败 → 全维达标 → near-miss → low-score。Near-miss 条件固定为加权分 ≥82，且每个维度最大缺口 ≤5。

`selection_policy()`：

```text
2 ELIGIBLE, gap > 4  -> DIRECT_SELECT
2 ELIGIBLE, gap <= 4 -> SELECTOR
1 ELIGIBLE           -> DIRECT_SELECT_LOW_CONFIDENCE
0 ELIGIBLE, NEAR_MISS exists -> REVISE
otherwise            -> WAITING_USER
```

专项结果只替换它负责维度的分数、置信度、硬失败和修订项，然后重新计算加权分；不能用 craft 复核覆盖 continuity 结论。

- [ ] **Step 5: 调整晋级函数**

保持分数阈值 85/90/82/82/82，但取消“必须获得 2 张选票”的旧前提。新前提为：`selection_confident is True`；如果执行过修订，则 `verification_passed is True`。单一合格稿只有在自身置信度、另一稿评估完整性、全部自动阈值及额外连续性专项复核均满足时才可自动晋级，否则等待用户。

- [ ] **Step 6: 运行测试**

Run: `python3 -m unittest tests.test_evaluation -v`

Expected: PASS。

- [ ] **Step 7: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/evaluation.py schemas/integrated_review.schema.json schemas/revision_verification.schema.json schemas/ballot.schema.json tests/test_evaluation.py
git commit -m "feat: add adaptive review and candidate policy"
```

---

### Task 5: 单章上下文包和分阶段视图

**Files:**
- Modify: `scripts/prequel/context_builder.py`
- Create: `agents/reviewer_integrated.md`
- Create: `agents/reviewer_verifier.md`
- Modify: `agents/writer.md`
- Modify: `scripts/prequel/artifacts.py`
- Modify: `tests/test_context_builder.py`
- Modify: `tests/test_run_manifest.py`

**Interfaces:**
- `build_chapter_context_pack(state, plan_context, recent, limits) -> dict`
- `select_candidate_focuses(plan, chapter_number) -> tuple[dict, dict]`
- `build_candidate_packet(context_pack, plan, candidate_index) -> dict`
- `build_integrated_review_packet(context_pack, plan, draft, static) -> dict`
- `build_specialist_packet(...)` 保留完整正文，只裁剪无关设定。
- `build_verification_packet(context_pack, plan, before, after, issues) -> dict`
- `context_metrics(packet) -> dict[str, int]`

- [ ] **Step 1: 写上下文边界测试**

```python
def test_three_focus_library_is_retained_but_each_chapter_selects_two(self):
    self.assertEqual({item["name"] for item in CANDIDATE_FOCUSES}, {
        "causal_tension", "character_pressure", "atmospheric_precision",
    })
    selected = select_candidate_focuses(investigation_plan(), chapter_number=3)
    self.assertEqual(len(selected), 2)
    self.assertEqual({item["name"] for item in selected}, {
        "causal_tension", "atmospheric_precision",
    })

def test_ambiguous_plan_rotates_focus_pair_by_chapter(self):
    pairs = [
        tuple(item["name"] for item in select_candidate_focuses(ambiguous_plan(), n))
        for n in (1, 2, 3)
    ]
    self.assertEqual(len(set(pairs)), 3)

def test_integrated_reviewer_gets_full_draft_but_not_unrelated_history(self):
    packet = build_integrated_review_packet(pack(), plan(), "完整正文", static())
    self.assertEqual(packet["draft"], "完整正文")
    self.assertNotIn("all_chapters", json.dumps(packet, ensure_ascii=False))

def test_core_facts_survive_section_limits(self):
    packet = build_chapter_context_pack(state(), huge_context(), recent(), tiny_limits())
    self.assertIn("era_bans", packet["core"])
    self.assertIn("active_characters", packet["core"])

def test_verifier_receives_diff_and_targeted_issues_not_full_review_history(self):
    packet = build_verification_packet(pack(), plan(), "旧稿", "新稿", issues())
    self.assertIn("diff", packet)
    self.assertNotIn("all_reviews", packet)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_context_builder tests.test_run_manifest -v`

Expected: FAIL，因为上下文包和新工件尚不存在。

- [ ] **Step 3: 保留三个焦点库并按章选择两个**

保留 `causal_tension`、`character_pressure`、`atmospheric_precision` 三个独立焦点。根据规划中的调查/规则、关系/对白、空间/氛围标签选择对应组合；标签不足时按章号稳定轮换三种组合。选择结果和原因写入上下文工件，不调用模型。两候选共享相同 CORE，不能读取另一候选。

- [ ] **Step 4: 实现优先级裁剪和指标**

配置增加：

```json
"context_limits": {
  "core_chars": 18000,
  "retrieved_chars": 12000,
  "recent_chars": 10000,
  "review_support_chars": 8000
}
```

这里的数字是首轮可配置基线，不是最终 token 承诺。CORE 内的本章规划、人物状态、事实锚点、时代禁令和不可破坏项禁止裁剪；其余按稳定 ID、相关度、章号和原始顺序确定性裁剪。将各分区字符数写入 `manifest.context_metrics`。

- [ ] **Step 5: 添加集成审查和验证 Agent 契约**

`reviewer_integrated.md` 明确一次输出四维评分、置信度、证据和专项请求；`reviewer_verifier.md` 只验证缺陷修复和回归，不重新创作正文。两者都只输出 Schema JSON。

- [ ] **Step 6: 扩展安全工件白名单**

允许：

```text
candidates/candidate_XX/integrated_review.json
candidates/candidate_XX/reviews/<dimension>.json
comparisons/initial/ballot_01.json
revisions/round_01/brief.json
revisions/round_01/draft.txt
revisions/round_01/static_review.json
revisions/round_01/verification.json
context_metrics.json
```

仍拒绝父目录穿越、未知叶子和正式章节路径。

- [ ] **Step 7: 运行测试**

Run: `python3 -m unittest tests.test_context_builder tests.test_run_manifest -v`

Expected: PASS。

- [ ] **Step 8: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/context_builder.py scripts/prequel/artifacts.py agents/reviewer_integrated.md agents/reviewer_verifier.md agents/writer.md tests/test_context_builder.py tests/test_run_manifest.py
git commit -m "feat: build bounded stage-specific chapter context"
```

---

### Task 6: 两候选并发生成与并发集成初筛

**Files:**
- Major modify: `scripts/prequel/evolution.py`
- Modify: `tests/test_evolution.py`
- Create: `tests/evolution_fixtures.py`

**Interfaces:**
- `QualityEvolutionEngine(..., caller: ModelCallExecutor, mode="balanced", max_workers=2)`
- 平衡模式基础路径固定：1 个既有规划 + 2 Writer + 2 Integrated Reviewer。
- Engine 本身不直接访问 `router.provider_for(...).generate()`。

- [ ] **Step 1: 建立可线程安全的脚本化 Provider 测试夹具**

`tests/evolution_fixtures.py` 使用按阶段队列和 `threading.Lock`，不要依赖两个并发任务的完成顺序。为每次调用记录 stage、开始/结束时间和返回工件。

- [ ] **Step 2: 写五调用早停测试**

```python
def test_balanced_happy_path_uses_five_total_calls_and_two_workers(self):
    result, manifest, recorder = run_engine(
        draft_outputs=[draft_a(), draft_b()],
        triage_outputs=[eligible_review(91), eligible_review(85)],
    )
    self.assertEqual(manifest.data["budget"]["spent"], 5)  # 包含 Planner
    self.assertEqual(result.selected_id, "candidate_01")
    self.assertNotIn("selector", recorder.stages)
    self.assertEqual(recorder.max_concurrent("candidate_writer"), 2)

def test_failed_candidate_is_not_retried_automatically(self):
    result, manifest, recorder = run_engine(draft_outputs=[ProviderError("x"), draft_b()])
    self.assertEqual(recorder.count("candidate_writer"), 2)
    self.assertNotEqual(result.status, "REPLAN")
    self.assertTrue(result.decision["degraded"])
    self.assertEqual(result.decision["failed_candidate"], "candidate_01")
    self.assertIn("best_available_artifact", result.decision)
    self.assertIn("automatic_retry_skipped_reason", result.decision)
    self.assertTrue(result.decision["recommended_actions"])
```

Planner 可以在 fixture 中预先通过 `ModelCallExecutor` 写入一次，或由测试初始化一个已花费的规划调用；断言必须覆盖章节总计，不只覆盖 Engine 内部。

- [ ] **Step 3: 运行测试并确认旧三候选流程失败**

Run: `python3 -m unittest tests.test_evolution -v`

Expected: FAIL，因为旧 Engine 强制 `candidate_count == 3`，串行执行并进行四次逐稿审查。

- [ ] **Step 4: 重写生成和初筛路径**

使用：

```python
with ThreadPoolExecutor(max_workers=2) as pool:
    generated = list(pool.map(generate_candidate, range(candidate_count)))
with ThreadPoolExecutor(max_workers=2) as pool:
    evaluated = list(pool.map(integrated_triage, valid_generated))
```

要求：

- `candidate_count` 平衡模式固定为 2；快速模式固定为 1；
- 每个任务先通过 `ModelCallExecutor.call()` 预留预算；
- 不循环 `candidate_retries`/`review_retries`；
- 静态 P1 失败使该候选成为 `HARD_FAIL`，不补写；
- 集成初筛输出验证失败使该候选成为不可自动晋级的失败结果，但不得抹去正文；
- 任一候选失败时，在 `decision.json`/`decision.md` 写入结构化降级原因、调用消耗、当前最佳工件、安全操作和需要新预算的操作；
- 每个候选阶段独立写入工件和哈希，完成顺序不影响 ID；
- `ThreadPoolExecutor(max_workers=2)` 不得从配置提升到更高值。

- [ ] **Step 5: 实现快速模式基础路径**

`mode="fast"` 只生成候选 1 和一次集成初筛，最多三次总调用（含 Planner）。不进入专项审查、Selector 或修订；未达到自动晋级条件时返回 `WAITING_USER`。

- [ ] **Step 6: 运行测试**

Run: `python3 -m unittest tests.test_evolution -v`

Expected: PASS；断言平衡早停为 5 次、快速模式为 3 次、没有自动重试。

- [ ] **Step 7: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/evolution.py tests/test_evolution.py tests/evolution_fixtures.py
git commit -m "refactor: generate and triage two candidates concurrently"
```

---

### Task 7: 条件专项审查和单次盲选

**Files:**
- Modify: `scripts/prequel/evolution.py`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `tests/test_evolution.py`

**Interfaces:**
- `plan_specialist_calls(evaluated, remaining, max_calls=2, shadow_dimension=None) -> list[SpecialistRequest]`
- `run_selector(left, right) -> str | None` 每次运行最多一次。

- [ ] **Step 1: 写专项触发优先级测试**

```python
def test_only_decision_changing_specialists_run_and_cap_at_two(self):
    requests = plan_specialist_calls(
        candidates_with_requests(
            continuity_hard_uncertain=True,
            character_near_floor=True,
            craft_near_floor=True,
        ),
        remaining=5,
        max_calls=2,
    )
    self.assertEqual(len(requests), 2)
    self.assertEqual(requests[0].dimension, "continuity")

def test_no_specialist_runs_for_high_confidence_clear_scores(self):
    self.assertEqual(plan_specialist_calls(clear_candidates(), 5, 2), [])

def test_deterministic_fact_conflict_triggers_review_without_model_request(self):
    candidates = candidates_with_requests()
    candidates[0].integrated_review["specialist_requests"] = []
    candidates[1].integrated_review["specialist_requests"] = []
    candidates[0].integrated_review["fact_findings"] = {"door_state": "open"}
    candidates[1].integrated_review["fact_findings"] = {"door_state": "sealed"}
    requests = plan_specialist_calls(candidates, remaining=5, max_calls=2)
    self.assertEqual(requests[0].dimension, "continuity")
    self.assertEqual(requests[0].reason_code, "CROSS_CANDIDATE_FACT_CONFLICT")

def test_single_eligible_requires_continuity_specialist_for_auto_promotion(self):
    requests = plan_specialist_calls(single_eligible_candidates(), 5, 2)
    self.assertIn(
        ("candidate_01", "continuity", "SINGLE_ELIGIBLE_AUTO_PROMOTE_GUARD"),
        [(r.candidate_id, r.dimension, r.reason_code) for r in requests],
    )

def test_shadow_review_uses_spare_specialist_slot_but_never_displaces_hard_risk(self):
    requests = plan_specialist_calls(
        one_hard_risk_candidate(), remaining=5, max_calls=2, shadow_dimension="craft"
    )
    self.assertEqual(requests[0].reason_code, "HARD_RISK")
    self.assertEqual(requests[1].reason_code, "BENCHMARK_SHADOW_REVIEW")
```

- [ ] **Step 2: 写单次 Selector 测试**

```python
def test_close_scores_use_exactly_one_blind_selector_call(self):
    result, manifest, recorder = run_engine(
        triage_outputs=[eligible_review(87), eligible_review(85)],
        selector_output=ballot("A"),
    )
    self.assertEqual(recorder.count("selector"), 1)
    self.assertEqual(result.selected_id, "candidate_01")

def test_gap_over_four_selects_by_score_without_selector(self):
    result, _, recorder = run_engine(
        triage_outputs=[eligible_review(91), eligible_review(85)]
    )
    self.assertEqual(recorder.count("selector"), 0)
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_evolution -v`

Expected: FAIL，因为旧流程总是四维逐稿审查并创建三张选票。

- [ ] **Step 4: 实现最多两个条件专项审查**

排序键固定为：

```text
1. 低置信度连续性/人物/因果硬风险
2. 两稿事实结论矛盾，或单一合格稿试图自动晋级
3. 结果会改变 HARD_FAIL/ELIGIBLE 分类的临界维度
4. 模型明确请求且能改变候选选择的维度
5. 其他靠近资格线的维度
```

触发器必须独立读取确定性门禁、规划风险标签、两稿事实结论、分数距离、置信度和候选完整性，不能只转发 Integrated Reviewer 的 `specialist_requests`。同优先级按候选 ID、维度固定顺序排序，确保恢复和测试可重复。两个互不依赖的专项任务可以并发；结果用 Task 4 的纯函数合并。

- [ ] **Step 5: 将 `_ballots()` 替换为 `_select_once()`**

只构建 A/B 一组匿名输入并写 `comparisons/initial/ballot_01.json`。若 Selector 输出无效，不重试；回退为高分稿但设置 `selection_confident=False`，最终进入人工确认，不能自动晋级。

- [ ] **Step 6: 运行测试**

Run: `python3 -m unittest tests.test_evolution tests.test_evaluation -v`

Expected: PASS；不存在三张票或循环赛工件。

- [ ] **Step 7: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/evolution.py scripts/prequel/context_builder.py tests/test_evolution.py
git commit -m "refactor: make specialist review and selection conditional"
```

---

### Task 8: 一次定向修订和差分验证

**Files:**
- Modify: `scripts/prequel/evolution.py`
- Modify: `scripts/prequel/context_builder.py`
- Modify: `scripts/prequel/evaluation.py`
- Modify: `scripts/prequel/artifacts.py`
- Modify: `tests/test_evolution.py`
- Modify: `tests/test_evaluation.py`

**Interfaces:**
- `build_revision_brief(selected) -> dict`
- `choose_verifier_stage(issues) -> "verifier" | "verifier_complex"`
- `validate_revision_verification(...)`
- 修订最多一次，验证最多一次。

- [ ] **Step 1: 写完整 10 次路径测试**

```python
def test_worst_valid_path_stops_at_ten_calls(self):
    # planner 1 + writers 2 + triage 2 + specialists 2
    # + selector 1 + reviser 1 + verifier 1 = 10
    result, manifest, recorder = run_full_budget_path()
    self.assertEqual(manifest.data["budget"]["spent"], 10)
    self.assertEqual(recorder.count("reviser"), 1)
    self.assertEqual(recorder.count_matching("verifier"), 1)
    self.assertNotIn("revision_02", json.dumps(manifest.data))
```

- [ ] **Step 2: 写动态验证模型和失败保留测试**

```python
def test_continuity_revision_uses_terra_high_verifier(self):
    _, manifest, _ = run_revision(issue_dimension="continuity")
    call = call_for_reason(manifest, "VERIFY_REVISION")
    self.assertEqual((call["model"], call["reasoning_effort"]),
                     ("gpt-5.6-terra", "high"))

def test_failed_verification_keeps_both_drafts_and_waits_for_user(self):
    result, workspace, _ = run_revision(verification_passed=False)
    self.assertEqual(result.status, "WAITING_USER")
    self.assertTrue(workspace.exists("revisions/round_01/draft.txt"))
    self.assertIsNotNone(result.decision["pre_revision_draft_path"])
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_evolution tests.test_evaluation -v`

Expected: FAIL，因为旧流程最多两轮修订，且每轮重新执行四审加三票。

- [ ] **Step 4: 实现单次修订**

修订触发条件：最佳稿为 `NEAR_MISS`，或存在明确、非互斥且可一次修复的 required revisions。修订简报只包含缺陷代码、原文证据、验收条件、必须保留项和禁止改变项。

Reviser 输出后先执行本地 `scan_draft()`；P1 失败直接进入 `WAITING_USER`，不重试。

- [ ] **Step 5: 实现差分验证**

使用 `difflib.unified_diff()` 构建差异；Verifier 同时接收修订稿全文、差异、目标问题和必要事实锚点。若问题涉及 `continuity`、`character` 或因果硬风险，使用 `verifier_complex`；其余使用 `verifier`。

Verification 输出必须说明每个目标缺陷是否解决、是否出现回归、更新后的目标维度分和新稿逐字证据。仅用验证过的目标维度分更新 scorecard；未复核维度保持原分并要求“无回归”。

- [ ] **Step 6: 确保预算不足时不发起半套修订**

修订需要通过 `ModelCallExecutor.reserve_many()` 同时保留两个槽位（Reviser + Verifier），随后分别用 `call_reserved()` 执行。若只剩一个槽位，跳过修订并进入 `WAITING_USER`；不得先花掉 Reviser 后才发现无法验证。

- [ ] **Step 7: 运行测试**

Run: `python3 -m unittest tests.test_evolution tests.test_evaluation tests.test_call_budget -v`

Expected: PASS；最坏路径精确为 10，任何路径不出现第 11 次调用。

- [ ] **Step 8: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/evolution.py scripts/prequel/context_builder.py scripts/prequel/evaluation.py scripts/prequel/artifacts.py tests/test_evolution.py tests/test_evaluation.py
git commit -m "feat: add one-pass revision and differential verification"
```

---

### Task 9: 接入 Pipeline、删除外层重试并拆分自动审计

**Files:**
- Modify: `scripts/prequel/pipeline.py`
- Modify: `scripts/prequel/audits.py`
- Create: `scripts/prequel/audit_manifest.py`
- Modify: `scripts/prequel/run_manifest.py`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_audits.py`

**Interfaces:**
- `WritingPipeline.run_next(dry_run=False, resume=False, mode="balanced")`
- Planner 也必须经同一个 `ModelCallExecutor`。
- 章节晋级只记录 `audits_due`，不调用 `AuditRunner`。

- [ ] **Step 1: 写 Planner 计入 10 次预算测试**

```python
def test_planner_is_first_metered_call(self):
    result = run_pipeline_with_fakes(mode="balanced")
    manifest = load_manifest(result.workspace)
    first = manifest["budget"]["calls"]["call_001"]
    self.assertEqual(first["stage"], "planner")
    self.assertEqual(first["model"], "gpt-5.6-terra")
```

- [ ] **Step 2: 写不重新规划和预算耗尽返回工件测试**

```python
def test_low_scores_stop_in_same_workspace_without_outer_attempt(self):
    result = run_pipeline_with_low_scores()
    self.assertFalse(result.promoted)
    self.assertEqual(result.status, "WAITING_USER")
    self.assertFalse((result.workspace.parent / "attempt_02").exists())

def test_budget_exhaustion_returns_workspace_instead_of_discarding_artifacts(self):
    result = run_pipeline_that_exhausts_budget()
    self.assertEqual(result.status, "BUDGET_EXHAUSTED")
    self.assertTrue((result.workspace / "run_manifest.json").exists())
    decision = read_json(result.workspace / "decision.json")
    self.assertEqual(decision["exhausted_stage"], "verifier")
    self.assertEqual(decision["calls_spent"], 10)
    self.assertTrue(decision["safe_actions"])
    self.assertTrue(decision["new_budget_actions"])
    self.assertIn("不会把上限扩展到第11次", decision["resume_warning"])

def test_legacy_replan_manifest_is_read_only_and_not_resumable(self):
    workspace = workspace_with_legacy_status("REPLAN")
    manifest = RunManifest.load(workspace)
    self.assertEqual(manifest.display_status(), "LEGACY_REPLAN")
    with self.assertRaises(LegacyRunNotResumable):
        resume_budgeted_run(workspace)
```

- [ ] **Step 3: 写晋级不自动审计测试**

```python
@patch("scripts.prequel.pipeline.AuditRunner")
def test_chapter_ten_promotion_marks_due_audit_without_calling_model(self, runner):
    result = promote_chapter_ten_with_fakes()
    runner.assert_not_called()
    decision = read_json(result.workspace / "decision.json")
    self.assertEqual(decision["audits_due"], ["health"])

def test_explicit_audit_has_independent_one_call_manifest(self):
    report = run_health_audit_with_fake_provider(through_chapter=10)
    manifest = read_json(report.with_suffix(".run.json"))
    self.assertEqual(manifest["budget"]["limit"], 1)
    self.assertEqual(manifest["budget"]["spent"], 1)
```

- [ ] **Step 4: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_pipeline tests.test_audits -v`

Expected: FAIL，因为当前 `_run_evolution()` 有外层 attempt 循环，并在晋级后调用 `run_due_audits()`。

- [ ] **Step 5: 重构 `_run_evolution()` 为单工作区事务**

删除 `quality_gates.max_retries`、`_prior_replan_count()` 和 `REPLAN` 分支在新流程中的作用。流程改为：

```text
preflight -> create/load one manifest -> recover interrupted reservations
-> build/reuse context -> metered planner call -> adaptive engine
-> write summary -> promote or return WAITING_USER/BUDGET_EXHAUSTED
```

`PipelineResult` 增加 `status: str` 和可空的 `static_review`/`semantic_review`，使没有最终合格稿时仍能正常返回工作区。兼容旧调用者时给新字段合理默认值或更新全部构造点。

同步更新 `_semantic_from_evolution()`：优先读取集成初筛的四维摘要与证据，再用实际执行过的专项审查覆盖对应维度；不得继续假设 `result.reviews` 一定含四个旧式专项文件。

- [ ] **Step 6: 修正恢复校验**

`can_reuse()` 除输入哈希和输出哈希外，还比较 `route_fingerprint`（profile/model/effort/prompt_version）。配置改变后不得复用模型结果。恢复时不重跑已完成且全部哈希一致的阶段；崩溃中的调用转为已花费失败调用。

旧清单原值 `REPLAN` 保持不变，通过 `display_status()` 派生为 `LEGACY_REPLAN`。新版恢复入口遇到它时必须停止，解释旧调用数不符合新版预算账本并要求用户显式创建新运行，不能自动续跑或分配新预算。

- [ ] **Step 7: 拆分审计**

将 `run_due_audits()` 改为不调用模型的 `mark_due_audits()`，只把 `due_audits()` 结果写入 `decision.json`。保留独立 `orchestrator.py audit` 命令，由用户显式运行；审计自身使用独立运行清单/预算，不能复用章节预算。

`audit_manifest.py` 实现一个与 `CallBudget` 所需最小存储协议兼容的 `AuditRunManifest`，保存到 `novel/reviews/<type>/chapter_NNN.run.json`。每次 health/arc 审计的默认上限为 1，调用同样通过独立 `ModelCallExecutor` 记录模型、强度、耗时和失败状态。审计失败不得修改 `creative_debts.json`。

同时修正 `accept_dry_run()` 中的晋级后自动审计路径。

- [ ] **Step 8: 运行测试**

Run: `python3 -m unittest tests.test_pipeline tests.test_audits tests.test_run_manifest -v`

Expected: PASS。

- [ ] **Step 9: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/pipeline.py scripts/prequel/audits.py scripts/prequel/audit_manifest.py scripts/prequel/run_manifest.py tests/test_pipeline.py tests/test_audits.py
git commit -m "refactor: enforce one budgeted chapter transaction"
```

---

### Task 10: CLI 模式、状态展示和人工接管

**Files:**
- Modify: `scripts/orchestrator.py`
- Modify: `README.md`
- Modify: `init.md`
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_repository_hygiene.py`

- [ ] **Step 1: 写 CLI 参数测试**

```python
def test_next_accepts_balanced_and_fast_modes(self):
    parser = build_parser()
    self.assertEqual(parser.parse_args(["next"]).mode, "balanced")
    self.assertEqual(parser.parse_args(["next", "--mode", "fast"]).mode, "fast")
    self.assertEqual(
        parser.parse_args(["next", "--shadow-review", "continuity"]).shadow_review,
        "continuity",
    )

def test_status_output_includes_budget_and_actual_routes(self):
    output = run_status_on_fixture_with_manifest()
    self.assertIn("调用: 5/10", output)
    self.assertIn("Sol", output)
    self.assertIn("Terra", output)

def test_degraded_candidate_output_explains_why_no_retry_occurred(self):
    output = render_next_result(degraded_result())
    self.assertIn("候选 A：生成失败", output)
    self.assertIn("系统未自动补写", output)
    self.assertIn("当前最佳有效工件", output)

def test_budget_exhausted_output_separates_safe_and_spending_actions(self):
    output = render_next_result(exhausted_result())
    self.assertIn("BUDGET_EXHAUSTED（10/10）", output)
    self.assertIn("无需新增调用", output)
    self.assertIn("会建立新预算", output)
    self.assertIn("--resume", output)
    self.assertIn("不会", output)

def test_legacy_replan_has_explicit_read_only_display(self):
    output = render_status(legacy_replan_manifest())
    self.assertIn("[旧流程] REPLAN", output)
    self.assertIn("只读", output)
    self.assertIn("不支持按新版 --resume 继续", output)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_pipeline tests.test_repository_hygiene -v`

Expected: FAIL，因为 CLI 尚无 `--mode` 和预算摘要。

- [ ] **Step 3: 修改命令行为**

`next` 增加：

```python
next_parser.add_argument(
    "--mode", choices=("balanced", "fast"), default="balanced",
    help="balanced最多10次调用；fast最多3次调用",
)
next_parser.add_argument(
    "--shadow-review",
    choices=("continuity", "character", "craft", "anti_slop"),
    help="仅用于获批的十次试运行；在预算内抽样复核集成初筛",
)
```

`command_next()` 将 mode 传给 Pipeline，并按 `COMPLETE`、`WAITING_USER`、`BUDGET_EXHAUSTED` 输出不同结果。命令返回工作区路径、调用总数、模型构成、总耗时和下一步人工命令。

`--shadow-review` 不是日常默认值，只在用户已经批准的十次试运行中使用。它仍受最多两次专项审查和总计十次调用约束；高风险专项优先级更高，预算不足时记录 `shadow_review_skipped`，不得为了完成抽样突破上限。

降级输出必须说明失败候选、原因、已消耗调用、当前最佳工件和未自动补写原因。`BUDGET_EXHAUSTED` 必须把“无需新增调用的安全操作”和“会建立新预算的额外消耗操作”分组，并明确 `--resume` 不会扩展上限。旧 `REPLAN` 显示为 `[旧流程] REPLAN`、只读且不可按新版恢复；不得伪装为 `WAITING_USER` 或 `BUDGET_EXHAUSTED`。

`accept --candidate` 不再静态限制为 `(1, 2, 3)`；改用正整数解析并在工作区中验证候选存在，以便既支持新两候选，也能读取旧第三候选工作区。

- [ ] **Step 4: 更新文档**

README/init 明确：

- 默认平衡模式与三调用快速模式；
- 每章硬上限 10 和所有失败均计数；
- Sol/Terra/Luna 的实际阶段路由；
- `--resume` 复用规则；
- `WAITING_USER`、`BUDGET_EXHAUSTED` 的人工操作；
- 候选失败降级提示，以及旧 `REPLAN` 的只读兼容显示；
- 到期审计必须显式执行；
- 不得把 `next --dry-run` 当作无额度成本的测试。

- [ ] **Step 5: 运行测试**

Run: `python3 -m unittest tests.test_pipeline tests.test_repository_hygiene -v`

Expected: PASS。

- [ ] **Step 6: 提交（仅 `.git` 可写时）**

```bash
git add scripts/orchestrator.py README.md init.md tests/test_pipeline.py tests/test_repository_hygiene.py
git commit -m "docs: expose budgeted chapter modes and recovery"
```

---

### Task 11: 运行指标汇总与十次试运行工具

**Files:**
- Create: `scripts/prequel/metrics.py`
- Create: `scripts/benchmark_pipeline.py`
- Create: `tests/test_metrics.py`
- Modify: `README.md`

**Interfaces:**
- `chapter_metrics(manifest) -> dict`
- `benchmark_summary(manifests) -> dict`
- 汇总器只读取工件，不启动模型。

- [ ] **Step 1: 写指标统计测试**

```python
def test_chapter_metrics_counts_models_and_wall_time(self):
    metrics = chapter_metrics(manifest_fixture())
    self.assertEqual(metrics["calls_total"], 8)
    self.assertEqual(metrics["calls_by_model"]["gpt-5.6-sol"], 3)
    self.assertIn("wall_time_seconds", metrics)

def test_benchmark_requires_exactly_ten_runs(self):
    with self.assertRaises(ArtifactValidationError):
        benchmark_summary([manifest_fixture()] * 9)

def test_acceptance_flags_match_approved_thresholds(self):
    result = benchmark_summary(ten_passing_manifests())
    self.assertTrue(result["acceptance"]["hard_call_cap"])
    self.assertTrue(result["acceptance"]["average_calls_le_8"])
    self.assertTrue(result["acceptance"]["median_minutes_le_25"])
    self.assertTrue(result["acceptance"]["shadow_reviews_at_least_5"])
    self.assertTrue(result["acceptance"]["shadow_hard_fail_misses_zero"])

def test_legacy_replan_is_excluded_from_new_benchmark(self):
    result = benchmark_summary([legacy_replan_manifest(), *ten_passing_manifests()])
    self.assertEqual(result["runs"], 10)
    self.assertEqual(result["excluded_legacy_runs"], 1)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_metrics -v`

Expected: FAIL，因为指标模块尚不存在。

- [ ] **Step 3: 实现只读汇总**

输出至少包含：

```text
runs, calls_total, calls_mean, calls_max
calls_by_model, sol_calls_mean
duration_p50, duration_max
status_counts, eligible_or_near_miss_count
silent_fallback_count, hard_fail_auto_promote_count
shadow_reviews_completed, shadow_hard_fail_misses
shadow_classification_disagreements, excluded_legacy_runs
acceptance flags
```

`scripts/benchmark_pipeline.py` 接受 10 个 `--manifest PATH`，只验证并汇总；不提供“自动连续运行 10 次”的入口，避免误触真实额度。

- [ ] **Step 4: 运行测试**

Run: `python3 -m unittest tests.test_metrics -v`

Expected: PASS。

- [ ] **Step 5: 文档化试运行步骤但不执行**

记录：选择 5 个代表性任务、每个重复 2 次；每次由用户显式执行；完成后将 10 个 manifest 交给汇总脚本。至少 5 次运行通过 `--shadow-review` 轮换连续性、人物、文学性和反 AI 痕迹；高风险专项占满两个槽位导致抽样跳过时，必须在其他运行补足，不能突破十次上限。影子复核漏掉一次硬失败即停止自动晋级试验；一般性分类差异最多允许 1/5。新旧正文盲评由人或独立审查流程完成，不能用生成者自身的分数替代。旧 `REPLAN` 清单单独报告并排除在新版十次统计之外。

- [ ] **Step 6: 提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel/metrics.py scripts/benchmark_pipeline.py tests/test_metrics.py README.md
git commit -m "feat: summarize ten-run quality and usage benchmark"
```

---

### Task 12: 全量回归、静态核对和安全交付

**Files:**
- Verify all files changed in Tasks 0–11
- Update if needed: `docs/superpowers/specs/2026-08-01-chapter-generation-budget-optimization-design.md`
- Update if needed: `docs/superpowers/plans/2026-08-01-chapter-generation-budget-optimization.md`

- [ ] **Step 1: 运行全量自动测试**

Run: `python3 -m unittest discover -v`

Expected: 全部 PASS；不得出现 `codex exec` 真实调用。

- [ ] **Step 2: 运行配置和旧逻辑静态核对**

```bash
rg -n 'candidate_count|candidate_retries|review_retries|revision_rounds|max_retries|REPLAN|run_due_audits|provider_for\(.+\)\.generate' scripts config tests README.md init.md
```

Expected:

- 新流程 `candidate_count` 为 2，快速模式为 1；
- 不存在新流程固定模型重试和自动重规划；
- 章节 Planner/Writer/Reviewer/Selector/Reviser/Verifier 均经 `ModelCallExecutor`；
- `AuditRunner` 只由显式 audit 命令使用；
- 允许保留旧工作区兼容解析和 legacy pipeline 中必要的旧字段，但必须有注释说明不属于预算化新流程。

- [ ] **Step 3: 运行无模型预检**

Run: `python3 scripts/orchestrator.py preflight`

Expected: PASS，并显示所有章节阶段解析后的模型和思考强度；不能启动模型。

- [ ] **Step 4: 检查调用上限的确定性证明**

Run: `python3 -m unittest tests.test_cli_capabilities tests.test_call_budget tests.test_model_calls tests.test_evolution tests.test_pipeline -v`

Expected: 覆盖并通过以下断言：

```text
fast path = 3
balanced smooth path = 5
selector path <= 8
full path = 10
concurrent race never starts call 11
resume never refunds interrupted calls or repeats completed calls
```

- [ ] **Step 5: 禁止在本任务中运行真实试写**

不要执行以下命令：

```bash
python3 scripts/orchestrator.py next
python3 scripts/orchestrator.py next --dry-run
python3 scripts/orchestrator.py next --mode fast
```

它们都会消耗真实 Codex 额度。实现交付后先由用户/Claude 审查代码，再由用户单独批准 10 次真实试运行。

- [ ] **Step 6: 生成实施报告**

报告必须列出：修改文件、测试命令与结果、未执行的真实试运行、任何偏离设计的地方、当前工作树中保留的用户原有改动，以及建议的下一步审查命令。

- [ ] **Step 7: 最终提交（仅 `.git` 可写时）**

```bash
git add scripts/prequel scripts/orchestrator.py config agents schemas tests README.md init.md docs/superpowers
git commit -m "feat: balance novel quality speed and codex usage"
```

提交前必须逐项核对 `git status --short`，不要把与本计划无关的用户文件纳入提交。

---

## Claude Review Checklist

Claude 审查本计划时应优先验证：

1. Planner 是否确实计入同一个 10 次预算；
2. 并发预留是否可能发生竞态或第 11 次调用；
3. 崩溃中的 active reservation 是否仍然计为已花费；
4. 是否还有任何章节路径绕过 `ModelCallExecutor` 直接调用 Provider；
5. 两次专项审查、Selector、Reviser 和 Verifier 的条件组合是否始终不超过 10；
6. 修订前是否原子预留 Reviser＋Verifier 两个槽位；
7. 单一合格稿是否可能在低置信度下错误自动晋级；
8. 集成 Reviewer 是否仍能读取完整候选正文；
9. 配置是否可能静默回退到默认模型或默认思考强度；
10. 晋级后审计是否已经完全脱离章节预算事务；
11. 恢复时模型、思考强度或 Prompt 变化是否会使旧阶段失效；
12. 自动测试和指标工具是否保证不触发真实 Codex 调用。
13. 集成初筛没有主动请求时，确定性风险触发器是否仍能启动专项复核；
14. 三个 `CANDIDATE_FOCUSES` 是否仍然保留，并且每章只确定性选择两个；
15. 候选降级和 `BUDGET_EXHAUSTED` 是否给出不新增调用与新增预算两类清晰操作；
16. CLI 能力门是否先于 Provider/Router 实施，并明确区分离线验证与 live canary；
17. 旧 `REPLAN` 是否只读显示、不可按新版恢复且不进入十次试运行统计。
