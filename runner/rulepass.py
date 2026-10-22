#!/usr/bin/env python3
"""规则预判层：不花一个 token，先把"原文自己说清楚了"的台词判掉。

目的：把调用模型的次数和上下文压到最小。原文里只要有"某某说道／问道／笑道"
这类明确标记，归属就不需要模型判断；模型只需处理剩下的模糊尾巴。

三条规则：
  R1 同段前置标记   本段引号之前最近的「名字+说话动词」            -> high
  R2 前段收尾标记   上一段以「名字+说话动词」收尾，本段直接开口    -> high
  R3 双人交替       全章只有两个说话人时，按已锚定的轮次交替补齐  -> medium

判不了的标 needs_model，交给模型（或人工）只处理这些。

用法：
  python runner/rulepass.py --chapter 330            # 看预判结果
  python runner/rulepass.py --chapter 330 --eval     # 与 benchmark 对答案
  python runner/rulepass.py --chapter 330 --context  # 只输出待判清单（喂给模型）
  python runner/rulepass.py --batch 324,330,339,630 --eval
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import chapter_index  # noqa: E402
from runner.pipeline.quote_audit import enumerate_quotes, normalize  # noqa: E402

VERB = ("说道|问道|笑道|骂道|喊道|吼道|回道|应道|答道|叫道|喝道|叹道|补充道|解释道|"
        "嘀咕道|嘟囔道|开口道|反问道|打断道|哭道|央求道")
VERB_TAIL = re.compile(rf"((?:{VERB}))")
NAME_BEFORE = re.compile(
    rf"([\u4e00-\u9fa5]{{2,3}})"
    rf"(?:又|一边|便|就|才|也|却|则|随即|终于|稍稍|然后|接着|随后)?"
    rf"(?:对[\u4e00-\u9fa5]{{1,3}}|冲着[\u4e00-\u9fa5]{{1,3}})?"
    rf"(?:{VERB})"
)
BAD = set("着的地得了很都就也又便才却则再还只刚已未不没把被让给和与在是有个这那说问叫笑骂喊答回叹哭")
STOP = {"一边说", "稍稍停", "然后", "随即", "终于", "随后", "于是", "所以", "但是", "因为",
        "接着", "这时", "此时", "那人", "有人", "大家", "我们", "你们", "他们", "对方"}

# 明显不是台词的引号内容（人名/地名/品牌/术语/口号）
NON_SPEECH_HINT = re.compile(r"^[\u4e00-\u9fa5A-Za-z]{2,8}$")


def clean_name(n: str) -> str | None:
    if n in STOP or any(c in BAD for c in n):
        return None
    return n


def find_markers(body: str) -> list[tuple[int, str]]:
    """全章「名字+说话动词」标记，返回 [(绝对位置, 名字)]，按位置排序。"""
    out = []
    for m in NAME_BEFORE.finditer(body):
        n = clean_name(m.group(1))
        if n:
            out.append((m.start(), n))
    return out


def paragraphs(body: str) -> list[tuple[int, int]]:
    """返回 [(段首绝对位置, 段尾绝对位置)]。"""
    out, pos = [], 0
    for line in body.split("\n"):
        out.append((pos, pos + len(line)))
        pos += len(line) + 1
    return out


def load_roster(path: Path) -> list[str]:
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    return list(d.get("names", {}).keys())


NARRATOR = "米高"
JUNK_ROSTER = {"开口", "一会儿", "点头", "继续", "众人", "半天", "曹学", "小青年", "久之后", "刻之后"}


def resolve_subject(clause: str, roster: list[str]) -> str | None:
    """在一个子句里解析说话主语：优先最右侧的名单人名，其次第一人称代词。"""
    best, best_pos = None, -1
    for n in roster:
        if n in JUNK_ROSTER:
            continue
        p = clause.rfind(n)
        if p > best_pos:
            best, best_pos = n, p
    if best:
        return best
    if "我" in clause:
        return NARRATOR
    return None


def clause_before(text: str, pos: int, window: int = 16) -> str:
    """取 pos 之前的最后一个子句（以标点切分），最多 window 字。"""
    seg = text[max(0, pos - window):pos]
    parts = re.split(r"[。！？…，；：、\n“”\s]", seg)
    parts = [p for p in parts if p.strip()]
    return parts[-1] if parts else seg


def rulepass(chapter_no: int, roster: list[str]) -> dict:
    _, body = chapter_index()[chapter_no]
    spans = enumerate_quotes(body)
    paras = paragraphs(body)

    # 标记 = (位置, 主语)，主语通过子句解析得到
    markers: list[tuple[int, str]] = []
    for m in NAME_BEFORE.finditer(body):
        verb_pos = m.start() + len(m.group(1))
        subj = resolve_subject(clause_before(body, m.start()), roster)
        if subj:
            markers.append((m.start(), subj))

    def para_of(pos: int) -> int:
        for i, (s, e) in enumerate(paras):
            if s <= pos <= e:
                return i
        return len(paras) - 1

    # 先给每条引号定位：所在段、段内引号序号
    for sp in spans:
        sp["para"] = para_of(sp["start"])

    rows = []
    for sp in spans:
        if sp["span"] is None:
            rows.append({"idx": sp["idx"], "line": None, "speaker": None,
                         "method": "skip", "confidence": None})
            continue
        pi = sp["para"]
        p_start, _ = paras[pi]
        # R1：同段、引号之前最近的标记
        before = [(pos, n) for pos, n in markers if p_start <= pos < sp["start"]]
        if before:
            rows.append({"idx": sp["idx"], "line": sp["span"], "speaker": before[-1][1],
                         "method": "R1 同段前置标记", "confidence": "high"})
            continue
        # R2：上一段以标记收尾（标记在引号之前且属上一段，且上一段无引号）
        if pi > 0:
            prev_s, prev_e = paras[pi - 1]
            prev_has_quote = any(prev_s <= q["start"] <= prev_e for q in spans if q["span"])
            prev_marks = [(pos, n) for pos, n in markers if prev_s <= pos <= prev_e]
            if prev_marks and not prev_has_quote:
                rows.append({"idx": sp["idx"], "line": sp["span"], "speaker": prev_marks[-1][1],
                             "method": "R2 前段收尾标记", "confidence": "high"})
                continue
        rows.append({"idx": sp["idx"], "line": sp["span"], "speaker": None,
                     "method": "needs_model", "confidence": None})

    # R3：双人交替。只对 needs_model 的条目生效，且要求全章说话人恰好两人
    known = [r["speaker"] for r in rows if r["speaker"]]
    speakers = sorted(set(known))
    if len(speakers) == 2 and known:
        a, b = speakers
        resolved = 0
        for i, r in enumerate(rows):
            if r["speaker"] is not None:
                continue
            # 向前找最近已定条目，按其说话人的"另一个"填
            prev = next((rows[j] for j in range(i - 1, -1, -1) if rows[j]["speaker"]), None)
            nxt = next((rows[j] for j in range(i + 1, len(rows)) if rows[j]["speaker"]), None)
            guess = None
            if prev:
                prev_sp = prev["speaker"]
                # 若前后锚点一致，说明中间是插话/换人，不猜
                if nxt and nxt["speaker"] == prev_sp:
                    continue
                guess = b if prev_sp == a else a
            elif nxt:
                guess = b if nxt["speaker"] == a else a
            if guess:
                r.update({"speaker": guess, "method": "R3 双人交替", "confidence": "medium"})
                resolved += 1
        if resolved:
            pass

    return {"chapter": chapter_no, "body": body, "rows": rows, "paragraphs": paras}


def evaluate(no: int, golden_path: Path, roster: list[str]) -> dict:
    rp = rulepass(no, roster)
    body = rp["body"]
    gold = json.loads(golden_path.read_text(encoding="utf-8"))
    gold_by_line = {}
    for g in gold["lines"]:
        gold_by_line.setdefault(normalize(g["line"]), []).append(g["speaker"])
    excl = {normalize(x) for x in gold.get("excluded", [])}

    stats = {"chapter": no, "total": len(rp["rows"]), "resolved": 0, "correct": 0,
             "wrong": 0, "needs_model": 0, "excluded": 0, "errors": [],
             "by_method": {}}
    for r in rp["rows"]:
        if r["method"] == "skip":
            continue
        nl = normalize(r["line"])
        if nl in excl:
            stats["excluded"] += 1
            continue
        if r["speaker"] is None:
            stats["needs_model"] += 1
            continue
        stats["resolved"] += 1
        bm = stats["by_method"].setdefault(r["method"], {"n": 0, "ok": 0})
        bm["n"] += 1
        exp = gold_by_line.get(nl)
        if exp and r["speaker"] in exp:
            stats["correct"] += 1
            bm["ok"] += 1
        elif exp:
            stats["wrong"] += 1
            stats["errors"].append({"idx": r["idx"], "line": r["line"][:36],
                                    "pred": r["speaker"], "gold": exp[0],
                                    "method": r["method"]})
        else:
            stats["errors"].append({"idx": r["idx"], "line": r["line"][:36],
                                    "pred": r["speaker"], "gold": "(基准里没有这条)",
                                    "method": r["method"]})
    stats["accuracy"] = round(stats["correct"] / max(1, stats["resolved"]), 4)
    stats["auto_rate"] = round(stats["resolved"] / max(1, len(rp["rows"]) - stats["excluded"]), 4)
    return stats


def context_digest(no: int, roster: list[str], window: int = 2) -> str:
    """只输出待判清单 + 每条的上下文窗口，代替整章喂给模型。"""
    rp = rulepass(no, roster)
    body, paras = rp["body"], rp["paragraphs"]
    out = [f"## 第{no}章 待判台词（规则层无法定归属，共 "
           f"{sum(1 for r in rp['rows'] if r['method'] == 'needs_model')} 条）\n"]
    seen = set()
    for r in rp["rows"]:
        if r["method"] != "needs_model":
            continue
        sp = next(s for s in enumerate_quotes(body) if s["idx"] == r["idx"])
        pi = sp["para"]
        lo, hi = max(0, pi - window), min(len(paras) - 1, pi + window)
        key = (lo, hi)
        if key in seen:
            out.append(f"- 〔第{r['idx']}条〕{r['line']}")
            continue
        seen.add(key)
        out.append(f"\n### 上下文 段{lo + 1}-{hi + 1}（包含第{r['idx']}条）")
        for k in range(lo, hi + 1):
            s, e = paras[k]
            txt = body[s:e].strip()
            mark = "  ← 待判" if k == pi else ""
            out.append(f"[{k + 1}] {txt}{mark}")
    return "\n".join(out)


def request_unknown() -> str:
    return "（未识别，请按原文判断）"


def make_packet(no: int, roster: list[str], rulefill: bool = False) -> tuple[str, dict]:
    """生成"编号答题包"：把编号直接嵌进原文的每个引号前。

    模型照常读原文（上下文一点不少），但回答时只回编号+说话人，
    不重抄台词——省掉绝大部分输出 token，也彻底消除"抄错/编造台词"。
    """
    idx = chapter_index()
    body = idx[no][1]
    title = idx[no][1][:0]  # 占位，标题下面单独取
    spans = [s for s in enumerate_quotes(body) if s["span"] is not None]

    # 在原位插入 [n] 编号
    out, cursor = [], 0
    for s in spans:
        out.append(body[cursor:s["start"]])
        out.append(f"[{s['idx']}]")
        cursor = s["start"]
    out.append(body[cursor:])
    annotated = "".join(out)

    roster_in = [n for n in roster if n in body and n not in JUNK_ROSTER]
    if NARRATOR not in roster_in and "我" in body:
        roster_in = [NARRATOR] + roster_in

    L = [f"## 原文（第{no}章）", "",
         f"本章出现的人名：{'、'.join(roster_in) if roster_in else request_unknown()}",
         "（含仅被提及者，谁在说话请看原文判断；人名表只是提示，不是白名单）",
         f"全文共 {len(spans)} 处引号，每处前面已标好编号 [n]。", "",
         annotated, "",
         "## 回答格式（只回这一段 JSON，不要重抄台词）", "",
         '{"a":[[1,"说话人","M"],[2,"说话人","T"]],'
         '"x":[引号内不是台词的编号],"n":{"编号":"低置信原因"}}', "",
         "## 什么必须归属、什么可以排除（口径，别自己发明）", "",
         "- **笑声与语气词也是台词**：咯咯、嘿嘿、呵呵、哈哈、嗯、唉、啊、噢……"
         "这类片段一律填进 a 并给出说话人，**不许放进 x**。",
         "- **x 只放引号内根本不是说话内容的片段**：店名、地名、品牌、术语、书名、"
         "被别人引用的绰号。拿不准就按台词处理并标 U，不要图省事丢进 x。",
         "- **齐声/群体发言**：多人同时说可用集体名（如「三个小孩」「众人」），不算未知。",
         "- **每个编号必须出现且只出现一次**：要么在 a 里，要么在 x 里。漏一个会被审计判失败。",
         "",
         "依据码：M=同段说话动词标记　T=对话轮次　A=称呼线索　S=动作主语　U=无法判断",
         "说话人用本名（杨思思 不写 杨如画/思思/四哥）。判断不了就填 未知 + U。"]
    packet = "\n".join(L)
    meta = {"chapter": no, "spans": len(spans), "chars": len(packet),
            "roster": roster_in, "body_chars": len(body)}
    return packet, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", type=int)
    ap.add_argument("--batch", help="如 324,330,339,630")
    ap.add_argument("--eval", action="store_true", help="与 benchmark 对答案")
    ap.add_argument("--golden-dir", default="novels/fenghua/benchmark")
    ap.add_argument("--context", action="store_true", help="输出待判清单（省 token 用）")
    ap.add_argument("--packet", action="store_true", help="生成编号答题包（最省 token）")
    ap.add_argument("--rulefill", action="store_true", help="答题包里附带规则初判提示")
    ap.add_argument("--packet-out", default="novels/fenghua/packets")
    a = ap.parse_args()

    nos = [int(x) for x in a.batch.split(",")] if a.batch else [a.chapter]
    gdir = ROOT / a.golden_dir
    roster = load_roster(ROOT / "novels/fenghua/roster.json")

    if a.packet:
        outdir = ROOT / a.packet_out
        outdir.mkdir(parents=True, exist_ok=True)
        total_chars = 0
        for n in nos:
            pkt, meta = make_packet(n, roster, a.rulefill)
            (outdir / f"ch{n:03d}.packet.md").write_text(pkt, encoding="utf-8")
            total_chars += meta["chars"]
            print(f"第{n}章：{meta['spans']} 条引号，包体 {meta['chars']} 字 → "
                  f"{a.packet_out}/ch{n:03d}.packet.md")
            if len(nos) == 1:
                print("\n" + pkt)
        print(f"\n包体合计 {total_chars} 字")
        return

    if a.context:
        for n in nos:
            print(context_digest(n, roster))
        return

    if a.eval:
        tot = {"total": 0, "resolved": 0, "correct": 0, "wrong": 0, "needs_model": 0,
               "excluded": 0}
        print(f"{'章节':<8}{'引号':>5}{'排除':>5}{'规则判定':>9}{'其中正确':>9}"
              f"{'仍待模型':>9}{'准确率':>8}{'自动化率':>9}")
        for n in nos:
            s = evaluate(n, gdir / f"ch{n:03d}.json", roster)
            for k in tot:
                tot[k] += s[k]
            print(f"第{n}章{'':<3}{s['total']:>5}{s['excluded']:>5}{s['resolved']:>9}"
                  f"{s['correct']:>9}{s['needs_model']:>9}"
                  f"{s['accuracy'] * 100:>7.1f}%{s['auto_rate'] * 100:>8.1f}%")
            for e in s["errors"]:
                print(f"    ✗ [{e['idx']}] 判成 {e['pred']}，基准是 {e['gold']}"
                      f"　{e['line']}　（{e['method']}）")
        base = tot["resolved"] + tot["needs_model"]
        print(f"{'合计':<8}{tot['total']:>5}{tot['excluded']:>5}{tot['resolved']:>9}"
              f"{tot['correct']:>9}{tot['needs_model']:>9}"
              f"{tot['correct'] / max(1, tot['resolved']) * 100:>7.1f}%"
              f"{tot['resolved'] / max(1, base) * 100:>8.1f}%")
        agg = {}
        for n in nos:
            s = evaluate(n, gdir / f"ch{n:03d}.json", roster)
            for k, v in s["by_method"].items():
                a = agg.setdefault(k, {"n": 0, "ok": 0})
                a["n"] += v["n"]
                a["ok"] += v["ok"]
        print("\n分规则精度")
        for k, v in agg.items():
            print(f"  {k:<14} 判定 {v['n']:>3} 条，正确 {v['ok']:>3} 条，"
                  f"精度 {v['ok'] / max(1, v['n']) * 100:.1f}%")
        return

    rp = rulepass(nos[0], roster)
    for r in rp["rows"]:
        sp = r["speaker"] or "?"
        print(f"{r['idx']:>3} [{r['method']:<10}] {sp:<6} {str(r['line'])[:44]}")


if __name__ == "__main__":
    main()
