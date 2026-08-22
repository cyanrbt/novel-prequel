# 多 Provider 同提示词文风盲测（2026-08-22）

## 目的

用同一段经典剧情、同一份逐字一致的试写提示词，对九个候选模型做一次性生成，再由四个不同模型进行匿名交叉评分。测试重点是中文商业网文正文能力，尤其是群像对话、恐怖递进、规则推理、未经加工的网络生命力和反 AI 模板能力。

本轮不是统计学意义上的完整模型排名。每个候选只有一个样本，因此结论只用于决定下一轮创作路由和是否值得继续扩大样本。

## 场景与防泄漏

- 场景：晚自习教室中敲门老人首次现身，范围止于周正命令学生逃走。
- 选择原因：这是群像、推理、空间动作和灵异恐怖同时存在的经典段落，且没有被此前三轮风格校准直接试写。
- 提示词只保留剧情事实和人物知识边界，不包含原文句段。
- 所有 CLI 均在 `/tmp` 下各自的空隔离目录运行，不能读取仓库中的原著文本、既有标杆或其他候选。
- 原始输出按模型实名保存；评审只收到固定的 A—I 匿名副本。

## 冻结材料

- `trial_prompt.md`：九个候选逐字一致接收的唯一试写提示。
- `scoring_rubric.md`：七维 100 分制、硬伤标签和按量模型采用门槛。
- `candidates.json`：实际 provider、模型与推理强度。
- `prompt.sha256` 与 `rubric.sha256`：首次调用前写入的冻结哈希。

## 调用策略

- 九个候选均为 one-shot；不因文风不佳而重抽。
- 只有明确的传输失败或 CLI 故障才允许重试，并在运行元数据中保留失败记录。
- DeepSeek V4 Flash 与 MiMo V2.5 各调用一次，且不作为评审，避免无必要的按量开销。
- Claude Sonnet 4.6 在当前 AGY 模型目录中没有独立 `thinking` slug，本轮使用 `claude-sonnet-4-6` 加 `high` effort，报告中不会把它冒充成可验证的独立 thinking 型号。
- Luna 的 `max` 已被当前 Codex CLI 模型目录声明支持；基准脚本直接构造隔离调用，不修改生产路由的现有 allowlist。

## 评审与汇总

四名评审分别为 Sol xhigh、Gemini 3.1 Pro high、Claude Opus 4.6 Thinking high、Grok 4.6 xhigh。它们接收完全相同的候选顺序、评分规程和 JSON 输出要求。最终报告同时展示：

1. 各维度四评审均值；
2. 总分均值、评审离散度和前二票数；
3. 硬伤标签分布；
4. 按量模型是否跨过预先冻结的“明显优势”门槛；
5. 单样本不确定性与下一步建议。

## 复现命令

```bash
python3 scripts/provider_style_benchmark.py list
python3 scripts/provider_style_benchmark.py generate --candidate <candidate_id>
python3 scripts/provider_style_benchmark.py prepare-blind
python3 scripts/provider_style_benchmark.py judge --judge <judge_id>
python3 scripts/provider_style_benchmark.py aggregate
```

脚本默认拒绝覆盖既有原始输出，避免不小心把一次性结果重抽。
