# 第4章元数据

- 标题: 好栓
- 事件: event_1
- 阶段: 试探
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['铁门栓只能增加主动开门的难度，不能阻止住户因门外动静自行拔栓。', '判断门栓作用时，必须记录住户开门前听见的内容及其开门理由。', '东巷死者家的门板内侧有纸灰。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 4
- character_updates: [{'name': '张洞', 'status': '开始记录开门前的逼迫条件', 'note': '已放弃把铁门栓当作可直接挡住异常的简单结论。'}, {'name': '张洞父亲', 'status': '质疑铁门栓的效用', 'note': '要求叔公为暗中订栓的理由提供事实，而非继续以沉默结束谈话。'}, {'name': '张家叔公', 'status': '继续隐瞒旧事', 'note': '面对反例只追问死者是否听见名字，未说明缘由。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['门外的声音可能会利用住户在现实上无法置之不理的内容，逼其主动开门；尚无足够样本确认。']

## 审查证据
- 铁栓被限定为拖慢人为开门，而非压制异常。：铁栓紧，他拔了两次，第二次才抽出来。
- 死者开门出于具体亲情恐惧，异常逼迫落在现实选择上。：他怕孩子掉河里。
- 张洞没有把门栓当解法；对幸存者只能给出有限且残酷的事实。：好栓。

## 静态指标
```json
{
  "char_count": 5038,
  "dash_count": 0,
  "dash_per_thousand": 0.0,
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
    "出事人家家属",
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "木匠老李",
    "李二"
  ],
  "locations": [
    "双桥镇·东巷出事人家门后",
    "双桥镇·木匠铺",
    "张家院落·堂屋"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "以一户装有完好铁栓仍因住户自行开门而死的反例，迫使张洞放弃把铁器当作安全手段，并开始记录开门前的逼迫"
}
```
