"""助手唯一可调用的只读工具。任何写库、下单、联网抓取均不在此注册。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from ..adapter import to_plan
from ..datasets import bundled
from ..futures import CROP_SYMBOLS, FUT_RAW, SYMBOLS, FuturesBar, liquidity
from ..graph import build_yield_graph
from ..market import coverage_report
from ..predict import forecast_yield
from ..regions import REGIONS
from ..store import DB_PATH, _connect, summary

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
TOOL_ARGUMENTS: dict[str, set[str]] = {
    "get_graph_summary": set(),
    "get_yield_history": {"crop"},
    "get_market_coverage": set(),
    "get_climate_evidence": {"region_id", "year", "month"},
    "get_yield_outlook": {"layout_id"},
    "get_futures_context": {"crop"},
    "get_intervention_research": {"layout_id"},
    "get_persona_comparison": {"persona"},
}

MODEL_DIR = DB_PATH.parent / "models"


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


def _climate_evidence(arguments: dict[str, Any]) -> dict[str, Any]:
    region_id = str(arguments["region_id"])
    year, month = int(arguments["year"]), int(arguments["month"])
    if region_id not in REGIONS:
        return {"error": f"未知 region_id: {region_id}", "citations": []}
    if not 2015 <= year <= 2030 or not 1 <= month <= 12:
        return {"error": "year 仅允许 2015–2030，month 仅允许 1–12。", "citations": []}
    if not DB_PATH.exists():
        return {"available": False, "message": "尚未建图，无法读取月度天气。", "citations": []}
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT region_id, year, month, tmean, tmax, tmin, precip_mm, source FROM weather_month WHERE region_id=? AND year=? AND month=?",
            (region_id, year, month),
        ).fetchone()
        if row is None:
            return {"available": False, "message": "该地区月份没有本地天气记录。", "citations": ["data/agri_graph.db:weather_month"]}
        return {"available": True, "data": dict(row), "citations": ["data/agri_graph.db:weather_month"]}
    finally:
        conn.close()


def _yield_outlook(arguments: dict[str, Any]) -> dict[str, Any]:
    layout_id = str(arguments["layout_id"])
    layouts = bundled.layout_map()
    layout = layouts.get(layout_id)
    if layout is None:
        return {"error": f"未知 layout_id: {layout_id}", "citations": []}
    # 这里刻意只用本地气候态种子：输出是“基线情景推断”，不是实时预报。
    climate = [bundled.climatology().get(layout.region_id, month) for month in range(1, 13)]
    forecast = forecast_yield(layout, climate, build_yield_graph(list(layouts.values())))
    plan = to_plan(forecast).to_dict()
    return {
        "data": {"forecast": forecast.to_dict(), "guidance_path": plan["path"], "allowed_actions": plan["allowed_actions"]},
        "interpretation": "基线气候态推断，只能解释图谱机制和数据需求，不能当作当季实测产量。",
        "citations": ["agro/datasets/bundled.py", "agro/predict.py", "agro/graph.py"],
    }


def _futures_context(arguments: dict[str, Any]) -> dict[str, Any]:
    crop = str(arguments["crop"])
    codes = CROP_SYMBOLS.get(crop)
    if not codes:
        return {"error": f"当前没有 {crop} 的期货映射目录。", "citations": []}
    rows = []
    for code in codes:
        meta = SYMBOLS[code]
        path = FUT_RAW / f"{code}.json"
        item: dict[str, Any] = {"symbol": code, "name": meta.name, "exchange": meta.exchange, "role": meta.role, "note": meta.note, "cached": path.exists()}
        if path.exists():
            try:
                bars = [FuturesBar(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
                item["liquidity"] = liquidity(bars)
                item["latest_date"] = bars[-1].date if bars else None
            except (json.JSONDecodeError, TypeError, ValueError):
                item["cache_error"] = "本地行情缓存格式异常。"
        rows.append(item)
    return {
        "data": {"crop": crop, "instruments": rows},
        "interpretation": "期货仅用于市场信息与流动性说明，不构成产量因果或交易建议。",
        "citations": ["agro/futures.py", "data/raw/futures/*（若 cached=true）"],
    }


def _load_model_report(filename: str) -> dict[str, Any] | None:
    path = MODEL_DIR / filename
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _intervention_research(arguments: dict[str, Any]) -> dict[str, Any]:
    report = _load_model_report("intervene_report.json")
    if report is None:
        return {
            "available": False,
            "message": "尚未生成信息干预研究报告；先运行 python -m agro.cli intervene --table。",
            "citations": [],
        }
    results = report.get("results") if isinstance(report.get("results"), dict) else {}
    layout_id = str(arguments.get("layout_id", "")).strip()
    links: dict[str, Any] = {}
    if layout_id:
        links = {
            key: {field: value for field, value in row.items() if field != "episodes"}
            for key, row in results.items()
            if key.startswith(f"{layout_id}→") and isinstance(row, dict)
        }
        if not links:
            return {
                "error": f"干预报告中没有布局 {layout_id}。",
                "available_layouts": sorted({key.split("→", 1)[0] for key in results}),
                "citations": ["data/models/intervene_report.json"],
            }
    return {
        "available": True,
        "data": {
            "framework": report.get("framework", {}),
            "scoreboard": report.get("scoreboard", {}),
            "links": links,
            "available_layouts": sorted({key.split("→", 1)[0] for key in results}),
        },
        "interpretation": "这是历史样本中的信息干预研究，只用于解释方向命中与失败证据，不构成当前买卖或仓位建议。",
        "citations": ["data/models/intervene_report.json", "agro/intervene.py"],
    }


def _persona_comparison(arguments: dict[str, Any]) -> dict[str, Any]:
    report = _load_model_report("persona_report.json")
    if report is None:
        return {
            "available": False,
            "message": "尚未生成用户画像比较报告；先运行 python -m agro.cli personas。",
            "citations": [],
        }
    persona = str(arguments.get("persona", "")).strip()
    allowed = {"beginner", "familiar", "expert"}
    if persona and persona not in allowed:
        return {"error": f"未知 persona: {persona}", "allowed": sorted(allowed), "citations": []}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    definitions = report.get("definitions") if isinstance(report.get("definitions"), dict) else {}
    data: dict[str, Any] = {"summary": summary, "definitions": definitions}
    if persona:
        data["selected"] = {
            "persona": persona,
            "summary": summary.get(persona, {}),
            "link_deltas": {
                key: row.get(f"{persona}_delta")
                for key, row in (report.get("by_link") or {}).items()
                if isinstance(row, dict)
            },
        }
    return {
        "available": True,
        "data": data,
        "interpretation": "画像是固定研究场景，不代表提问者身份；模拟净收益用于比较信息增量，不能生成个性化投资建议。",
        "citations": ["data/models/persona_report.json", "agro/personas.py"],
    }


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
                "name": "get_climate_evidence",
                "description": "Read one local monthly climate observation for an agricultural region. It is evidence, not a forecast.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "region_id": {"type": "string", "enum": sorted(REGIONS)},
                        "year": {"type": "integer"},
                        "month": {"type": "integer"},
                    },
                    "required": ["region_id", "year", "month"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_yield_outlook",
                "description": "Run the local graph yield model for a layout using the bundled baseline climatology. It is not a real-time yield observation.",
                "parameters": {
                    "type": "object",
                    "properties": {"layout_id": {"type": "string", "enum": sorted(bundled.layout_map())}},
                    "required": ["layout_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_futures_context",
                "description": "Read local futures instrument mapping and cached liquidity context for a crop. It never fetches the network or gives trading advice.",
                "parameters": {
                    "type": "object",
                    "properties": {"crop": {"type": "string", "enum": sorted(CROP_SYMBOLS)}},
                    "required": ["crop"],
                    "additionalProperties": False,
                },
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
        {
            "type": "function",
            "function": {
                "name": "get_intervention_research",
                "description": "Read historical intervention-study evidence and its limitations. Never turns the study into a current trade recommendation.",
                "parameters": {
                    "type": "object",
                    "properties": {"layout_id": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_persona_comparison",
                "description": "Read fixed beginner, familiar and expert research scenarios without identifying or profiling the current user.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "persona": {"type": "string", "enum": ["beginner", "familiar", "expert"]}
                    },
                    "additionalProperties": False,
                },
            },
        },
    ]


def tool_registry() -> dict[str, ToolHandler]:
    return {
        "get_graph_summary": _graph_summary,
        "get_yield_history": _yield_history,
        "get_market_coverage": _market_coverage,
        "get_climate_evidence": _climate_evidence,
        "get_yield_outlook": _yield_outlook,
        "get_futures_context": _futures_context,
        "get_intervention_research": _intervention_research,
        "get_persona_comparison": _persona_comparison,
    }


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
