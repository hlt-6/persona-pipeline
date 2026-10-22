"""渲染层：persona.json（单一数据源）-> persona.md + card.json

解决原体系的两个硬伤：
  1. card.json 的 system_prompt 不再是占位符，强制注入 persona.md 全文；
  2. Layer 0 元约束改写到 post_history_instructions（前端里永远压在上下文末尾），
     经历档案改写进 character_book（lorebook），不再占用最贵的 system_prompt 名额。
缺字段直接抛错，不做静默降级。
"""

from __future__ import annotations

import json
from pathlib import Path

LAYER0 = """## Layer 0：硬规则

1. 你是{name}，活在原著的世界里，不是 AI，不是助手
2. 原著设定、时间线、人物关系是事实：不说{name}不可能说的话，不做{name}不可能做的事，
   不提前知道当前时间点之后才发生的事
3. 保持{name}的棱角：缺点、坏脾气、局限性都在，不突然变得完美、温柔、无条件包容
4. 对不同的人用不同的态度和腔调（见 Layer 4），不许对谁都一个样
5. 被问到原著未交代的事，用符合{name}性格的方式回应（搪塞、猜测、反问），
   不许跳出角色解释「书里没写」
6. 用{name}的时代和世界的常识说话，不使用现代网络用语（除非原著本身如此）
7. 对话者是进入这个世界与你相遇的人：用{name}对待此类人的方式对待他，
   关系随对话按{name}的性格逻辑自然演变，不无条件亲密、不无条件敌对"""

REQUIRED = ("character", "speech", "emotion", "relations")


def _need(obj: dict, key: str, where: str):
    val = obj.get(key)
    if val in (None, "", [], {}):
        raise ValueError(f"persona.json 缺少必填字段 {where}.{key}")
    return val


def _join(val, sep: str = "；") -> str:
    if isinstance(val, (list, tuple)):
        return sep.join(str(v) for v in val if v)
    return str(val or "")


def render_persona_md(p: dict) -> str:
    for k in REQUIRED:
        _need(p, k, "persona")
    ch, sp, em, rel = p["character"], p["speech"], p["emotion"], p["relations"]
    name = _need(ch, "name", "character")

    L = [f"# {name} · Persona", ""]
    L += [LAYER0.format(name=name), ""]

    L += ["## Layer 1：身份", ""]
    L.append(f"- 名字：{name}（别名/称号：{_join(ch.get('aliases'), '、') or '无'}）")
    L.append(f"- 身份：{_need(ch, 'identity', 'character')}")
    L.append(f"- 当前状态：{_need(ch, 'status', 'character')}")
    L.append(f"- 外貌与标志性动作：{_need(ch, 'appearance', 'character')}")
    if ch.get("reputation"):
        L.append(f"- 在世人眼中的名声：{ch['reputation']}")
    L.append("")

    L += ["## Layer 2：说话风格", ""]
    L.append(f"- 自称：{_need(sp, 'self_reference', 'speech')}")
    if sp.get("address"):
        addr = "、".join(f"{k}称「{v}」" for k, v in sp["address"].items())
        L.append(f"- 称呼方式：{addr}")
    L.append(f"- 语域：{_need(sp, 'register', 'speech')}")
    L.append(f"- 句长与节奏：{_need(sp, 'rhythm', 'speech')}")
    if sp.get("catchphrases"):
        L.append(f"- 口头禅/标志性台词：{_join(sp['catchphrases'], '；')}")
    if sp.get("particles"):
        L.append(f"- 语气词与标点：{sp['particles']}")
    if sp.get("gestures"):
        L.append(f"- 说话时伴随动作：{_join(sp['gestures'], '；')}")
    L.append("")
    exs = sp.get("examples") or []
    if exs:
        verified = [e for e in exs if e.get("verified", True)]
        L.append("### 原著台词示例（风格锚点，不可改写）")
        L.append("")
        for e in exs:
            tag = "" if e.get("verified", True) else "〔未经原文核验，仅作风格参考〕"
            scene = e.get("scene", "")
            L.append(f"- 「{e.get('line', '')}」{tag}" + (f"（{scene}）" if scene else ""))
        if not verified:
            L.append("")
            L.append("> 注意：本卡暂无经语料核验的原台词，以上均为改写样本，不得对外标注为「原著原话」。")
        L.append("")

    L += ["## Layer 3：情感与价值观", ""]
    L.append(f"- 核心信念：{_need(em, 'beliefs', 'emotion')}")
    L.append(f"- 愤怒：{_need(em, 'anger', 'emotion')}")
    L.append(f"- 喜悦：{_need(em, 'joy', 'emotion')}")
    L.append(f"- 悲伤与脆弱：{_need(em, 'sadness', 'emotion')}")
    L.append(f"- 软肋与雷区：{_need(em, 'soft_spots', 'emotion')}")
    L.append(f"- 决策风格：{_need(em, 'decision', 'emotion')}")
    L.append("")

    L += ["## Layer 4：关系行为", ""]
    groups = rel.get("groups") or {}
    if groups:
        L.append("### 对各类人的态度")
        for k, v in groups.items():
            L.append(f"- {k}：{v}")
        L.append("")
    spec = rel.get("specific") or []
    if spec:
        L.append("### 关键关系")
        for s in spec:
            if isinstance(s, dict):
                L.append(f"- 与{s.get('with', '?')}：{s.get('pattern', '')}")
            else:
                L.append(f"- {s}")
        L.append("")
    L.append("### 底线")
    L.append(f"- 绝对不做的事：{_join(rel.get('dealbreakers'), '；') or '（未提供）'}")
    L.append(f"- 绝对不伤的人：{_join(rel.get('protected'), '；') or '（未提供）'}")
    L.append("")

    if p.get("timeline"):
        L += ["## Layer 5：经历档案（时间线要点）", ""]
        for t in p["timeline"]:
            L.append(f"- {t}")
        L.append("")
    return "\n".join(L)


def render_card(p: dict, persona_md: str) -> dict:
    ch, sp, em, rel = p["character"], p["speech"], p["emotion"], p["relations"]
    name = ch["name"]
    exs = [e for e in (sp.get("examples") or []) if e.get("verified", True)]
    only_unverified = False
    if not exs:
        exs = sp.get("examples") or []
        only_unverified = bool(exs)

    mes = "\n\n".join(
        f"<START>\n（{e.get('scene', '')}）\n{{{{char}}}}: 「{e.get('line', '')}」" for e in exs
    )

    entries = []
    for i, t in enumerate(p.get("timeline") or [], 1):
        entries.append({
            "id": i, "keys": [name], "secondary_keys": [],
            "comment": f"经历 {i}", "content": t, "enabled": True,
            "insertion_order": 100 + i, "constant": False, "selective": True,
        })
    for s in rel.get("specific") or []:
        if isinstance(s, dict) and s.get("with"):
            entries.append({
                "id": len(entries) + 1, "keys": [s["with"]], "secondary_keys": [],
                "comment": f"关系：{s['with']}", "content": s.get("pattern", ""),
                "enabled": True, "insertion_order": 200 + len(entries),
                "constant": False, "selective": True,
            })

    meta = p.get("meta", {})
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": f"{'、'.join(ch.get('aliases') or [])}。{ch['identity']}。{ch['appearance']}。"
                           f"当前：{ch['status']}。",
            "personality": f"{em['beliefs']} 愤怒时：{em['anger']} 喜悦时：{em['joy']} "
                           f"悲伤时：{em['sadness']} 决策风格：{em['decision']}",
            "scenario": ch["status"],
            "first_mes": p.get("first_mes") or f"（{name}抬眼打量着你）",
            "mes_example": mes,
            "system_prompt": persona_md,
            "post_history_instructions": LAYER0.format(name=name),
            "creator_notes": (f"persona-pipeline v{meta.get('schema_version', '1.0')} 生成"
                              f"（{meta.get('generated_by', 'unknown')}）；"
                              f"经核验原台词 {meta.get('verified_quotes', 0)} 条。"
                              + ("本卡台词示例未经语料核验，不得对外称「原著原话」。"
                                 if only_unverified else "")
                              + f"许可：{meta.get('source_license', '未声明')}"),
            "tags": meta.get("tags") or ["小说角色", "persona-pipeline"],
            "character_book": {"name": f"{name} · 设定集", "entries": entries},
            "extensions": {
                "persona_pipeline": {
                    "schema_version": meta.get("schema_version", "1.0"),
                    "generated_by": meta.get("generated_by"),
                    "verified_quotes": meta.get("verified_quotes", 0),
                    "source_license": meta.get("source_license"),
                }
            },
        },
    }


def write_outputs(p: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_persona_md(p)
    card = render_card(p, md)
    (out_dir / "persona.md").write_text(md, encoding="utf-8")
    (out_dir / "card.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "persona.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    # 自检：占位符与空字段
    problems = []
    sp = card["data"]["system_prompt"]
    if len(sp) < 400:
        problems.append(f"system_prompt 过短（{len(sp)} 字符），疑似占位符")
    for k in ("name", "description", "personality", "scenario", "first_mes", "mes_example"):
        if not card["data"].get(k):
            problems.append(f"card.{k} 为空")
    return {"persona_md": str(out_dir / "persona.md"),
            "card_json": str(out_dir / "card.json"),
            "system_prompt_chars": len(sp),
            "lorebook_entries": len(card["data"]["character_book"]["entries"]),
            "problems": problems}
