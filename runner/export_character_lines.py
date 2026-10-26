"""按角色导出台词，带对话语境。

蒸馏人格时需要的不是孤立台词，而是"她为什么这么说"——所以每个目标台词
都附上前一轮（谁在跟她说话），这样能看出回应关系，而不是一堆无上下文的句子。

用法：
    python runner/export_character_lines.py 杨思思 --dir novels/fenghua/out-yangsis-v2 \
        --out /tmp/lines.md
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import chapter_index  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--dir", required=True, help="回填产物目录，如 novels/fenghua/out-<角色>")
    ap.add_argument("--out")
    ap.add_argument("--context", type=int, default=1, help="附带前几轮对话")
    args = ap.parse_args()

    files = sorted(glob.glob(f"{args.dir}/ch*.json"))
    if not files:
        print("没有找到结果文件", file=sys.stderr)
        return 1

    idx = chapter_index()
    blocks: list[str] = []
    total = 0
    chars = 0
    peers: Counter[str] = Counter()
    chs: list[int] = []

    for f in files:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        no = d["chapter"]
        lines = d["lines"]
        title = (d.get("title") or "").strip()
        body = idx.get(no, (0, ""))[1]

        mine = [i for i, l in enumerate(lines) if l.get("speaker") == args.name]
        if not mine:
            continue
        chs.append(no)
        blocks.append(f"\n## 第{no}章 {title}（{len(mine)} 条）\n")
        for i in mine:
            l = lines[i]
            ctx = []
            for k in range(max(0, i - args.context), i):
                p = lines[k]
                ctx.append(f"  ← {p.get('speaker')}：{p.get('line')}")
            blocks.append("\n".join(ctx + [f"- **{args.name}**：{l.get('line')}"]))
            total += 1
            chars += len(l.get("line") or "")
            # 同章里她前后最近的非本人说话人，算"对话对象"
            for k in (i - 1, i + 1):
                if 0 <= k < len(lines):
                    who = lines[k].get("speaker")
                    if who and who != args.name and who != "未知":
                        peers[who] += 1
            # 说话时的伴随动作（台词所在段落的叙述部分）
            para = body.split("\n")
            if l.get("span_idx") is not None:
                for ln in para:
                    if (l.get("line") or "")[:8] in ln and l.get("line"):
                        act = ln.replace(l["line"], "")
                        if len(act) > 4:
                            pass
                        break

    head = [f"# {args.name} 台词集（{len(chs)} 章 / {total} 条 / {chars} 字）", ""]
    peer_s = "、".join(f"{k}{v}" for k, v in peers.most_common(12))
    head.append(f"高频对话对象：{peer_s}")
    head.append("")

    text = "\n".join(head + blocks)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"{total} 条 / {chars} 字 → {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
