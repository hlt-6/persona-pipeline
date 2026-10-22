#!/usr/bin/env python
"""persona-pipeline 本地运行时 CLI（零第三方依赖）

    python runner/cli.py doctor              # 环境自检
    python runner/cli.py verify --input sample/input-sun-wukong.json
    python runner/cli.py verify --input ... --corpus data/xiyouji.txt
    python runner/cli.py run --input ... --offline     # 不调模型，跑通流程
    python runner/cli.py run --input ...                # 调 DS 端点产出真实成品
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pipeline import config as cfgmod          # noqa: E402
from pipeline import llm, render, stub, verify  # noqa: E402

OK, BAD, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def _print(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _load_prompts(root: Path) -> dict:
    out = {}
    for name in ("01-input-spec.md", "02-novel-persona-analyzer.md", "03-novel-persona-builder.md"):
        p = root / name
        out[name] = p.read_text(encoding="utf-8") if p.exists() else None
    return out


# ---------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    root = cfgmod.repo_root_from(HERE)
    cfg = cfgmod.load_config(root, args.config)
    fails = 0

    _print("== 1. 运行时 ==")
    v = sys.version_info
    if v >= (3, 11):
        _print(f"{OK} Python {v.major}.{v.minor}.{v.micro}（tomllib 可用）")
    else:
        _print(f"{BAD} Python {v.major}.{v.minor} 过低，需要 >= 3.11"); fails += 1
    _print(f"{OK} 解释器：{sys.executable}")

    _print("\n== 2. 项目文件 ==")
    for f in ("01-input-spec.md", "02-novel-persona-analyzer.md", "03-novel-persona-builder.md",
              "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        p = root / f
        _print(f"{OK if p.exists() else WARN} {f}" + ("" if p.exists() else "（缺失）"))

    _print("\n== 3. 配置 ==")
    if cfg["_config_path"]:
        _print(f"{OK} 已加载 {cfg['_config_path']}")
    else:
        _print(f"{WARN} 未找到 runner/config.toml，使用内置默认值")
        _print("       修复：copy runner\\config.example.toml runner\\config.toml")
    _print(f"       base_url = {cfg['llm']['base_url']}")
    _print(f"       model    = {cfg['llm']['model']}")

    _print("\n== 4. 凭据 ==")
    key, src = cfgmod.resolve_key(cfg)
    if key:
        _print(f"{OK} 已找到密钥（{src}，长度 {len(key)}）")
    else:
        _print(f"{WARN} 未找到密钥（{src}）—— 在线模式不可用，离线模式可正常跑")
        _print("       设置：export DS_API_KEY=sk-xxx   或  setx DS_API_KEY sk-xxx")
    if args.probe:
        if not key:
            _print(f"{WARN} 跳过端点探测（无密钥）")
        else:
            ok, msg = llm.probe(cfg)
            _print(f"{OK if ok else BAD} 端点探测：{msg}")
            fails += 0 if ok else 1

    _print("\n== 5. 自检结论 ==")
    _print(f"{OK} 环境可用：可执行 verify / run --offline" if fails == 0 else
           f"{BAD} 有 {fails} 项阻断，见上")
    return 1 if fails else 0


# ---------------------------------------------------------------- verify
def cmd_verify(args) -> int:
    root = cfgmod.repo_root_from(HERE)
    cfg = cfgmod.load_config(root, args.config)
    inp = Path(args.input)
    if not inp.exists():
        _print(f"{BAD} 输入包不存在：{inp}"); return 2
    corpus = Path(args.corpus) if args.corpus else None
    if corpus and not corpus.exists():
        _print(f"{BAD} 语料不存在：{corpus}"); return 2

    rep = verify.verify_input(inp, corpus, cfg["verify"])
    _print(verify.format_report(rep, verbose=not args.quiet))

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        _print(f"\n报告已写入 {out}")

    s = rep["summary"]
    if s["missing"] or rep["structure"]["errors"]:
        return 1
    if s["paraphrase"]:
        return 3
    if s["unverified"]:
        return 4
    return 0


# ---------------------------------------------------------------- run
ONLINE_SCHEMA = """你必须只输出一个 JSON 对象，不要输出任何解释、不要用 markdown 围栏。结构：
{
 "schema_version":"1.0",
 "character":{"name":"","aliases":[],"identity":"","status":"","appearance":"","reputation":""},
 "speech":{"self_reference":"","address":{"对象":"称呼"},"register":"","rhythm":"",
           "catchphrases":[],"particles":"","gestures":[],
           "examples":[{"scene":"","line":"逐字引用输入包台词"}]},
 "emotion":{"beliefs":"","anger":"","joy":"","sadness":"","soft_spots":"","decision":""},
 "relations":{"groups":{"对某某类人":"态度与腔调"},"specific":[{"with":"","pattern":""}],
              "dealbreakers":[],"protected":[]},
 "timeline":["要点"],
 "first_mes":"用角色口吻新写一句开场白，不得整句照抄原著台词"
}
要求：
- examples.line 必须逐字复制输入包 dialogue_samples 里的 line，禁止改写、禁止凭记忆补写；
- 没有任何证据支撑的条目写「[信息不足，使用默认]」并给出克制推断；
- relations.groups 要体现同一话语对不同对象的腔调差异，不要复述 relations 原文。
"""


def cmd_run(args) -> int:
    root = cfgmod.repo_root_from(HERE)
    cfg = cfgmod.load_config(root, args.config)
    inp = Path(args.input)
    if not inp.exists():
        _print(f"{BAD} 输入包不存在：{inp}"); return 2

    name = args.name or inp.stem
    out_dir = Path(args.out) if args.out else root / cfg["paths"]["out_dir"] / name
    corpus = Path(args.corpus) if args.corpus else None

    _print(f"== 1/4 校验输入包 ==")
    rep = verify.verify_input(inp, corpus, cfg["verify"])
    _print(f"   verified={rep['summary']['verified']} paraphrase={rep['summary']['paraphrase']} "
           f"missing={rep['summary']['missing']} unverified={rep['summary']['unverified']}")
    for e in rep["structure"]["errors"]:
        _print(f"   {WARN} {e}")
    if not args.skip_verify and (rep["summary"]["missing"] or rep["structure"]["errors"]):
        _print(f"{BAD} 输入包未通过校验，中止（确认要跑请加 --skip-verify）")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "verify-report.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        return 1

    pkg = json.loads(inp.read_text(encoding="utf-8"))
    t0 = time.time()

    if args.offline:
        _print("== 2/4 生成（离线骨架，不调用模型） ==")
        persona = stub.build(pkg, rep)
        gen = "offline-stub"
    else:
        _print("== 2/4 调用模型（02 分析 + 03 构建，单次结构化输出） ==")
        prompts = _load_prompts(root)
        sys_msg = "\n\n---\n\n".join(
            x for x in (prompts["02-novel-persona-analyzer.md"],
                        prompts["03-novel-persona-builder.md"]) if x)
        msgs = [
            {"role": "system", "content": sys_msg + "\n\n---\n\n" + ONLINE_SCHEMA},
            {"role": "user", "content": "输入包如下：\n" +
                                        json.dumps(pkg, ensure_ascii=False, indent=2)},
        ]
        try:
            raw = llm.chat(msgs, cfg)
        except llm.LLMError as e:
            _print(f"{BAD} 模型调用失败：{e}")
            _print("   可用 --offline 先验证流程，或检查 doctor --probe")
            return 1
        persona = llm.parse_json_loose(raw)
        persona.setdefault("meta", {})
        persona["meta"].update({
            "generated_by": f"{cfg['llm']['model']} @ {cfg['llm']['base_url']}",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "verified_quotes": rep["summary"]["verified"],
            "source_license": pkg.get("meta", {}).get("source_license", "未声明"),
        })
        gen = cfg["llm"]["model"]

    _print("== 3/4 成品侧复核（引文必须经核验） ==")
    persona, issues = verify.verify_output(persona, rep)
    for i in issues:
        _print(f"   {WARN} {i}")
    if not issues:
        _print(f"   {OK} 所有台词示例均可在输入包中逐字命中")

    _print("== 4/4 渲染 persona.md + card.json ==")
    info = render.write_outputs(persona, out_dir)
    _print(f"   {OK} {info['persona_md']}")
    _print(f"   {OK} {info['card_json']}")
    _print(f"   system_prompt = {info['system_prompt_chars']} 字符（不再是占位符）")
    _print(f"   lorebook 条目 = {info['lorebook_entries']}")
    for pr in info["problems"]:
        _print(f"   {BAD} {pr}")

    (out_dir / "verify-report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "run-report.json").write_text(json.dumps({
        "input": str(inp), "corpus": str(corpus) if corpus else None,
        "generated_by": gen, "elapsed_sec": round(time.time() - t0, 2),
        "verify_summary": rep["summary"], "output_issues": issues,
        "render": info,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    _print(f"\n{OK} 完成，耗时 {time.time() - t0:.1f}s，产物在 {out_dir}")
    return 0 if not info["problems"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(prog="persona-pipeline")
    ap.add_argument("--config", help="指定 config.toml 路径")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--probe", action="store_true", help="额外探测模型端点（不消耗额度）")
    d.set_defaults(func=cmd_doctor)

    v = sub.add_parser("verify", help="校验输入包结构与引文")
    v.add_argument("--input", required=True)
    v.add_argument("--corpus", help="原著全文 txt，用于逐字核验引文")
    v.add_argument("--json", help="把报告写到指定路径")
    v.add_argument("--quiet", action="store_true")
    v.set_defaults(func=cmd_verify)

    r = sub.add_parser("run", help="跑完整流水线")
    r.add_argument("--input", required=True)
    r.add_argument("--corpus")
    r.add_argument("--out")
    r.add_argument("--name")
    r.add_argument("--offline", action="store_true", help="不调模型，跑通流程")
    r.add_argument("--skip-verify", action="store_true")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
