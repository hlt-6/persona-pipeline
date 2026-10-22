#!/usr/bin/env python
"""本地 mock 模型端点：在没有 API key 时验证「在线模式」的完整链路。

    python runner/mock_llm.py --input sample/input-sun-wukong.json --port 8765
    # 另开一个终端
    PERSONA_BASE_URL=http://127.0.0.1:8765/v1 DS_API_KEY=dummy \
      python runner/cli.py run --input sample/input-sun-wukong.json --skip-verify

它实现 /v1/chat/completions 和 /v1/models，返回的结构与真实 OpenAI 兼容端点一致，
用来验证请求构造、鉴权头、响应解析、JSON 容错提取这条链路——不验证模型推理质量。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from pipeline import stub  # noqa: E402

PERSONA: dict = {}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            if not self.headers.get("Authorization", "").startswith("Bearer "):
                return self._send(401, {"error": {"message": "missing api key"}})
            return self._send(200, {"data": [{"id": "mock-persona-model"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            return self._send(401, {"error": {"message": "missing api key"}})
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        content = "```json\n" + json.dumps(PERSONA, ensure_ascii=False) + "\n```"
        self._send(200, {
            "id": "mock-1", "object": "chat.completion",
            "model": req.get("model", "mock"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    def log_message(self, fmt, *args):
        sys.stderr.write("[mock] " + fmt % args + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="sample/input-sun-wukong.json")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    pkg = json.loads(Path(args.input).read_text(encoding="utf-8"))
    PERSONA.update(stub.build(pkg))
    PERSONA["meta"]["generated_by"] = "mock-llm（链路验证，非真实推理）"

    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"mock 端点已启动：http://127.0.0.1:{args.port}/v1  （Ctrl+C 结束）")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
