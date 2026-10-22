#!/usr/bin/env python3
"""事件答卷回填 + 锚点校验（规范见 05-event-track-spec.md）。

模型只回段号区间与类型码；事件原文由本脚本机械回填。
事实（fact_text）与推论（inference）分栏落盘：推论可以错，事实不许错。

用法：
  python runner/apply_events.py --chapter 330 --target 杨思思 \
      --aliases 杨思思,思思,杨如画,四哥 --answers events-ch330.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.event_packet import load_chapter, paragraphs  # noqa: E402

TYPES = {"C", "T", "F", "S", "R", "B", "M"}
MAX_SPAN = 40


def parse_inference(note: str) -> dict:
    out = {"choice": "", "cost": "", "impact": "", "address_terms": ""}
    if not note:
        return out
    keymap = {"选择": "choice", "代价": "cost", "影响": "impact", "称呼": "address_terms"}
    for part in re.split(r"[；;]", note):
        m = re.match(r"\s*(选择|代价|影响|称呼)[:：]\s*(.*)", part)
        if m:
            out[keymap[m.group(1)]] = m.group(2).strip()
    return out


def build(chapter: int, target: str, aliases: list[str], raw: dict) -> dict:
    _, body = load_chapter(chapter)
    paras = paragraphs(body)
    evs = raw.get("e") or []
    notes = raw.get("n") or {}
    events, fatal, warn = [], [], []

    for i, item in enumerate(evs, 1):
        eid = f"E{chapter}-{i:02d}"
        try:
            start, end = int(item[0]), int(item[1])
            etype = str(item[2]).upper()
            parts = [str(x) for x in (item[3] or [])]
            conf = str(item[4]) if len(item) > 4 else "high"
        except Exception:
            fatal.append(f"{eid}: 答卷条目格式无法解析：{item!r}")
            continue
        if not (1 <= start <= end <= len(paras)):
            fatal.append(f"{eid}: 段号区间 [{start},{end}] 越界（全章 {len(paras)} 段）")
            continue
        if end - start + 1 > MAX_SPAN:
            fatal.append(f"{eid}: 区间跨度 {end-start+1} 段 > 上限 {MAX_SPAN}")
            continue
        if etype not in TYPES:
            fatal.append(f"{eid}: 未知类型码 {etype}")
            continue
        fact = "\n".join(paras[start - 1:end])
        present = any(a in fact for a in aliases)
        if not present and etype != "M":
            fatal.append(f"{eid}: 区间内未出现目标本名或别名，且未标 M（疑似串线）")
            continue
        if not present and etype == "M":
            warn.append(f"{eid}: M 类但区间内无目标名字/别名，请人工确认谈论对象确为目标")
        for p in parts:
            if p in aliases and p != target:
                warn.append(f"{eid}: 参与者用了别名「{p}」，已归一为本名")
        parts = [target if (p in aliases and p != target) else p for p in parts]
        inf = parse_inference(str(notes.get(str(i), "")))
        events.append({
            "event_id": eid, "chapter": chapter,
            "para_start": start, "para_end": end,
            "fact_text": fact, "participants": parts, "type": etype,
            "inference": inf, "target_present": present or etype != "M",
            "confidence": conf,
        })

    # 重叠检查
    for x in range(len(events)):
        for y in range(x + 1, len(events)):
            a, b = events[x], events[y]
            lo, hi = max(a["para_start"], b["para_start"]), min(a["para_end"], b["para_end"])
            if hi >= lo:
                span = min(a["para_end"] - a["para_start"], b["para_end"] - b["para_start"]) + 1
                if (hi - lo + 1) / max(1, span) > 0.8:
                    warn.append(f"{a['event_id']} 与 {b['event_id']} 区间重叠 >80%，疑似重复")
    return {"events": events, "fatal": fatal, "warn": warn}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--aliases", default="")
    ap.add_argument("--answers", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    aliases = [x.strip() for x in a.aliases.split(",") if x.strip()] or [a.target]
    raw = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    r = build(a.chapter, a.target, aliases, raw)
    out = Path(a.out) if a.out else ROOT / f"novels/fenghua/out/ch{a.chapter}.events.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"第{a.chapter}章 事件 {len(r['events'])} 条 | 致命 {len(r['fatal'])} | 警告 {len(r['warn'])}")
    for f in r["fatal"]:
        print(f"  ❌ {f}")
    for w in r["warn"]:
        print(f"  ⚠ {w}")
    print(f"已写入 {out}")
    return 1 if r["fatal"] else 0


if __name__ == "__main__":
    sys.exit(main())
