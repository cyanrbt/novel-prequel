# 通用 Agent 工作流

本文件是项目的 Agent 无关入口。任何能够读取仓库文件、遵循提示词并返回文本或 JSON 的 Agent，都可以执行本项目；不得假设 Codex、AGY、OpenCode、Grok、Claude 或任何特定模型存在。

## 启动指令

用户只需告诉当前 Agent：

> 阅读 `WORKFLOW.md`，执行指定工作流。若支持 sub-agent，可并行委派互不依赖的任务；否则由主 Agent 按同一协议顺序执行。

没有指定工作流时，先运行“只读状态检查”，报告当前状态、阻塞原因与可安全执行的下一步，不得自行生成或提升正文。

## 权威边界

1. 先解析根目录 `project.json` 指向的项目清单；清单 `paths` 指定的状态、正式章节、知识登记、规则和用户偏好合同是当前项目的权威事实。
2. 项目 `agents` 选择的基础角色和 `role_overlays` 只定义行为与故事约束，不定义运行平台。
3. `schemas/*.schema.json` 定义结构化工件契约。
4. `workflows/*.md` 定义任务顺序、输入、输出和停止条件。
5. 项目清单的 `work_dir` 只存放可恢复运行工件，不是正式正文来源。
6. 未通过全部门禁的结果不得写入项目的 `chapters_dir` 或正式状态。

完整权威文件说明仍以 [`init.md`](init.md) 为准；本文件只定义与执行平台无关的调度协议。

## 通用执行协议

每项可委派任务都使用 `creative-task/1` 信封，结构见 [`schemas/task_envelope.schema.json`](schemas/task_envelope.schema.json)。执行者返回 `creative-result/1`，结构见 [`schemas/agent_result.schema.json`](schemas/agent_result.schema.json)。旧的 `prequel-task/1` / `prequel-result/1` 工件仅作为兼容输入保留。

执行者必须遵守以下规则：

1. 只读取任务信封 `inputs` 明确列出的内容；角色提示词另有更严格限制时，以更严格者为准。
2. 不依赖隐藏会话记忆补足设定，不根据模型常识扩写未授权正典。
3. `output_contract.format=json` 时只返回符合指定 Schema 的 JSON 工件。
4. 失败、信息不足或能力不匹配时返回明确状态，不伪造成功结果。
5. 主 Agent 验证任务编号、输入指纹、输出格式和门禁结果后，才可推进后续任务。
6. sub-agent 是可选执行方式，不是工作流依赖；不支持 sub-agent 时必须顺序执行。

## 能力降级

| 执行能力 | 工作方式 |
|---|---|
| 支持 sub-agent | 对同一阶段中互不依赖的任务并行委派，等待全部结果后再推进 |
| 不支持 sub-agent | 主 Agent 按任务清单顺序执行，保持相同输入与输出契约 |
| 支持结构化输出 | 直接使用目标 JSON Schema |
| 不支持结构化输出 | 在提示词中附带 Schema，并由主 Agent 解析、验证；禁止从解释文字猜测字段 |
| 支持命令与文件工具 | 可运行确定性校验，但不得让工具替代语义审查 |
| 不支持命令或写文件 | 只返回工件内容与建议路径，等待具备权限的主 Agent落盘 |

## 可执行工作流

- [`workflows/status-check.md`](workflows/status-check.md)：只读检查项目是否可以继续。
- [`workflows/style-calibration.md`](workflows/style-calibration.md)：用三候选盲选校准正向文风画像，不修改正式正文。
- [`workflows/scene-generation-experiment.md`](workflows/scene-generation-experiment.md)：在同一事实锁下比较预先规划、角色模拟和滚动规划，只停在匿名人工盲选。
- [`workflows/next-chapter.md`](workflows/next-chapter.md)：规划、生成、审查下一章并停在安全边界。
- [`workflows/accept-candidate.md`](workflows/accept-candidate.md)：重新验证并提升已通过的候选。
- [`workflows/protocol-smoke-test.md`](workflows/protocol-smoke-test.md)：不调用外部 Agent、不修改正式内容的协议冒烟测试。

## 确定性工具

Python 工具是可选安全设施，不是 Agent 驱动器：

```bash
# 只检查通用工作流、任务协议和示例工件
python3 scripts/orchestrator.py workflow-check

# 验证冻结的场景实验输入，不调用模型、不改正式正文
python3 scripts/orchestrator.py scene-experiment validate --packet <scene_packet.json>

# 检查项目状态、规则、章节连续性和正式审核绑定
python3 scripts/orchestrator.py preflight
```

如果当前执行环境没有 Python，主 Agent 仍可按工作流逐项读取和核对；但任何无法确定性验证的事项必须记录为未验证，不能假定通过。

## 执行边界

创作引擎配置位于 `config/engine_config.json`，故事配置和资产由当前 `creative-project/1` 清单选择，具体组合规则见 [`docs/project-packages.md`](docs/project-packages.md)。这些配置都不包含 Agent CLI 或模型名称。仓库没有模型 Provider、模型路由、模型调用器或 Agent 命令启动器；Python 只读取和验证工件，不生成、审查或改写文本。

语义任务由正在阅读本仓库的当前 Agent 执行：支持 sub-agent 时按任务图委派，不支持时由主 Agent 顺序完成。若未来接入新的宿主平台，只能由宿主消费任务信封并回传结果工件，不得在仓库内恢复 Agent 命令启动器，也不得修改角色提示词、故事状态或质量门禁语义。
