#!/usr/bin/env python
"""校验器自检：确认 verify 的判定不是一律报错（假阴性），而是真能区分三种情况。

    python runner/selftest.py --corpus data/xiyouji.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pipeline.verify import _locate, normalize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/xiyouji.txt")
    args = ap.parse_args()

    p = Path(args.corpus)
    if not p.exists():
        print(f"[FAIL] 语料不存在：{p}")
        return 2
    raw = p.read_text(encoding="utf-8", errors="ignore")
    norm = normalize(raw)
    start = len(norm) // 2

    cases = []

    # 1) 真原文：从语料中间截 40 字
    real = norm[start:start + 40]
    cases.append(("真实原文片段", real, "verified"))

    # 2) 改写：替换 4 个字
    fake = real[:10] + "天地玄黄" + real[14:]
    cases.append(("原文改 4 字", fake, "paraphrase"))

    # 3) 编造：不存在于语料
    made = "俺老孙今日特来讨一杯酒吃，你快快把宝贝献上来"
    cases.append(("凭空编造", made, "missing"))

    # 4) 短引文（4 字）应能被命中但含金量低
    short = "行者道"
    cases.append(("4 字常见短语", short, "verified"))

    print(f"语料：{p}（{len(raw)} 字）")
    print(f"{'用例':<16}{'期望':<12}{'实际':<12}{'score':<8}{'结论'}")
    print("-" * 58)
    ok = True
    for name, quote, expect in cases:
        status, score, _ = _locate(norm, normalize(quote), 0.90)
        good = status == expect
        ok &= good
        print(f"{name:<16}{expect:<12}{status:<12}{score:<8.3f}{'通过' if good else '不符'}")
        print(f"    引文：{quote[:36]}")

    print("-" * 58)
    print("[OK] 校验器可区分三种判定" if ok else "[FAIL] 存在误判，需检查归一化或阈值")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
