#!/usr/bin/env python
"""抓取维基文库公版语料（默认《西游记》），转为简体后存为纯文本，供 verify --corpus 使用。

    python runner/fetch_corpus.py --work 西遊記 --chapters 100 --out data/xiyouji.txt

支持增量续抓：已存在的回不会重复请求；遇 429 会指数退避重试。
仅用于公版作品，请遵守维基文库使用条款并控制频率。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://zh.wikisource.org/w/api.php"
HEAD = re.compile(r"^=== 第(\d+)回 ===$", re.M)


def _get(params: dict, retry: int = 4) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "persona-pipeline/0.2 (corpus fetch)"})
    last = None
    for attempt in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                wait = 5 * (attempt + 1)
                sys.stderr.write(f"\n[429 限流] 等待 {wait}s 后重试…\n")
                time.sleep(wait)
                continue
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"重试 {retry} 次仍失败：{last}")


def _clean(wikitext: str) -> str:
    s = wikitext
    if s.lstrip().startswith("{{"):                      # 去掉开头 header 模板
        depth, i = 0, s.find("{{")
        while i < len(s):
            if s.startswith("{{", i):
                depth, i = depth + 1, i + 2
            elif s.startswith("}}", i):
                depth, i = depth - 1, i + 2
                if depth == 0:
                    s = s[i:]
                    break
            else:
                i += 1
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"'''?", "", s)
    s = re.sub(r"^[:*#]+\s*", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _load_existing(out: Path) -> dict[int, str]:
    if not out.exists():
        return {}
    text = out.read_text(encoding="utf-8")
    idx = [(m.start(), int(m.group(1))) for m in HEAD.finditer(text)]
    got = {}
    for k, (pos, n) in enumerate(idx):
        end = idx[k + 1][0] if k + 1 < len(idx) else len(text)
        got[n] = text[pos:end].strip()
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="西遊記")
    ap.add_argument("--chapters", type=int, default=100)
    ap.add_argument("--out", default="data/xiyouji.txt")
    ap.add_argument("--sleep", type=float, default=1.2)
    args = ap.parse_args()

    try:
        import opencc
    except ImportError:
        opencc = None
        print("提示：未安装 opencc，保留繁体原文（pip install opencc-python-reimplemented）")

    out = Path(args.out)
    got = _load_existing(out)
    if got:
        print(f"已有 {len(got)} 回，将跳过并续抓")

    failed = []
    for n in range(1, args.chapters + 1):
        if n in got:
            continue
        try:
            r = _get({"action": "parse", "page": f"{args.work}/第{n:03d}回",
                      "prop": "wikitext", "format": "json", "formatversion": 2})
            if "error" in r:
                failed.append(f"{n}: {r['error'].get('info', 'error')}")
                continue
            text = _clean(r["parse"]["wikitext"])
            if opencc:
                text = opencc.OpenCC("t2s").convert(text)
            got[n] = f"=== 第{n}回 ===\n{text}"
            sys.stdout.write(f"\r已抓 {len(got)}/{args.chapters} 回，"
                             f"{sum(len(v) for v in got.values())} 字   ")
            sys.stdout.flush()
        except Exception as e:  # noqa: BLE001
            failed.append(f"{n}: {e}")
        time.sleep(args.sleep)

    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n\n".join(got[n] for n in sorted(got))
    out.write_text(body, encoding="utf-8")
    print(f"\n已写入 {out}：{len(got)}/{args.chapters} 回，{out.stat().st_size / 1024:.0f} KB")
    if failed:
        print(f"仍失败 {len(failed)} 回：{failed[:6]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
