#!/usr/bin/env python3
"""把 benchmark（完整答案卷）转成"答题包格式"（编号+说话人+依据码）。

用途：闭环自检——模拟一份模型答题，验证 apply_answers 的回填与编号对账。
**不是真实模型输出**，分数不能当模型能力看。

关键实现细节：同文台词（如两处「咯咯」）必须按出现顺序一对一消耗，
早期用 dict 以文本为 key，会互相覆盖，造成"重复答一个号 + 漏答一个号"，
而文本级覆盖率仍报 100%。本脚本显式做多重集匹配，并在结尾断言编号闭合。

用法：
  python runner/bench_to_answers.py --chapters 324,330,339,630
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import chapter_index  # noqa: E402
from runner.pipeline.quote_audit import enumerate_quotes, normalize  # noqa: E402

BENCH = ROOT / "novels/fenghua/benchmark"
PUBLIC = ROOT / "novels/fenghua/benchmark-public"
OUT = ROOT / "novels/fenghua/answers"


def code_of(line: dict) -> str:
    """把 benchmark 的自由文本依据映射成协议的依据码。

    默认 T（轮次推断）—— 依据多半是"承接上一句/上下文"这类叙述；
    只有明确出现说话动词标记才升为 M。M/T 只是溯源标签，不影响正确性判定。
    """
    e = line.get("evidence") or ""
    if line.get("confidence") == "low" or line.get("speaker") == "未知":
        return "U"
    if "标记" in e or "说道" in e or "说道" in e or "动词" in e:
        return "M"
    if "称呼" in e:
        return "A"
    if "动作" in e:
        return "S"
    return "T"


def convert(no: int) -> dict:
    body = chapter_index()[no][1]
    spans = [s for s in enumerate_quotes(body) if s["span"] is not None]
    # 按文本建多重集索引：文本 -> [编号...]（按出现顺序）
    pool: dict[str, collections.deque] = collections.defaultdict(collections.deque)
    for s in spans:
        pool[normalize(s["span"])].append(s["idx"])

    gold = json.loads((BENCH / f"ch{no:03d}.json").read_text(encoding="utf-8"))
    ans, notes, used = [], {}, set()
    for ln in gold["lines"]:
        key = normalize(ln["line"])
        if not pool.get(key):
            raise SystemExit(f"第{no}章 benchmark 有一条对不上原文引号：{ln['line'][:40]}")
        idx = pool[key].popleft()
        used.add(idx)
        code = code_of(ln)
        ans.append([idx, ln["speaker"], code])
        if code == "U" and (ln.get("note") or "").strip():
            notes[str(idx)] = ln["note"]

    excl = []
    for e in gold.get("excluded", []):
        key = normalize(e)
        if pool.get(key):
            idx = pool[key].popleft()
            used.add(idx)
            excl.append(idx)
        else:
            print(f"  ⚠ 排除项对不上引号编号：{e}")

    all_ids = {s["idx"] for s in spans}
    missing = sorted(all_ids - used)
    dup = [k for k, v in collections.Counter(x[0] for x in ans).items() if v > 1]
    assert not missing and not dup, f"第{no}章编号不闭合：missing={missing} dup={dup}"
    return {"a": ans, "x": sorted(excl), "n": notes}


def convert_public(no: int) -> dict:
    """从脱敏答案卷（只含编号，无原文）直接生成答题。

    公开仓库里的答案卷不含台词文本，编号已是权威索引，不需要按文本对齐。
    """
    gold = json.loads((PUBLIC / f"ch{no:03d}.json").read_text(encoding="utf-8"))
    body = chapter_index()[no][1]
    spans = [s for s in enumerate_quotes(body) if s["span"] is not None]
    all_ids = {s["idx"] for s in spans}

    ans, notes = [], {}
    for ln in gold["lines"]:
        idx = int(ln["idx"])
        # 脱敏卷已存好依据码（剥引号前算的，避免关键字被带走）
        code = ln.get("code") or code_of(ln)
        ans.append([idx, ln["speaker"], code])
        if code == "U" and (ln.get("note") or "").strip():
            notes[str(idx)] = ln["note"]
    excl = [int(x) for x in gold.get("excluded_idx", [])]

    missing = sorted(all_ids - {x[0] for x in ans} - set(excl))
    dup = [k for k, v in collections.Counter(x[0] for x in ans).items() if v > 1]
    assert not missing and not dup, f"第{no}章编号不闭合：missing={missing} dup={dup}"
    return {"a": ans, "x": sorted(excl), "n": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", default="324,330,339,630")
    ap.add_argument("--from-public", action="store_true",
                    help="用 benchmark-public/（脱敏版）生成，不需要按文本对齐")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for n in [int(x) for x in a.chapters.split(",") if x.strip()]:
        d = convert_public(n) if a.from_public else convert(n)
        p = OUT / f"ch{n:03d}.json"
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"第{n}章 答题 {len(d['a'])} 条 + 排除 {len(d['x'])} 个"
              f" = {len(d['a']) + len(d['x'])}，编号闭合 ✅")


if __name__ == "__main__":
    main()
