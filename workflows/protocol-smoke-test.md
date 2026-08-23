# 通用协议冒烟测试

## 目的

证明任务协议不依赖任何外部 Agent CLI、模型名称或 sub-agent 功能。本测试不修改 `novel/` 下的正式内容。

## 输入

- `tests/fixtures/prompt_native_task.json`
- `tests/fixtures/prompt_native_result.json`
- `schemas/task_envelope.schema.json`
- `schemas/agent_result.schema.json`

## 检查

1. 两个示例工件均为合法 JSON object。
2. 协议版本分别为 `prequel-task/1` 和 `prequel-result/1`。
3. `task_id` 完全一致。
4. 结果声明的 `input_fingerprint` 与任务一致。
5. 任务指定的角色文件和输出 Schema 均存在。
6. 核心配置中不包含 `provider`、`model_profiles` 或 `stage_routes`。
7. 可选执行后端示例不影响上述检查。

通过后只报告协议可用；不得据此声称小说正文质量门禁已经通过。
