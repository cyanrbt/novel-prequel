# 第3章元数据

- 标题: 少一只鞋
- 事件: event_1
- 阶段: 试探
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['镇上已有住户在听见与下葬者生前相同的三下敲门后死在门后，门无明显破损。', '门槛外一只鞋与死者脚边另一只鞋同时存在，鞋的摆放尚不能说明作用。', '李二家也听过与张家昨夜相同的敲门节奏。', '李二已将自家遭遇只告诉张洞，两人暂不向长辈坦白。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 3
- character_updates: [{'name': '李二', 'status': '与张洞共同隐瞒自家异响', 'note': '承认家中听过同样三下敲门，因害怕家人反应而未告知长辈。'}, {'name': '张洞', 'status': '获得镇上外部对照', 'note': '不再只把昨夜敲门视为张家孤例，开始记录声音、门与鞋的事实。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['门外的三下敲门可能与死者生前的节奏有关，但尚不能确定来者是否就是尸体。', '门槛外留鞋可能与事件有关，但没有证据证明它能保护住户。']

## 审查证据
- 张洞遵守父亲要求，逐项核对余家媳妇亲眼所见。：父亲要他只问亲见的人，他把每个字都说慢了些。
- 余家媳妇明确否定把门槛留鞋当成安全规则，保留证据边界。：谁告诉你鞋能挡？
- 李二以可核对的生活习惯否定醉汉猜测，形成与张家相同节奏的外部样本。：我爹回来从来走前门，怕湿布蹭墙。

## 静态指标
```json
{
  "char_count": 4373,
  "dash_count": 3,
  "dash_per_thousand": 0.69,
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
    "出事人家家属",
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "李二"
  ],
  "locations": [
    "双桥镇出事人家门前与堂屋",
    "张家院落门后",
    "染坊后巷"
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
  "summary": "将张家的夜间异响转化为可核对的镇上死亡事件，并使张洞与李二因共同隐瞒而绑定。"
}
```
