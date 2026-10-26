"""角色语料探针：在全书中量化某个角色的分布与可抽取规模。

只做机械统计，不调用任何模型。用途是在真正跑抽取之前，先判断
"全书蒸馏"这件事的成本量级，而不是拍脑袋开工。

用法：
    python runner/probe_character.py 杨思思
    python runner/probe_character.py 杨思思 --alias 杨如画 思思 --top 20
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import MARKED_RE, chapter_index  # noqa: E402
from pipeline.quote_audit import enumerate_quotes  # noqa: E402

# "名字 + 说话动词" 与引号之间的最大字符距离（允许 "杨思思点了点头，说道：" 这类）
GAP = 12


def count_marked(body: str, names: list[str]) -> int:
    """机械命中上限：名字后紧跟说话动词、且其后不远出现引号。"""
    n = 0
    for m in MARKED_RE.finditer(body):
        if m.group(1) not in names:
            continue
        tail = body[m.end(): m.end() + GAP]
        if any(q in tail for q in "“”「"):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--alias", nargs="*", default=[])
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    names = [args.name, *args.alias]
    idx = chapter_index()
    print(f"全书章数：{len(idx)}")

    hit_chapters: list[tuple[int, str, int, int, int]] = []  # 章号,标题,引号数,提及数,机械命中
    total_quotes = 0
    total_mentions = 0
    total_marked = 0
    per_chapter_mentions: Counter[int] = Counter()

    for no in sorted(idx):
        _, body = idx[no]
        mentions = sum(body.count(x) for x in names)
        if mentions == 0:
            continue
        quotes = enumerate_quotes(body)
        marked = count_marked(body, names)
        title = ""
        hit_chapters.append((no, title, len(quotes), mentions, marked))
        total_quotes += len(quotes)
        total_mentions += mentions
        total_marked += marked
        per_chapter_mentions[no] = mentions

    print(f"出现「{'/'.join(names)}」的章数：{len(hit_chapters)}")
    print(f"这些章的字数合计：{sum(len(idx[n][1]) for n, *_ in hit_chapters):,}")
    print(f"名字提及总次数：{total_mentions}")
    print(f"这些章内的引号片段总数：{total_quotes}")
    print(f"机械可命中（名字+说话动词+引号）：{total_marked}")
    print()

    print(f"提及密度 Top {args.top}：")
    print(f"{'章':>5} {'提及':>4} {'引号':>4} {'机械命中':>6}")
    for no, _, q, m, k in sorted(hit_chapters, key=lambda r: -r[3])[: args.top]:
        print(f"{no:>5} {m:>4} {q:>4} {k:>6}")

    print()
    marks = sorted(per_chapter_mentions.values(), reverse=True)
    for lo in (1, 3, 5, 10):
        c = sum(1 for v in marks if v >= lo)
        q = sum(r[2] for r in hit_chapters if r[3] >= lo)
        print(f"提及 ≥{lo} 次的章：{c} 章，引号片段 {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
