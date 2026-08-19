# 《神秘复苏前传：张洞传》创作引擎

本手册说明创作管线的权威文件、Agent 分工、状态控制、质量门禁和命令入口。项目采用质量优先的里程碑结构，目标约500章、自然完结区间440—580章，同时保持设定、时间、人物能力与叙事声音连续。

## 核心原则

1. **状态唯一**：当前章号、时间、人物、伏笔和已揭示规则只写入 `novel/state/current.json`。
2. **规则唯一**：正文约束只认 `novel/rules/rulebook.md`。
3. **风格唯一**：运行参数使用 `novel/style/compact_style.yaml`；`style_anchors.txt` 只有完成人工逐段核准后才允许进入运行上下文。
4. **职责分离**：Planner 维护完整 Constraint Ledger；Writer 只读取精简 Story Brief；Reviewer 使用完整账本审计，Selector 只做匿名比较。
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

## 质量进化角色

### Planner

- 读取当前状态、事件大纲、设定边界和近期章节摘要。
- 输出符合 `schemas/plan.schema.json` 的规划 JSON。
- 先建立 `reader_investment`：依恋必须分别说明对象、危险前的当场体验、私人意义与将被夺走的内容；情绪余震必须分别说明具体活人、已发生的伤口、未决选择及谜题所服务的人物处境。再加入青年张洞的愿望—缺点矛盾、正在改变活人处境的威胁、揭示变形和带真实阻力的线索取得。
- 再建立可验证的戏剧脊柱：开场压力与首章早期类型信号、主角即时欲望与个人利害、扰动事件、主角亲自作出的选择、由本人实际支付代价的正文结果、关系摩擦、三至四步非重复问题升级、情绪转折、连载承诺和章末行动的真实制约力。
- 每个场景说明威胁的主动动作、活人处境的转向与奖励类型；证据型奖励占多数、末场仅奖励证据或关键线索依赖高风险巧合时，计划不得进入正文生成。
- 明确场景、状态变化、依据 ID、伏笔操作和禁用元素。
- 不生成正文，不提前调用未解锁能力。

### Writer

- 读取精简故事简报、硬约束、规则、风格参数和受控上下文，不读取场景验证路径、普通解释清单或预设对话推理过程。
- 输出纯正文，不解释创作过程，不修改状态。
- 保持第三人称限知、民国语境和人物认知边界。
- 实际上下文及来源哈希写入各候选的 `writer_context.json`；近期正文尾部会真正进入 Writer，而不只用于统计。

### 集成、专项 Reviewer 与 Selector

- 集成 Reviewer 一次完成连续性、人物、文学性和反 AI 痕迹初筛，并逐维给出置信度与正文证据。
- 低置信度、门槛边界、事实冲突或单合格候选守卫才触发专项 Reviewer；每章最多两次。
- 输出符合 `schemas/specialist_review.schema.json` 的证据化计分 JSON。
- 只能引用正文中实际存在的证据，不能用语义判断推翻静态门禁。
- 不直接改写正文。
- Selector 只使用候选 A/B 标签做成对比较，输出 `schemas/ballot.schema.json`。

### Blind Reader 与 State Settler

- Blind Reader 不读取规划和隐藏状态，先做连续阅读，再检查可读性、人物可信度、目标情绪、叙事动量、开场抓力、主角行动权、问题升级、章末追读力、续读意愿与首个弃读点。
- “通顺、清楚、没有明显漏洞”本身最多是 3/5；PASS 要求八项体验分以及人物依恋、主动威胁、主角独特性、揭示变形、情绪余震五项标杆诊断均达到 4/5，并判断成稿达到同类型优秀网文的开篇竞争力 `MATCH`。`NEAR` 不再允许提升。
- State Settler 在盲读通过后逐项核对计划中的待提交变化；每条状态、伏笔和里程碑变化都必须引用最终正文的连续原句。
- 章节摘要和章末钩子从最终正文重新提取，不再把 `chapter_purpose` 和计划钩子直接写入长期记忆。

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
Writer 并发生成两份正文
  ↓
静态质量检查
  ↓
集成 Reviewer 并发初筛
  ↓
条件专项复核 → 必要时一次匿名盲选 → 一次定向修订与差分验证
  ├─ 不合格：保留工件，等待人工
  ├─ 边界结果：WAITING_USER
  └─ 高置信结果：AUTO_PROMOTE 候选
             ↓
       无大纲阅读效果门禁
             ↓
       最终正文状态证据结算
             ↓
       正文章节 + 元数据 + 当前状态
```

平衡模式含 Planner、Blind Reader 和 State Settler 在内最多 12 次模型调用，最大并发固定为 2；快速模式正文路径最多 3 次且必须人工确认，接受时仍须重新执行最终门禁。启动 Provider 后的失败、超时和无效输出均计数，不自动补写候选、不外层重新规划。达到上限后返回 `BUDGET_EXHAUSTED` 和现有工件。

## 工作区与原子提升

每次尝试写入：

```text
novel/work/chapter_NNN/attempt_MM/
├── run_manifest.json
├── plan.json
├── context_metrics.json
├── candidates/candidate_01..02/
│   ├── draft.txt
│   ├── writer_context.json
│   ├── static_review.json
│   ├── reviews/
│   └── scorecard.json
├── comparisons/
├── revisions/round_01/
├── reader_review.json
├── state_settlement.json
├── decision.json
└── decision.md
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
- 规划包含读者投入引擎；揭示改变问题种类，关键线索有阻力，场景奖励不由证据增量主导。
- 正文不包含占位内容、越视角信息或禁入设定。

### 语义门禁

- 审查证据能在正文中定位。
- P1 问题直接阻断提升。
- PASS 结论不能伴随低分或要求重写。
- 审查结论不能覆盖静态检查失败。

### 阅读效果与状态证据门禁

- PASS 必须在可读性、人物可信度、目标情绪、叙事动量、开场抓力、主角行动权、问题升级和章末追读力八项上均达到 4/5，并且普通读者明确愿意继续阅读。
- 盲读者还必须在人物依恋、主动威胁、主角独特性、揭示变形和情绪余震上均达到 4/5，给出 `MATCH` 的同类竞争力判断，并确认会优先继续当前故事；`NEAR`、仅靠证据变可靠、完整清楚或没有漏洞的稿件不得提升。
- 首个弃读点、所有审查问题和状态变化证据都必须逐字存在于当前正文，旧稿报告不能复用。
- 状态变化候选来自规划，但只有成稿已经演出的候选才可提交；缺一项就进入 `WAITING_USER`。
- 伏笔不得同章播种并回收；回收必须消费更早章节的播种，里程碑必须满足前置条件、所属卷和到期回收要求。
- 卷末出口里程碑通过后，当前卷、下一卷入口事件和世界揭示层由配置同步推进。

### 计分与提升

- 权重：连续性 30%、人物 25%、文学性 30%、反 AI 痕迹 15%。
- 候选资格线：85 / 75 / 75 / 80；总分不能掩盖单项不足。
- 自动提升要求总分至少 85、连续性至少 90、其余单项至少 82；接近候选需一次有效盲选，单合格候选需额外连续性专项守卫。
- 总分至少 78 但未满足全部自动条件时进入人工确认；正式状态保持不变。

## 长期记忆与阶段审计

`memory_index.json` 以正式正文哈希校验来源；`quality_lessons.json` 只在同类问题最近十章出现三次后激活，并在连续十章无复发后退休。Writer 每次最多接收八条相关经验。`creative_debts.json` 保存只作用于未来章节的阶段债务。

每十章到期健康检查，每二十章到期阶段复审。章节晋级只记录到期标记；用户必须显式执行 `audit`。每次审计有独立的单次调用清单，只写入 `novel/reviews/` 和未来创作债务，不会占用章节预算或改写历史正文。

长期质量基础见 [质量进化管线设计](docs/superpowers/specs/2026-08-01-quality-evolution-pipeline-design.md)，当前预算化执行规则见 [章节生成预算优化设计](docs/superpowers/specs/2026-08-01-chapter-generation-budget-optimization-design.md)。

## 命令参考

```bash
# 查看当前进度
python3 scripts/orchestrator.py status

# 执行完整写作前检查
python3 scripts/orchestrator.py preflight

# 生成下一章的全部工件，但不提升正式章节
python3 scripts/orchestrator.py next --dry-run

# 三调用快速模式；始终等待人工确认
python3 scripts/orchestrator.py next --mode fast --dry-run

# 按运行清单恢复，输入或正式状态变化时拒绝复用
python3 scripts/orchestrator.py next --resume --dry-run

# 重新校验并提升最近一次通过的尝试
python3 scripts/orchestrator.py accept

# 人工接受指定的合格候选
python3 scripts/orchestrator.py accept --candidate 2

# 规划、写作、审查并直接提升下一章
python3 scripts/orchestrator.py next

# 审查最近五章
python3 scripts/orchestrator.py review --last 5

# 手动执行二十章阶段复审
python3 scripts/orchestrator.py audit --arc

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
5. 模型调用中断时使用 `next --resume --dry-run`；只有正式状态、输入、工件和模型路由指纹都匹配才会复用。中断调用已消耗预算，恢复不会扩展上限。
6. 恢复后人工检查 `decision.md`，再执行 `accept` 或选择明确的合格候选。
7. `WAITING_USER` 可无新增调用地检查最佳工件；`BUDGET_EXHAUSTED` 还会分组列出安全操作与会创建新预算的操作。旧 `REPLAN` 仅可读取，不可按新版恢复。
