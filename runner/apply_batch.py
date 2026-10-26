#!/usr/bin/env python3
"""单进程批量回填。

`apply_answers.py` 一次只处理一章，每章重启解释器会重复解析 6MB 原文；
全书 771 章按单章跑要读 4.6GB。本脚本复用 chapter_index 缓存，一次进程跑完。

用法：
  python runner/apply_batch.py --answers-dir novels/fenghua/answers-full \
      --out-dir novels/fenghua/out-full --chapters 1-110
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

import runner.accept as A  # noqa: E402
from runner.apply_answers import build, save_result  # noqa: E402
from runner.extract import chapter_index  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--chapters", default="")
    ap.add_argument("--model", default="packet-answer")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    adir, odir = Path(a.answers_dir), Path(a.out_dir)
    odir.mkdir(parents=True, exist_ok=True)
    idx = chapter_index()
    chapters = A.parse_chapters(a.chapters, idx.keys())

    ok = fail = missing = 0
    fails = []
    t0 = time.time()
    for no in chapters:
        f = adir / f"ch{no:03d}.json"
        if not f.exists():
            missing += 1
            print(f"  缺答卷：第{no}章")
            continue
        raw = json.loads(f.read_text(encoding="utf-8"))
        built = build(no, raw)
        res = save_result(no, "", built["body"], built["lines"], built["excluded"],
                          {"model": a.model, "mode": "packet-answer",
                           "prompt": "runner/rulepass.py --packet", "elapsed": 0},
                          id_audit=built["id_audit"], out_dir=odir)
        aud = built["id_audit"]
        if aud["ok"] and not built["skipped"]:
            ok += 1
        else:
            fail += 1
            detail = []
            if aud.get("duplicate_ids"):
                detail.append(f"重复{aud['duplicate_ids'][:5]}")
            if aud.get("missing_ids"):
                detail.append(f"缺失{aud['missing_ids'][:5]}")
            if aud.get("unknown_ids"):
                detail.append(f"越界{aud['unknown_ids'][:5]}")
            if aud.get("answered_and_excluded"):
                detail.append(f"既答又排{aud['answered_and_excluded'][:5]}")
            if built["skipped"]:
                detail.append(f"跳过{built['skipped'][:5]}")
            fails.append((no, "；".join(detail)))
            print(f"  ✗ 第{no}章 " + "；".join(detail))

    el = time.time() - t0
    print(f"=== 回填 {len(chapters)} 章：通过 {ok} / 失败 {fail} / 缺 {missing}，用时 {el:.1f}s ===")
    if fails:
        print("失败明细：")
        for no, d in fails:
            print(f"  第{no}章：{d}")
    return 0 if fail == 0 and missing == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
