# 文风校准工作流

## 目的

用同一事实场景的三份独立改写和用户盲选，校准 `novel/style/reference_voice_profile.md`。本流程只在 `novel/work/style-calibration/` 写入可恢复工件，不直接修改正式章节、正式状态或审核绑定。

## 前置条件

1. `reference_voice_profile.md`、`agents/prose_director.md`、`agents/reference_style_reviewer.md` 和 `schemas/style_comparison.schema.json` 均存在。
2. 选取的源场景来自当前正式章节，范围约800—1200个汉字，并以起止原文和 SHA-256 绑定。
3. 主 Agent 先建立 `fact_lock`：人物、时地、已发生事件、知识边界、关键物件状态、选择、代价与场景结束状态。
4. 源场景之外的参考材料不得写入任务信封、候选、比较报告或公开仓库；只允许使用已归纳的高层文风画像。

## 阶段一：冻结源场景

建立 `scene_packet.json`，至少包含：

- `calibration_id`；
- 正式章节路径、章节 SHA-256、场景起止原文与场景 SHA-256；
- `fact_lock` 和 `continuity_lock`；
- 当前 `voice_profile` 与用户偏好合同哈希；
- 三种目标策略及统一输出边界。

场景、事实锁或画像发生变化后，旧候选与旧比较报告全部失效。

## 阶段二：三候选独立改写

分别向三个隔离任务提供相同 `source_draft`、事实锁、连续性锁、文风画像和偏好合同，只改变 `target_strategy`：

1. `plain_cold_narration`：朴素冷叙，减少修辞和解释；
2. `character_interest_filter`：让人物利益、羞耻和误判过滤注意力；
3. `ordinary_life_intrusion`：保留日常与关系自身重量，让异常侵入其中。

每项任务读取 `agents/prose_director.md`。支持 sub-agent 时可以并行；否则主 Agent 顺序执行，但三项输入必须事先冻结，候选之间不得相互读取。输出保存为策略名文件，盲评前由主 Agent 随机映射为 A、B、C，并单独保存映射；Reviewer 和用户不得看到映射。

## 阶段三：相对盲评

向 `agents/reference_style_reviewer.md` 只提供：

- `calibration_id`、源场景指纹；
- `voice_profile`；
- 随机化后的 A、B、C 正文及各自指纹；
- `schemas/style_comparison.schema.json`。

输出必须符合比较 Schema，且满足：

- `ranking` 恰好包含 A、B、C 各一次；
- `preferred_candidate` 等于 `ranking[0]`；
- `candidate_findings` 恰好覆盖 A、B、C 各一次；
- 所有引用逐字存在于对应候选；
- 候选指纹与当前文本一致。

相对盲评只能提供诊断，不能代替用户选择，也不能单独把画像状态改为 `READY`。

## 阶段四：用户盲选

向用户展示去除策略和模型信息后的 A、B、C，优先询问：

1. 最愿意继续读哪一版；
2. 从哪一句开始明显出戏；
3. 哪一版最像人物正在经历事情，而不是作者在兑现提纲。

保存用户原话、选择、候选指纹和当时的画像哈希。不得把 Reviewer 排名冒充用户意见。

## 阶段五：更新画像

只有用户明确确认的偏好才能成为新的强约束。单次候选偶然出现的措辞、句长或意象不得直接固化为模板。更新时记录：

- 新增、修改或删除的画像条目；
- 支持它的用户原话与候选引用；
- 是否只适用于当前场景；
- 新旧画像 SHA-256。

至少完成一轮有效盲选后，可以把 `calibration_status` 从 `CALIBRATING` 改为 `READY`。如果用户认为三稿都明显不合格，保持 `CALIBRATING` 并再做一轮，不得勉强选优。

## 阶段六：整章验证

画像为 `READY` 后，使用 Prose Director 重写完整第1章候选，但仍停在 `novel/work/`。整章必须重新通过现有确定性检查、语义审查、盲读和状态证据结算；通过 `accept-candidate.md` 前不得覆盖正式第1章。

## 停止条件

- 源场景、正式章节、偏好合同或画像哈希发生变化：`BLOCKED`，重新冻结输入。
- 任一候选改变 `fact_lock`：该候选无效，不得送审或自动补写第四稿。
- 三候选未全部完成：保存现有工件并返回 `BLOCKED`。
- 等待用户盲选：返回 `BLOCKED`，并在 `artifact.workflow_state` 标记 `WAITING_USER`。
- 不具备写文件能力：返回任务与候选内容，等待具备权限的主 Agent 落盘。
