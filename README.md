# 小说角色人格提取流水线（persona-pipeline）

把 ex-skill（create-ex）的「材料 → 人格提示词」方法论，改造成吃 **txt2world 输出** 的小说版。
由 Starstacker 于 2026-10-22 组装，John 的角色智能体产品的核心零件 v0.1。

## 流水线

```
小说文本（合法来源，由 John 负责）
   │  txt2world 解析
   ▼
① 输入包（见 01-input-spec.md）
   人物条目 + 关系条目 + 章节经历摘要 + 该角色台词样本
   │  02-novel-persona-analyzer.md（分析提示词）
   ▼
② 人格分析草稿（五层结构草稿：说话风格 / 情感模式 / 价值观与底线 / 对人差异）
   │  03-novel-persona-builder.md（构建提示词）
   ▼
③ 成品（两件套）
   persona.md（五层人设卡，可直接当系统提示词）
   + card.json（SillyTavern V2 兼容字段，可导入酒馆类前端）
```

## 与 ex-skill 原版的三处关键改造

1. **输入源不同**：原版吃双人聊天记录（真人第一人称发言）；小说版吃 txt2world 的结构化
   输出（第三人称经历档案）+ 从正文筛出的该角色台词样本。台词样本在分析中等价于聊天记录。
2. **关系框架不同**：原版 Layer 4 是「与用户的恋爱关系行为」；小说版改为「角色在故事
   关系网中的行为」+「与对话者（以故事世界内身份进入的用户）的互动定位」。
   原版 Layer 0 的「分手是事实」改为「原著设定与时间线是事实，不许 OOC」。
3. **合规边界**：只做虚构角色；不用真人聊天记录蒸馏真人出售（隐私雷区）。
   商业化使用受版权保护的在世作者角色，需要权利人授权（见 short-video-plan/PLAN.md）。

## 试跑样本

`sample/` 内含一份按 txt2world 字段格式编写的《西游记》孙悟空输入包（公版作品，
Starstacker 依据公开常识编写的模拟输出），以及用本流水线实际跑出的成品人设卡，
证明端到端可用。真实项目时，把输入包换成 txt2world 对目标小说的真实输出即可。

> 说明：该样本跑通了流程，但输入包本身未通过引文核验——6 条台词里至少 3 条不是《西游记》原文，
> 且条数低于 01 要求的 20–50 条。见下节。

## 引文核验

01 要求台词样本是「原著原话（逐字，不要改写）」，但此前没有任何校验手段，导致仿写被当作原文
一路带进成品卡。现在补上两层：

- 规范：[04-evidence-policy.md](04-evidence-policy.md)（三级判定、输入/输出侧要求、轻量证据模式）
- 执行：`runner/`（`verify` 校验引文可在语料中精确定位，`run` 在成品侧复核）

## 本地运行

仓库本身是提示词文档，没有可执行入口；`runner/` 补了这一层，零第三方依赖，Python 3.11+ 即可：

```bash
cp runner/config.example.toml runner/config.toml
python runner/cli.py doctor
python runner/cli.py verify --input sample/input-sun-wukong.json
python runner/cli.py run --input sample/input-sun-wukong.json --offline
```

在线模式（DS 端点）见 [runner/README.md](runner/README.md)。
