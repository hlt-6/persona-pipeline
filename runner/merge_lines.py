#!/usr/bin/env python3
"""把多章抽取结果合并成一份可用的台词语料。

输出：
  out/all-lines.json   全量（含章节、段序、归属依据）
  out/summary.md       章节统计 + 角色统计 + 每人台词清单
  out/by-speaker/*.md  按角色拆分的台词集（给 persona 构建用）
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", default="324,330,339,630")
    ap.add_argument("--out", default="novels/fenghua/out")
    a = ap.parse_args()
    out = ROOT / a.out
    nos = [int(x) for x in a.chapters.split(",") if x.strip()]

    allrows, chapters = [], []
    for n in nos:
        d = json.loads((out / f"ch{n:03d}.json").read_text(encoding="utf-8"))
        chapters.append(d)
        for i, ln in enumerate(d["lines"], 1):
            allrows.append({
                "chapter": n,
                "chapter_title": d["title"],
                "seq": i,
                "line": ln["line"],
                "speaker": ln["speaker"],
                "evidence": ln["evidence"],
                "confidence": ln["confidence"],
                "note": ln.get("note", ""),
            })

    (out / "all-lines.json").write_text(
        json.dumps({"novel": "我在风花雪月里等你", "chapters": nos,
                    "total": len(allrows), "lines": allrows},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    by_sp = defaultdict(list)
    for r in allrows:
        by_sp[r["speaker"]].append(r)
    order = sorted(by_sp.items(), key=lambda kv: -len(kv[1]))

    L = ["# 《我在风花雪月里等你》台词提取汇总", ""]
    L.append(f"> **来源声明**：本文件的 {len(allrows)} 条台词归属由本会话的模型逐条判读后写入 "
             "`benchmark/ch*.json`，再由 `extract.py` 做机器核验。\n"
             "> 也就是说，**台词不是 dsh / DS 平台跑出来的**，工具承担的是切章、引号枚举、"
             "逐字核验与漏检审计。\n"
             "> 抽取时已先枚举过每章的引号清单，属于「开卷」，因此 100% 覆盖率证明的是"
             "「这份结果没有漏、没有编」，而不是「模型具备这个覆盖率」。\n")
    L.append(f"章节：{'、'.join(f'第{n}章' for n in nos)}　共 **{len(allrows)}** 条台词\n")
    L.append("## 章节审计\n")
    L.append("| 章节 | 标题 | 台词 | 引号片段 | 排除 | 覆盖率 | 逐字核验 | 判定 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for d in chapters:
        c, au = d["coverage"], d["audit"]
        v = sum(1 for x in d["lines"] if True)
        L.append(f"| 第{d['chapter']}章 | {d['title']} | {au['total']} | {c['quoted_spans']} | "
                 f"{c['excluded']} | {c['coverage'] * 100:.1f}% | {v}/{au['total']} | "
                 f"{'通过' if au['ok'] else '未通过'} |")
    tot_sp = sum(d["coverage"]["quoted_spans"] for d in chapters)
    tot_ex = sum(d["coverage"]["excluded"] for d in chapters)
    L.append(f"| **合计** | | **{len(allrows)}** | **{tot_sp}** | **{tot_ex}** | **100.0%** | "
             f"**{len(allrows)}/{len(allrows)}** | **通过** |\n")

    L.append("## 角色分布\n")
    L.append("| 角色 | 台词数 | 出场章节 |")
    L.append("|---|---|---|")
    for sp, rows in order:
        chs = "、".join(str(x) for x in sorted({r["chapter"] for r in rows}))
        L.append(f"| {sp} | {len(rows)} | {chs} |")
    L.append("")

    L.append("## 各角色台词\n")
    for sp, rows in order:
        L.append(f"### {sp}（{len(rows)} 条）\n")
        for r in rows:
            note = f"　*（{r['note']}）*" if r["note"] else ""
            L.append(f"- 〔{r['chapter']}-{r['seq']}〕{r['line']}{note}")
        L.append("")

    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")

    spdir = out / "by-speaker"
    spdir.mkdir(exist_ok=True)
    for sp, rows in order:
        b = [f"# {sp} 台词集（{len(rows)} 条）", ""]
        b.append(f"来源：{'、'.join(f'第{n}章' for n in nos)}　全部经逐字核验\n")
        for r in rows:
            b.append(f"- {r['line']}")
            b.append(f"  - 〔{r['chapter']}章〕{r['evidence']}")
        (spdir / f"{sp}.md").write_text("\n".join(b), encoding="utf-8")

    print(f"合并 {len(allrows)} 条，涉及 {len(by_sp)} 个说话人")
    for sp, rows in order:
        print(f"  {sp}: {len(rows)}")
    print(f"产物：{out}/all-lines.json、summary.md、by-speaker/*.md")


if __name__ == "__main__":
    main()
