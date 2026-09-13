"""开发用本地 HTTP API。生产环境请部署在受认证的反向代理之后。"""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .harness import AgriAssistantHarness


class AssistantHandler(BaseHTTPRequestHandler):
    harness: AgriAssistantHarness

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8000")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "service": "agri-ai-harness"})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/assistant":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 32_000:
                raise ValueError("请求体必须在 1 到 32000 字节之间。")
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
            result = self.harness.answer(payload.get("question", ""), payload.get("history", []))
            self._json(HTTPStatus.OK, result.to_dict())
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    AssistantHandler.harness = AgriAssistantHarness.from_env()
    with ThreadingHTTPServer((host, port), AssistantHandler) as server:
        print(f"Agri AI Harness listening on http://{host}:{port}")
        server.serve_forever()
