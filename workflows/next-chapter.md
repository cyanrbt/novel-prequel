# 下一章工作流

## 前置条件

先完整执行 [`status-check.md`](status-check.md)。只有结果为 `READY`，且 `reference_voice_profile.md` 的 `calibration_status` 为 `READY`，才能继续。

## 任务图

1. **Planner**
   - 读取 `agents/planner.md`。
   - 只接收规划上下文工件。
   - 输出必须符合 `schemas/plan.schema.json`。
2. **Writer 候选**
   - 每个执行者读取 `agents/writer.md`。
   - 每个执行者接收已校准的 `reference_voice_profile.md`，不接收未筛选的参考原文、完整审查规则或旧式模仿锚点。
   - 候选之间不得相互读取结果。
   - 支持 sub-agent 时可以并行；否则顺序生成，但输入必须冻结且一致。
   - 只输出正文，不读写项目文件。
3. **确定性正文检查**
   - 检查长度、禁入项、重复、时间与硬约束。
   - 硬失败候选不得进入语义审查。
4. **集成初筛与条件专项审查**
   - 集成初筛读取 `agents/reviewer_integrated.md`。
   - 专项审查分别读取对应 `agents/reviewer_*.md`。
   - 互不依赖的审查可并行；审查者不得修改正文。
5. **选择与一次定向修订**
   - 只有候选接近时才使用 `agents/selector.md`。
   - 修订必须保留原稿、修订指令和差分验证结果。
6. **无大纲盲读**
   - 读取 `agents/reader_reviewer.md`。
   - 不得接收规划、隐藏诊断或作者意图。
7. **状态证据结算**
   - 读取 `agents/state_settler.md`。
   - 所有状态变化必须引用最终正文逐字证据。
8. **停止或提升**
   - dry-run、门禁未通过、证据不足或权限不足时停在 `WAITING_USER`。
   - 只有全部门禁有效且用户允许自动提升时，才可进入 `accept-candidate.md`。

## 工件要求

每个任务保存：任务信封、原始输出、规范化工件、输入指纹、输出指纹、验证结果和执行状态。主 Agent不得把对话摘要当作正式工件。

## 跨平台原则

- sub-agent、线程池和顺序执行必须产生相同逻辑工件。
- Agent 或模型选择只影响执行记录，不得写入故事状态。
- 某平台不支持指定模型、思考强度或 JSON Schema 时，必须显式降级并记录，不能静默忽略。
