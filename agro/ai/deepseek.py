"""DeepSeek OpenAI-compatible Chat Completions HTTP client, stdlib only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import DeepSeekConfig


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig) -> None:
        self.config = config

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "stream": False,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"无法连接 DeepSeek API: {exc.reason}") from exc


def assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"DeepSeek 响应格式异常: {response!r}") from exc
    # 保留 tool_calls、reasoning_content 等原始字段，满足兼容模型的后续调用要求。
    return {key: value for key, value in message.items() if key in {"role", "content", "tool_calls", "reasoning_content"}}
