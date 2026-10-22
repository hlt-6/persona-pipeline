#!/usr/bin/env python3
"""从全书切出指定章节，落到 novels/fenghua/chapters/。

切分口径与 extract.chapter_index 完全一致（同一套正则、同一套重复章号处理），
避免"工具算的段落"和"人读的文件"对不上。

用法：
  python runner/split_chapters.py --chapters 357,610
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import CHAPTER_DIR, chapter_index, load_chapter  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", required=True, help="如 357,610")
    a = ap.parse_args()

    idx = chapter_index()
    nos = [int(x) for x in a.chapters.split(",") if x.strip()]
    CHAPTER_DIR.mkdir(parents=True, exist_ok=True)

    mf = CHAPTER_DIR / "manifest.json"
    manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}

    for n in nos:
        if n not in idx:
            print(f"第{n}章 不存在（全书 {len(idx)} 章）")
            continue
        title, body = load_chapter(n)
        p = CHAPTER_DIR / f"ch{n:03d}.txt"
        p.write_text(f"第{n}章 {title}\n\n{body}", encoding="utf-8")
        paras = [x for x in body.split("\n") if x.strip()]
        manifest[str(n)] = {"file": f"ch{n:03d}.txt", "title": title,
                            "chars": len(body), "paras": len(paras),
                            "quotes": body.count("“")}
        print(f"第{n}章 {title}　{len(body)} 字　{len(paras)} 段　"
              f"{body.count('“')} 处开引号")
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nmanifest 现有 {len(manifest)} 章")


if __name__ == "__main__":
    main()
