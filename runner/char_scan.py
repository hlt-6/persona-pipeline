"""角色称呼演化 + 共现网络（全量，零模型成本）。

蒸馏人格时，最容易搞错的是"跨章一致性"：同一个人在不同阶段被叫不同名字，
称呼本身是最硬的性格证据（谁在什么情境下改口、改口前后态度如何）。
这些用统计就能拿到，不必靠模型读完全书。

用法：
    python runner/char_scan.py 杨思思 --alias 杨如画 思思 --bucket 100
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import chapter_index  # noqa: E402
from rulepass import load_roster  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--alias", nargs="*", default=[])
    ap.add_argument("--bucket", type=int, default=100)
    ap.add_argument("--roster", default="novels/fenghua/roster.json")
    ap.add_argument("--out")
    args = ap.parse_args()

    names = [args.name, *args.alias]
    roster = [n for n in load_roster(Path(args.roster)) if len(n) >= 2]
    idx = chapter_index()
    chapters = sorted(idx)

    print(f"=== 称呼演化（每 {args.bucket} 章：各名字出现次数）===")
    header = "章段".ljust(12) + "".join(n.ljust(10) for n in names)
    print(header)
    # 逐名统计，看改口发生在哪一段
    for b in range(0, chapters[-1], args.bucket):
        seg = [c for c in chapters if b < c <= b + args.bucket]
        text = "\n".join(idx[c][1] for c in seg)
        counts = [text.count(n) for n in names]
        if not any(counts):
            continue
        print(f"{b + 1}-{b + args.bucket}".ljust(12) + "".join(str(c).ljust(10) for c in counts))

    print("\n=== 共现网络（同章出现的次数，仅计与主角有交集的）===")
    co: Counter[str] = Counter()
    for c in chapters:
        body = idx[c][1]
        if not any(n in body for n in names):
            continue
        for r in roster:
            if r in names:
                continue
            if r in body:
                co[r] += 1
    for r, n in co.most_common(15):
        print(f"  {r:<8} {n:>4} 章")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "character": args.name,
            "aliases": args.alias,
            "cooccurrence": dict(co.most_common(30)),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
