#!/usr/bin/env python3
"""把"编号答题"回填成完整结果，并走同一套核验。

模型只回了 [[编号, 说话人, 依据码], ...]，台词原文由本脚本从章节里机械取出
（不经过模型），所以不可能抄错或编造。

两条审计同时做：
  1. 文本级：覆盖率 = 已覆盖引号 / (全部引号 - 显式排除)，一对一消耗匹配
  2. 编号级：每个编号必须"被答"或"被排除"，且只能出现一次
     —— 文本级会被同文台词掩盖（两处「咯咯」时一条答案抵扣两个引号），
        编号级才抓得住真正的漏答

用法：
  python runner/apply_answers.py --chapter 330 --answers novels/fenghua/answers/ch330.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import chapter_index, save_result  # noqa: E402
from runner.pipeline.quote_audit import enumerate_quotes  # noqa: E402

CODE_EVIDENCE = {
    "M": "同段说话动词标记",
    "T": "对话轮次推断",
    "A": "称呼线索",
    "S": "动作主语",
    "U": "无法判断",
}


def audit_ids(spans: dict, answers: list, excluded_idx) -> dict:
    """编号级对账：每个引号编号必须"被答"或"被排除"，且只能出现一次。

    为什么必须在编号层面查：文本级覆盖率会被同文台词掩盖。
    同一章里两处「咯咯」，一条答案会同时抵扣两个引号片段，
    实际却有另一个编号从未被回答 —— 覆盖率照样 100%。
    """
    ans_ids = [int(item[0]) for item in answers]
    all_ids = set(spans)
    excl_ids = {int(x) for x in excluded_idx}
    dup_ids = sorted(k for k, v in Counter(ans_ids).items() if v > 1)
    unknown_ids = sorted((set(ans_ids) | excl_ids) - all_ids)
    both = sorted(set(ans_ids) & excl_ids)
    missing = sorted(all_ids - set(ans_ids) - excl_ids)
    return {
        "spans": len(spans),
        "answered_ids": len(set(ans_ids) & all_ids),
        "excluded_ids": len(excl_ids & all_ids),
        "duplicate_ids": dup_ids,
        "unknown_ids": unknown_ids,
        "answered_and_excluded": both,
        "missing_ids": missing,
        "ok": not (dup_ids or unknown_ids or both or missing),
    }


def build(chapter: int, raw: dict) -> dict:
    """把答题 JSON 展开成可核验的 lines/excluded，并做编号级对账。"""
    body = chapter_index()[chapter][1]
    spans = {s["idx"]: s for s in enumerate_quotes(body) if s["span"] is not None}
    answers = raw.get("a") or raw.get("answers") or []
    excluded_idx = set(raw.get("x") or [])
    reasons = raw.get("n") or {}

    lines, excluded, skipped = [], [], []
    for item in answers:
        n, sp = int(item[0]), str(item[1])
        code = (item[2] if len(item) > 2 else "U").upper()
        s = spans.get(n)
        if not s:
            skipped.append(n)
            continue
        lines.append({
            "line": s["span"],                      # 台词原文取自章节，不经模型
            "speaker": sp,
            "evidence": CODE_EVIDENCE.get(code, code),
            "confidence": "low" if code == "U" or sp == "未知" else "high",
            "note": reasons.get(str(n), "") or "",
            "span_idx": n,
        })
    for n in sorted(excluded_idx):
        s = spans.get(int(n))
        if s:
            excluded.append(s["span"])

    id_audit = audit_ids(spans, answers, excluded_idx)
    lines.sort(key=lambda x: x["span_idx"])
    return {"body": body, "spans": spans, "lines": lines, "excluded": excluded,
            "id_audit": id_audit, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--model", default="packet-answer")
    # 默认写 novels/fenghua/out；蒸馏别的角色时要单独落盘，否则会覆盖基准卷
    ap.add_argument("--out-dir", default=None)
    a = ap.parse_args()

    raw = json.loads(Path(a.answers).read_text(encoding="utf-8"))
    built = build(a.chapter, raw)
    lines, excluded, id_audit = built["lines"], built["excluded"], built["id_audit"]
    if built["skipped"]:
        print(f"  ⚠ 编号不存在，已跳过：{built['skipped']}")

    title = ""
    chfile = ROOT / f"novels/fenghua/chapters/ch{a.chapter:03d}.txt"
    if chfile.exists():
        first = chfile.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        title = first.split(" ", 1)[1].strip() if " " in first else first
    t0 = time.time()
    result = save_result(a.chapter, title, built["body"], lines, excluded,
                         {"model": a.model, "mode": "packet-answer",
                          "prompt": "runner/rulepass.py --packet",
                          "elapsed": round(time.time() - t0, 2)},
                         id_audit=id_audit,
                         out_dir=Path(a.out_dir) if a.out_dir else None)
    cov = result["coverage"]
    print(f"第{a.chapter}章 回填 {len(lines)} 条 | 引号 {cov['quoted_spans']} 个 | "
          f"覆盖率 {cov['coverage'] * 100:.1f}% | 逐字核验 "
          f"{sum(1 for v in result['verbatim'] if v['status'] == 'verbatim')}/{len(lines)}")
    if id_audit["ok"]:
        print(f"  编号对账 ✅ 答 {id_audit['answered_ids']} + 排除 {id_audit['excluded_ids']}"
              f" = {id_audit['spans']}，无重复、无缺失、无越界")
    else:
        if id_audit["duplicate_ids"]:
            print(f"  ❌ 重复作答的编号：{id_audit['duplicate_ids']}")
        if id_audit["missing_ids"]:
            print(f"  ❌ 既未作答也未排除的编号：{id_audit['missing_ids']}")
        if id_audit["unknown_ids"]:
            print(f"  ❌ 不存在的编号：{id_audit['unknown_ids']}")
        if id_audit["answered_and_excluded"]:
            print(f"  ❌ 既作答又被排除的编号：{id_audit['answered_and_excluded']}")
    print(f"致命 {result['fatal']} | 警告 {result['warn']} | "
          f"{'✅ 通过' if result['ok'] else '❌ 未通过'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
