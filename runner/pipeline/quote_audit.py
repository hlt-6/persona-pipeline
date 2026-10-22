"""引号枚举与漏检审计。

dsh 提示词要求"一条不漏"，但没有任何机制能验证是否漏了。
本模块把章节原文里所有成对引号机械枚举出来，用于：
  1. 给模型/人工一个可核对的清单（第几条，在哪一段）
  2. 事后审计：提取结果是否覆盖了全部引号片段
  3. 区分"被排除项"（人名、外号、术语被引号包起来）与"真漏检"
"""
from __future__ import annotations

import re

OPEN = "“"
CLOSE = "”"

# 纯笑声/语气词/拟声片段，仍属台词，必须提取（不算"单字引号异常"）
LAUGH_RE = re.compile(r"^[咯嘻嘿呵哈唉哎嗯哦噢呀呃吁嗐哟喔欸嘁呸哼嗷\s、，。…！？~～]+$")


def normalize(text: str) -> str:
    """归一化：统一全半角、去所有空白与常见标点，用于逐字比对。"""
    import unicodedata

    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    return re.sub(r"[\s\u3000·・.,;:!?\"'、，。；：！？…—－\-~～()（）《》〈〉\[\]【】]", "", t)


def enumerate_quotes(chapter: str) -> list[dict]:
    """按出现顺序枚举所有成对引号片段。

    返回 [{idx, para, span, start, end, raw}]，para 为所在段落序号（从 1 起）。
    未闭合的开引号会以 span=None 记录，便于发现原文本身的引号错误。
    """
    paras = chapter.split("\n")
    marks: list[tuple[int, int]] = []  # (line_idx, col)
    pos = 0
    line_starts = []
    for line in paras:
        line_starts.append(pos)
        for col, ch in enumerate(line):
            if ch == OPEN:
                marks.append((pos, col))
        pos += len(line) + 1

    books: list[dict] = []
    pending: tuple[int, int, int, int] | None = None  # start_abs, line_idx, col, para_no
    abs_pos = 0
    line_idx = 0
    col = 0
    for ch in chapter:
        if ch == "\n":
            line_idx += 1
            col = 0
            abs_pos += 1
            continue
        if ch == OPEN and pending is None:
            pending = (abs_pos, line_idx, col, line_idx + 1)
        elif ch == CLOSE and pending is not None:
            s_abs, s_line, s_col, s_para = pending
            raw = chapter[s_abs + 1 : abs_pos]
            books.append(
                {
                    "idx": len(books) + 1,
                    "para": s_para,
                    "span": raw,
                    "start": s_abs,
                    "end": abs_pos,
                    "raw": chapter[s_abs : abs_pos + 1],
                }
            )
            pending = None
        abs_pos += 1
        col += 1
    if pending is not None:
        books.append(
            {
                "idx": len(books) + 1,
                "para": pending[3],
                "span": None,
                "start": pending[0],
                "end": None,
                "raw": chapter[pending[0] :],
            }
        )
    return books


def audit_coverage(chapter: str, lines: list[str], excluded: list[str] | None = None) -> dict:
    """审计提取结果对章节引号片段的覆盖率。

    lines: 提取出的台词的 line 字段
    excluded: 被显式排除的引号片段（人名/外号/术语）

    **一对一消耗匹配**：同一条提取结果只能抵扣一个引号片段。
    早期版本用"存在性判断"，同一章里两处「咯咯」时，一条结果会同时抵扣两条，
    导致重复答 + 漏答在文本层面互相掩盖、覆盖率仍报 100%。现在必须逐条消耗。
    """
    from collections import Counter

    spans = enumerate_quotes(chapter)
    excluded_norm = {normalize(e) for e in (excluded or []) if e}
    chap_norm = normalize(chapter)
    line_norm = [normalize(x) for x in lines if x]
    pool = Counter(line_norm)

    hit, missed, excl = [], [], []
    for sp in spans:
        if sp["span"] is None:
            missed.append({"idx": sp["idx"], "para": sp["para"], "reason": "引号未闭合", "span": None})
            continue
        n = normalize(sp["span"])
        if n in excluded_norm:
            excl.append({"idx": sp["idx"], "para": sp["para"], "span": sp["span"]})
            continue
        matched = None
        if n and n in chap_norm:
            for ln in line_norm:
                if pool[ln] > 0 and (n in ln or ln in n):
                    matched = ln
                    break
        if matched is not None:
            pool[matched] -= 1
            hit.append(sp["idx"])
        else:
            missed.append(
                {
                    "idx": sp["idx"],
                    "para": sp["para"],
                    "span": sp["span"],
                    "reason": "未出现在提取结果中（或同文台词已被另一条抵扣）",
                }
            )

    leftover = sum(v for v in pool.values() if v > 0)
    return {
        "quoted_spans": len(spans),
        "extracted_lines": len(lines),
        "covered": len(hit),
        "excluded": len(excl),
        "missed": missed,
        "excluded_detail": excl,
        "unmatched_lines": leftover,
        "coverage": round(len(hit) / max(1, len(spans) - len(excl)), 4),
    }


def check_verbatim(chapter: str, lines: list[str]) -> list[dict]:
    """逐条核验台词是否真的逐字出现在本章原文中。"""
    chap_norm = normalize(chapter)
    out = []
    for i, ln in enumerate(lines, 1):
        n = normalize(ln)
        if not n:
            out.append({"idx": i, "status": "empty", "line": ln})
        elif n in chap_norm:
            out.append({"idx": i, "status": "verbatim", "line": ln})
        else:
            out.append({"idx": i, "status": "not_in_chapter", "line": ln})
    return out
