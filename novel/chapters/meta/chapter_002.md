# 第2章元数据

- 标题: 不止一个人能开门
- 事件: event_1
- 阶段: 日常边界
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['孙有田退开门栓后，门外没有再敲，声音却改叫张洞母亲。', '孙有田辨认出门外曾借用其亡父的私人称呼。', '孙有田已将未完成殓衣交给张洞。']
- protagonist_inventory_add: ['孙周氏未完成殓衣（暂代修补）', '母亲限借的细针、相容线与小剪']
- protagonist_inventory_remove: []
- protagonist_location: 大汉市·双桥镇·张家院外厢
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 0
- character_updates: []
- world_confirmed_add: []
- world_hypotheses_add: ['门外借声会依据同一关闭边界内仍能开门的活人转移目标。']

## 审查证据
- continuity: 延续上一章行囊被母亲扣留，并以可见状态兑现。：行囊仍锁在正屋柜中，柜门上的铜锁朝外，钥匙系在母亲腰间。
- continuity: 折断木样未无因恢复，位置连续。：折断的木样有一截还楔在正门栓槽上方
- continuity: 另一截木样保留在母亲正屋，未随张洞进入孙家。：另一截压在正屋桌角

## 静态指标
```json
{
  "char_count": 5455,
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
    "两名帮丧者",
    "孙有田",
    "张洞",
    "张洞母亲",
    "木匠老李"
  ],
  "locations": [
    "东巷孙家外院、内屋门帘外与关闭的街门内侧",
    "孙家外院至张家外厢",
    "张家院正屋、正门内侧与外厢"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_inventory_add",
    "protagonist_location",
    "timeline_year",
    "timeline_elapsed_days",
    "world_hypotheses_add"
  ],
  "hook_type": "代价展示",
  "summary": "孙家门外声音在孙有田退开后转叫张洞母亲；张洞接下未完殓衣，独自留在外厢修补并面对日落前交衣的追责。"
}
```

## 正文状态结算
```json
{
  "chapter_number": 2,
  "draft_sha256": "bb7a95255edce194d150d23b10434d913794d6e53bf1d5081088e4dd3a3f71d3",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "孙家门外声音在孙有田退开后转叫张洞母亲；张洞接下未完殓衣，独自留在外厢修补并面对日落前交衣的追责。",
    "evidence": [
      "门外没有再敲。\n\n片刻后，那声音却变了。\n\n“张家嫂子。”",
      "她若独自回来，哪怕身后还跟着脚步，张洞也不能抬栓。"
    ]
  },
  "hook": {
    "type": "代价展示",
    "content": "母亲独自去警告陈婶；她若一人返家，张洞即使看见她身后有人也不能开栓，而他还须在日落前补好殓衣。",
    "quote": "她若独自回来，哪怕身后还跟着脚步，张洞也不能抬栓。"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[0]",
      "value": "孙有田退开门栓后，门外没有再敲，声音却改叫张洞母亲。",
      "quote": "孙有田看看他们，终于把手垂下，自己往院中退了三步。\n\n张洞仍盯着他。\n\n门外没有再敲。\n\n片刻后，那声音却变了。\n\n“张家嫂子。”",
      "finding": "孙有田退开后，敲门停止，门外声音转而呼叫张洞母亲。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[1]",
      "value": "孙有田辨认出门外曾借用其亡父的私人称呼。",
      "quote": "她睡下后听见外面有人叫我。叫的是‘田伢子’，后头还咳两声。那是我爹从前的叫法。",
      "finding": "孙有田说明门外所用的“田伢子”是其父从前对他的叫法。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[2]",
      "value": "孙有田已将未完成殓衣交给张洞。",
      "quote": "孙有田看了他一眼，忽然掀开门帘，将那件未完的殓衣抱出来，往张洞怀里一送。",
      "finding": "孙有田把未完殓衣递入张洞怀中。"
    },
    {
      "path": "state_changes.protagonist_inventory_add[0]",
      "value": "孙周氏未完成殓衣（暂代修补）",
      "quote": "张洞把第二针穿过裂布，针脚慢慢追上孙周氏衣上原有的细线。",
      "finding": "张洞正持有孙周氏的衣物并进行修补。"
    },
    {
      "path": "state_changes.protagonist_inventory_add[1]",
      "value": "母亲限借的细针、相容线与小剪",
      "quote": "母亲从篮中取出一枚细针、一轴颜色最接近的旧线和那把小剪，放在床边。\n\n“针只借你这一枚。线够用，别拆错边。剪子用完还我。”",
      "finding": "母亲将细针、旧线和小剪交给张洞使用，并明确为限借。"
    },
    {
      "path": "state_changes.protagonist_location",
      "value": "大汉市·双桥镇·张家院外厢",
      "quote": "他独自坐回外厢，把第三针送进旧针眼。",
      "finding": "章末张洞独自留在张家院的外厢。"
    },
    {
      "path": "state_changes.world_hypotheses_add[0]",
      "value": "门外借声会依据同一关闭边界内仍能开门的活人转移目标。",
      "quote": "张洞这才松开麻绳。他发现孙有田也不再是唯一该盯的人。门前的几个人，谁都可能在下一句话里往前迈。",
      "finding": "张洞据声音转向母亲的现象，形成对门前其他可能开门者也会成为目标的假说。"
    }
  ],
  "missing_changes": [
    "state_changes.timeline_elapsed_days"
  ]
}
```
