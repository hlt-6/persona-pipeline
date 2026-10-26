#!/usr/bin/env python3
"""dsh 台词抽取器（persona-pipeline 改造版）

与原提示词的差别只有一条：结果的每一处断言都要能被机器验证。

  · 台词 line     -> 必须逐字出现在本章原文里（normalize 后子串命中）
  · 说话人 speaker -> 该名字必须在本章出现过，且不得用别名（须归一到本名）
  · 遗漏         -> 章节引号片段总数 vs 提取条数，做覆盖率审计
  · 判断依据     -> confidence=low / speaker=未知 时必须有 note 说明

用法：
  python runner/extract.py doctor
  python runner/extract.py spans  --chapter 330
  python runner/extract.py run    --chapter 330                    # 需要 DS_API_KEY
  python runner/extract.py run    --chapter 330 --result r.json    # 离线核验已有结果
  python runner/extract.py batch  --chapters 324,330,339,630       # 批量
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runner.pipeline import llm, quote_audit  # noqa: E402
from runner.pipeline.config import load_config  # noqa: E402

# 小说原文与抽取提示词都不随仓库分发：
#   - 小说受版权保护，只在你本地合法持有的副本上运行
#   - 提示词是使用者的工作文件，因人而异
# 两者都用环境变量覆盖，缺省落在被 .gitignore 排除的本地文件上。
NOVEL = Path(os.environ.get("PERSONA_NOVEL")
             or (ROOT / "novels/fenghua/source.txt"))
PROMPT_FILE = Path(os.environ.get("PERSONA_PROMPT")
                   or (ROOT / "novels/fenghua/extract-prompt.md"))
CHAPTER_DIR = ROOT / "novels/fenghua/chapters"
OUT_DIR = ROOT / "novels/fenghua/out"

# 提示词里的别名表，落到可校验的结构
ALIAS = {
    "杨思思": ["杨思思", "杨如画", "思思", "四哥", "我四哥", "四哥对三弟"],
}
CANON = {a: canon for canon, aliases in ALIAS.items() for a in aliases}

# 第一人称叙述者：本名可能整章不出现，不能因此判为"编造人物"
NARRATOR = "米高"
NARRATOR_ALIASES = ["我", "俺"]
SPEAKER_ALIAS = {
    "杨思思": ["杨如画", "思思", "四哥", "我四哥"],
    "米高": ["我", "教练"],
}

SPEECH_VERB = "说道|问道|笑道|骂道|喊道|吼道|回道|应道|答道|叫道|喝道|叹道|补充道|解释道|嘀咕道|嘟囔道|开口道|反问道|打断道|哭道|央求道|说道|问道"
# 名字 + 可选状语 + 说话动词；靠"名字里不含虚词"和"名字在正文重复出现"两道过滤
MARKED_RE = re.compile(
    rf"([\u4e00-\u9fa5]{{2,4}}?)(?:又|一边|便|就|才|也|却|则|随即|终于|稍稍|接着)?"
    rf"(?:对我|向她|向他|对我|冲着[\u4e00-\u9fa5]{{2,4}})?(?:{SPEECH_VERB})"
)
VERB_CHARS = set("着的地得了很都就也又便才却则再还只刚已未不没把被让给和与在是有个这那说问叫笑骂喊答回叹哭嗷咦嗯呃啊吗呢吧")
STOP_NAMES = {"一边说", "稍稍停", "然后", "随即", "终于", "随后", "于是", "所以", "但是", "因为",
              "接着", "这时", "此时", "这时", "那人", "有人", "大家", "我们", "你们", "他们"}


# ---------------------------------------------------------------- 章节读取
DUPLICATES: list[int] = []
RENUMBERED: list[tuple[int, int]] = []


_IDX_CACHE: dict[int, tuple[int, str]] | None = None


def chapter_index() -> dict[int, tuple[int, str]]:
    """带缓存的入口。全书 771 章只解析一次——批量回填时每章重启解释器
    会重复读 6MB 原文，110 章就要读 660MB。"""
    global _IDX_CACHE
    if _IDX_CACHE is None:
        _IDX_CACHE = _load_index()
    return _IDX_CACHE


def _load_index() -> dict[int, tuple[int, str]]:
    """章号 -> (起始行, 正文)。

    源文件存在章号标错（第 392 章出现两次），两次的标题与正文都不同——
    它不是重复，而是后一章被误标成了 392。实测序列为
    391, 392, 393, 394, 392(患难见真情), 396，缺 395，故该章实为 395。

    处理：真正重复（正文完全相同）才丢弃；标错号但正文不同的，按"位置吻合的
    缺失章号"重编号并记入 RENUMBERED，绝不静默丢整章。丢弃与重编号都会打印。
    """
    t = NOVEL.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    lines = t.split("\n")
    pat = re.compile(r"^\s*第\s*(\d+)\s*章\s*(.*)$")
    marks = []
    for i, l in enumerate(lines):
        m = pat.match(l)
        if m:
            marks.append((i, int(m.group(1)), m.group(2).strip()))

    nums = [n for _, n, _ in marks]
    maxn = max(nums) if nums else 0
    missing = set(range(1, maxn + 1)) - set(nums)
    out: dict[int, tuple[int, str]] = {}
    DUPLICATES.clear()
    RENUMBERED.clear()

    for k, (i, n, title) in enumerate(marks):
        end = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = "\n".join(lines[i + 1 : end]).strip("\n")
        if n in out:
            # 完全相同的正文才算真重复，直接丢弃
            if out[n][1] == body:
                DUPLICATES.append(n)
                continue
            # 正文不同 = 章号标错。优先取夹在前一章号与后一章号之间的缺号
            prev_n = nums[k - 1] if k else n - 1
            next_n = nums[k + 1] if k + 1 < len(nums) else maxn + 1
            cands = sorted(x for x in missing if prev_n < x < next_n)
            if cands:
                new_n = cands[0]
            elif missing:
                new_n = min(missing)
            else:
                new_n = max(out) + 1
            missing.discard(new_n)
            RENUMBERED.append((n, new_n))
            out[new_n] = (i, body)
            continue
        out[n] = (i, body)
    return out


def load_chapter(no: int) -> tuple[str, str]:
    idx = chapter_index()
    if no not in idx:
        raise SystemExit(f"找不到第 {no} 章（共 {len(idx)} 章）")
    _, body = idx[no]
    title = re.search(rf"^第{no}章\s*(.*)$", NOVEL.read_text(encoding="utf-8-sig"), re.M)
    return (title.group(1).strip() if title else ""), body


# ---------------------------------------------------------------- 提示词
def load_prompt() -> str:
    """取 dsh 提示词文件里第一段 --- 围栏之间的正文。"""
    raw = PROMPT_FILE.read_text(encoding="utf-8")
    blocks = re.findall(r"\n---\n(.*?)\n---\n", raw, re.S)
    body = blocks[0].strip() if blocks else raw.strip()
    return body


def build_messages(no: int, title: str, body: str) -> list[dict]:
    prompt = load_prompt()
    user = f"""{prompt}

## 本章原文（第{no}章 {title}）

{body}

## 补充要求（流程约束，不改变上面的字段定义）
- 只处理本章，不要引入其他章节的内容或记忆中的情节。
- 输出一个 JSON 对象，形如 {{"lines": [ ... ]}}，lines 为上面规定格式的数组。
- 另外在 "excluded" 字段里列出你判定为"引号内不是说话内容"的片段原文（如人名、外号、术语、笑声之外的名词），供机器审计。
- 不要在 JSON 之外输出任何解释文字。"""
    return [
        {"role": "system", "content": "你是严谨的小说台词抽取员。只依据给出的章节原文作答，不补充、不推测原文之外的内容。"},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- 校验
def normalize_speaker(s: str) -> tuple[str, bool]:
    s = (s or "").strip()
    if s in CANON:
        return CANON[s], s != CANON[s]
    return s, False


def speaker_present(sp: str, body: str) -> bool:
    """说话人是否在本章有依据。

    叙述者本名可以整章不出现（第一人称），只要正文是"我"叙事即成立；
    其他人物必须在本章正文里出现名字或别名，否则视为编造。
    """
    if sp == NARRATOR and any(a in body for a in NARRATOR_ALIASES):
        return True
    if sp in body:
        return True
    return any(a in body for a in SPEAKER_ALIAS.get(sp, []))


def verify(no: int, body: str, lines: list[dict], excluded: list[str]) -> dict:
    texts = [x.get("line", "") for x in lines]
    verbatim = quote_audit.check_verbatim(body, texts)
    coverage = quote_audit.audit_coverage(body, texts, excluded)

    issues: list[dict] = []
    speakers = set()
    for i, (item, v) in enumerate(zip(lines, verbatim), 1):
        line = item.get("line", "")
        sp_raw = item.get("speaker", "")
        sp, used_alias = normalize_speaker(sp_raw)
        conf = (item.get("confidence") or "").lower()
        note = item.get("note") or ""

        if v["status"] == "not_in_chapter":
            issues.append({"level": "FATAL", "idx": i, "field": "line",
                           "msg": "台词不在本章原文中（疑似编造或跨章串入）", "value": line[:50]})
        if used_alias:
            issues.append({"level": "WARN", "idx": i, "field": "speaker",
                           "msg": f"说话人用了别名 {sp_raw}，应归一为本名 {sp}", "value": sp_raw})
        if sp and sp != "未知" and not speaker_present(sp, body):
            issues.append({"level": "FATAL", "idx": i, "field": "speaker",
                           "msg": "说话人未在本章出场（疑似编造人物）", "value": sp})
        if sp == "未知" and conf != "low":
            issues.append({"level": "WARN", "idx": i, "field": "confidence",
                           "msg": "speaker=未知 但 confidence 不是 low", "value": conf})
        if conf not in ("high", "low"):
            issues.append({"level": "WARN", "idx": i, "field": "confidence",
                           "msg": "confidence 取值非法（应为 high/low）", "value": conf})
        if conf == "low" and not note:
            issues.append({"level": "WARN", "idx": i, "field": "note",
                           "msg": "低置信但未写原因", "value": ""})
        if not (item.get("evidence") or "").strip():
            issues.append({"level": "FATAL", "idx": i, "field": "evidence",
                           "msg": "缺少判断依据", "value": ""})
        if sp:
            speakers.add(sp)

    for m in quote_audit.enumerate_quotes(body):
        # 单字引号通常是源文件引号写错，但语气词/拟声词（「吁」「嗯」「咯」）是合法台词
        if m["span"] and len(m["span"]) <= 1 and not quote_audit.LAUGH_RE.match(m["span"]):
            issues.append({"level": "WARN", "idx": m["idx"], "field": "quote",
                           "msg": "出现单字引号片段，疑似源文件引号错误", "value": m["span"]})

    # 原文中有说话动词标记、但结果里没有的说话人
    marked = set()
    for m in MARKED_RE.finditer(body):
        n = m.group(1)
        if any(c in VERB_CHARS for c in n) or n in STOP_NAMES:
            continue
        if len(n) < 2 or body.count(n) < 2:
            continue
        marked.add(n)
    missing_speakers = sorted(x for x in marked if x not in speakers and x not in CANON)

    counts = {}
    for item in lines:
        sp, _ = normalize_speaker(item.get("speaker", ""))
        counts[sp] = counts.get(sp, 0) + 1

    fatal = [x for x in issues if x["level"] == "FATAL"]
    warn = [x for x in issues if x["level"] == "WARN"]
    return {
        "chapter": no,
        "total": len(lines),
        "by_speaker": counts,
        "unknown": counts.get("未知", 0),
        "coverage": coverage,
        "verbatim": verbatim,
        "fatal": len(fatal),
        "warn": len(warn),
        "issues": issues,
        "marked_speakers_not_in_result": missing_speakers,
        "ok": len(fatal) == 0 and not coverage["missed"],
    }


# ---------------------------------------------------------------- 输出
def render_report(no: int, title: str, result: dict, extra: dict,
                  id_audit: dict | None = None) -> str:
    cov = result["coverage"]
    L = []
    L.append(f"# 第{no}章 {title} —— 台词提取审计\n")
    L.append(f"- 提取台词：**{result['total']}** 条")
    L.append(f"- 原文引号片段：{cov['quoted_spans']} 个（显式排除 {cov['excluded']} 个，覆盖 {cov['covered']} 个）")
    L.append(f"- 覆盖率：**{cov['coverage'] * 100:.1f}%**")
    L.append(f"- 逐字核验通过：{sum(1 for v in result['verbatim'] if v['status'] == 'verbatim')}/{result['total']}")
    if cov.get("unmatched_lines"):
        L.append(f"- ⚠ 有 {cov['unmatched_lines']} 条提取结果未能抵扣任何引号（重复作答或越界）")
    L.append(f"- 致命问题：{result['fatal']}　警告：{result['warn']}")
    L.append(f"- 判定：{'✅ 通过' if result['ok'] else '❌ 未通过'}\n")
    if extra.get("model"):
        L.append(f"- 模型：{extra['model']}　耗时：{extra.get('elapsed', 0):.1f}s\n")

    if id_audit:
        L.append("## 编号级对账（答题包路径）\n")
        L.append("| 项 | 值 |")
        L.append("|---|---|")
        L.append(f"| 章节引号总数 | {id_audit['spans']} |")
        L.append(f"| 已作答编号 | {id_audit['answered_ids']} |")
        L.append(f"| 已排除编号 | {id_audit['excluded_ids']} |")
        L.append(f"| 重复作答 | {id_audit['duplicate_ids'] or '无'} |")
        L.append(f"| 未作答且未排除（漏号） | {id_audit['missing_ids'] or '无'} |")
        L.append(f"| 不存在的编号 | {id_audit['unknown_ids'] or '无'} |")
        L.append(f"| 既作答又排除 | {id_audit['answered_and_excluded'] or '无'} |")
        L.append(f"| 对账结果 | {'✅ 闭合' if id_audit['ok'] else '❌ 不闭合'} |")
        L.append("")
        L.append("> 文本级覆盖率会被「同文不同编号」掩盖（如两处「咯咯」），"
                 "所以每个编号必须在这里逐号闭合才算通过。\n")


    L.append("## 说话人分布\n")
    L.append("| 说话人 | 条数 |")
    L.append("|---|---|")
    for k, v in sorted(result["by_speaker"].items(), key=lambda x: -x[1]):
        L.append(f"| {k} | {v} |")
    L.append("")

    if cov["missed"]:
        L.append("## 漏检引号（必须复核）\n")
        for m in cov["missed"]:
            L.append(f"- 第{m['idx']}个引号（第{m['para']}段）：`{(m['span'] or '')[:60]}` —— {m['reason']}")
        L.append("")

    if result["issues"]:
        L.append("## 问题清单\n")
        for it in result["issues"]:
            tag = "🔴" if it["level"] == "FATAL" else "🟡"
            L.append(f"- {tag} [{it['idx']}] {it['field']}：{it['msg']}"
                     + (f"　`{it['value']}`" if it["value"] else ""))
        L.append("")

    if result["marked_speakers_not_in_result"]:
        L.append("## 原文有说话动词、但结果中未出现的名字（可能是漏归属）\n")
        L.append("、".join(result["marked_speakers_not_in_result"]) + "\n")

    if cov["excluded_detail"]:
        L.append("## 被判为非台词的引号片段\n")
        for e in cov["excluded_detail"]:
            L.append(f"- 第{e['idx']}个：`{e['span']}`")
        L.append("")
    return "\n".join(L)


def save_result(no: int, title: str, body: str, lines: list[dict], excluded: list[str],
                extra: dict, id_audit: dict | None = None,
                out_dir: Path | None = None) -> dict:
    out = Path(out_dir) if out_dir else OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    result = verify(no, body, lines, excluded)
    if id_audit is not None:
        # 编号不闭合 = 静默丢句通道，必须让通过状态失败，不能只打一行警告
        result["ok"] = result["ok"] and bool(id_audit.get("ok"))
    payload = {
        "chapter": no,
        "title": title,
        "source": str(NOVEL),
        "meta": extra,
        "audit": {k: result[k] for k in
                  ("total", "by_speaker", "unknown", "fatal", "warn", "ok",
                   "marked_speakers_not_in_result")},
        "coverage": result["coverage"],
        "id_audit": id_audit,
        "issues": result["issues"],
        "lines": lines,
    }
    (out / f"ch{no:03d}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"ch{no:03d}.report.md").write_text(
        render_report(no, title, result, extra, id_audit), encoding="utf-8")
    return result


# ---------------------------------------------------------------- 命令
def cmd_spans(args):
    title, body = load_chapter(args.chapter)
    spans = quote_audit.enumerate_quotes(body)
    print(f"第{args.chapter}章 {title} —— 共 {len(spans)} 个引号片段\n")
    for s in spans:
        mark = "" if s["span"] is not None else "  ⚠ 未闭合"
        print(f"{s['idx']:>2} [段{s['para']:>3}] {s['span']}{mark}")
    print(f"\n引号左 {body.count('“')} 右 {body.count('”')}　段落 {len([p for p in body.split(chr(10)) if p.strip()])}")


def cmd_run(args):
    title, body = load_chapter(args.chapter)
    t0 = time.time()
    extra = {"model": None, "prompt": str(PROMPT_FILE), "mode": "offline"}

    if args.result:
        raw = json.loads(Path(args.result).read_text(encoding="utf-8"))
        extra["mode"] = f"prefilled:{args.result}"
    else:
        cfg = load_config(ROOT / "runner/config.toml")
        key, src = llm.resolve_key(cfg) if hasattr(llm, "resolve_key") else (None, "")
        if not key:
            from runner.pipeline.config import resolve_key
            _k, _s = resolve_key(cfg)
            if not _k:
                raise SystemExit(f"未配置 API key（{_s}）。要么设置 DS_API_KEY，"
                                 "要么用 --result 传入已生成的结果做离线核验。")
        msgs = build_messages(args.chapter, title, body)
        content = llm.chat(msgs, cfg, json_mode=True)
        try:
            raw = llm.parse_json_loose(content)
        except Exception:
            i, j = content.find("["), content.rfind("]")
            raw = {"lines": json.loads(content[i:j + 1])}
        extra["model"] = cfg["llm"]["model"]
        extra["mode"] = f"llm:{cfg['llm']['base_url']}"
        (OUT_DIR / f"ch{args.chapter:03d}.raw.txt").parent.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"ch{args.chapter:03d}.raw.txt").write_text(content, encoding="utf-8")

    lines = raw.get("lines") if isinstance(raw, dict) else raw
    excluded = list((raw.get("excluded") or []) if isinstance(raw, dict) else [])
    if not isinstance(lines, list):
        raise SystemExit("结果缺少 lines 数组")
    for item in lines:
        item.setdefault("line", "")
        item.setdefault("speaker", "未知")
        item.setdefault("evidence", "")
        item.setdefault("confidence", "low")
        item.setdefault("note", "")

    extra["elapsed"] = round(time.time() - t0, 2)
    result = save_result(args.chapter, title, body, lines, excluded, extra)

    cov = result["coverage"]
    print(f"第{args.chapter}章 {title}")
    print(f"  提取 {result['total']} 条 | 引号片段 {cov['quoted_spans']} 个 | "
          f"覆盖率 {cov['coverage'] * 100:.1f}% | 逐字核验 "
          f"{sum(1 for v in result['verbatim'] if v['status'] == 'verbatim')}/{result['total']}")
    print(f"  致命 {result['fatal']} | 警告 {result['warn']} | "
          f"{'✅ 通过' if result['ok'] else '❌ 未通过'}")
    for it in [x for x in result["issues"] if x["level"] == "FATAL"][:6]:
        print(f"   🔴 [{it['idx']}] {it['field']}：{it['msg']}  {it['value']}")
    for m in cov["missed"][:6]:
        print(f"   🟡 漏检 第{m['idx']}个引号：{m['span']}")
    print(f"  产物：novels/fenghua/out/ch{args.chapter:03d}.json / .report.md")
    return 0 if result["ok"] else 1


def cmd_batch(args):
    nos = [int(x) for x in args.chapters.split(",") if x.strip()]
    rc = 0
    summary = []
    for n in nos:
        ns = argparse.Namespace(chapter=n, result=(args.result_dir and f"{args.result_dir}/ch{n:03d}.json"))
        try:
            r = cmd_run(ns)
        except SystemExit as e:
            print(f"第{n}章 跳过：{e}")
            r = 1
        rc = rc or r
        summary.append((n, r))
        print()
    print("=== 汇总 ===")
    for n, r in summary:
        print(f"  第{n}章：{'通过' if r == 0 else '未通过'}")
    return rc


def cmd_doctor(args):
    ok = True
    print("=== 环境检查 ===")
    print(f"  Python      : {sys.version.split()[0]}")
    print(f"  小说文件    : {'✅' if NOVEL.exists() else '❌'} {NOVEL}"
          + (f"（{NOVEL.stat().st_size / 1048576:.1f} MB）" if NOVEL.exists() else ""))
    ok &= NOVEL.exists()
    if not NOVEL.exists():
        print("               ↑ 把你自己合法持有的副本放到该路径，"
              "或设 PERSONA_NOVEL 指向它")
    # 提示词只有 --llm 模式才用得上，缺失不算失败
    print(f"  提示词文件  : {'✅' if PROMPT_FILE.exists() else '⚠ 未配置（仅 --llm 需要）'} "
          f"{PROMPT_FILE}")
    idx = chapter_index() if NOVEL.exists() else {}
    _ch = f"  章节数      : {len(idx)}"
    if RENUMBERED:
        _ch += "（标错号已重编：" + "、".join(f"{a}→{b}" for a, b in RENUMBERED) + "）"
    if DUPLICATES:
        _ch += f"（真重复已丢弃：{DUPLICATES}）"
    print(_ch)
    cfg = load_config(ROOT / "runner/config.toml")
    from runner.pipeline.config import resolve_key
    key, src = resolve_key(cfg)
    print(f"  API key     : {'✅' if key else '⚠ 未配置'}（{src}）")
    if PROMPT_FILE.exists():
        p = load_prompt()
        print(f"  提示词长度  : {len(p)} 字")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="dsh 台词抽取器（带证据核验）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    sp = sub.add_parser("spans", help="枚举章节内全部引号片段")
    sp.add_argument("--chapter", type=int, required=True)
    sp.set_defaults(func=cmd_spans)

    r = sub.add_parser("run", help="抽取单章并核验")
    r.add_argument("--chapter", type=int, required=True)
    r.add_argument("--result", help="已有结果 JSON（离线核验，不调用模型）")
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("batch", help="批量抽取")
    b.add_argument("--chapters", required=True, help="如 324,330,339,630")
    b.add_argument("--result-dir", help="已有结果所在目录（离线核验）")
    b.set_defaults(func=cmd_batch)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
