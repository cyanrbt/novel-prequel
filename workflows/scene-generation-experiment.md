# 场景生成机制隔离实验

## 目的

在事实、模型、文风、长度和起始状态一致的条件下，比较三种故事生成机制：

1. `contract_first`：先决定完整事件链与结果，再写正文；
2. `simulation_fixed`：角色按有限认知行动，世界结算后按冻结的短期压力继续；
3. `simulation_rolling`：与第二组共享第一 tick，结算后依据实际结果修订短期压力，再继续行动与结算。

本流程只写入当前项目 `work_dir/scene-experiments/` 或显式指定的基准目录，不修改正式章节、正式状态、审核绑定和文风画像。

## 公平性约束

- 三组必须绑定同一个 `scene_packet.json` 指纹。
- 使用同一 Writer 模型与推理强度；结构化角色也应使用同一模型档案，除非运行记录明确标注降级。
- 三组接收相同的开场公开事实、世界规则、禁入结果、正向文风画像、偏好合同和正文长度。
- 候选正文不得互相读取。盲评前不得显示路线、模型或文件名。
- 自动 Reader 只诊断；用户盲选才是实验结论。

## 阶段一：冻结输入

建立符合 `schemas/scene_experiment.schema.json` 的 `scene_packet.json`。角色私有卡可以共存于冻结工件中，但每个 Character Actor 任务只能收到自己的卡；基线 Planner、Rolling Planner 和 Event Renderer 都不得读取这些私有卡。

至少绑定：当前状态、源章节、正向文风画像和偏好合同的 SHA-256。任一来源变化后，既有结果失效，不得与新结果混评。

## 阶段二：控制组 `contract_first`

1. `agents/scene_contract_planner.md` 只读取公开种子，输出 `contract_scene_plan.schema.json`。
2. `agents/scene_contract_writer.md` 使用与其他组相同的正文模型，根据公开种子、完整计划、画像和偏好合同生成正文。
3. Writer 不得读取其他路线工件。

这组应被认真优化，不能故意制造模板感来证明实验假设。

## 阶段三：共享第一 tick

1. 为每个参与角色建立独立任务，只提供该角色卡和可见事实，执行 `agents/character_actor.md`。
2. 支持并行时可以并行；否则顺序执行，但后执行者不得读取先执行者结果。
3. 主 Agent 验证所有 `character_intention` 的角色、tick、源指纹和事实引用。
4. `agents/world_resolver.md` 读取全部已验证意图并输出第一 tick 的 `world_resolution`。
5. 确定性工具从 `observable_by` 生成 POV 轨迹，必须删除 `hidden_cause` 与 POV 不可见事件。

## 阶段四：固定与滚动分叉

### `simulation_fixed`

沿冻结的第二个 `initial_horizon` 压力，向各角色发送第一 tick 后各自可见的新事实；再次独立生成意图并结算。不得因为第一 tick 已改变条件而重写节拍措辞，只有角色可以在行动上拒绝或绕开它。

### `simulation_rolling`

1. `agents/rolling_scene_planner.md` 根据第一 tick 结算和 POV 轨迹修订接下来一至三个压力节拍。
2. 把修订后的当前压力分别发送给各角色，再独立生成第二 tick 意图并结算。
3. Rolling Planner 只能提出压力问题，不能替角色指定动作或替 Resolver 指定结果。

## 阶段五：事件成稿

两条模拟路线分别把两个 tick 的 POV 轨迹交给 `agents/event_renderer.md`。Renderer 不得读取私有卡、完整世界结算或隐藏原因。

控制组与两条模拟路线的正文长度必须位于同一边界。确定性检查只淘汰事实越界、正文为空、长度越界和提示泄漏，不按“更文学”自动选胜者。

## 阶段六：匿名化与盲评

1. 将三份正文以冻结随机种子映射为 A、B、C，保存私有 `blind_mapping.json`。
2. 面向 Reader 和用户的 `blind_packet.json` 只包含 A、B、C、正文指纹和问题，不含路线。
3. 可选执行 `agents/scene_experiment_reader.md`，报告必须符合 `scene_experiment_comparison.schema.json` 并通过逐字引用验证。
4. 向用户优先询问：
   - 最愿意继续读哪一版；
   - 从哪一句开始明显像 AI；
   - 哪个关键行动最像只能由这个人物作出；
   - 哪一版最像事情真的发生了，而不是作者兑现提纲。

## 停止条件

- 来源哈希变化、角色意图越过知识边界、Resolver 确认未确认真相、POV 轨迹泄漏隐藏原因：`BLOCKED`。
- 任一路线缺少有效正文：保留已有工件，不生成替代条件或第四候选。
- 三候选准备完毕：状态为 `WAITING_USER`，不得自动更新正典或画像。
- 用户认为三稿均不合格：记录为有效的“全部失败”结果，不强行选优。
