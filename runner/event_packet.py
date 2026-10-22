#!/usr/bin/env python3
"""事件轨答题包生成：整章按自然段编号，模型只回段号区间（规范见 05-event-track-spec.md）。

与台词轨同构：模型没有机会改写原文，事件文本由 apply_events.py 机械回填。

用法：
  python runner/event_packet.py --chapter 330 --target 杨思思 --aliases 杨思思,思思,杨如画,四哥
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def load_chapter(chapter: int) -> tuple[str, str]:
    """读 novels/fenghua/chapters/chNNN.txt（首行是章标题，余下是正文）。"""
    f = ROOT / f"novels/fenghua/chapters/ch{chapter:03d}.txt"
    text = f.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    first, _, rest = text.partition("\n")
    return first.strip(), rest.strip("\n")


def paragraphs(body: str) -> list[str]:
    return [ln.strip() for ln in body.split("\n") if ln.strip()]


def build_packet(chapter: int, target: str, aliases: list[str]) -> str:
    title, body = load_chapter(chapter)
    paras = paragraphs(body)
    alias_line = "、".join(aliases)
    out = [
        f"## 事件提取（{title}）",
        "",
        f"目标人物：{target}（别名：{alias_line}）",
        f"全文共 {len(paras)} 个自然段，每段前面已标好编号 [Pn]。",
        "",
        "任务：找出与目标人物有关的**关键事件**——塑造其性格、关系或命运的事件。",
        "优先：她做出抉择的事件（C）、她付出代价的事件（S）、转折点（T）。",
        "普通的日常过场不要选。一章选 1–6 个最关键的即可，宁缺毋滥。",
        "她不在场、但他人正在谈论/评价她的段落也可入选，类型码用 M，并注明谈论者对她的称呼。",
        "",
        "**本项目主轴（重要）**：我们只还原她对「米高」一个人的态度——那是独一无二的。",
        "推论的「影响」格：若事件与米高有关，写清它对她与米高关系/对米高态度的影响；",
        "若事件涉及她对其他人的态度，必须在句首注明对象（如「对陈进：…」），",
        "绝不许把她对别人的态度写成对米高的态度。参与者只需填目标本人，他人可略。",
        "",
        "## 原文",
        "",
    ]
    for i, p in enumerate(paras, 1):
        out.append(f"[P{i}]{p}")
        out.append("")
    out += [
        "## 回答格式（只回这一段 JSON，不要重抄原文）",
        "",
        '{"e":[[起始段号,结束段号,"类型码",["参与者本名",…],"high或low"],…],'
        '"n":{"1":"选择：…；代价：…；影响：…"}}',
        "",
        "类型码：C=抉择 T=转折 F=冲突 S=付出 R=关系 B=背景往事 M=被提及（她不在场）",
        "n 里按事件序号（从 1 开始）写推论，三格用「选择：」「代价：」「影响：」开头、分号隔开；",
        "没有把握的格子写「无」；M 类事件在 n 里另加「称呼：谈论者对她的称呼原文」。",
        "参与者用本名（杨思思 不写 思思/杨如画/四哥）。段号区间要完整覆盖整个事件，不要只截一句。",
    ]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--aliases", default="", help="逗号分隔，含本名")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    aliases = [x.strip() for x in a.aliases.split(",") if x.strip()] or [a.target]
    pkt = build_packet(a.chapter, a.target, aliases)
    out = Path(a.out) if a.out else ROOT / f"novels/fenghua/packets/ch{a.chapter}.events.packet.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(pkt, encoding="utf-8")
    print(f"答题包已生成：{out}（{len(pkt)} 字）")


if __name__ == "__main__":
    main()
