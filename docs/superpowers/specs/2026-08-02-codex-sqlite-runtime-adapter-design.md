# Codex SQLite 运行目录适配设计

日期：2026-08-02

## 背景

第 1 次真实试运行在 Planner 启动后、模型请求发出前失败。Codex CLI `0.146.0` 报告 app-server client 无法初始化，原因是默认 SQLite 状态目录位于当前受管环境的只读区域。章节预算按保守规则记录该调用失败 1 次，`attempt_05` 不允许自动重试。

官方 Codex 配置提供 `sqlite_home`，可单独移动 CLI/app-server 的 SQLite 运行状态，不改变 `CODEX_HOME` 中的认证、用户配置、技能或会话来源。

## 方案比较

### 方案 A：项目内独立 SQLite 目录（采用）

在 `provider.command` 中追加：

```text
--config sqlite_home="novel/work/.codex-runtime"
```

优点：改动最小；目录位于当前允许写入的工作区；认证仍使用现有只读 Codex Home；不需要复制令牌；普通终端和受管环境使用同一配置。缺点：运行数据库位于可再生成的 `novel/work` 下，需要保持忽略规则。

### 方案 B：改写 `CODEX_HOME`（排除）

会同时改变认证、配置、技能和状态来源，需要复制敏感认证材料，范围过大且不符合最小权限原则。

### 方案 C：只让用户在普通终端运行（兜底）

无需代码变化，但当前会话无法监控预算与工件，不满足用户已批准的自动试运行目标。

## 行为边界

- 只修改 Codex CLI 的 SQLite 状态位置。
- 不修改模型、思考强度、提示词、认证来源、章节预算或沙箱模式。
- 不恢复 `attempt_05`，而是创建 `attempt_06`。
- 先验证配置解析和目录可写；该验证不发模型请求。
- 随后只执行第 1 次替代试运行，不自动启动第 2 次。
- 若仍在模型请求前失败，立即停止并保留新清单，不继续尝试其他目录。

## 验证

1. 配置 JSON 与 Provider argv 单元测试通过。
2. `codex exec --strict-config --config 'sqlite_home="novel/work/.codex-runtime"' --help` 退出成功。
3. 全量自动测试通过。
4. `attempt_06/run_manifest.json` 中 Planner 为 `call_001`，实际总调用不超过 10。
5. 正式章节和 `novel/state/current.json` 保持不变。

## 回滚

删除 `provider.command` 中的两项 SQLite 参数即可恢复原行为；运行数据库属于 `novel/work` 下的可再生成数据，不影响小说正式状态。
