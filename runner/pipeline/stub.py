"""离线骨架：不调用模型，把输入包机械映射成 persona.json。

用途：验证"流程是否跑得通、产物结构是否合法"。它不做任何分析，
所以 meta.generated_by 明确写 offline-stub，内容不得当作成品人设使用。
"""

from __future__ import annotations

import datetime as _dt


def build(input_pkg: dict, verify_report: dict | None = None) -> dict:
    ch = input_pkg.get("character", {})
    dlg = input_pkg.get("dialogue_samples", []) or []
    nar = input_pkg.get("narration_samples", []) or []
    rels = input_pkg.get("relations", []) or []

    verified_map = {}
    vcount = 0
    if verify_report:
        for q in verify_report.get("quotes", []):
            if q["kind"] == "dialogue":
                verified_map[q["index"]] = q["status"] == "verified"
        vcount = verify_report.get("summary", {}).get("verified", 0)

    examples = []
    for i, d in enumerate(dlg):
        line = d.get("line", "") if isinstance(d, dict) else str(d)
        scene = d.get("scene", "") if isinstance(d, dict) else ""
        examples.append({
            "scene": scene, "line": line,
            "verified": verified_map.get(i, False),
        })

    groups, specific = {}, []
    for r in rels:
        bond = r.get("bond", "其他")
        head = f"对{bond}（{r.get('with', '')}）"
        groups[head] = (f"态度倾向：{r.get('attitude', '未给出')}；"
                        f"相处模式：{r.get('narrative_role', '未给出')}")
        specific.append({
            "with": r.get("with", ""),
            "pattern": (f"{bond}；态度 {r.get('attitude', '-')}；"
                        f"叙事功能 {r.get('narrative_role', '-')}"),
        })

    timeline = []
    for a in ch.get("arc", []) or []:
        if isinstance(a, dict):
            timeline.append(f"[{a.get('chapters', '')}] {a.get('events', '')}")
        else:
            timeline.append(str(a))

    return {
        "schema_version": "1.0",
        "character": {
            "name": ch.get("name", "未命名"),
            "aliases": ch.get("aliases", []) or [],
            "identity": "；".join(ch.get("ident_infos", []) or []) or "（输入包未提供）",
            "status": ch.get("life_status") or "（输入包未提供 life_status）",
            "appearance": (nar[0] if nar else "（输入包未提供叙述样本）"),
            "reputation": (ch.get("summary", "") or "")[:120],
        },
        "speech": {
            "self_reference": "（离线骨架：自称需由模型从台词样本归纳）",
            "address": {r.get("with", "?"): r.get("with", "?") for r in rels[:5]},
            "register": "（离线骨架：语域需由模型判定）",
            "rhythm": f"（离线骨架：请参考下方 {len(examples)} 条台词样本的句长与标点）",
            "catchphrases": [e["line"] for e in examples if len(e["line"]) <= 12][:3],
            "particles": "（离线骨架：待模型归纳）",
            "gestures": nar,
            "examples": examples,
        },
        "emotion": {
            "beliefs": f"（离线骨架）人物摘要：{ch.get('summary', '')[:150]}",
            "anger": "（离线骨架：待模型分析）",
            "joy": "（离线骨架：待模型分析）",
            "sadness": "（离线骨架：待模型分析）",
            "soft_spots": "（离线骨架：待模型分析）",
            "decision": "（离线骨架：待模型分析）",
        },
        "relations": {
            "groups": groups, "specific": specific,
            "dealbreakers": [], "protected": [],
        },
        "timeline": timeline,
        "first_mes": f"（{ch.get('name', '')}看向你，像在掂量来意）",
        "meta": {
            "schema_version": "1.0",
            "generated_by": "offline-stub（未经模型分析，仅验证流程）",
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "source_license": input_pkg.get("meta", {}).get("source_license", "未声明"),
            "verified_quotes": vcount,
            "tags": ["离线骨架"],
        },
    }
