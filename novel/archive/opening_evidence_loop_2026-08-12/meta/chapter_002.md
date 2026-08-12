# 第2章元数据

- 标题: 守位簿的缺页
- 事件: event_1
- 阶段: 日常边界
- 审查: PASS / A

## 不可逆变化
- protagonist_known_info_add: ['张大成守夜记录中存在“子时复检后侧工作门”的记号。', '叔公在查档当日试图转移守位相关账箱。', '父亲因张洞撬门取档，已收走其代记零工账的权限。']
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
- continuity: 封存账箱的锁与钥匙位置明确，未见无动作的开锁矛盾。：铜锁昨夜由父亲亲手扣上，钥匙一直穿在他腰带内侧。
- continuity: 工作门从内闩到敞开的变化有可见的撬动与断裂过程。：木栓先是弹起，随即在旧裂纹处断成两截。
- continuity: 张洞没有把袖口纸纤维直接定为叔公撕页的证明，保留了事实等级。：“不能定。”张洞松开残页，“所以才要留着。”

## 静态指标
```json
{
  "char_count": 5305,
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
    "张洞父亲"
  ],
  "locations": [
    "大汉市·双桥镇·张家院正屋与灶间",
    "大汉市·双桥镇·张家院正屋门槛",
    "大汉市·双桥镇·张氏祠堂正门、后侧工作门与防火夹巷"
  ],
  "event_id": "event_1",
  "foreshadows": [],
  "irreversible_changes": [
    "protagonist_known_info_add",
    "timeline_year",
    "timeline_elapsed_days"
  ],
  "hook_type": "未解问题",
  "summary": "张洞撬开祠堂工作门取出张大成守位簿残页，父亲据页抄录并收走他誊工票权限，后侧门的复检内容仍未明。"
}
```

## 正文状态结算
```json
{
  "chapter_number": 2,
  "draft_sha256": "7d4ac9a9d8594a5d98e7babe8d7102bff3c32501e2e59bcbe4f1d65781a67349",
  "verdict": "PASS",
  "reader_visible_summary": {
    "core": "张洞撬开祠堂工作门取出张大成守位簿残页，父亲据页抄录并收走他誊工票权限，后侧门的复检内容仍未明。",
    "evidence": [
      "木栓先是弹起，随即在旧裂纹处断成两截。",
      "“从今日起，这本账你别碰。”",
      "“子时复检，后侧工作门。”"
    ]
  },
  "hook": {
    "type": "未解问题",
    "content": "张大成在子时复检后侧工作门时，究竟看见了什么？",
    "quote": "可他究竟看见了什么，残页上没有写。"
  },
  "change_evidence": [
    {
      "path": "state_changes.protagonist_known_info_add[1]",
      "value": "张大成守夜记录中存在“子时复检后侧工作门”的记号。",
      "quote": "“子时复检，后侧工作门。”",
      "finding": "残页被直接读出该记号。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[2]",
      "value": "叔公在查档当日试图转移守位相关账箱。",
      "quote": "叔公没回头，只说：“抬到前廊，等会儿从正门走。”",
      "finding": "叔公直接指挥账箱继续转移。"
    },
    {
      "path": "state_changes.protagonist_known_info_add[4]",
      "value": "父亲因张洞撬门取档，已收走其代记零工账的权限。",
      "quote": "“从今日起，这本账你别碰。”",
      "finding": "父亲明确禁止张洞再碰木行零工账。"
    }
  ],
  "missing_changes": [
    "state_changes.protagonist_known_info_add[0]",
    "state_changes.protagonist_known_info_add[3]",
    "state_changes.timeline_elapsed_days",
    "state_changes.character_updates[0]",
    "state_changes.character_updates[1]",
    "state_changes.character_updates[2]",
    "state_changes.world_hypotheses_add[0]",
    "state_changes.world_hypotheses_add[1]"
  ]
}
```
