"""台词风格量化：把"说话什么味儿"变成可核对的数据。

人格卡里最虚的一栏是"语域/句长与节奏"，通常靠感觉写。这个脚本用
纯统计给出硬指标：分句长度分布、疑问/感叹/省略号比例、语气词频次、
自称与他称、高频片段。写卡时照着数据写，别照着印象写。

无第三方依赖（不分词，用字符 n-gram）。

用法：
    python runner/style_profile.py 杨思思 --dir novels/fenghua/out-yangsis-v2
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

SENT_END = "。！？…”』」"
PARTICLES = ["啊", "呀", "吧", "呢", "嘛", "哦", "噢", "哟", "呗", "啦", "咯", "唉", "哎", "嘿", "哈"]
SELF = ["我", "人家", "老娘", "姑奶奶", "姐"]
# 称呼对方的常见前缀/后缀模式
ADDRESS_PAT = re.compile(r"(老[\u4e00-\u9fa5]|小[\u4e00-\u9fa5]|[\u4e00-\u9fa5]{2,3}(?:哥|姐|叔|姨|总|指导|教练|老师))")


def split_sents(text: str) -> list[str]:
    out, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in SENT_END:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    if buf:
        s = "".join(buf).strip()
        if s:
            out.append(s)
    return out


def ngrams(lines: list[str], n: int, top: int) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for ln in lines:
        s = re.sub(r"[^\u4e00-\u9fa5]", "", ln)
        for i in range(len(s) - n + 1):
            c[s[i:i + n]] += 1
    # 过滤跨句无意义片段：只保留在本角色台词里反复出现的
    return [(k, v) for k, v in c.most_common(top * 3) if v >= 3][:top]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--dir", required=True, help="回填产物目录，如 novels/fenghua/out-<角色>")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    lines: list[str] = []
    for f in sorted(glob.glob(f"{args.dir}/ch*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for l in d["lines"]:
            if l.get("speaker") == args.name:
                t = (l.get("line") or "").strip()
                if t:
                    lines.append(t)

    if not lines:
        print("没有该角色的台词", file=sys.stderr)
        return 1

    sents: list[str] = []
    for ln in lines:
        sents += split_sents(ln)
    lens = [len(re.sub(r"[^\u4e00-\u9fa5]", "", s)) for s in sents]
    lens = [x for x in lens if x > 0]

    n = len(lines)
    print(f"=== {args.name} 台词 {n} 条 / 分句 {len(sents)} 个 / 正文 {sum(len(x) for x in lines)} 字 ===")
    print()
    print("句长（按汉字计）：")
    avg = sum(lens) / max(1, len(lens))
    srt = sorted(lens)
    print(f"  平均 {avg:.1f} 字　中位 {srt[len(srt) // 2]} 字　最长 {max(lens)} 字")
    for lo, hi, lab in [(1, 4, "≤4字（短促应答）"), (5, 10, "5-10字"),
                        (11, 20, "11-20字"), (21, 40, "21-40字"), (41, 999, ">40字（长段）")]:
        c = sum(1 for x in lens if lo <= x <= hi)
        print(f"  {lab:<16}{c:>5} 条 {c / max(1, len(lens)) * 100:>5.1f}%")

    print("\n标点/语气：")
    allc = "".join(lines)
    for ch in ["？", "！", "…", "，", "。"]:
        print(f"  {ch}：{allc.count(ch)} 次（每百条 {allc.count(ch) / n * 100:.0f}）")
    print("\n语气词（出现在台词里的条数占比）：")
    for p in PARTICLES:
        c = sum(1 for x in lines if p in x)
        if c / max(1, n) >= 0.03:
            print(f"  {p}：{c} 条（{c / n * 100:.0f}%）")

    print("\n自称：")
    for s in SELF:
        c = sum(1 for x in lines if s in x)
        if c:
            print(f"  {s}：{c} 条")

    print("\n她怎么称呼别人（模式匹配，出现 ≥3 次）：")
    addr: Counter[str] = Counter()
    for x in lines:
        for m in ADDRESS_PAT.finditer(x):
            addr[m.group(1)] += 1
    for k, v in addr.most_common(15):
        if v >= 3:
            print(f"  {k}：{v}")

    print(f"\n高频三字片段（≥3 次，多半是口头禅）：")
    for k, v in ngrams(lines, 3, args.top):
        print(f"  {k}：{v}")

    print("\n最短的 12 条（看应答习惯）：")
    for x in sorted(set(lines), key=len)[:12]:
        print(f"  [{'·' * 0}{len(x)}字] {x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
