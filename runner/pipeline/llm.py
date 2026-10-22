"""LLM 调用层：任何 OpenAI 兼容端点（DeepSeek 官方 / DS 平台网关 / one-api / vLLM）。

只用标准库 urllib，装完 Python 即可运行，不需要 pip install。
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

from .config import resolve_key


class LLMError(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        ctx = ssl.create_default_context()
        if os.environ.get("PERSONA_INSECURE") == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:600]
        raise LLMError(f"HTTP {e.code} {e.reason}：{body}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"无法连接 {url}：{e.reason}（检查网络/代理或 base_url）") from e
    except json.JSONDecodeError as e:
        raise LLMError(f"响应不是合法 JSON：{e}") from e


def chat(messages: list[dict], cfg: dict, json_mode: bool = True) -> str:
    key, src = resolve_key(cfg)
    if not key:
        raise LLMError(
            f"未找到 API key：请设置环境变量 {cfg['llm']['api_key_env']}"
            "（或用 --offline 跑通流程）"
        )
    base = cfg["llm"]["base_url"].rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") or "/v" in base[-3:] else "/chat/completions")
    payload = {
        "model": cfg["llm"]["model"],
        "messages": messages,
        "temperature": cfg["llm"]["temperature"],
        "max_tokens": cfg["llm"]["max_tokens"],
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post(url, payload, {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": "persona-pipeline/0.2",
    }, int(cfg["llm"]["timeout"]))

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"响应结构异常（缺少 choices[0].message.content）：{str(data)[:400]}") from e


def probe(cfg: dict, timeout: int = 20) -> tuple[bool, str]:
    """健康检查：不消耗额度，只验证端点可达与鉴权有效。"""
    key, src = resolve_key(cfg)
    if not key:
        return False, f"未配置密钥（{src}）"
    base = cfg["llm"]["base_url"].rstrip("/")
    try:
        req = urllib.request.Request(base + "/models",
                                     headers={"Authorization": f"Bearer {key}"})
        ctx = ssl.create_default_context()
        if os.environ.get("PERSONA_INSECURE") == "1":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = json.loads(r.read().decode("utf-8"))
        models = [m.get("id", "") for m in body.get("data", [])][:5]
        return True, f"鉴权正常（{src}），可用模型示例：{', '.join(models) or '未列出'}"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, f"鉴权失败 HTTP {e.code}：key 无效或已过期（{src}）"
        if e.code == 404:
            return True, f"端点可达但未提供 /models（HTTP 404），可继续尝试对话接口"
        return False, f"HTTP {e.code} {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, f"无法连接：{e}"


def parse_json_loose(text: str) -> dict:
    """容错解析模型输出：去掉 ```json 围栏，截取最外层 {}。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise LLMError(f"模型未返回 JSON：{text[:300]}")
    return json.loads(t[i:j + 1])
