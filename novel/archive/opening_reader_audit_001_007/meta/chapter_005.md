# 第5章元数据

- 标题: 门槛上的鞋
- 事件: event_1
- 阶段: 试探
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['门槛外留鞋后，三下敲门曾停在鞋边，但这一次观察不足以证明旧鞋能保护住户。', '天亮时门槛外旧鞋鞋尖朝向屋内，且比夜里靠近门槛一尺。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: 大汉市·双桥镇·张家院落
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1908
- timeline_elapsed_days: 5
- character_updates: [{'name': '张洞父亲', 'status': '决定查祠堂旧账', 'note': '不再接受叔公以沉默处置风险，准备从旧账中追查家中安排的依据。'}, {'name': '张洞母亲', 'status': '私藏备用旧鞋', 'note': '虽不信镇上传言，仍为家人另留一双鞋作退路。'}, {'name': '张洞', 'status': '记录门槛旧鞋试验结果', 'note': '将敲门停顿与鞋位变化记为未证实观察，未把留鞋当作安全规则。'}]
- world_confirmed_add: []
- world_hypotheses_add: ['门槛外旧鞋可能会影响门外之物的停留位置，但其作用不稳定，也不能证明能解除危险。']

## 审查证据
- continuity: 父亲从上一章对叔公沉默的不满，连贯推进为查祠堂旧账的明确行动。：父亲朝那扇门看了一眼，提高了些声音：“他不肯说，就去查账。
- continuity: 准确兑现计划中的晨后核对：门栓未被从内开启、鞋尖转向屋内并向门槛移动一尺。：天明，门仍自内落栓。父亲开门。旧鞋两只尚在，方向相反，鞋尖朝院内；由第三砖缝移至第二砖缝内，近门槛约一尺。无人承认触碰。
- continuity: 张洞将夜间声响保留为不确定观察，没有把单次结果升级为旧鞋已证实有效的规则。：他在“似有”两字上停了停，没有改。

## 静态指标
```json
{
  "char_count": 4547,
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
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲"
  ],
  "locations": [
    "大汉市·双桥镇·张家院落堂屋与院门后",
    "大汉市·双桥镇·张家院落门槛"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "protagonist_location",
    "timeline_year",
    "timeline_elapsed_days",
    "character_updates",
    "world_hypotheses_add"
  ],
  "hook_type": "未解问题",
  "summary": "让张家以门槛旧鞋尝试自保却无法验证其效用，并迫使父母分别转向查旧账与私留退路。"
}
```
