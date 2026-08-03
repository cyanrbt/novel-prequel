# 第6章元数据

- 标题: 被墨吃掉的人
- 事件: event_1
- 阶段: 试探
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['叔公主动隐瞒过近年家族死亡，而非单纯不知门外异响的来历。', '族谱被涂名者中至少一人是张洞幼年认识的近年族人。', '被涂名旁的年份可被父子私下保存，但姓名仍不完整。']
- protagonist_inventory_add: ['被涂名相邻年份的私抄记录']
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 6
- character_updates: [{'name': '张洞父亲', 'status': '与张洞私下抄录残存年份', 'note': '拒绝再让叔公独占家族记录，开始保存可追查的删改证据。'}, {'name': '张洞母亲', 'status': '确认被抹者涉及近年熟人', 'note': '认出曾给张洞做木摇车的人，进一步失去对叔公沉默的信任。'}, {'name': '张家叔公', 'status': '收走族谱并继续隐瞒', 'note': '以“不算在家里”否认被涂者的家族位置，拒绝解释死亡原因。'}, {'name': '张洞', 'status': '与父亲共同保存删改年份', 'note': '知道叔公曾主动抹除近年死亡记录。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['被涂黑的姓名可能与祠堂内反复修补的地砖有关，但目前仅有数量上的对应线索。']

## 审查证据
- 叔公主动切断被涂名者与家族的身份关联，构成核心隐瞒。：这些人不算在家里。
- 父亲为张洞确立证据边界，避免人物用猜测补全真相。：能记多少留多少，别补自己没看见的。
- 母亲辨认出阿满的旧日生活痕迹，使被涂名字落到具体人物与家庭记忆。：说是出远门。

## 静态指标
```json
{
  "char_count": 4530,
  "dash_count": 0,
  "dash_per_thousand": 0.0,
  "negation_template_count": 0,
  "action_counts": {
    "停步不回头": 0,
    "折纸入怀": 0,
    "守灯到天亮": 0,
    "渡口扔石": 0,
    "眼光变暗": 0
  },
  "length_policy": {
    "safe_min": 2500,
    "target_min": 3200,
    "target_max": 5000,
    "safe_max": 8000
  }
}
```

## Memory Record
```json
{
  "characters": [
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲"
  ],
  "locations": [
    "张家院落·堂屋",
    "张家院落·堂屋案前",
    "张家院落·灶间"
  ],
  "event_id": "event_1",
  "foreshadows": [
    "F-A02"
  ],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_inventory_add",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "张洞通过父亲查族谱，确认叔公在主动抹除近年死者，并与父亲建立私下保存证据的共同选择。"
}
```
