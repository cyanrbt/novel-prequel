# 第3章元数据

- 标题: 开门的人
- 事件: event_1
- 阶段: 逃离受阻
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['已核对的样本显示，未知事物未必需要撞破门板；该户住户是因听见熟人求助而主动开门。', '门未被撞坏不等于不开门就绝对安全，现有样本不足以确认完整触发条件。', '一户出事人家的门板内侧留有不该在室内出现的细纸灰。', '渡船底舱的插销仍在原位，但缠门麻绳少了一股，季三否认开过舱。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: 大汉市·双桥镇·季三渡口
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 2
- character_updates: [{'name': '张洞', 'status': '失去当夜陆路离镇机会', 'note': '为核对出事人家的证词错过最后一个陆路合车名额。'}, {'name': '李二', 'status': '与张洞风险绑定', 'note': '共同取得开门证词，不再能把镇上异响只当闲谈。'}, {'name': '张洞母亲', 'status': '当夜安置进一步受阻', 'note': '张洞未能赶上她争取的陆路合车名额。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['未知事物可能借熟人声音制造求助理由，诱使活人自行开门；这仍不足以说明沉默或门栓是否安全。', '门板内侧的纸灰可能与祠堂或渡船底舱的异常有关，但尚无证据证明同源。']

## 审查证据
- continuity: 开门证词提供了可核对的在场矛盾，支撑“熟人声音”异常而非将其直接定为完整规律。：满仓第二天午后才回来，他前一夜根本没进镇。
- continuity: 对门内纸灰的出现时点保持证据等级，没有把未知过程写成确定事实。：所以只能说，开门以后见到了。不能说它什么时候进来。
- continuity: 张洞核对孙家证词后，母亲争取到的最后陆路合车名额已不可逆地失去。：人不来，地方给了陈家小子。

## 静态指标
```json
{
  "char_count": 5438,
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
    "刘婶",
    "季三",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "李二",
    "死者家属",
    "阿顺"
  ],
  "locations": [
    "双桥镇一户近日出事人家的门前与堂屋",
    "季三渡口的停航渡船旁",
    "季三渡口的渡船底舱门外"
  ],
  "event_id": "event_1",
  "foreshadows": [
    "F-A01"
  ],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_location",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "张洞以失去最后一条当夜离镇资源为代价，取得可核对的开门证词，并与李二形成共同承担风险的关系。"
}
```
