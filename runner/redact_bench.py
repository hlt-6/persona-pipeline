#!/usr/bin/env python3
"""把完整答案卷（benchmark/）脱敏成可公开的 benchmark-public/。

完整答案卷里每条都带台词原文和引用原文的依据片段，共约 1.2 万字原著引用，
不能进公开仓库。脱敏后保留：

  idx        答题包里的编号（与 packets/ 一致，可复现）
  speaker    判定结果
  evidence   段落指针 + 判定类型（剥掉所有引号内的原文）
  confidence 置信度
  note       低置信原因
  len        台词字数（供统计，不含内容）

丢掉：line（台词全文）、excluded 的文本（改存编号）。

拿到原著合法副本的人可以用它完整复现评测：
  python runner/bench_to_answers.py --from-public --chapters 330
  python runner/apply_answers.py --chapter 330 --answers ...

用法：
  python runner/redact_bench.py --chapters 324,330,339,630,357,610
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import chapter_index  # noqa: E402
from runner.pipeline.quote_audit import enumerate_quotes, normalize  # noqa: E402

BENCH = ROOT / "novels/fenghua/benchmark"
PUBLIC = ROOT / "novels/fenghua/benchmark-public"

QUOTED = re.compile(r"『[^』]*』|「[^」]*」|“[^”]*”|\"[^\"]*\"")


def strip_quotes(s: str) -> str:
    """剥掉依据里的原文引用，只留段落指针与判定类型的说明。"""
    s = QUOTED.sub("〔原文〕", s or "")
    s = re.sub(r"〔原文〕{2,}", "〔原文〕", s)
    s = re.sub(r"\s+", "", s)
    return s.strip("，,、 ")


def code_of(line: dict) -> str:
    """依据码必须在剥引号之前算：关键字（说道/标记）常落在引文内部。"""
    e = line.get("evidence") or ""
    if line.get("confidence") == "low" or line.get("speaker") == "未知":
        return "U"
    if "标记" in e or "说道" in e or "动词" in e:
        return "M"
    if "称呼" in e:
        return "A"
    if "动作" in e:
        return "S"
    return "T"


def convert(no: int) -> dict:
    body = chapter_index()[no][1]
    spans = [s for s in enumerate_quotes(body) if s["span"] is not None]
    pool: dict[str, collections.deque] = collections.defaultdict(collections.deque)
    for s in spans:
        pool[normalize(s["span"])].append(s["idx"])

    gold = json.loads((BENCH / f"ch{no:03d}.json").read_text(encoding="utf-8"))
    lines, used = [], set()
    for ln in gold["lines"]:
        key = normalize(ln["line"])
        if not pool.get(key):
            raise SystemExit(f"第{no}章 benchmark 对不上原文引号，无法定位编号")
        idx = pool[key].popleft()
        used.add(idx)
        lines.append({
            "idx": idx,
            "speaker": ln["speaker"],
            "code": code_of(ln),
            "evidence": strip_quotes(ln.get("evidence", "")),
            "confidence": ln.get("confidence", "high"),
            "note": strip_quotes(ln.get("note", "")),
            "len": len(ln["line"]),
        })

    excl = []
    for e in gold.get("excluded", []):
        key = normalize(e)
        if pool.get(key):
            idx = pool[key].popleft()
            used.add(idx)
            excl.append(idx)

    missing = sorted({s["idx"] for s in spans} - used)
    assert not missing, f"第{no}章有引号未编号：{missing}"
    lines.sort(key=lambda x: x["idx"])
    return {
        "chapter": no,
        "spans": len(spans),
        "text_removed": True,
        "note": "台词原文与依据内引用已按版权要求剔除；idx 与 packets/ 中编号一致，"
                "配合原著可在本地完整复现。",
        "lines": lines,
        "excluded_idx": sorted(excl),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", default="324,330,339,630,357,610")
    a = ap.parse_args()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for n in [int(x) for x in a.chapters.split(",") if x.strip()]:
        d = convert(n)
        p = PUBLIC / f"ch{n:03d}.json"
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        kept = sum(len(x["evidence"]) for x in d["lines"])
        print(f"第{n}章 台词 {len(d['lines'])} 条 + 排除 {len(d['excluded_idx'])} 个，"
              f"依据说明 {kept} 字，原文 0 字 → {p.name}")


if __name__ == "__main__":
    main()
