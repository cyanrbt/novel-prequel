# 审查可观测性与运行进度修复实施报告

**状态：** 已完成  
**日期：** 2026-08-02  
**对应设计：** `docs/superpowers/specs/2026-08-02-review-observability-and-progress-design.md`  
**对应计划：** `docs/superpowers/plans/2026-08-02-review-observability-and-progress.md`

## 交付内容

### 1. 无效审查诊断

- 集成初筛和专项复核在 JSON 解析、结构或逐字证据校验失败时保存模型原始输出；
- 诊断文件使用严格白名单路径：`candidates/candidate_XX/diagnostics/*.invalid.txt`；
- raw 内容通过原子写入保存，不规范化尾部空白；
- Provider 未返回文本时不伪造 raw 文件，`diagnostic_artifact` 明确为 `null`；
- 诊断写入失败不会覆盖原始审查错误。

### 2. 失败语义

- 新增 `REVIEW_INVALID`，不再把审查器失真记为正文 `HARD_FAIL`；
- 候选分别记录 `content_status`、`review_status`、`failure_kind` 和诊断路径；
- 运行分别记录 `generation_degraded`、`content_degraded` 与 `evaluation_degraded`；
- `decision.json` 增加结构化 `failures`，同时保留 `failed_candidate`、`failure_stage` 和 `failure_reason` 兼容字段；
- 无效审查、正文静态硬失败和候选生成失败均不会自动追加模型调用；
- 任一必要审查无效时禁止自动晋级，保留当前有效分数和最佳正文供人工判断。

### 3. 实时运行进度

- 新增线程安全的进度事件分发器；
- CLI 实时显示 `CALL_STARTED`、`CALL_COMPLETED`、`CALL_FAILED`、`ARTIFACT_INVALID` 和 `STAGE_REUSED`；
- Provider 完成和审查工件有效是两个独立状态；
- 事件不包含 prompt 或模型正文；
- 输出观察器自身失败不会改变调用预算或中断创作管线。

### 4. 耗时与用户指引

- CLI 分别显示“实际墙钟耗时”和“并发调用耗时合计”；
- 缺少旧清单时间戳时墙钟显示未知，不再使用调用累计时间冒充；
- `WAITING_USER` 和 `BUDGET_EXHAUSTED` 均显示等待原因、诊断工件、安全操作、新预算操作和恢复警告；
- README 已补充进度、诊断和耗时口径说明。

## 验证结果

- 全量测试命令：`python3 -m unittest discover -s tests -v`；
- 结果：110 项通过，0 项失败；
- 状态只读检查：`python3 scripts/orchestrator.py status`，退出码 0；
- 能力预检：`python3 scripts/orchestrator.py preflight`，退出码 0；
- 当前配置解析：Planner Terra medium、Writer Sol medium、专项 Terra high、Verifier Luna high；
- 真实模型调用：0；
- 未执行 `next`、`review --specialists` 或 `audit`。

## 保持不变

- 平衡模式 10 次、快速模式 3 次调用硬上限；
- Sol/Terra/Luna 路由和思考强度；
- 两候选、最大并发 2、评分权重与资格阈值；
- 逐字证据真实性门禁；
- 正式章节、状态提升和原子写入边界；
- `novel/work/chapter_003/attempt_07` 历史工件未改写。

## 后续试运行预期

下一次由用户显式启动的 `next --dry-run` 会在运行期间持续输出阶段信息。若 Reviewer 再次引用正文中不存在的句子，系统会显示 `ARTIFACT_INVALID`，将候选或专项结果标为审查无效，并给出 `.invalid.txt` 路径；不会把它伪装成正文零分，也不会自动重试消耗额度。
