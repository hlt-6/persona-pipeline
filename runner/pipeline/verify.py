"""输入包校验 —— 阻断级问题 1 的解决方案。

核心主张：人格卡的合法性建立在"引文是原著原文"之上。
因此每条引文必须能在语料中被定位；定位不到就只能降级为 paraphrase（改写），
而 paraphrase 禁止进入成品卡的 mes_example，也不许被标注为"原著台词"。

三级判定：
  verified    精确命中（归一化后完全包含）
  paraphrase  近似命中（与某段原文高度重合但不等同，或完全找不到）
  missing     完全不命中
无 --corpus 时全部记为 unverified（结构仍校验，但明确告知"未经原文核验"）。
"""

from __future__ import annotations

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

_STRIP = re.compile(r"[^\w\u4e00-\u9fff]", re.UNICODE)


def normalize(s: str) -> str:
    """归一化：全半角统一、引号统一、去掉所有空白与标点。"""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    return _STRIP.sub("", s)


def _locate(corpus_norm: str, q: str, fuzzy_threshold: float):
    """返回 (status, score, index)。"""
    if not q:
        return "missing", 0.0, -1
    idx = corpus_norm.find(q)
    if idx >= 0:
        return "verified", 1.0, idx

    # 模糊：用前 8 字作锚点，围绕命中位置做局部比对
    anchor = q[:8] if len(q) >= 8 else q
    positions, start = [], 0
    while len(positions) < 40:
        i = corpus_norm.find(anchor, start)
        if i < 0:
            break
        positions.append(i)
        start = i + 1

    best_score, best_idx = 0.0, -1
    for p in positions:
        lo = max(0, p - 24)
        seg = corpus_norm[lo:p + len(q) + 24]
        sm = SequenceMatcher(None, q, seg, autojunk=False)
        matched = sum(b.size for b in sm.get_matching_blocks())
        score = matched / len(q)
        if score > best_score:
            best_score, best_idx = score, p

    if best_score >= 0.995:
        return "verified", best_score, best_idx
    if best_score >= fuzzy_threshold:
        return "paraphrase", best_score, best_idx
    return "missing", best_score, best_idx


def _snippet(corpus_raw: str, quote: str, q_norm: str) -> str:
    """从原文里捞出可人工核对的片段。"""
    anchor = q_norm[:8] if len(q_norm) >= 8 else q_norm
    for probe in (anchor, quote[:6]):
        if not probe:
            continue
        i = corpus_raw.find(probe)
        if i >= 0:
            return corpus_raw[max(0, i - 10):i + len(quote) + 30].replace("\n", " ")
    return ""


def verify_input(input_path: Path, corpus_path: Path | None, rules: dict) -> dict:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    min_d, min_n = rules.get("min_dialogue_samples", 20), rules.get("min_narration_samples", 5)
    fuzzy = rules.get("fuzzy_threshold", 0.90)

    errors, warnings = [], []
    for key in ("character", "relations", "dialogue_samples", "narration_samples"):
        if key not in data:
            errors.append(f"缺少顶层字段：{key}")

    ch = data.get("character", {})
    for key in ("name", "summary", "arc"):
        if not ch.get(key):
            errors.append(f"character 缺少字段：{key}")

    dl = data.get("dialogue_samples", []) or []
    nr = data.get("narration_samples", []) or []
    if len(dl) < min_d:
        errors.append(f"dialogue_samples 只有 {len(dl)} 条，规范要求 >= {min_d} 条")
    if len(nr) < min_n:
        errors.append(f"narration_samples 只有 {len(nr)} 条，规范要求 >= {min_n} 条")
    if len(data.get("relations", []) or []) < 2:
        warnings.append("relations 少于 2 条，Layer 4 的『对人差异』将缺少证据")

    corpus_raw = corpus_norm = None
    if corpus_path:
        corpus_raw = corpus_path.read_text(encoding="utf-8", errors="ignore")
        corpus_norm = normalize(corpus_raw)

    quotes = []
    for kind, items in (("dialogue", dl), ("narration", nr)):
        for i, item in enumerate(items):
            text = item.get("line") if isinstance(item, dict) else str(item)
            scene = item.get("scene", "") if isinstance(item, dict) else ""
            if not text or len(text.strip()) < 2:
                quotes.append({"kind": kind, "index": i, "scene": scene, "quote": text,
                               "status": "missing", "score": 0.0, "snippet": "",
                               "note": "空引文"})
                continue
            if corpus_norm is None:
                status, score, idx = "unverified", 0.0, -1
            else:
                status, score, idx = _locate(corpus_norm, normalize(text), fuzzy)
            quotes.append({
                "kind": kind, "index": i, "scene": scene, "quote": text,
                "status": status, "score": round(score, 3),
                "location": idx,
                "snippet": _snippet(corpus_raw, text, normalize(text)) if corpus_raw else "",
            })

    summary = {s: sum(1 for q in quotes if q["status"] == s)
               for s in ("verified", "paraphrase", "missing", "unverified")}

    ok = not errors and summary["missing"] == 0 and summary["paraphrase"] == 0
    return {
        "input": str(input_path),
        "corpus": str(corpus_path) if corpus_path else None,
        "ok": ok,
        "structure": {"errors": errors, "warnings": warnings},
        "summary": summary,
        "quotes": quotes,
    }


def verify_output(persona: dict, input_report: dict, strict: bool | None = None) -> tuple[dict, list[str]]:
    """成品侧复核：卡里出现的"原著台词"必须是 verified 集合的成员。

    strict=True（提供了语料）时，不达标的引文直接降级剔除；
    strict=False（无 语料）时保留内容但标 verified=False，并提示"未经原文核验"，
    避免把"无法核验"误判成"伪引文"。
    """
    if strict is None:
        strict = input_report.get("corpus") is not None
    verified = {normalize(q["quote"]) for q in input_report["quotes"]
                if q["status"] == "verified"}
    issues = []
    for k, ex in enumerate(persona.get("speech", {}).get("examples", []) or [], 1):
        line = ex.get("line", "") if isinstance(ex, dict) else str(ex)
        if normalize(line) in verified:
            if isinstance(ex, dict):
                ex["verified"] = True
            continue
        if strict:
            issues.append(f"speech.examples[{k}] 非原文（已降级，不写入卡片台词示例）：{line[:30]}…")
        else:
            issues.append(f"speech.examples[{k}] 未经原文核验（保留但标注）：{line[:30]}…")
        if isinstance(ex, dict):
            ex["verified"] = False
    return persona, issues


def format_report(report: dict, verbose: bool = True) -> str:
    s = report["summary"]
    out = []
    out.append(f"输入包：{report['input']}")
    out.append(f"语料：{report['corpus'] or '（未提供，引文未经原文核验）'}")
    out.append(f"结论：{'通过' if report['ok'] else '未通过'}  "
               f"verified={s['verified']} paraphrase={s['paraphrase']} "
               f"missing={s['missing']} unverified={s['unverified']}")
    if report["structure"]["errors"]:
        out.append("\n结构错误：")
        out += [f"  - {e}" for e in report["structure"]["errors"]]
    if report["structure"]["warnings"]:
        out.append("\n提示：")
        out += [f"  - {w}" for w in report["structure"]["warnings"]]
    if verbose:
        bad = [q for q in report["quotes"] if q["status"] in ("paraphrase", "missing")]
        if bad:
            out.append("\n引文问题明细：")
            for q in bad:
                kind = "台词" if q["kind"] == "dialogue" else "叙述"
                out.append(f"  [{kind}#{q['index'] + 1}] {q['status']} score={q['score']} "
                           f"场景={q['scene'] or '-'}")
                out.append(f"      引文：{q['quote'][:60]}")
                if q.get("snippet"):
                    out.append(f"      原文近似片段：{q['snippet'][:60]}")
    return "\n".join(out)
