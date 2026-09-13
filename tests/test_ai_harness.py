from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agro.ai.config import _load_local_env
from agro.ai.harness import AgriAssistantHarness
from agro.ai.tools import invoke_tool, tool_definitions


class FakeClient:
    class Config:
        model = "fake-deepseek"
        max_tool_rounds = 2

    config = Config()

    def __init__(self):
        self.calls = 0

    def complete(self, _messages, _tools):
        self.calls += 1
        if self.calls == 1:
            return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "function": {"name": "get_market_coverage", "arguments": "{}"}}]}}]}
        return {"choices": [{"message": {"role": "assistant", "content": "DCE 映射仅是市场信号。 [来源: agro/market.py]"}}]}


class TestAIHarness(unittest.TestCase):
    def test_local_env_does_not_override_system_env(self):
        old = os.environ.get("DEEPSEEK_API_KEY")
        path = None
        try:
            os.environ["DEEPSEEK_API_KEY"] = "system-value"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                handle.write("DEEPSEEK_API_KEY=file-value\n")
                path = Path(handle.name)
            _load_local_env(path)
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "system-value")
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
            if old is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = old

    def test_only_whitelisted_tools_are_exposed(self):
        self.assertEqual(
            {item["function"]["name"] for item in tool_definitions()},
            {"get_graph_summary", "get_yield_history", "get_market_coverage", "get_climate_evidence", "get_yield_outlook", "get_futures_context"},
        )
        self.assertIn("禁止调用", invoke_tool("delete_database", "{}")["error"])

    def test_harness_executes_tool_and_returns_trace(self):
        result = AgriAssistantHarness(FakeClient()).answer("DCE 覆盖哪些作物？")
        self.assertIn("市场信号", result.answer)
        self.assertEqual(result.tool_trace[0]["tool"], "get_market_coverage")
        self.assertIn("agro/market.py", result.citations)

    def test_invalid_tool_json_is_safe(self):
        self.assertIn("合法 JSON", invoke_tool("get_yield_history", "bad")["error"])
        self.assertIn("不允许", invoke_tool("get_graph_summary", '{"drop": true}')["error"])

    def test_yield_outlook_declares_baseline_not_observation(self):
        result = invoke_tool("get_yield_outlook", '{"layout_id": "maize.dongbei.spring"}')
        self.assertIn("基线气候态", result["interpretation"])
        self.assertIn("forecast", result["data"])
