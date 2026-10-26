"""角色时间线 + 分层选章。

两个用途：
1. 全量统计某个角色在全书中的存在曲线（提及密度、对话密度），
   用来判断人物弧线与转折点，而不是只凭印象挑章节。
2. 按时间段分层抽样出待抽取章节：跨全书均匀取，避免只读开头
   导致人格样本停留在初期状态。

用法：
    python runner/char_timeline.py 杨思思 --alias 杨如画
    python runner/char_timeline.py 杨思思 --alias 杨如画 --pick 40 --out novels/fenghua/pick-yangsis.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import chapter_index  # noqa: E402
from pipeline.quote_audit import enumerate_quotes  # noqa: E402

QUOTE_CHARS = "“”「」"


def dialogue_rows(body: str) -> int:
    """含引号的行数：比"引号片段数"更接近真实对话轮次。"""
    return sum(1 for ln in body.split("\n") if any(q in ln for q in QUOTE_CHARS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--alias", nargs="*", default=[])
    ap.add_argument("--pick", type=int, default=0, help="分层抽样章数，0 表示不选")
    ap.add_argument("--out", help="选章结果写入的 json")
    ap.add_argument("--bucket", type=int, default=50, help="曲线聚合的章数粒度")
    args = ap.parse_args()

    names = [args.name, *args.alias]
    idx = chapter_index()
    chapters = sorted(idx)

    rows = []
    for no in chapters:
        _, body = idx[no]
        mention = sum(body.count(x) for x in names)
        if mention == 0:
            continue
        rows.append({
            "ch": no,
            "mention": mention,
            "quotes": len(enumerate_quotes(body)),
            "rows": dialogue_rows(body),
            "chars": len(body),
        })

    if not rows:
        print("未找到任何提及", file=sys.stderr)
        return 1

    first, last = rows[0]["ch"], rows[-1]["ch"]
    print(f"全书 {len(chapters)} 章；该角色出现 {len(rows)} 章，首见第 {first} 章，末见第 {last} 章")
    print()

    print(f"存在曲线（每 {args.bucket} 章聚合：章数 / 提及 / 引号）：")
    buckets: dict[int, list[dict]] = {}
    for r in rows:
        b = (r["ch"] - 1) // args.bucket * args.bucket + 1
        buckets.setdefault(b, []).append(r)
    peak_b, peak_v = None, -1
    for b in sorted(buckets):
        g = buckets[b]
        m = sum(x["mention"] for x in g)
        q = sum(x["quotes"] for x in g)
        bar = "█" * max(1, round(m / 6)) if m else ""
        print(f"  {b:>4}-{b + args.bucket - 1:<4} {len(g):>3}章 {m:>5}提 {q:>5}引 {bar}")
        if m > peak_v:
            peak_b, peak_v = b, m
    print(f"\n提及最集中的区段：第 {peak_b}-{peak_b + args.bucket - 1} 章（{peak_v} 次）")

    if not args.pick:
        return 0

    # 分层抽样：按出现章序号四等分，各段内按 提及×对话行 的乘积降序取
    n = args.pick
    seg = max(1, len(rows) // 4)
    picks: list[dict] = []
    quota = [n // 4 + (1 if i < n % 4 else 0) for i in range(4)]
    for i in range(4):
        lo = i * seg
        hi = len(rows) if i == 3 else (i + 1) * seg
        part = sorted(rows[lo:hi], key=lambda r: -(r["mention"] * r["rows"]))
        picks += part[: quota[i]]

    picks.sort(key=lambda r: r["ch"])
    tot_q = sum(p["quotes"] for p in picks)
    tot_r = sum(p["rows"] for p in picks)
    tot_c = sum(p["chars"] for p in picks)
    print(f"\n分层抽样 {len(picks)} 章：正文 {tot_c:,} 字 / 对话行 {tot_r} / 引号片段 {tot_q}")
    print("章号：" + " ".join(str(p["ch"]) for p in picks))

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "character": args.name,
            "aliases": args.alias,
            "total_chapters": len(chapters),
            "appeared": len(rows),
            "first": first,
            "last": last,
            "picked": picks,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
