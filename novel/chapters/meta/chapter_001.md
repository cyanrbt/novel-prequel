# 第1章元数据

- 标题: 母亲又回来了
- 事件: event_1
- 阶段: 阶段一：门外身份重演与棺下影双暴露
- 审查: PASS / B

## 不可逆变化
- protagonist_known_info_add: ['屋内来人的布包内层没有铜顶针、染坊收据和结回的钱。', '张洞从院门的半尺缝看见一只沾着蓝色染料的手，手里夹着盖红印的收据并攥着几枚钱。', '张洞当日晚船的学徒空位已补给别人。']
- protagonist_inventory_add: []
- protagonist_inventory_remove: []
- protagonist_location: None
- protagonist_body_updates: []
- ability_updates: []
- timeline_year: 1911
- timeline_elapsed_days: 0
- character_updates: []
- world_confirmed_add: []
- world_hypotheses_add: ['屋内来人的布包可能停留在张母清晨的旧状态。']

## 审查证据
- 开篇明确了张洞依靠木作离镇的现实目标与不可替代的时间压力。：样子合格，他才能跟船去大汉市；误了这趟，空位不会留。
- 异常借父亲已承认的家庭身份完成了不可逆的现实剥夺，核心状态变化已落地。：就这一错手，那人从桌角开着的木匣抽出押钱凭条，连同荐帖塞进伙计的账袋
- 张洞以亲见的物件状态发现两份行程无法归于同一人，未越过当前证据作超自然定论。：包的内层是空的。清晨已经装进内层的铜顶针不在，也没有染坊的收据和结回的钱。
- 结尾把行动收束为有限核验请求；院内外身份均未被提前确认。：先把顶针递进来。

## 静态指标
```json
{
  "char_count": 4123,
  "dash_count": 1,
  "dash_per_thousand": 0.24,
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
  },
  "taste_contract": {
    "enabled": true,
    "dialogue_count": 79,
    "longest_staccato_run": 4
  }
}
```

## Memory Record
```json
{
  "characters": [
    "外观与张母相同的来人",
    "张家叔公",
    "张洞",
    "张洞母亲",
    "张洞父亲",
    "木作铺来人"
  ],
  "locations": [
    "张家后院与正屋门口，清晨",
    "张家正屋至双桥镇渡口，黄昏",
    "张家院门与正屋，夜初"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "timeline_year",
    "timeline_elapsed_days",
    "world_hypotheses_add"
  ],
  "hook_type": "安全区崩坏",
  "summary": "张洞错失晚船返家后，发现屋内来人的布包缺少顶针、收据和钱；门外又出现带收据与钱的来人，他用榫样卡住门"
}
```

## 正文状态结算
```json
{
  "chapter_number": 1,
  "draft_sha256": "19b2396240ffe994fcb67ac2a88e748376d20c6e90978d0a290809a3c0d5f7ba",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "张洞错失晚船返家后，发现屋内来人的布包缺少顶针、收据和钱；门外又出现带收据与钱的来人，他用榫样卡住门楔，并要求递入顶针核验。",
    "evidence": [
      "清晨已经装进内层的铜顶针不在，也没有染坊的收据和结回的钱。",
      "隔着左边院门的半尺缝，张洞先看见一只沾着蓝色染料的手。",
      "“先把顶针递进来。”"
    ]
  },
  "hook": {
    "type": "安全区崩坏",
    "content": "家门内外出现相互冲突的来人，张洞既未开门也未封门，只能以顶针核验。",
    "quote": "“先把顶针递进来。”"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[0]",
      "value": "屋内来人的布包内层没有铜顶针、染坊收据和结回的钱。",
      "quote": "清晨已经装进内层的铜顶针不在，也没有染坊的收据和结回的钱。",
      "finding": "张洞检查布包内层后，直接发现三者均不在其中。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[1]",
      "value": "张洞从院门的半尺缝看见一只沾着蓝色染料的手，手里夹着盖红印的收据并攥着几枚钱。",
      "quote": "隔着左边院门的半尺缝，张洞先看见一只沾着蓝色染料的手。那只手里夹着一张盖了红印的收据，下面还攥着几枚钱。",
      "finding": "张洞直接看见门缝外的手及其夹着的收据和攥着的钱。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[2]",
      "value": "张洞当日晚船的学徒空位已补给别人。",
      "quote": "你家退帖在先，空位给陈家了。",
      "finding": "伙计明确说明空位已给陈家。"
    },
    {
      "path": "state_changes.world_hypotheses_add[0]",
      "value": "屋内来人的布包可能停留在张母清晨的旧状态。",
      "quote": "他回头望见桌上那个停在清晨的旧布包，又看向门缝外盖着当日红印的收据。",
      "finding": "张洞将屋内的旧布包与门外当日收据对照，形成布包停在清晨状态的假说。"
    }
  ],
  "missing_changes": []
}
```
