#!/usr/bin/env python3
"""核验器反证测试（tamper test）：故意注入错误，确认每一类都能被拦住。

覆盖两类错误：
  A 内容层（verify）  改写台词 / 未出场说话人 / 用别名 / 空证据 / 非法置信度
  B 答卷层（audit_ids）重复编号 / 漏号 / 越界编号 / 既答又排除 / 同文不同编号

其中「同文不同编号」是这次返包复审发现的静默丢句通道：
同一章两处「咯咯」，把两条都答成同一个编号，文本级覆盖率仍是 100%，
只有编号级对账能发现另一个编号从未被回答。

用法：
  python runner/tamper_test.py                 # 默认用第 330 章
  python runner/tamper_test.py --chapter 339
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.apply_answers import build  # noqa: E402
from runner.extract import chapter_index, save_result  # noqa: E402
from runner.pipeline.quote_audit import enumerate_quotes  # noqa: E402


def probe(no: int, body: str, lines: list[dict], excluded: list[str],
          id_audit: dict | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        r = save_result(no, "tamper", body, lines, excluded,
                        {"model": "tamper-test", "mode": "tamper"}, id_audit, out_dir=td)
        return {"ok": r["ok"], "fatal": r["fatal"], "warn": r["warn"],
                "coverage": r["coverage"]["coverage"],
                "unmatched": r["coverage"].get("unmatched_lines", 0),
                "id_ok": (id_audit or {}).get("ok"),
                "issues": r["issues"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, default=330)
    a = ap.parse_args()
    no = a.chapter

    raw = json.loads((ROOT / f"novels/fenghua/answers/ch{no:03d}.json").read_text(encoding="utf-8"))
    base = build(no, raw)
    body = base["body"]
    spans = base["spans"]

    # 找一对"同文不同编号"（如两处「咯咯」），没有就跳过该用例
    groups = defaultdict(list)
    for idx, s in spans.items():
        groups[s["span"]].append(idx)
    twins = next((v for v in groups.values() if len(v) > 1), None)

    cases = []

    def add(name, kind, mut=None, expect=None):
        cases.append((name, kind, mut, expect))

    add("基准（未注入）", "none", expect="通过")
    # 改写样本从原文现取，不把台词写死在代码里（原著正文不入库）
    _l0 = base["lines"][0]["line"]
    _tampered = (_l0[:-2] + "××") if len(_l0) > 3 else (_l0 + "×")
    add("改写台词 2 字", "content", ("line", 0, _tampered), expect="致命")
    add("换成本章未出场的人", "content", ("speaker", 1, "陈进"), expect="致命")
    add("说话人填别名（四哥）", "content", ("speaker", 2, "四哥"), expect="警告")
    add("抽掉判断依据", "content", ("evidence", 3, ""), expect="致命")
    add("置信度填非法值", "content", ("confidence", 4, "medium"), expect="警告")
    add("删掉一条答案（漏号）", "ids", ("drop", 0), expect="编号不闭合")
    add("同一个编号答两次", "ids", ("dup", 0), expect="编号不闭合")
    add("排除一个不存在的编号", "ids", ("ghost_exclude", 999), expect="编号不闭合")
    add("同一编号既作答又排除", "ids", ("both", 0), expect="编号不闭合")
    if twins:
        add(f"同文不同编号（{twins[0]}/{twins[1]} 都答成 {twins[0]}）", "ids",
            ("twin", (twins[0], twins[1])), expect="编号不闭合")

    rows = []
    for name, kind, mut, expect in cases:
        ans = copy.deepcopy(raw)
        if kind == "content":
            field, i, val = mut
            lines = copy.deepcopy(base["lines"])
            lines[i][field] = val
            id_audit = base["id_audit"]
            excluded = base["excluded"]
        elif kind == "ids":
            lines, excluded = base["lines"], base["excluded"]
            op = mut[0]
            if op == "drop":
                ans["a"] = [x for j, x in enumerate(ans["a"]) if j != mut[1]]
            elif op == "dup":
                victim = ans["a"][mut[1]]
                ans["a"] = ans["a"] + [list(victim)]
            elif op == "ghost_exclude":
                ans.setdefault("x", []).append(mut[1])
            elif op == "both":
                ans.setdefault("x", []).append(ans["a"][mut[1]][0])
            elif op == "twin":
                keep, drop = mut[1]
                for x in ans["a"]:
                    if x[0] == drop:
                        x[0] = keep
            b = build(no, ans)
            lines, excluded, id_audit = b["lines"], b["excluded"], b["id_audit"]
        else:
            lines, excluded, id_audit = base["lines"], base["excluded"], base["id_audit"]

        r = probe(no, body, lines, excluded, id_audit)
        blocked = not r["ok"]
        if expect == "通过":
            passed = r["ok"]
        elif expect == "致命":
            passed = r["fatal"] > 0 and blocked
        elif expect == "警告":
            passed = r["warn"] > 0          # 警告是合法降级，不算失败，但必须报出来
        else:                                # 编号不闭合
            passed = (r["id_ok"] is False) and blocked
        rows.append({"case": name, "expect": expect, "blocked": blocked, **r,
                     "pass": passed})

    print(f"第{no}章 反证测试（{len(rows)} 个用例）\n")
    print(f"{'用例':<34}{'预期':<12}{'致命':>4}{'警告':>4}{'文本覆盖':>9}{'编号闭合':>9}  结论")
    print("-" * 92)
    for r in rows:
        cov = f"{r['coverage'] * 100:.0f}%"
        idok = "—" if r["id_ok"] is None else ("✅" if r["id_ok"] else "❌")
        print(f"{r['case']:<34}{r['expect']:<12}{r['fatal']:>4}{r['warn']:>4}"
              f"{cov:>9}{idok:>9}  {'PASS' if r['pass'] else 'FAIL'}")

    bad = [r for r in rows if not r["pass"]]
    print()
    print("=== 结论 ===")
    print(f"  用例 {len(rows)} 个，PASS {len(rows) - len(bad)}，FAIL {len(bad)}")
    for r in bad:
        print(f"  ❌ FAIL：{r['case']}（预期 {r['expect']}，实际致命{r['fatal']}/警告{r['warn']}"
              f"/编号{'闭合' if r['id_ok'] else '不闭合'}）")

    # 存档
    out = ROOT / f"novels/fenghua/out/_tamper-test.report.md"
    L = [f"# 第{no}章 核验器反证测试\n",
         f"用 `runner/tamper_test.py` 生成。逐条注入错误，确认能拦住；"
         f"「基准」一行必须通过，否则说明测试本身破坏了正确答案。\n",
         "| 用例 | 预期 | 致命 | 警告 | 文本覆盖率 | 编号闭合 | 结论 |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        idok = "—" if r["id_ok"] is None else ("✅" if r["id_ok"] else "❌")
        L.append(f"| {r['case']} | {r['expect']} | {r['fatal']} | {r['warn']} | "
                 f"{r['coverage'] * 100:.0f}% | {idok} | {'PASS' if r['pass'] else 'FAIL'} |")
    L.append("")
    L.append(f"结论：{len(rows)} 个用例，PASS {len(rows) - len(bad)}，FAIL {len(bad)}。")
    L.append("")
    L.append("> 关键用例：「同文不同编号」注入后文本覆盖率仍为 100%，"
             "只有编号级对账判失败——这正是返包复审 N1 指出的静默丢句通道。")
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"  报告：novels/fenghua/out/_tamper-test.report.md")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
