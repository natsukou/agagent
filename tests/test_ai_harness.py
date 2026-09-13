from __future__ import annotations

import json
import unittest

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
    def test_only_whitelisted_tools_are_exposed(self):
        self.assertEqual({item["function"]["name"] for item in tool_definitions()}, {"get_graph_summary", "get_yield_history", "get_market_coverage"})
        self.assertIn("禁止调用", invoke_tool("delete_database", "{}")["error"])

    def test_harness_executes_tool_and_returns_trace(self):
        result = AgriAssistantHarness(FakeClient()).answer("DCE 覆盖哪些作物？")
        self.assertIn("市场信号", result.answer)
        self.assertEqual(result.tool_trace[0]["tool"], "get_market_coverage")
        self.assertIn("agro/market.py", result.citations)

    def test_invalid_tool_json_is_safe(self):
        self.assertIn("合法 JSON", invoke_tool("get_yield_history", "bad")["error"])
        self.assertIn("不允许", invoke_tool("get_graph_summary", '{"drop": true}')["error"])
