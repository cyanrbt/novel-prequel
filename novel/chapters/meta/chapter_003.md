# 第3章元数据

- 标题: 少一只鞋
- 事件: event_1
- 阶段: 征兆
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['张洞确认镇上出事人家的门没有明显破损，门槛外与死者脚边各有一只鞋。', '张洞得知死者家属将敲门节奏认作下葬者生前的节奏。', '张洞确认李二家也曾听过与张家相同的三下敲门声。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 3
- character_updates: [{'name': '李二', 'status': '与张洞形成共同隐瞒', 'note': '他承认自家也听过相同敲门声，并与张洞约定暂不告知大人。'}]
- world_confirmed_add: ['出事人家的院门无明显破损，门槛外一只鞋与死者脚边一只鞋同时存在。', '张家与李二家所闻敲门节奏相同。']
- world_hypotheses_add: ['敲门声可能会模仿近期下葬者生前的节奏，但目前只有死者家属的辨认，尚不能确定来源。']

## 审查证据
- continuity: 现场鞋位与章节规划一致，形成可核对的门外事实。：门槛外摆着一只黑布鞋。
- continuity: 共同隐瞒被明确落实，承接上一章张家所闻三下敲门声。：谁也不提两家听见过同样的敲门声
- continuity: 对“靠近门”的推断及时保留为猜测，未把鞋或敲门写成已证实规则。：这只是猜的。

## 静态指标
```json
{
  "char_count": 4698,
  "dash_count": 0,
  "dash_per_thousand": 0.0,
  "negation_template_count": 1,
  "action_counts": {
    "停步不回头": 0,
    "折纸入怀": 0,
    "守灯到天亮": 0,
    "渡口扔石": 0,
    "眼光变暗": 0
  }
}
```

## Memory Record
```json
{
  "characters": [
    "张洞",
    "李二",
    "死者家属"
  ],
  "locations": [
    "大汉市·双桥镇·出事人家门外与堂屋",
    "大汉市·双桥镇·回张家途中僻巷"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_confirmed_add",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "把镇上传言转化为可核对的死亡现场，并使张洞与李二因共同隐瞒而形成无法撤回的关系。"
}
```
