# 审查失真诊断、运行进度与耗时口径修复设计

**状态：** 已批准  
**日期：** 2026-08-02  
**适用范围：** `novel-prequel` 预算化章节生成管线  
**前置设计：** `2026-08-01-chapter-generation-budget-optimization-design.md`

## 1. 背景与结论

第 3 章 `attempt_07` 已在 6/10 次调用内完成：两份候选均成功生成，其中第二份候选通过集成初筛，第一份候选的集成初筛和第二份候选的连续性专项复核则因为引用了正文中不存在的原句而被严格证据门禁拒绝。最终进入 `WAITING_USER` 是正确的安全结果，但现有系统暴露出五个可观测性问题：

1. 无效审查的模型原始输出没有落盘，事后无法定位究竟是 JSON、字段还是证据引用失真；
2. 集成审查工件无效被记录成候选正文 `HARD_FAIL`，混淆“正文质量失败”和“审查器输出失败”；
3. CLI 只显示最终状态，看不到具体哪个审查无效及诊断工件位置；
4. 长时间运行期间没有阶段进度，用户无法区分正常推理、挂起和失败；
5. CLI 只展示并发调用耗时之和，容易把 656.5 秒误读为实际等待时间；本次实际墙钟约 368.5 秒。

本修复保持严格证据真实性门禁、10 次调用硬上限、现有评分阈值和模型路由不变。它只改进失败语义、诊断工件、运行反馈和时间统计，不会为无效审查自动追加模型重试，也不会在实施或测试阶段运行真实模型。

## 2. 方案选择

### 2.1 采用方案：严格门禁＋失败分类＋本地诊断

模型输出继续接受逐字证据校验。校验失败时保留原始输出，并将结果分类为 `REVIEW_INVALID`；候选不得因为这个无效审查获得分数、晋级或被判定为正文硬失败。运行可继续利用其他完整有效的候选，但最终决策必须明确处于降级状态。

同时增加结构化进度事件，由 CLI 实时呈现模型调用开始、完成、失败和审查工件校验失败。最终摘要分别展示实际墙钟耗时和各并发调用耗时之和。

### 2.2 未采用方案

- **放宽引用匹配或进行模糊纠正：** 会让模型捏造的近似句绕过证据门禁，无法保证评审依据确实存在于正文。
- **无效审查后自动重试 Reviewer：** 会额外消耗额度，并可能掩盖提示词或验证契约的系统性问题。需要重试时由用户在新运行或后续显式机制中决定。
- **将无效审查继续映射为零分 `HARD_FAIL`：** 虽然能阻止晋级，但会错误归因到正文，污染候选比较和后续质量统计。

## 3. 失败语义与决策数据

### 3.1 候选的两个独立状态轴

每份候选在 `decision.json` 中至少记录：

```json
{
  "id": "candidate_01",
  "content_status": "VALID",
  "review_status": "INVALID",
  "classification": "REVIEW_INVALID",
  "failure_stage": "triage_candidate_01",
  "failure_kind": "EVIDENCE_VALIDATION",
  "diagnostic_artifact": "candidates/candidate_01/diagnostics/integrated_review.invalid.txt"
}
```

- `content_status`：`VALID`、`HARD_FAIL` 或 `GENERATION_FAILED`；只描述正文生成和确定性静态门禁。
- `review_status`：`VALID`、`INVALID`、`SKIPPED` 或 `NOT_RUN`；只描述语义审查工件是否完整可信。
- `classification`：保留现有 `HARD_FAIL`、`ELIGIBLE`、`NEAR_MISS`、`LOW_SCORE`，新增 `REVIEW_INVALID`。`REVIEW_INVALID` 不参与分数比较和自动晋级。
- `failure_kind`：至少区分 `PROVIDER_ERROR`、`PARSE_ERROR`、`SCHEMA_ERROR`、`EVIDENCE_VALIDATION` 和 `BUDGET_ERROR`。

静态 P1 仍然得到 `content_status=HARD_FAIL` 与 `classification=HARD_FAIL`。集成初筛无法形成有效工件时，不再伪造四维零分的正文硬失败评分卡；可写入一个明确标记 `evaluation_status=INVALID` 的诊断性 scorecard，以兼容现有工件读取，但它不得被当成有效评分。

### 3.2 运行级降级状态

`decision.json` 的 `degraded` 在以下任一情况为真：

- 候选生成失败；
- 候选正文静态硬失败；
- 集成初筛工件无效；
- 已触发的专项复核工件无效或未完成；
- 预算使会改变决策的必要审查无法启动。

同时新增或规范以下字段：

- `generation_degraded`、`content_degraded` 与 `evaluation_degraded`，分别表示生成失败、正文静态硬门禁失败和审查工件失败；
- `failures`：结构化失败列表，不再只能表达一个 `failed_candidate`；
- `best_available_artifact`：当前最佳有效正文路径；
- `automatic_retry_skipped_reason`：明确说明固定预算下没有自动重跑无效 Reviewer；
- `recommended_actions`：给用户无需新增调用和会消耗新调用的两类选择。

旧字段 `failed_candidate`、`failure_stage` 暂时保留为首个失败的兼容投影，避免破坏已有脚本。旧运行清单和旧 `decision.json` 只读显示，不做批量迁移。

### 3.3 晋级安全性

- `REVIEW_INVALID` 候选永远不能自动晋级；
- 单一 `ELIGIBLE` 候选只有在另一候选也完成有效评估，并且必要的连续性专项复核有效通过时，才可沿用现有自动晋级条件；
- 专项复核无效时，保留已验证的集成评分，但将整个运行置为降级并进入 `WAITING_USER`；
- 失败分类不改变任何质量阈值、权重或证据逐字匹配规则。

## 4. 无效审查原文工件

### 4.1 路径

仅在模型调用已经返回文本、但后续解析或验证失败时保存原始文本：

```text
candidates/candidate_XX/diagnostics/integrated_review.invalid.txt
candidates/candidate_XX/diagnostics/<dimension>_review.invalid.txt
revisions/round_XX/diagnostics/<dimension>_review.invalid.txt
revisions/round_XX/diagnostics/verification.invalid.txt
comparisons/<stage>/diagnostics/selector.invalid.txt
```

本次实施首先覆盖当前管线实际使用的集成初筛和专项复核；同一保存函数用于后续 Selector、Verifier 等结构化模型工件，避免再次丢失诊断信息。

`ChapterWorkspace` 的白名单只增加上述精确模式，不允许任意路径写入。文件内容是模型最终输出，不保存隐藏推理、完整提示词、环境变量或 Provider 命令中的敏感配置。

### 4.2 保存时机与错误优先级

1. Provider 返回非空文本；
2. 解析与证据验证在内存中执行；
3. 若失败，先原子写入 `.invalid.txt`，再记录结构化失败；
4. 若诊断写入本身失败，保留原始审查错误为主错误，把写入错误作为附加诊断，不能用后者覆盖根因；
5. 有效输出仍只写现有 `.json` 正式工件，不额外保存重复 raw 文件。

Provider 在返回正文前就失败时没有 raw 文本可保存，决策中将 `diagnostic_artifact` 设为 `null`，并保留退出或超时摘要。

## 5. 实时进度输出

### 5.1 事件边界

模型调用执行器增加可选进度回调；库默认静默，只有 CLI 注册输出器。事件不进入模型调用预算，也不影响恢复哈希。

事件最少包括：

```text
CALL_STARTED
CALL_COMPLETED
CALL_FAILED
ARTIFACT_INVALID
STAGE_REUSED
```

每个事件只携带安全、有限的字段：时间、call id、阶段、候选或维度、模型、思考强度、状态、单次耗时和诊断路径。不得输出 prompt 或模型正文。

并发线程共享一个加锁的 CLI 输出器，保证每条消息整行写出并立即 `flush`。示例：

```text
[call_004] 开始 集成初筛 candidate_01 · gpt-5.6-terra/medium
[call_004] 完成 模型调用 · 84.2秒；正在校验工件
[审查无效] candidate_01 集成初筛 · EVIDENCE_VALIDATION
诊断: .../candidates/candidate_01/diagnostics/integrated_review.invalid.txt
```

“模型调用完成”和“审查工件有效”是两个不同事件，避免将 Provider 正常退出误报为评审成功。

### 5.2 最终 CLI 摘要

最终输出继续显示章节状态、工作区、调用数和模型构成，并新增：

- 每个无效审查的候选、阶段、失败类型和诊断路径；
- `WAITING_USER` 的直接原因；
- 当前最佳有效正文；
- 不增加调用的安全操作，例如阅读工件、人工审查、接受 dry-run；
- 会消耗新预算的操作，例如显式开启新 attempt；
- 固定预算禁止自动重跑的说明。

若终端非交互或输出被重定向，仍然逐行输出，不依赖动画、进度条或控制字符。

## 6. 耗时口径

最终摘要分开显示：

```text
实际墙钟耗时: 368.5秒
并发调用耗时合计: 656.5秒
```

- **实际墙钟耗时**：完成运行使用 `finished_at - started_at`；运行中状态使用当前时间减 `started_at`。这是用户真实等待时间。
- **并发调用耗时合计**：所有已完成或失败模型调用的 `duration_ms` 之和，用于评估模型资源占用；它可能大于墙钟时间。
- 缺少旧清单时间戳时显示“未知”，不得用调用耗时合计冒充墙钟时间。
- `chapter_metrics` 和 CLI 使用同一计算函数，避免报告与终端出现两套口径。

## 7. 实施边界

预计修改：

- `scripts/prequel/artifacts.py`：诊断工件白名单；
- `scripts/prequel/evolution.py`：raw 保存、失败分类、结构化降级信息；
- `scripts/prequel/model_calls.py`：可选进度事件；
- `scripts/prequel/pipeline.py`：回调贯穿和终态数据；
- `scripts/prequel/metrics.py`：统一墙钟与调用累计耗时；
- `scripts/orchestrator.py`：实时输出与最终诊断提示；
- 对应单元测试和使用说明。

明确不修改：

- 10 次调用硬上限与调用记账语义；
- Sol/Terra/Luna 路由、思考强度和最大并发 2；
- 候选数、评分权重、门槛、逐字证据验证；
- 自动晋级条件、正式章提升边界和 `current.json`；
- `attempt_07` 历史工件；
- 任何真实模型调用。

## 8. 验证与验收

### 8.1 自动化测试

1. 集成初筛返回合法 JSON 但引用不存在原句：保存 raw，候选为 `REVIEW_INVALID`，不生成正文 `HARD_FAIL`。
2. 集成初筛返回非法 JSON：保存 raw，`failure_kind=PARSE_ERROR`。
3. 专项复核引用无效：保留原集成评分，运行降级并进入 `WAITING_USER`。
4. Provider 未返回文本即失败：无 raw 文件，仍有结构化失败说明。
5. 诊断路径白名单允许目标路径并拒绝路径穿越及未声明文件。
6. 模型调用成功、失败和并发时，进度事件顺序在单个 call 内保持 `STARTED -> COMPLETED/FAILED`，输出行不交错。
7. Provider 完成但审查验证失败时，同时出现 `CALL_COMPLETED` 与 `ARTIFACT_INVALID`。
8. 两个并发调用各 100 秒、墙钟 105 秒时，CLI 分别显示约 105 秒和 200 秒。
9. 旧 manifest 缺少新字段时，`status` 和 CLI 仍可读取，墙钟缺失显示“未知”。
10. 全量单元测试通过，且测试替身确认没有启动 `codex exec` 真实调用。

### 8.2 验收标准

- 用户可在运行期间持续看到正在执行的阶段，长时间静默不再是正常界面表现；
- 任一结构化审查无效后，都能从最终 CLI 输出定位到保留的原始工件；
- CLI 和 `decision.json` 不再把审查器失真表述为正文质量硬失败；
- 无效审查不会绕过门禁、得到有效分数或触发自动晋级；
- 修复不增加任何固定或自动模型调用；
- CLI 对实际等待时间与并发资源累计时间使用清晰、不同的名称。
