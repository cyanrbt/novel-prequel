# 创作引擎与故事包

仓库把可复用的创作管线、题材策略和某一部小说的剧情事实分成三层。

| 层 | 位置 | 负责内容 |
|---|---|---|
| 创作引擎 | `scripts/`、`schemas/`、`workflows/`、`agents/`、`config/engine_config.json` | 状态机、任务协议、事务提升、通用质量门禁和基础角色 |
| 题材配置 | `profiles/<profile-id>/` | 题材词表、专用审计和可叠加的角色约束 |
| 故事包 | `stories/<story-id>/` 及清单声明的数据目录 | 主角、年代、卷结构、剧情、人物、正典、文风、状态和正文 |

## 项目清单

根目录 `project.json` 只是默认项目指针。它当前指向 `stories/zhangdong/project.json`。真正的项目清单使用 `creative-project/1`，并声明：

- `engine_config`：通用质量、上下文和事务策略；
- `story_config`：主角、篇幅、卷结构、角色文件和故事约束；
- `profiles`：需要叠加的题材配置；
- `paths`：状态、剧情、人物、知识、风格、工作区和正式章节的实际位置。

运行时先读引擎配置，再用故事配置深度覆盖。Python 不再从固定的 `novel/` 或 `config/prequel_config.json` 推断当前故事。

## 角色与题材叠加

`story_config.json` 的 `agents` 为角色选择基础 Markdown，`role_overlays` 再按顺序追加题材和故事约束。因此 Planner 和 Writer 可以保留通用职责，而“灵异规则”“张洞的认知边界”等约束只对张洞故事生效。

确定性场景审计也按 `profiles` 组合。`horror-mystery` 会启用受限视线、门窗边界、身份确认、死亡冲击和证据等级检查；未选择该题材的故事不会启用这些专用语义。

## 选择项目

不传参时使用根目录默认指针；也可在子命令之前显式选择另一个项目：

```bash
python3 scripts/orchestrator.py status
python3 scripts/orchestrator.py --project stories/zhangdong/project.json preflight
python3 scripts/orchestrator.py --project tests/fixtures/decoupled_story/project.json status
```

## 新建故事包

1. 复制项目清单结构，换掉 `project_id`、`title` 和全部 `paths`。
2. 新建故事配置，声明主角、卷结构、章节长度和所需角色。
3. 从空白状态、A/B/C 事实表、里程碑表、伏笔表、当前事件和文风画像开始。
4. 只选择真正需要的题材配置；故事独有要求放入自己的 `role_overlays`。
5. 运行 `--project <manifest> preflight`，不通过前不开始创作。

`tests/fixtures/decoupled_story/` 是一个完整的当代现实题材最小样例，用于防止引擎重新与张洞剧情耦合。

## 兼容性

根目录没有 `project.json` 时，引擎仍能以只读兼容方式解析旧的 `config/prequel_config.json` 和 `novel/` 路径。该通道用于恢复旧工作区，新故事不应依赖它。
