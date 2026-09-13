"""只从环境变量读取 AI 配置，禁止在代码或前端暴露密钥。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_s: int = 45
    max_tool_rounds: int = 4

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY；请复制 .env.example 后在系统环境变量中设置密钥。")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            timeout_s=int(os.environ.get("DEEPSEEK_TIMEOUT_S", "45")),
            max_tool_rounds=int(os.environ.get("AGRI_AI_MAX_TOOL_ROUNDS", "4")),
        )
