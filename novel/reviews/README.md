# 小说试读工作区

本目录保存 AI 与真人共同形成的章节阅读记录和最终反馈。项目提供两种不同用途的试读方式，不得互相替代。

## 方式一：逐段共读（默认的人机协作方式）

适用于用户希望与 AI 按正文顺序阅读、交换真实感受并共同形成反馈的场景。项目技能位于：

- `.agents/skills/novel-close-reading/SKILL.md`

新会话可直接说：

> 使用 `$novel-close-reading`，和我按顺序逐阅读单元共读第一章。你先说感受，我再说；讨论稳定后再继续，读完全章后把联合反馈保存到本地。

固定循环为：

1. AI 选择一个自然阅读单元并先给初读感受；
2. 真人给出自己的实际感受；
3. 双方核对原文、交流并形成暂定结论；
4. 结论写入同一份章级工作记录；
5. 真人确认继续后再进入下一单元；
6. 全章结束后共同汇总，确认后保存最终文档；
7. 创作 agent 根据反馈自行评估、重构或修改。

逐段共读的模板随技能提供：

- `.agents/skills/novel-close-reading/assets/close-reading-record.md`

建议输出为 `reviews/chapter_NNN_close_reading.md`。

## 方式二：整章独立盲读（可选发布门禁）

适用于候选正文完成后，让 AI 与陌生真人互不提示地验证整章可读性。它不能取代逐段共读。

- `trial_reading_protocol.md`：整章独立盲读、反证和重测流程。
- `templates/chapter_trial_read.md`：整章联合试读模板。

正式章节可在项目根目录运行：

```bash
python3 scripts/orchestrator.py reader-review --chapter N
```

自动报告保存在 `novel/work/reader_reviews/`，并绑定正文 SHA-256。正文变化后旧报告失效。

## 现有反馈

- `chapter_001_reader_feedback.md`：失败样本《渡船不开》的联合反馈，也是逻辑、空间、人物行为与异常规则的回归用例。

## 共同原则

- AI 与真人都不需要承诺找出所有问题。
- AI 不得用类型套路、作者意图或后文信息替正文补洞。
- 真人的实际感受是阅读效果证据，不需要被理论分析“纠正”。
- 达到 `REPLAN` 后可以停止穷举局部病句，但是否继续读完全章由用户决定。
- 最终反馈记录阅读效果、正文证据、问题诊断和验收条件，不替创作 agent 逐句代写。
