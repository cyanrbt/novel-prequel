# 场景生成机制基准：第一章门内外对峙

本基准从正式第一章最后一句之后继续，但所有结果仅用于隔离实验，不是第二章草稿，也不得写入正式状态。

## 比较条件

- `contract_first`：完整事件链先于正文确定；
- `simulation_fixed`：有限认知角色行动经世界结算后，继续使用冻结短期压力；
- `simulation_rolling`：与固定路线共享第一 tick，之后按已结算结果修订短期压力。

三组共用 `scene_packet.json`、正文模型、长度、文风画像和偏好合同。角色模拟工件必须隔离；Event Renderer 只读取确定性 POV 轨迹。

执行和停止条件见 [`workflows/scene-generation-experiment.md`](../../../workflows/scene-generation-experiment.md)。正式结论只记录用户对匿名 A/B/C 的选择；自动 Reader 不拥有决定权。
