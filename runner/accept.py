#!/usr/bin/env python3
"""全书验收：静态检查 / 答案核验 / 抽检清单 / 错人率算分。

对应 ACCEPTANCE.md v1.0 的判据。零第三方依赖，只用 stdlib。

四种模式：
  --static   判读【之前】跑：C1 文本冻结 / C2 章号集合 / C3 包-原文还原 / C4 包内编号
  --verify   判读【之后】跑：C4 编号闭合 / C6 笑声覆盖 / C7 单章集中度 / 全书未知率
  --sample   生成 30 章抽检清单（20 随机 + 10 特征），供人工复核
  --audit    读人工复核结果，算判错人率（C9，阈值 2%）

用法：
  python runner/accept.py --static
  python runner/accept.py --verify --answers-dir novels/fenghua/answers-full
  python runner/accept.py --sample --out novels/fenghua/audit-sample.json
  python runner/accept.py --audit novels/fenghua/audit-sample.json

退出码：0 = 全部通过；1 = 有阻断项；2 = 有告警但不阻断。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.extract import chapter_index  # noqa: E402
from runner.pipeline.quote_audit import LAUGH_RE, enumerate_quotes  # noqa: E402

# ---------------------------------------------------------------- 阈值（锁定）
WRONG_MAX = 0.02          # 判错人率
UNKNOWN_MAX = 0.05        # 全书未知率
CH_UNKNOWN_MAX = 0.10     # 单章未知率告警
CH_LOWCONF_MAX = 0.10     # 单章低置信率告警
CH_EXCLUDED_MAX = 0.20    # 单章排除率告警
TOP_SPEAKER_OBS = 0.85    # 单章单一说话人占比（观察项）
DISMISS_MAX = 0.05        # 口径分歧标注占比告警

SAMPLE_SEED = 20261030
SAMPLE_N_RANDOM = 20
SAMPLE_N_TOTAL = 30

COLLECTIVE = {"众人", "全场", "所有人", "大家", "三个小孩", "两人", "几人", "众女", "一群人"}
ALIAS = {
    "杨如画": "杨思思", "思思": "杨思思", "四哥": "杨思思", "如画": "杨思思",
    "我": "米高", "教练": "米高", "老米": "米高", "米高": "米高",
}
# 同一实体的称谓变体（组内互认）
GROUPS = [
    {"母亲", "她妈", "妈妈", "杨思思母亲", "思思她妈"},
    {"父亲", "她爸", "爸爸", "杨思思父亲", "思思她爸"},
]

_INDEX: dict | None = None


def index() -> dict:
    global _INDEX
    if _INDEX is None:
        _INDEX = chapter_index()
    return _INDEX


def canon(name: str) -> str:
    n = (name or "").strip()
    return ALIAS.get(n, n)


def same_entity(a: str, b: str) -> bool:
    a, b = canon(a), canon(b)
    if a == b:
        return True
    for g in GROUPS:
        if a in g and b in g:
            return True
    return False


# ---------------------------------------------------------------- 包结构
def split_packet(text: str) -> tuple[str, int]:
    """返回 (标注正文段, 声明引号数)。"""
    if "全文共" not in text:
        raise ValueError("找不到「全文共 N 处引号」声明行，包格式异常")
    declared = int(re.search(r"全文共\s*(\d+)\s*处引号", text).group(1))
    a = text.index("全文共")
    a = text.index("\n\n", a) + 2
    b = text.index("\n\n## 回答格式") if "\n\n## 回答格式" in text else len(text)
    return text[a:b], declared


def packet_ids(annotated: str) -> list[int]:
    return [int(x) for x in re.findall(r"\[(\d+)\]", annotated)]


def restore(annotated: str) -> str:
    return re.sub(r"\[\d+\]", "", annotated)


# ---------------------------------------------------------------- 单项检查
def check_packet(no: int, body: str, ptext: str) -> dict:
    r = {"chapter": no, "errors": [], "warns": []}
    try:
        ann, declared = split_packet(ptext)
    except Exception as e:  # noqa: BLE001
        r["errors"].append(f"包解析失败：{e}")
        return r

    ids = packet_ids(ann)
    span_n = len([s for s in enumerate_quotes(body) if s["span"] is not None])
    r.update(declared=declared, inserted=len(ids), spans=span_n)

    # C4（包内）：编号从 1 连续到 N，无重复
    if ids != list(range(1, len(ids) + 1)):
        dup = [k for k, v in Counter(ids).items() if v > 1]
        miss = sorted(set(range(1, max(ids) + 1)) - set(ids)) if ids else []
        r["errors"].append(f"包内编号不连续：重复{dup[:5]} 缺失{miss[:5]}")

    if declared != span_n:
        r["errors"].append(f"声明引号数 {declared} ≠ 原文枚举 {span_n}")

    if len(ids) != span_n:
        r["errors"].append(f"实际插入编号 {len(ids)} ≠ 原文枚举 {span_n}")

    # C3：去编号后必须逐字节还原原文
    if re.search(r"\[\d+\]", body):
        r["warns"].append("原文本身含 [数字]，无法用去标记法校验还原，本章跳过 C3")
    else:
        if restore(ann) != body:
            a, b = restore(ann), body
            pos = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
            r["errors"].append(
                f"包—原文还原不一致（长度 {len(a)} vs {len(b)}，首个差异位置 {pos}）"
            )
    return r


def check_answers(no: int, body: str, ans: dict) -> dict:
    r = {"chapter": no, "errors": [], "warns": [], "alerts": []}
    spans = {s["idx"]: s for s in enumerate_quotes(body) if s["span"] is not None}
    A = ans.get("a") or ans.get("answers") or []
    X = ans.get("x") or []
    notes = ans.get("n") or {}

    ans_ids = [int(i[0]) for i in A]
    x_ids = [int(i) for i in X]
    all_ids = set(spans)

    dup = sorted(k for k, v in Counter(ans_ids).items() if v > 1)
    unknown_ids = sorted((set(ans_ids) | set(x_ids)) - all_ids)
    both = sorted(set(ans_ids) & set(x_ids))
    missing = sorted(all_ids - set(ans_ids) - set(x_ids))

    if dup:
        r["errors"].append(f"编号重复：[{','.join(map(str, dup[:8]))}]")
    if unknown_ids:
        r["errors"].append(f"编号越界：[{','.join(map(str, unknown_ids[:8]))}]")
    if both:
        r["errors"].append(f"既答又排除：[{','.join(map(str, both[:8]))}]")
    if missing:
        r["errors"].append(f"编号缺失 {len(missing)} 个：[{','.join(map(str, missing[:8]))}]")

    # C6 笑声口径
    laugh = {i for i, s in spans.items() if LAUGH_RE.match(s["span"])}
    l_missing = sorted(laugh - set(ans_ids) - set(x_ids))
    l_in_x = sorted(laugh & set(x_ids))
    l_no_reason = [i for i in l_in_x if not str(notes.get(str(i), "")).strip()]
    if l_missing:
        r["errors"].append(f"笑声片段未被回答 {len(l_missing)} 个：[{','.join(map(str, l_missing[:8]))}]")
    if l_no_reason:
        r["errors"].append(f"笑声片段被排除且无理由：[{','.join(map(str, l_no_reason[:8]))}]")
    l_unknown = sum(1 for i in A if int(i[0]) in laugh and str(i[1]) == "未知")

    # C7 单章集中度
    n_ans = len(A)
    n_span = len(spans)
    unk = sum(1 for i in A if str(i[1]) == "未知")
    low = sum(1 for i in A if (i[2] if len(i) > 2 else "U").upper() == "U")

    r.update(spans=n_span, answered=n_ans, excluded=len(x_ids), unknown=unk,
             low_conf=low, laugh=len(laugh), laugh_unknown=l_unknown)

    if n_ans:
        if unk / n_ans > CH_UNKNOWN_MAX:
            r["alerts"].append(f"单章未知率 {unk / n_ans:.1%} > 10%")
        if low / n_ans > CH_UNKNOWN_MAX:
            r["alerts"].append(f"单章低置信率 {low / n_ans:.1%} > 10%")
        top = Counter(canon(str(i[1])) for i in A).most_common(1)
        if top and top[0][1] / n_ans > TOP_SPEAKER_OBS:
            r["warns"].append(f"单一说话人「{top[0][0]}」占 {top[0][1] / n_ans:.0%}（观察项）")
    if n_span and len(x_ids) / n_span > CH_EXCLUDED_MAX:
        r["alerts"].append(f"单章排除率 {len(x_ids) / n_span:.1%} > 20%")
    if l_unknown > 0 and l_unknown / max(len(laugh), 1) > CH_UNKNOWN_MAX:
        r["alerts"].append(f"笑声片段未知率 {l_unknown / max(len(laugh), 1):.1%} > 10%")

    return r


def check_lines(no: int, body: str, out: dict) -> dict:
    """C5：回填产物里的台词必须逐字来自原文（防工具层 span 错位）。"""
    r = {"chapter": no, "errors": [], "checked": 0}
    norm = lambda s: re.sub(r"[\s\u3000]", "", s)  # noqa: E731
    nb = norm(body)
    for ln in out.get("lines") or []:
        r["checked"] += 1
        if norm(ln.get("line") or "") not in nb:
            r["errors"].append(f"编号 {ln.get('span_idx')} 的台词不在原文中")
    return r


# ---------------------------------------------------------------- 模式实现
def parse_chapters(spec: str, keys) -> list[int]:
    if not spec:
        return sorted(keys)
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return [n for n in out if n in keys]


def freeze_path(root: Path) -> Path:
    return root / ".freeze.json"


def do_static(root: Path, chapters: list[int]) -> dict:
    idx = index()
    src = root / "source.txt"
    rep = {"mode": "static", "chapters": len(chapters), "errors": [], "warns": [], "items": []}

    # C1 文本冻结
    if src.exists():
        raw = src.read_bytes()
        cur = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "chapters": len(idx)}
        fp = freeze_path(root)
        if fp.exists():
            base = json.loads(fp.read_text(encoding="utf-8"))
            if base["sha256"] != cur["sha256"]:
                rep["errors"].append(
                    f"C1 文本已变更：预期 {base['sha256'][:12]} 实际 {cur['sha256'][:12]}，全部答案作废"
                )
        else:
            fp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
            rep["warns"].append(f"C1 已建立冻结基线 {cur['sha256'][:12]}（{fp}）")
        rep["freeze"] = cur["sha256"][:12]
    else:
        rep["warns"].append("找不到 source.txt，跳过 C1")

    # C2 章号集合
    pdir = root / "packets"
    have = set()
    if pdir.exists():
        for p in pdir.glob("ch*.packet.md"):
            m = re.match(r"ch(\d+)", p.name)
            if m:
                have.add(int(m.group(1)))
    expect = set(chapters)
    miss = sorted(expect - have)
    extra = sorted(have - set(idx))
    if miss:
        rep["errors"].append(f"C2 缺包 {len(miss)} 章：{[f'{m:03d}' for m in miss[:12]]}")
    if extra:
        rep["errors"].append(f"C2 存在越界章号：{extra[:12]}")
    rep["packets"] = len(have)

    # C3 / C4（包内）
    for no in [c for c in chapters if c in have]:
        p = pdir / f"ch{no:03d}.packet.md"
        body = idx[no][1]
        r = check_packet(no, body, p.read_text(encoding="utf-8"))
        rep["items"].append(r)
        rep["errors"].extend(f"第{no}章 {e}" for e in r["errors"])
        rep["warns"].extend(f"第{no}章 {w}" for w in r["warns"])
    return rep


def do_verify(root: Path, chapters: list[int], adir: Path, odir: Path | None) -> dict:
    idx = index()
    rep = {"mode": "verify", "chapters": 0, "errors": [], "warns": [], "alerts": [], "items": []}
    tot_ans = tot_unk = tot_laugh = 0

    for no in chapters:
        ap = adir / f"ch{no:03d}.json"
        if not ap.exists():
            rep["warns"].append(f"第{no}章 无答卷，跳过")
            continue
        body = idx[no][1]
        try:
            ans = json.loads(ap.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            rep["errors"].append(f"第{no}章 答卷不是合法 JSON：{e}")
            continue
        r = check_answers(no, body, ans)
        if odir:
            op = odir / f"ch{no:03d}.json"
            if op.exists():
                lr = check_lines(no, body, json.loads(op.read_text(encoding="utf-8")))
                r["errors"].extend(lr["errors"])
                r["lines_checked"] = lr["checked"]
        rep["items"].append(r)
        rep["errors"].extend(f"第{no}章 {e}" for e in r["errors"])
        rep["warns"].extend(f"第{no}章 {w}" for w in r["warns"])
        rep["alerts"].extend(f"第{no}章 {a}" for a in r["alerts"])
        rep["chapters"] += 1
        tot_ans += r.get("answered", 0)
        tot_unk += r.get("unknown", 0)
        tot_laugh += r.get("laugh", 0)

    # C8 全书未知率
    if tot_ans:
        rate = tot_unk / tot_ans
        rep.update(total_answered=tot_ans, total_unknown=tot_unk, unknown_rate=round(rate, 4))
        if rate > UNKNOWN_MAX:
            rep["errors"].append(f"C8 全书未知率 {rate:.2%} > 5%")
    rep["total_laugh"] = tot_laugh
    return rep


def do_sample(root: Path, chapters: list[int], adir: Path | None, target: str, out: Path) -> dict:
    idx = index()
    pdir = root / "packets"
    stat = {}
    for no in chapters:
        p = pdir / f"ch{no:03d}.packet.md"
        if not p.exists():
            continue
        body = idx[no][1]
        spans = [s for s in enumerate_quotes(body) if s["span"] is not None]
        laugh = sum(1 for s in spans if LAUGH_RE.match(s["span"]))
        stat[no] = {"spans": len(spans), "laugh": laugh}

    pool = sorted(stat)
    if len(pool) < SAMPLE_N_TOTAL:
        raise SystemExit(f"可用章只有 {len(pool)} 章，不足 {SAMPLE_N_TOTAL} 章")

    rnd = random.Random(SAMPLE_SEED)
    picked = rnd.sample(pool, SAMPLE_N_RANDOM)
    picked_set = set(picked)
    picks = [(n, "random") for n in picked]

    # 特征：高密度 4 / 含笑声 3 / M 类被提及 3
    by_density = sorted(pool, key=lambda n: -stat[n]["spans"])
    by_laugh = [n for n in sorted(pool, key=lambda n: -stat[n]["laugh"]) if stat[n]["laugh"] >= 2]

    def take(cands, tag, k):
        got = 0
        for n in cands:
            if got >= k:
                break
            if n in picked_set:
                continue
            picked_set.add(n)
            picks.append((n, tag))
            got += 1
        return got

    take(by_density, "高密度", 4)
    take(by_laugh, "含笑声", 3)

    # M 类被提及章：人名表含目标、但其实发言很少
    if target and adir:
        cand = []
        for n in pool:
            head = (pdir / f"ch{n:03d}.packet.md").read_text(encoding="utf-8")[:600]
            m = re.search(r"本章出现的人名：(.+)", head)
            if not m or target not in m.group(1):
                continue
            ap = adir / f"ch{n:03d}.json"
            if not ap.exists():
                continue
            ans = json.loads(ap.read_text(encoding="utf-8"))
            A = ans.get("a") or []
            if not A:
                continue
            share = sum(1 for i in A if canon(str(i[1])) == target) / len(A)
            if share < 0.05:
                cand.append(n)
        take(cand, "被提及", 3)

    # 不足按密度补齐
    if len(picks) < SAMPLE_N_TOTAL:
        take(by_density, "补齐", SAMPLE_N_TOTAL - len(picks))

    # 展开成待复核条目（不含原文，只给定位）
    items = []
    for no, tag in picks[:SAMPLE_N_TOTAL]:
        body = idx[no][1]
        spans = {s["idx"]: s for s in enumerate_quotes(body) if s["span"] is not None}
        A = []
        if adir and (adir / f"ch{no:03d}.json").exists():
            A = json.loads((adir / f"ch{no:03d}.json").read_text(encoding="utf-8")).get("a") or []
        if not A:
            for i in sorted(spans):
                items.append({"chapter": no, "idx": i, "tag": tag,
                              "speaker": "", "evidence": "", "truth": "", "flag": ""})
            continue
        for i in A:
            # 只收"已明确判定"的条目：未知条目由 C8 未知率单独统计，
            # 让复核人去看"未知"只会白花时间（口径已定：未知不计判错）。
            if str(i[1]) == "未知":
                continue
            items.append({
                "chapter": no, "idx": int(i[0]), "tag": tag,
                "speaker": str(i[1]),
                "evidence": str(i[2]) if len(i) > 2 else "U",
                "truth": "", "flag": "",
            })

    payload = {
        "seed": SAMPLE_SEED,
        "threshold": WRONG_MAX,
        "chapters": [n for n, _ in picks[:SAMPLE_N_TOTAL]],
        "tags": {str(n): t for n, t in picks[:SAMPLE_N_TOTAL]},
        "instruction": (
            "逐条核对：对照原文，在 truth 填真实说话人（填「未知」表示原文确实不可判）。"
            "若属口径分歧（未知↔集体名、别名、称谓变体），flag 填 dismiss 并在 note 写理由。"
        ),
        "items": items,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    per = Counter(it["chapter"] for it in items)
    return {"mode": "sample", "chapters": payload["chapters"], "items": len(items),
            "out": str(out), "per_chapter": {str(k): v for k, v in sorted(per.items())},
            "avg": round(len(items) / max(len(payload["chapters"]), 1), 1)}


def do_audit(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    items = d["items"]
    judged = wrong = dismissed = pending = unknown_truth = 0
    wrongs = []

    for it in items:
        truth = (it.get("truth") or "").strip()
        ans = (it.get("speaker") or "").strip()
        if not truth:
            pending += 1
            continue
        if truth == "未知":
            unknown_truth += 1          # 复核人也判不出 → 移出分母
            continue
        if (it.get("flag") or "") == "dismiss":
            dismissed += 1
            continue
        if not ans:                     # 答卷里根本没这条（漏答）
            judged += 1
            wrong += 1
            wrongs.append({**it, "reason": "漏答"})
            continue
        if ans == "未知":
            # 未知 vs 具体人：只有复核人判出、答卷判不出 → 不算判错人（由未知率管）
            if canon(truth) in COLLECTIVE or truth in COLLECTIVE:
                continue
            continue
        judged += 1
        if not same_entity(ans, truth):
            wrong += 1
            wrongs.append({**it, "reason": f"{ans} → {truth}"})

    total = len(items)
    rate = wrong / judged if judged else 0.0
    d_rate = dismissed / total if total else 0.0
    return {
        "mode": "audit", "items": total, "judged": judged, "wrong": wrong,
        "pending": pending, "unknown_truth": unknown_truth, "dismissed": dismissed,
        "wrong_rate": round(rate, 4), "dismiss_rate": round(d_rate, 4),
        "threshold": WRONG_MAX,
        "pass": judged > 0 and rate <= WRONG_MAX and d_rate <= DISMISS_MAX,
        "warnings": ([] if d_rate <= DISMISS_MAX else [f"口径分歧标注占 {d_rate:.1%} > 5%，疑似放水"]),
        "wrongs": wrongs[:50],
    }

# ---------------------------------------------------------------- 主入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="全书蒸馏验收（ACCEPTANCE.md v1.0）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--static", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--sample", action="store_true")
    g.add_argument("--audit", metavar="FILE")
    ap.add_argument("--root", default=str(ROOT / "novels/fenghua"))
    ap.add_argument("--chapters", default="", help="如 1-771 或 324,330")
    ap.add_argument("--answers-dir", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--target", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", default="", help="把报告写到文件")
    a = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    root = Path(a.root)
    chapters = parse_chapters(a.chapters, index().keys())

    if a.static:
        rep = do_static(root, chapters)
    elif a.verify:
        adir = Path(a.answers_dir) if a.answers_dir else root / "answers-full"
        odir = Path(a.out_dir) if a.out_dir else None
        rep = do_verify(root, chapters, adir, odir)
    elif a.sample:
        adir = Path(a.answers_dir) if a.answers_dir else (root / "answers-full")
        out = Path(a.out) if a.out else root / "audit-sample.json"
        rep = do_sample(root, chapters, adir if adir.exists() else None, a.target, out)
    else:
        rep = do_audit(Path(a.audit))

    # 人类可读输出
    if rep["mode"] == "sample":
        print(f"抽检清单已生成：{rep['items']} 条 / {len(rep['chapters'])} 章 → {rep['out']}")
        print(f"  随机 {SAMPLE_N_RANDOM} 章，种子 {SAMPLE_SEED}（可复现）")
        print(f"  章节：{rep['chapters']}")
        print(f"  平均每章 {rep['avg']} 条 | 每章条数：{rep['per_chapter']}")
    elif rep["mode"] == "audit":
        if rep["judged"] == 0:
            print(f"尚未复核任何条目（{rep['items']} 条待填 truth），无法计分")
            print(f"  已填但标「未知」{rep['unknown_truth']} 条 | 口径分歧 {rep['dismissed']} 条")
        else:
            print(f"判错人率 {rep['wrong_rate']:.2%}（{rep['wrong']}/{rep['judged']}，阈值 {WRONG_MAX:.0%}）"
                  f"  → {'通过' if rep['pass'] else '不通过'}")
            print(f"  未复核 {rep['pending']} 条 | 复核人判未知 {rep['unknown_truth']} 条"
                  f" | 口径分歧 {rep['dismissed']} 条（{rep['dismiss_rate']:.1%}）")
            for w in rep["wrongs"][:10]:
                print(f"  ✗ 第{w['chapter']}章 [{w['idx']}] {w['reason']}")
    else:
        print(f"=== {rep['mode']} ===")
        print(f"章数 {rep.get('chapters')} | 错误 {len(rep['errors'])} | 告警 {len(rep.get('alerts', []))}"
              f" | 提示 {len(rep['warns'])}")
        for e in rep["errors"][:15]:
            print(f"  ✗ {e}")
        if len(rep["errors"]) > 15:
            print(f"  ... 另有 {len(rep['errors']) - 15} 条错误")
        for w in rep["warns"][:8]:
            print(f"  ! {w}")
        for al in rep.get("alerts", [])[:8]:
            print(f"  ~ {al}")
        if rep["mode"] == "verify" and rep.get("total_answered"):
            print(f"  全书未知率 {rep['unknown_rate']:.2%}（{rep['total_unknown']}/{rep['total_answered']}）")

    if a.json:
        Path(a.json).write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入 {a.json}")

    if rep.get("errors"):
        return 1
    if rep.get("alerts") or rep.get("warns"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
