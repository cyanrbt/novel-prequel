# 第1章元数据

- 标题: 蒸笼里的灰
- 事件: event_1
- 阶段: 日常边界
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['张家蒸笼中的淡纸灰至少两次出现，第二次未随新米或灶火进入。', '孙周氏死亡已实际切断张家赊米担保。']
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
- continuity: 张洞亲手支付全部船钱的不可逆代价已在正文中落实。：最后一枚铜元被推过去时，张洞看见布包底下只剩一道钱压出来的圆印。
- continuity: 纸灰证物由可烧弃的污物改为上锁保存，章节核心控制方式变化已落实。：茶叶罐被放进正屋的黑漆账箱。父亲当着叔公的面合上箱盖，将铜锁扣紧。
- continuity: 人物仍将普通解释作为待核对对象，未把纸灰来源直接定为超自然结论。：灰烧掉，明天谁再说是灶里飞进去的，我们拿什么对？

## 静态指标
```json
{
  "char_count": 4777,
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
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "李二",
    "米铺掌柜"
  ],
  "locations": [
    "大汉市·双桥镇·东巷米铺及孙家门外",
    "大汉市·双桥镇·张家院灶间",
    "大汉市·双桥镇·张家院灶间与正屋门槛"
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
  "hook_type": "隐藏规则发现",
  "summary": "张洞用全部船钱买米后，发现清洗封住的空蒸笼再现纸灰；父子将灰封存上锁，决定次日查八年前守祠记录。"
}
```

## 正文状态结算
```json
{
  "chapter_number": 1,
  "draft_sha256": "6d4fc0bd89313c7136285eed904b16680315d7b98d4e6a9d283fdca884b6dd19",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "张洞用全部船钱买米后，发现清洗封住的空蒸笼再现纸灰；父子将灰封存上锁，决定次日查八年前守祠记录。",
    "evidence": [
      "蒸笼从清洗后就没装过新米，也没有再上锅。",
      "茶叶罐被放进正屋的黑漆账箱。父亲当着叔公的面合上箱盖，将铜锁扣紧。"
    ]
  },
  "hook": {
    "type": "隐藏规则发现",
    "content": "清洗、倒扣并罩住的空蒸笼再现纸灰，令纸灰的来处成为待查的异常。",
    "quote": "可第二回蒸笼空着、洗过、倒扣，又被布和麻绳罩住。烧了以后，家里只剩几句话，谁都能说是记错了。"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[0]",
      "value": "张家蒸笼中的淡纸灰至少两次出现，第二次未随新米或灶火进入。",
      "quote": "蒸笼从清洗后就没装过新米，也没有再上锅。那层灰比第一回薄，颜色却一样，指腹一擦便散开，留下淡白的痕。",
      "finding": "第二次纸灰在未装新米、未再上锅的蒸笼内出现，且明确与第一回相同。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[1]",
      "value": "孙周氏死亡已实际切断张家赊米担保。",
      "quote": "孙周氏的保不算了。",
      "finding": "米铺掌柜直接否认孙周氏的担保效力。"
    },
    {
      "path": "foreshadow_operations.plant[0]",
      "value": "F-A01",
      "quote": "蒸笼从清洗后就没装过新米，也没有再上锅。那层灰比第一回薄，颜色却一样，指腹一擦便散开，留下淡白的痕。",
      "finding": "不应出现的淡纸灰重复出现，构成纸灰异常的正文落点。"
    }
  ],
  "missing_changes": [
    "state_changes.protagonist_known_info_add[2]",
    "state_changes.timeline_elapsed_days",
    "state_changes.character_updates[0]",
    "state_changes.character_updates[1]",
    "state_changes.character_updates[2]",
    "state_changes.character_updates[3]",
    "state_changes.world_hypotheses_add[0]"
  ]
}
```
