# 第5章元数据

- 标题: 门槛上的鞋
- 事件: event_1
- 阶段: 征兆
- 审查: PASS / A

## 不可逆变化

- protagonist_known_info_add: ['张洞确认张家门外留鞋的当夜，三下敲门声在鞋边停住，未继续响起。', '张洞确认天亮后门外的鞋离门槛更近、鞋尖改朝门内，石阶墨线未被擦掉，周围没有拖痕或脚印。', '张洞得知父亲从祭田散页中查到祠堂周边旧日反复修门并伴随收租人姓名消失的记录，但现有账目无法说明原因。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 5
- character_updates: [{'name': '张洞母亲', 'status': '留下门槛留鞋并暗藏备用旧鞋', 'note': '她不确信鞋能保命，却在无可靠办法时选择保留这条民间偏方。'}, {'name': '张洞父亲', 'status': '取得祭田散页并决定向族长索看祠堂旧账', 'note': '他发现祠堂周边多次修门记录与收租人姓名消失相邻，决定继续追查。'}]
- world_confirmed_add: ['张家门外留鞋的当夜响起三下敲门声，声音在鞋边停住后未再继续。', '天亮后张家门外的鞋向门槛移动约一尺、鞋尖改朝门内，石阶墨线仍在，鞋周无拖痕和脚印。']
- world_hypotheses_add: ['门槛留鞋可能改变门外未知事物停留的位置或停顿时间，但单次观察不足以证明它能保护住户。']

## 审查证据

- 母亲摆鞋不是迷信胜利，而是在缺乏办法时作出的低成本选择。：不一定真。可鞋摆着不用花钱。
- 敲门停顿与鞋的存在被写成同一夜的事实，未将因果写死。：第三下终于响了。随后再没有声音。
- 鞋的位移通过墨线、距离和无痕地面建立为可复核异常。：鞋离门槛比夜里近了约一尺，鞋尖也不再朝巷口。
- 父亲的旧账调查推进为下一章的现实行动，而非一句设定解释。：今日去找族长。祠堂旧账一定要看。

## 静态指标

```json
{
  "char_count": 3540,
  "dash_count": 0,
  "dash_per_thousand": 0.0,
  "negation_template_count": 2
}
```

## Memory Record

```json
{
  "characters": ["张洞", "张洞父亲", "张洞母亲", "木匠老李", "李二"],
  "locations": ["大汉市·双桥镇·张家院", "大汉市·双桥镇·木匠铺", "大汉市·双桥镇·石桥下"],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": ["protagonist_known_info_add", "timeline_elapsed_days", "character_updates", "world_confirmed_add", "world_hypotheses_add"],
  "hook_type": "未解问题",
  "summary": "门槛留鞋让三下敲门暂时停住，却在天亮后以向内移动的方式显出不可靠；父亲则从祭田散页找到追查祠堂旧账的线索。"
}
```
