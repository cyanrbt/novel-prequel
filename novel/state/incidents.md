# 事件记录（异常事件）

> 本文件记录所有异常事件，用于调试和改进

## 事件列表

（当前为空）

---

## 事件类型
- TOKEN_EXCEEDED: Token超限
- INTERRUPT: 中断
- FILE_CORRUPTED: 文件损坏
- INFINITE_LOOP: 无限循环
- SETTING_VIOLATION: 设定违反
- STYLE_DRIFT: 风格漂移
- FORESHADOW_CONFLICT: 伏笔冲突

## 事件格式
```
## [事件类型] [时间]
- 章节: N
- 描述: [事件描述]
- 处理: [处理方式]
- 结果: [处理结果]
```
