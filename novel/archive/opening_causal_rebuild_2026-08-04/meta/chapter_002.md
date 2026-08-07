# 第2章元数据

- 标题: 不开的舱门
- 事件: event_1
- 阶段: 逃离受阻
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['张洞取得一条可复核记录：封闭且受看守的底舱门后，仍有声音并能推出物件。', '渡船停航造成的借宿争夺已使一家人失去一处当夜退路。', '祠堂方向出现过与底舱相同的三下敲击。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: 大汉市·双桥镇·季三渡口
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 1
- character_updates: [{'name': '张洞', 'status': '掌握首条异常查验记录', 'note': '选择留在渡船完成查验，错过一处当夜借宿。'}, {'name': '张洞母亲', 'status': '当夜落脚受阻', 'note': '优先争取借宿未果，与张洞继续查验的选择形成实际冲突。'}, {'name': '张洞父亲', 'status': '参与查验', 'note': '要求只按可见事实记录，仍主张另寻现实离镇路线。'}, {'name': '季三', 'status': '继续停船并保留底舱', 'note': '拒绝开舱，也同意在众人见证下核对舱门与货物。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['封闭底舱中的异常可能不依赖打开舱门便能将物件送到门外；现有样本不足以说明其条件。', '渡船底舱异响与祠堂方向的三下敲击可能相关，但尚无证据证明它们来自同一异常。']

## 审查证据
- continuity: 查验前的可见状态被明确记录，且未将门后状况伪作已知事实。：门关着，插销在外，麻绳四圈，绳结没有松。
- continuity: 异常记录限定在五名看守者可复核的观察范围内，门、插销、绳结与纸张位移均有对应事实。：门没见开。插销没见动。绳没见松。我们五个从放木牌起一直在这里。门后有声音，纸向外多了一寸三分。
- continuity: 父亲限制声音身份的结论等级，避免将模仿声音提前确认为阿顺本人或异常本体。：你只能记像。

## 静态指标
```json
{
  "char_count": 4582,
  "dash_count": 1,
  "dash_per_thousand": 0.22,
  "negation_template_count": 1,
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
    "刘婶",
    "季三",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "阿顺"
  ],
  "locations": [
    "双桥镇渡口外的借宿铺与祠堂方向街巷",
    "季三渡船客舱与底舱门前",
    "季三渡船底舱门前"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_location",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "张洞以失去当夜借宿机会为代价，把渡船异响从传言变成可复核记录，并将一家人的逃离分歧推入现实选择。"
}
```
