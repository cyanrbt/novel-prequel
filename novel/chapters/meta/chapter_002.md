# 第2章元数据

- 标题: 不止一个人能开门
- 事件: event_1
- 阶段: 日常边界
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['孙有田退开门栓后，门外没有再敲，声音却改叫张洞母亲。', '孙有田辨认出门外曾借用其亡父的私人称呼。', '孙有田已将未完成殓衣交给张洞。', '张洞从外厢小窗看见母亲因陈婶拒绝而独自返家。', '母亲独自返家后，张家院门外出现她的声音，并谎称陈婶与她同行。']
- protagonist_inventory_add: ['孙周氏未完成殓衣（暂代修补）', '母亲限借的细针、相容线与小剪']
- protagonist_inventory_remove: []
- protagonist_location: 大汉市·双桥镇·张家院外厢
- protagonist_body_updates: [{'key': '状态', 'value': '嘴角轻伤'}, {'key': '伤情', 'value': '在孙家阻止孙有田开门时被其手肘撞破嘴角，已有轻微出血。'}]
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 0
- character_updates: []
- world_confirmed_add: []
- world_hypotheses_add: ['门外借声会依据同一关闭边界内仍能开门的活人转移目标。', '借声者可能抢先模仿活人、利用已知危险隔断邻里互证，并伪造刚约定的开门条件。']

## 审查证据
- continuity: 行囊仍由母亲扣留，延续上一章关系后果。：行囊仍锁在正屋柜中，钥匙系在她腰间。
- continuity: 折断木样没有复原或无故易手。：半截木样仍楔在栓槽，另一截压在正屋桌角，他看得见却拿不到。
- character: 张洞阻止开门的个人代价已经当场实现。：方才挣扎时，孙有田的肘撞在张洞嘴角。张洞用舌尖碰到破口，尝见了血腥味，仍没有退开。
- craft: 章末把真实母亲、假声和既定验人规则压成同一个即时选择。：门外传来母亲的声音：“阿洞，陈婶跟我回来了。开门。”

## 静态指标
```json
{
  "char_count": 4749,
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
    "两名帮丧者",
    "孙有田",
    "张洞",
    "张洞母亲",
    "木匠老李",
    "许嫂",
    "陈婶"
  ],
  "locations": [
    "东巷孙家外院、内屋门帘外与关闭的街门内侧",
    "孙家外院至张家外厢",
    "张家院正屋、正门内侧与外厢",
    "张家外厢临街小窗内外"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_inventory_add",
    "protagonist_location",
    "protagonist_body_updates",
    "timeline_year",
    "timeline_elapsed_days",
    "world_hypotheses_add"
  ],
  "hook_type": "代价展示",
  "summary": "借声转向不同开门者并抢先冒充张洞母亲隔断邻里互证；张洞接下殓衣负伤担责，真实母亲被挡在院外。"
}
```

## 正文状态结算
```json
{
  "chapter_number": 2,
  "draft_sha256": "a8ee49b614bb757565f33ac969e0aeda01b035e731da9e86df91f028d337e079",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "借声在孙家转向不同开门者，又抢先冒充张洞母亲隔断邻里互证；张洞接下殓衣并负伤担责，章末看见真实母亲被自己的验人规则挡在院外。",
    "evidence": [
      "外面有人叫道：“田……田伢子，给爹开门。”",
      "方才挣扎时，孙有田的肘撞在张洞嘴角。",
      "门外传来母亲的声音：“阿洞，陈婶跟我回来了。开门。”"
    ]
  },
  "hook": {
    "type": "代价展示",
    "content": "真实母亲独自被挡在小窗外，院门外的同声者却谎称陈婶同行；张洞必须在不开门的前提下救回她。",
    "quote": "新铁栓横在槽里。张洞看得见母亲，却仍不能替她开门。"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[0]",
      "value": "孙有田退开门栓后，门外没有再敲，声音却改叫张洞母亲。",
      "quote": "孙有田退开后，门外没有再敲。片刻后，那声音却变了。",
      "finding": "孙有田离开栓柄后，敲击停止，声音随后转向张洞母亲。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[1]",
      "value": "孙有田辨认出门外曾借用其亡父的私人称呼。",
      "quote": "她睡下后听见外面有人叫我。叫的是‘田伢子’，后头还咳两声。那是我爹从前的叫法。",
      "finding": "孙有田说明门外所用的称呼与咳嗽来自其亡父。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[2]",
      "value": "孙有田已将未完成殓衣交给张洞。",
      "quote": "孙有田看了他一眼，掀开门帘将殓衣抱出来，往张洞怀里一送。",
      "finding": "孙有田明确把未完成殓衣交给张洞。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[3]",
      "value": "张洞从外厢小窗看见母亲因陈婶拒绝而独自返家。",
      "quote": "母亲独自站在窗下，针线篮还挎在臂上。“别抬栓。”她先说。",
      "finding": "张洞从小窗看见母亲独自返回。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[4]",
      "value": "母亲独自返家后，张家院门外出现她的声音，并谎称陈婶与她同行。",
      "quote": "门外传来母亲的声音：“阿洞，陈婶跟我回来了。开门。”",
      "finding": "母亲独返后，院门外同声者谎称陈婶同行。"
    },
    {
      "path": "state_changes.protagonist_inventory_add[0]",
      "value": "孙周氏未完成殓衣（暂代修补）",
      "quote": "张洞摊开殓衣。",
      "finding": "张洞持有孙周氏殓衣并开始修补。"
    },
    {
      "path": "state_changes.protagonist_inventory_add[1]",
      "value": "母亲限借的细针、相容线与小剪",
      "quote": "母亲从篮中取出一枚细针、一轴颜色最接近的旧线和那把小剪，放在床边。",
      "finding": "母亲把细针、旧线和小剪交给张洞限时使用。"
    },
    {
      "path": "state_changes.protagonist_location",
      "value": "大汉市·双桥镇·张家院外厢",
      "quote": "他独自坐回外厢，把第三针送进旧针眼。",
      "finding": "章末张洞仍在张家外厢。"
    },
    {
      "path": "state_changes.protagonist_body_updates[0]",
      "value": {"key": "状态", "value": "嘴角轻伤"},
      "quote": "方才挣扎时，孙有田的肘撞在张洞嘴角。张洞用舌尖碰到破口，尝见了血腥味，仍没有退开。",
      "finding": "张洞的嘴角已被撞破并出血。"
    },
    {
      "path": "state_changes.protagonist_body_updates[1]",
      "value": {"key": "伤情", "value": "在孙家阻止孙有田开门时被其手肘撞破嘴角，已有轻微出血。"},
      "quote": "方才挣扎时，孙有田的肘撞在张洞嘴角。张洞用舌尖碰到破口，尝见了血腥味，仍没有退开。",
      "finding": "正文说明轻伤来源、位置和出血状态。"
    },
    {
      "path": "state_changes.world_hypotheses_add[0]",
      "value": "门外借声会依据同一关闭边界内仍能开门的活人转移目标。",
      "quote": "孙有田退开后，门外没有再敲。片刻后，那声音却变了。\n\n“张家嫂子。”",
      "finding": "第一名开门者退开后，声音转向另一名能开门者。"
    },
    {
      "path": "state_changes.world_hypotheses_add[1]",
      "value": "借声者可能抢先模仿活人、利用已知危险隔断邻里互证，并伪造刚约定的开门条件。",
      "quote": "她说我到以前，已有个张嫂子在后门叫她，还说前门有死人声，千万别开。",
      "finding": "母亲抵达前，同声者已利用借声危险阻断陈婶开门。"
    }
  ],
  "missing_changes": [
    "state_changes.timeline_elapsed_days"
  ]
}
```
