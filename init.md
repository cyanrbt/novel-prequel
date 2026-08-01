# 《神秘复苏前传：张洞传》创作引擎

本手册说明创作管线的权威文件、Agent 分工、状态控制、质量门禁和命令入口。项目目标是持续生成约 600 章正文，同时保持设定、时间、人物能力与叙事声音连续。

## 核心原则

1. **状态唯一**：当前章号、时间、人物、伏笔和已揭示规则只写入 `novel/state/current.json`。
2. **规则唯一**：正文约束只认 `novel/rules/rulebook.md`。
3. **风格唯一**：运行参数使用 `novel/style/compact_style.yaml`，示例使用 `novel/style/style_anchors.txt`。
4. **职责分离**：Planner 只规划，Writer 只写正文，Reviewer 只审查。
5. **失败隔离**：未通过门禁的内容只保存在工作区，不修改正式章节和当前状态。
6. **原子提升**：章节、元数据和状态必须一起成功写入，任何一步失败都回滚。

## 权威文件

| 文件 | 职责 | 更新时机 |
|---|---|---|
| `novel/state/current.json` | 当前创作状态 | 正式章节通过后 |
| `novel/rules/rulebook.md` | 创作与发布约束 | 规则确认后 |
| `novel/style/compact_style.yaml` | 运行时风格参数 | 风格策略调整时 |
| `novel/style/style_anchors.txt` | 民国语境示例 | 经人工确认后 |
| `novel/knowledge/canon_registry.json` | 事实等级与年代禁入项 | 设定核验后 |
| `novel/rules/setting_whitelist.md` | 当前事件可用设定 | 事件规划时 |
| `novel/rules/setting_blacklist.md` | 禁止自行扩展的设定 | 事件规划时 |
| `novel/foreshadow_tracker.md` | 伏笔播种与回收 | 规划和验收时 |
| `novel/chapters/` | 正式章节与元数据 | 原子提升时 |
| `novel/full_novel.txt` | 连续阅读合订本 | 执行 merge 时 |

## 三 Agent 职责

### Planner

- 读取当前状态、事件大纲、设定边界和近期章节摘要。
- 输出符合 `schemas/plan.schema.json` 的规划 JSON。
- 明确场景、状态变化、依据 ID、伏笔操作和禁用元素。
- 不生成正文，不提前调用未解锁能力。

### Writer

- 读取核准规划、规则、风格参数和受控上下文。
- 输出纯正文，不解释创作过程，不修改状态。
- 保持第三人称限知、民国语境和人物认知边界。

### Reviewer

- 对正文执行规则、连续性、时代、重复、语言和证据审查。
- 输出符合 `schemas/review.schema.json` 的审查 JSON。
- 只能引用正文中实际存在的证据，不能用语义判断推翻静态门禁。
- 不直接改写正文。

## 状态机

状态保存在 `novel/state/current.json` 的 `machine_state` 字段中。

| 状态 | 含义 |
|---|---|
| `IDLE` | 等待命令 |
| `INIT` | 初始化检查 |
| `OUTLINE` | 事件规划 |
| `WRITE` | 章节生成 |
| `REVIEW` | 独立审查 |
| `RECOVERY` | 从有效备份恢复 |
| `WAITING_USER` | 等待人工判断 |
| `ERROR` | 流程停止，正式内容不变 |

运行前必须通过状态结构、模型提供方、设定注册表、事件大纲和正式章号连续性检查。

## 写作管线

```text
预检
  ↓
Planner 生成规划 JSON
  ↓
规划结构与设定依据校验
  ↓
Writer 生成正文
  ↓
静态质量检查
  ↓
Reviewer 语义审查
  ├─ 未通过：保留尝试工件，进入下一次规划
  └─ 通过：等待提升或直接原子提升
             ↓
       正文章节 + 元数据 + 当前状态
```

同一章尝试次数由配置控制。达到上限后流程停止，由用户检查工作区中的规划、正文、静态报告和语义报告。

## 工作区与原子提升

每次尝试写入：

```text
novel/work/chapter_NNN/attempt_MM/
├── plan.json
├── draft.txt
├── static_review.json
├── semantic_review.json
└── manifest.json
```

工作区属于可再生成数据，不进入公开仓库。只有通过全部门禁的尝试可以提升到：

```text
novel/chapters/vol_NN/chapter_NNN.txt
novel/chapters/meta/chapter_NNN.md
novel/state/current.json
```

提升前会拒绝覆盖已有正式章节。状态写入前生成 `current.json.bak`，该备份只保留在本地。

## 质量门禁

### 静态门禁

- 章号、年份、人物和时代词汇正确。
- 能力满足解锁章号。
- 最近章节没有完整段落或长句复用。
- 规划包含真实状态变化和注册依据。
- 正文不包含占位内容、越视角信息或禁入设定。

### 语义门禁

- 审查证据能在正文中定位。
- P1 问题直接阻断提升。
- PASS 结论不能伴随低分或要求重写。
- 审查结论不能覆盖静态检查失败。

## 命令参考

```bash
# 查看当前进度
python3 scripts/orchestrator.py status

# 执行完整写作前检查
python3 scripts/orchestrator.py preflight

# 生成下一章的全部工件，但不提升正式章节
python3 scripts/orchestrator.py next --dry-run

# 重新校验并提升最近一次通过的尝试
python3 scripts/orchestrator.py accept

# 规划、写作、审查并直接提升下一章
python3 scripts/orchestrator.py next

# 审查最近五章
python3 scripts/orchestrator.py review --last 5

# 从正式章节重建合订本
python3 scripts/orchestrator.py merge

# 从有效状态备份恢复
python3 scripts/orchestrator.py recover

# 运行全部自动测试
python3 -m unittest discover -v
```

## 故障恢复

1. 先运行 `status` 和 `preflight` 确认失败位置。
2. 未通过的章节尝试保留在 `novel/work/`，不会污染正式内容。
3. 状态文件损坏时运行 `recover`；命令只接受能够通过完整校验的备份。
4. 若本地备份不可用，从最后一个可信 Git 提交恢复项目文件，再重新执行预检。
5. 恢复后先使用 `next --dry-run`，人工检查工件后再执行 `accept`。
