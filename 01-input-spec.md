# 01 · 输入规范：txt2world 输出 → 分析输入包

分析提示词（02）需要一个 JSON 输入包，字段直接对应 txt2world 的数据模型
（WorldObj + CharacterRelation + 章节摘要），另加一个 txt2world 不直接产出、
但对人格提取最关键的字段：`dialogue_samples`（角色台词样本）。

```json
{
  "character": {
    "name": "角色正名（WorldObj.name）",
    "aliases": ["别名、称号、化名（WorldObj.aliases）"],
    "ident_infos": ["身份线索（WorldObj.identInfos）"],
    "importance_level": "主角/重要配角/配角（WorldObj.importanceLevel）",
    "life_status": "故事当前时间点的状态（WorldObj.lifeStatus）",
    "summary": "人物全局摘要（WorldObj.summary）",
    "arc": [
      { "chapters": "出场章节段", "events": "该阶段关键经历（来自 chunkSummaryList 提炼）" }
    ]
  },
  "relations": [
    {
      "with": "对方名字",
      "attitude": "态度（CharacterRelation 维度）",
      "hierarchy": "上下关系",
      "favorability": "好感度倾向",
      "bond": "羁绊类型：亲情/师徒/恋人/仇敌/同伴…",
      "narrative_role": "对方在 ta 人生中的叙事功能"
    }
  ],
  "dialogue_samples": [
    { "scene": "场景一句话", "line": "原著中该角色的原话（逐字，不要改写）" }
  ],
  "narration_samples": [
    "原著中描写该角色外貌/动作/心理的代表性叙述句（逐字）"
  ]
}
```

## 填写说明

- `character` 与 `relations` 直接从 txt2world 的 `tables/` JSON 摘取即可，字段一一对应。
- `dialogue_samples` 是人格提取的命根子：txt2world 暂不直接输出台词，需要从正文按
  「角色名 + 说话动词」筛取，建议 20–50 条，覆盖 ta 对不同对象（亲近者/敌人/陌生人）
  和不同情绪（日常/愤怒/脆弱）下的发言。没有台词样本的人设卡只会是百科词条，不像真人。
- `narration_samples` 5–10 条即可，用于锚定外貌、动作习惯和文风。
- 原文逐字引用仅限用户有权使用的文本（自有/授权/公版）。分析与商用边界见 README。
