"""助手唯一可调用的只读工具。任何写库、下单、联网抓取均不在此注册。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..market import coverage_report
from ..store import DB_PATH, _connect, summary

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
TOOL_ARGUMENTS: dict[str, set[str]] = {
    "get_graph_summary": set(),
    "get_yield_history": {"crop"},
    "get_market_coverage": set(),
}


def _graph_summary(_: dict[str, Any]) -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"available": False, "message": "尚未建图；先运行 collect 和 graph-build。", "citations": []}
    conn = _connect()
    try:
        return {"available": True, "data": summary(conn), "citations": ["data/agri_graph.db"]}
    finally:
        conn.close()


def _yield_history(arguments: dict[str, Any]) -> dict[str, Any]:
    crop = str(arguments["crop"])
    if crop not in {"玉米", "水稻", "小麦", "谷物"}:
        return {"error": "只允许查询 玉米、水稻、小麦、谷物。", "citations": []}
    if not DB_PATH.exists():
        return {"available": False, "message": "尚未建图。", "citations": []}
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT crop, year, yield_t_ha, production_t, source FROM yield_year WHERE crop=? ORDER BY year DESC LIMIT 10", (crop,)
        )]
        return {"data": rows, "citations": ["data/agri_graph.db:yield_year"]}
    finally:
        conn.close()


def _market_coverage(_: dict[str, Any]) -> dict[str, Any]:
    return {"data": coverage_report(), "citations": ["agro/market.py"]}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_graph_summary",
                "description": "Read the local agricultural graph database coverage and current data span.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_yield_history",
                "description": "Read local annual yield history for exactly one supported crop.",
                "parameters": {
                    "type": "object",
                    "properties": {"crop": {"type": "string", "enum": ["玉米", "水稻", "小麦", "谷物"]}},
                    "required": ["crop"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_market_coverage",
                "description": "Read local futures-market mapping, coverage and known limitations.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]


def tool_registry() -> dict[str, ToolHandler]:
    return {"get_graph_summary": _graph_summary, "get_yield_history": _yield_history, "get_market_coverage": _market_coverage}


def invoke_tool(name: str, arguments_json: str) -> dict[str, Any]:
    registry = tool_registry()
    if name not in registry:
        return {"error": f"禁止调用未注册工具: {name}", "citations": []}
    try:
        arguments = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return {"error": "工具参数不是合法 JSON。", "citations": []}
    if not isinstance(arguments, dict):
        return {"error": "工具参数必须是 JSON 对象。", "citations": []}
    unexpected = set(arguments) - TOOL_ARGUMENTS[name]
    if unexpected:
        return {"error": f"工具参数包含不允许的字段: {sorted(unexpected)}", "citations": []}
    try:
        return registry[name](arguments)
    except (KeyError, ValueError) as exc:
        return {"error": f"工具参数无效: {exc}", "citations": []}
