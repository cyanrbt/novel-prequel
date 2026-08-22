# 章节生成预算优化实施报告

日期：2026-08-02

## 结论

已按批准设计完成代码、配置、CLI、测试和运维文档实施。新版章节流程采用单工作区事务，平衡模式硬上限 10 次调用，快速模式硬上限 3 次；Planner、候选生成、审查、选择、修订和验证统一经过持久化预算入口。实施和自动测试期间没有执行真实章节生成，也没有运行十次 live benchmark。

## 已实施能力

- Codex CLI 能力门：无模型请求地校验 Sol、Terra、Luna 及 medium/high 组合，并验证 `--config` 参数格式。
- 显式路由：Planner/集成初筛使用 Terra medium；候选/Selector 使用 Sol medium；专项使用 Terra high；修订使用 Sol high；普通验证使用 Luna high；复杂验证使用 Terra high。
- 原子预算：调用前预留，Provider 启动后的成功、失败、超时和无效输出均结算；并发最大为 2；第 11 次调用在 Provider 启动前被拒绝。
- 自适应质量流程：平衡模式两候选并发、两次集成初筛、最多两个确定性触发专项、必要时一次盲选、最多一次定向修订和差分验证。
- 恢复：已完成阶段只有在输入、输出和模型路由指纹一致时复用；中断活动调用记为已花费失败；已明确失败的阶段不自动重试。
- 降级：候选失败不自动补写，决策工件记录失败候选、阶段、原因、调用数、最佳工件和人工动作。
- 预算耗尽：`BUDGET_EXHAUSTED` 分组列出无需新增调用的安全动作和会创建新预算的动作；`--resume` 不扩展上限。
- 旧状态兼容：旧 `REPLAN` 原值不改，显示为只读旧流程状态，新版恢复入口明确拒绝续跑。
- 审计隔离：章节晋级只记录 `audits_due`；health/arc 审计必须显式执行，并使用独立的单次调用清单。
- 试运行指标：只读汇总器要求恰好 10 份新版 manifest，统计调用、模型、耗时、降级、影子复核和批准阈值。

## 六项风险的落地

1. 集成初筛单点：保留确定性静态门禁、低置信/门槛/事实冲突/单候选连续性守卫，并支持预算内影子复核。
2. 候选失败提示：`decision.json`、`decision.md` 和 CLI 均给出失败详情、未重试原因和最佳工件。
3. 三个焦点压为两个：保留三个焦点库，每章按规划关键词或章号轮换动态选择两个，不永久删除第三类。
4. 预算耗尽指引：安全动作、新预算动作、耗尽阶段和恢复限制均被持久化。
5. CLI 参数风险：能力门最先实现；本机 Codex CLI `0.146.0` 的无模型检查通过，目标模型/强度组合存在，严格配置 help 解析成功。
6. 旧 `REPLAN`：只读显示、基准排除和不可恢复行为都有自动测试。

## 验证记录

- `python -m compileall -q scripts tests`：通过。
- `pytest -q`：94 项通过。
- `python -m unittest discover -v`：94 项通过。
- `python scripts/orchestrator.py preflight`：通过；列出全部实际阶段路由；没有发起模型请求。
- `codex exec --strict-config --model gpt-5.6-terra --config 'model_reasoning_effort="medium"' --help`：退出成功；仅验证参数解析。
- `codex debug models --bundled`：退出成功；仅读取内置目录。
- `git diff --check`：通过。

## 尚未执行的有成本步骤

- 未执行真实 canary。
- 未生成任何新章节。
- 未执行用户批准的 10 次真实试运行。代码审查通过后，应由用户逐次显式启动，并至少完成 5 次轮换影子复核；之后用 `scripts/benchmark_pipeline.py` 只读汇总。

## 首次真实试运行记录

审查通过后尝试启动第 1 次真实 dry-run，但当前受管执行环境阻止了嵌套 Codex app-server 初始化，尚未形成可纳入十次质量 benchmark 的有效运行。

- `attempt_05`：Planner `call_001` 在 148ms 后失败；默认 SQLite 状态目录只读。没有生成规划或正文。
- 按批准的 `sqlite_home` 适配，将 SQLite 状态迁移到被 Git 忽略的 `novel/work/.codex-runtime`。新增测试后全量 `unittest` 为 95 项通过；严格配置 help 检查通过。
- `attempt_06`：项目内 SQLite 数据库已成功创建和写入，但嵌套 app-server 仍因另一个受管只读位置在 560ms 后初始化失败。按计划立即停止，没有尝试第三个目录。
- 两次清单都按保守规则记录 Planner 失败 1/10；均未生成 `plan.json`、候选正文、审查或修订稿，也不计入批准的十次质量 benchmark。
- 试运行前后正式文件哈希完全一致：`current.json` 为 `20479318c2a491ce9ee7240ea90e61726b17d90bb6408c5502c3a33568b47c1d`，第 1 章为 `53e26c73e00b754c2f4951d46b2e01542d2c4ad8413a1d6e5e60575d2615627a`，第 2 章为 `9772c66a5c72631c00602cc40451c5388dde637c162e59f93ff86bc54ae18948`。
- 当前可靠执行路径是在普通用户终端运行同一命令；该环境的 Codex Home 和相关本地状态位置可写，章节预算器仍会独立限制最多 10 次调用。

## 主要入口

- 设计：`docs/superpowers/specs/2026-08-01-chapter-generation-budget-optimization-design.md`
- 实施计划：`docs/superpowers/plans/2026-08-01-chapter-generation-budget-optimization.md`
- 章节 CLI：`scripts/orchestrator.py`
- 预算与调用：`scripts/prequel/call_budget.py`、`scripts/prequel/model_calls.py`
- 自适应引擎：`scripts/prequel/evolution.py`
- 指标汇总：`scripts/prequel/metrics.py`、`scripts/benchmark_pipeline.py`
