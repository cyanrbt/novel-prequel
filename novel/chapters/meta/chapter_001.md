# 第1章元数据

- 标题: 门上的灰
- 事件: event_1
- 阶段: 日常边界
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['本班直达大汉市的十二块客牌已发尽，张洞因未本人到场失去木行引荐对应的客位。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 0
- character_updates: []
- world_confirmed_add: []
- world_hypotheses_add: []

## 审查证据
- continuity: 正文保留活人冒充等普通解释，未将门外声音直接定论为超自然。：可这仍不能证明外面没有一个知道孙家口音、又听过张家行程的活人。
- continuity: 木样损毁与栓槽被卡住均有可见动作和连续结果。：木样从榫肩处折开，一半楔在槽里，一半掉到地上。
- continuity: 张洞错过本班客牌的不可逆结果已在正文兑现。：十二块都发了。

## 静态指标
```json
{
  "char_count": 4910,
  "dash_count": 1,
  "dash_per_thousand": 0.2,
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
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "李二"
  ],
  "locations": [
    "大汉市双桥镇·张家院正屋与正门内侧",
    "大汉市双桥镇·张家院正屋，渡口发牌后",
    "大汉市双桥镇·张家院正门内外"
  ],
  "event_id": "event_1",
  "foreshadows": [
    "F-A01"
  ],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "timeline_year",
    "timeline_elapsed_days"
  ],
  "hook_type": "代价展示",
  "summary": "张洞发现倒扣饭碗中出现纸灰，阻止母亲为门外冒充孙周氏的声音开门，并毁坏木样错失渡船客牌，母子因此决裂"
}
```

## 正文状态结算
```json
{
  "chapter_number": 1,
  "draft_sha256": "a4578c988e7a7920b0c8967f28985e23475dbaaa042b040662f9a10ff04fde3b",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "张洞发现倒扣饭碗中出现纸灰，阻止母亲为门外冒充孙周氏的声音开门，并毁坏木样错失渡船客牌，母子因此决裂。",
    "evidence": [
      "米饭中央落着一撮灰。",
      "木样从榫肩处折开，一半楔在槽里，一半掉到地上。",
      "行囊已经不在他手上，客牌也发完了。"
    ]
  },
  "hook": {
    "type": "代价展示",
    "content": "张洞为守住门而失去行囊与客牌，母亲更将是否让他继续住下作为天亮后的问题。",
    "quote": "天亮后，先说你还该不该住在这里。"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[3]",
      "value": "本班直达大汉市的十二块客牌已发尽，张洞因未本人到场失去木行引荐对应的客位。",
      "quote": "十二块都发了。最后一块给了抱孩子的，那孩子也占一个名额。木行留给学徒的引荐位，有人带样子顶上了。登记的人说，你本人没到，他们等过一阵，没法再留。",
      "finding": "李二带回的消息直接确认客牌发尽、张洞未到场，以及木行引荐位已被顶替。"
    },
    {
      "path": "foreshadow_operations.plant[0]",
      "value": "F-A01",
      "quote": "米饭中央落着一撮灰。",
      "finding": "倒扣饭碗中的异常纸灰已在正文实际出现。"
    }
  ],
  "missing_changes": [
    "state_changes.protagonist_known_info_add[0]",
    "state_changes.protagonist_known_info_add[1]",
    "state_changes.protagonist_known_info_add[2]",
    "state_changes.timeline_elapsed_days",
    "state_changes.character_updates[0]",
    "state_changes.character_updates[1]",
    "state_changes.character_updates[2]",
    "state_changes.world_hypotheses_add[0]"
  ]
}
```
