# 第7章元数据

- 标题: 七口棺，十一颗钉
- 事件: event_1
- 阶段: 试探
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['修棺旧单记载七口旧棺、十一颗棺钉。', '祠堂地下从通气格可见的棺角只有七处，多出的四颗棺钉暂无对应。', '叔公封死通气格，张洞失去安全观察祠堂地下的入口。']
- protagonist_inventory_add: ['修棺旧单（记载七口旧棺、十一颗棺钉）']
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 7
- character_updates: [{'name': '张洞父亲', 'status': '持有并核对修棺旧单', 'note': '与张洞确认祠堂可见棺数无法对应旧单钉数。'}, {'name': '李二', 'status': '被张家叔公赶出张家范围', 'note': '失去与张洞一同从祠堂通气格观察的机会。'}, {'name': '张家叔公', 'status': '封死祠堂通气格', 'note': '以补格和驱赶李二中断张洞对祠堂地下的观察。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['祠堂地下可能存在未能从通气格看见的更深结构，以对应多出的四颗棺钉；现有观察不足以确认。']

## 审查证据
- 旧账以具体用物建立可复查的七棺十一钉数量矛盾。：旧棺七口，补漆二斤。
- 李二提出反例，张洞仍将七粒与四粒豆子分开，守住观察与推测的边界。：看不见的不算，话是这么说。
- 叔公封死通气格，张洞失去观察口，事件资源发生不可逆变化。：该修的地方，我会修。

## 静态指标
```json
{
  "char_count": 3674,
  "dash_count": 1,
  "dash_per_thousand": 0.27,
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
    "张洞父亲",
    "李二"
  ],
  "locations": [
    "张家院落·祭田账册整理处",
    "张氏祠堂外·通气格",
    "张氏祠堂外·通气格与张家院外"
  ],
  "event_id": "event_1",
  "foreshadows": [
    "F-A03"
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
  "summary": "以父子取得的修棺旧单和祠堂下可见棺角的对照，建立无法用现有常识解释的数量矛盾，并令张洞失去唯一安全观"
}
```
