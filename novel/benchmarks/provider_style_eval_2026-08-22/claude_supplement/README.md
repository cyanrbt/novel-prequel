# Claude 修复后补盲测

## 目的与边界

首次九模型基准中，Claude Opus 4.6 Thinking 与 Claude Sonnet 4.6 Thinking 因 AGY 错误附加 `--effort high` 而连续空响应。修复提交 `b2d8fca` 将 Claude 的推理强度标记为 `none`，并在调用时省略 `--effort`。

本补测不覆盖首轮工件，也不把修复后的样本伪装成首轮同步结果。执行方式为：

1. 两款 Claude 各使用原冻结提示生成一次有效正文；
2. 其余七篇逐字复用首轮原始输出，不再次生成；
3. 九篇使用新种子整体重新洗牌为 A—I；
4. Sol xhigh、Luna max、Gemini 3.1 Pro high、Grok 4.6 xhigh 接收同一份九候选评审提示并重新评分；
5. DeepSeek V4 Flash 与 MiMo V2.5 不产生新的按量生成调用。

## 冻结材料

- 试写提示：父目录 `trial_prompt.md`，SHA-256 `57183b1e13b0b1fdd2018dab2336e0576a5acbe13e715d976bfa388428f0c4c3`。
- 评分规程：父目录 `scoring_rubric.md`，SHA-256 `69011d4643870c5ee06da7beab9230beb2a3c2a9af0a8a646bee3d70e04a877b`。
- 原有七篇的 `output_sha256` 必须与首轮 `raw/` 完全一致。
- Claude smoke test 不计入正式样本；补测目录中的正文才是修复后的首次正式有效生成。

## 命令

```bash
python3 scripts/provider_style_benchmark_supplement.py list
python3 scripts/provider_style_benchmark_supplement.py generate --candidate agy_claude_opus_4_6_thinking
python3 scripts/provider_style_benchmark_supplement.py generate --candidate agy_claude_sonnet_4_6_thinking
python3 scripts/provider_style_benchmark_supplement.py prepare-blind
python3 scripts/provider_style_benchmark_supplement.py judge --judge <judge_id>
python3 scripts/provider_style_benchmark_supplement.py aggregate
```
