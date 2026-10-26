#!/usr/bin/env python3
"""每章追溯记录 + verify 报告。

用途：ACCEPTANCE.md 第 7 节要求每章留存可追溯信息，缺任一字段的章视为"未验收"。
本脚本把判读结果整理成**不含原文**的 trace，可入库。

每章字段：
  chapter, packet_sha256, answers_sha256, tool_version,
  span_count, answered, excluded, unknown, low_conf, alerts, verdict

verdict：
  pass    全部检查通过
  warn    通过但有告警（未知率/低置信率/排除率超阈）
  fail    编号不闭合、笑声缺失等硬错误
  missing 无package或无答卷

用法：
  python runner/trace.py                      # 全量
  python runner/trace.py --chapters 1-20
  python runner/trace.py --out-dir novels/fenghua/trace
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import runner.accept as A  # noqa: E402
from runner.extract import chapter_index  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def tool_version() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           cwd=ROOT, capture_output=True, text=True, timeout=5)
        return "runner@" + (r.stdout.strip() or "unknown")
    except Exception:  # noqa: BLE001
        return "runner@unknown"


def one(no: int, body: str, pdir: Path, adir: Path, ver: str) -> dict:
    p = pdir / f"ch{no:03d}.packet.md"
    a = adir / f"ch{no:03d}.json"
    rec = {
        "chapter": no,
        "packet_sha256": sha256(p),
        "answers_sha256": sha256(a),
        "tool_version": ver,
    }
    if not a.exists():
        rec.update(span_count=len([s for s in A.enumerate_quotes(body) if s["span"] is not None]),
                   answered=0, excluded=0, unknown=0, low_conf=0,
                   alerts=["无答卷"], verdict="missing")
        return rec
    try:
        ans = json.loads(a.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        rec.update(span_count=0, answered=0, excluded=0, unknown=0, low_conf=0,
                   alerts=[f"答卷不是合法 JSON: {e}"], verdict="fail")
        return rec

    r = A.check_answers(no, body, ans)
    alerts = list(r.get("errors", [])) + list(r.get("alerts", []))
    rec.update(
        span_count=r.get("spans", 0),
        answered=r.get("answered", 0),
        excluded=r.get("excluded", 0),
        unknown=r.get("unknown", 0),
        low_conf=r.get("low_conf", 0),
        alerts=alerts,
        verdict="fail" if r.get("errors") else ("warn" if alerts else "pass"),
    )
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "novels/fenghua"))
    ap.add_argument("--packets-dir", default="")
    ap.add_argument("--answers-dir", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--chapters", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(a.root)
    pdir = Path(a.packets_dir) if a.packets_dir else root / "packets"
    adir = Path(a.answers_dir) if a.answers_dir else root / "answers-full"
    odir = Path(a.out_dir) if a.out_dir else root / "trace"
    odir.mkdir(parents=True, exist_ok=True)

    idx = chapter_index()
    chapters = A.parse_chapters(a.chapters, idx.keys())
    ver = tool_version()

    recs = []
    for no in chapters:
        rec = one(no, idx[no][1], pdir, adir, ver)
        recs.append(rec)
        (odir / f"ch{no:03d}.trace.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

    cnt = Counter(r["verdict"] for r in recs)
    tot_ans = sum(r["answered"] for r in recs)
    tot_unk = sum(r["unknown"] for r in recs)
    tot_span = sum(r["span_count"] for r in recs)
    summary = {
        "tool_version": ver,
        "chapters": len(recs),
        "verdicts": dict(cnt),
        "span_count": tot_span,
        "answered": tot_ans,
        "excluded": sum(r["excluded"] for r in recs),
        "unknown": tot_unk,
        "low_conf": sum(r["low_conf"] for r in recs),
        "unknown_rate": round(tot_unk / tot_ans, 4) if tot_ans else 0.0,
        "alerts": [{"chapter": r["chapter"], "alerts": r["alerts"]}
                   for r in recs if r["alerts"]],
    }
    (odir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人类可读报告（不含原文）
    L = ["# verify 报告", "",
         f"- 工具版本：`{ver}`",
         f"- 章数：{len(recs)}",
         f"- 引号片段：{tot_span}",
         f"- 已答 / 排除 / 未知 / 低置信：{tot_ans} / {summary['excluded']} / {tot_unk} / {summary['low_conf']}",
         f"- 全书未知率：{summary['unknown_rate']:.2%}（阈值 5%）",
         "", "## verdict 分布", ""]
    for k in ("pass", "warn", "fail", "missing"):
        if cnt.get(k):
            L.append(f"- {k}: {cnt[k]}")
    if summary["alerts"]:
        L += ["", "## 告警章", ""]
        for item in summary["alerts"][:50]:
            L.append(f"- 第{item['chapter']}章：" + "；".join(item["alerts"][:3]))
    (odir / "verify-report.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"章数 {len(recs)} | " + " ".join(f"{k}={v}" for k, v in cnt.items()))
    print(f"引号 {tot_span} | 已答 {tot_ans} | 未知 {tot_unk}（{summary['unknown_rate']:.2%}）")
    print(f"输出 {odir}/ （每章 trace + summary.json + verify-report.md）")
    if a.json:
        Path(a.json).write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if not cnt.get("fail") and not cnt.get("missing") else 1


if __name__ == "__main__":
    raise SystemExit(main())
