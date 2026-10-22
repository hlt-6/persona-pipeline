#!/usr/bin/env python3
"""从全本小说里挖人物名单（零 token）。

思路：中文本小说里，人物名会反复出现在说话动词之前。把全本所有
「动词前 2-4 字」都统计一遍，出现次数低于阈值的（颤声/半晌/口向他 这类）
自然被过滤掉，剩下的就是真名单。

产出 novels/fenghua/roster.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERB = ("说道|问道|笑道|骂道|喊道|吼道|回道|应道|答道|叫道|喝道|叹道|补充道|解释道|"
        "嘀咕道|嘟囔道|开口道|反问道|打断道|哭道|央求道|低声道|沉声道|冷声道|"
        "说道|问道")
BAD = set("着的地得了很都就也又便才却则再还只刚已未不没把被让给和与在是有个这那说问叫笑骂喊答回叹哭"
          "我你他她们咱这那些个么什怎样吗呢吧啊哦嗯呃呀哈嘿唉哎")
STOP = {"一边说", "稍稍停", "然后", "随即", "终于", "随后", "于是", "所以", "但是", "因为",
        "接着", "这时", "此时", "那人", "有人", "大家", "我们", "你们", "他们", "对方",
        "一边", "停了停", "笑了笑", "顿了顿", "想了想", "看了看", "愣了", "沉默"}

PAT = re.compile(rf"([\u4e00-\u9fa5]{{2,4}})(?:{VERB})")

# 剥掉粘连的介词/副词前缀与语气性后缀，露出真正的名字
PRE = re.compile(r"^(?:对|向|冲着|朝|和|跟|给|被|把|然后|随即|终于|接着|随后|于是|又|便|就|才|也|却|则|稍稍|这时|此时|继而)+")
TAIL = re.compile(r"(?:低声|沉声|冷声|轻声|颤声|高声|笑着|哭着|忙|才|又|也|便|就)$")


def clean(tok: str) -> str | None:
    t = PRE.sub("", tok)
    t = TAIL.sub("", t)
    if len(t) < 2 or len(t) > 3:
        return None
    if any(c in BAD for c in t) or t in STOP:
        return None
    return t


def mine(novel: Path, threshold: int) -> dict:
    text = novel.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cnt: Counter[str] = Counter()
    for m in PAT.finditer(text):
        n = clean(m.group(1))
        if n:
            cnt[n] += 1

    # 统计特征两道过滤：
    #   1) 副词几乎只贴在说话动词前 -> near/total 偏高
    #   2) 名字能带「的」作定语（杨思思的、陈进的），副词不能（半晌的、开口的）
    kept = {}
    for tok, near in cnt.items():
        if near < threshold:
            continue
        total = text.count(tok)
        if total and near / total > 0.45:
            continue
        if text.count(tok + "的") < 2:
            continue
        kept[tok] = near

    # 短名被长名包含时合并（「杨思」并入「杨思思」）
    names = sorted(kept, key=len, reverse=True)
    final = {}
    for n in names:
        parent = next((o for o in names if o != n and n in o), None)
        if parent:
            kept[parent] = kept.get(parent, 0) + kept[n]
        else:
            final[n] = kept[n]
    return {
        "threshold": threshold,
        "candidates": len(cnt),
        "names": dict(sorted(final.items(), key=lambda kv: -kv[1])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--novel", default=r"C:/Users/pc/Desktop/我在风花雪月里等你（简体）.txt")
    ap.add_argument("--threshold", type=int, default=12)
    ap.add_argument("--out", default="novels/fenghua/roster.json")
    a = ap.parse_args()
    r = mine(Path(a.novel), a.threshold)
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"候选 {r['candidates']} 个，阈值 {a.threshold} 后保留 {len(r['names'])} 个：")
    items = list(r["names"].items())
    for i in range(0, min(len(items), 60), 6):
        print("  " + "　".join(f"{k}({v})" for k, v in items[i:i + 6]))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
