# 第4章元数据

- 标题: 好栓
- 事件: event_1
- 阶段: 征兆
- 审查: PASS / A

## 不可逆变化

- protagonist_known_info_add: ['张洞确认赵家铁门栓和门扇均完整，赵老大仍死在门后的堂屋里。', '张洞得知赵老大在听见亡母声音后自行抽开铁门栓，是否实际开门无人看见。', '张洞确认赵家与张家门板内侧均出现过纸灰。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 4
- character_updates: [{'name': '张洞父亲', 'status': '开始追查祠堂与祭田旧账', 'note': '赵家铁栓完好仍死人后，他不再接受铁器本身能保命的说法，转而查问旧账和叔公曾隐瞒的案例。'}, {'name': '张家叔公', 'status': '继续以是否应声追问赵家死因', 'note': '他拒绝解释旧例，只强调门能关住风而关不住人心。'}]
- world_confirmed_add: ['赵家铁门栓与门扇均完整，但赵老大在夜里自行抽开门栓后仍死在门后的堂屋里。', '赵家与张家院门内侧都出现过纸灰。']
- world_hypotheses_add: ['铁门栓不能直接阻止门外的未知事物；它至多在住户未自行开门时延迟一次选择。']

## 审查证据

- 赵家的完整铁栓构成对“铁能保命”的直接反例。：栓身完整，铁槽也没松，地上没有断钉、木屑和撞开的泥痕。
- 死亡原因只保留住户自行抽栓这一可见事实，门是否打开仍未越界断言。：铁栓是他拔的。门开没开，没人看见。
- 父亲的调查方向由防门转向旧账，形成现实而非知识性的状态变化。：明日我去祠堂看旧账。
- 门板内侧纸灰与此前封闭容器中的异常位置形成新的可核对关联。：门板内侧有一点白落进了灯影里。

## 静态指标

```json
{
  "char_count": 3510,
  "dash_count": 0,
  "dash_per_thousand": 0.0,
  "negation_template_count": 1
}
```

## Memory Record

```json
{
  "characters": ["张洞", "张洞父亲", "张洞母亲", "张家叔公", "木匠老李", "李二"],
  "locations": ["大汉市·双桥镇·木匠铺", "大汉市·双桥镇·赵家", "大汉市·双桥镇·张家院"],
  "event_id": "event_1",
  "foreshadows": ["F-A01"],
  "irreversible_changes": ["protagonist_known_info_add", "timeline_elapsed_days", "character_updates", "world_confirmed_add", "world_hypotheses_add"],
  "hook_type": "新证据",
  "summary": "赵家铁栓完好却仍有人死在门后，推翻铁器可直接保命的想法；张洞同时确认纸灰已出现在院门内侧。"
}
```
