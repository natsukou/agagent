"""从魔搭检索、并下载独立产业对照测试集。

魔搭检索使用公开 OpenAPI；只有确认数据口径和文件结构后，候选数据集才可
作为产量测试表。当前产业对照仍使用 USDA PSD 中国主粮（与 OWID/FAO
不同机构），避免把仅名称相关的魔搭数据集误当成可比标签。
"""

from __future__ import annotations

import csv
import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .collect import RAW, UA, _get

MS_SEARCH = RAW / "modelscope_search.json"
USDA_RAW = RAW / "usda_psd_china.json"
EVAL_META = RAW / "evalset_meta.json"

USDA_ZIP = "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"

CROP_MATCH = {
    "小麦": ("wheat",),
    "水稻": ("rice, milled", "rice, paddy", "rice"),
    "玉米": ("corn", "maize"),
}

MODELSCOPE_DATASETS = "https://www.modelscope.cn/openapi/v1/datasets"
# 兼容外部代码可能导入的旧常量名；请求不再走已失效的 dolphin 接口。
DOLPHIN = MODELSCOPE_DATASETS

DEFAULT_MODELSCOPE_QUERIES = ("agriculture", "crop", "yield", "wheat", "rice", "maize")
AGRICULTURE_TERMS = ("agri", "crop", "wheat", "rice", "maize", "corn", "grain", "cotton", "soy")


def _request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _modelscope_page(payload: dict) -> tuple[list[dict], int]:
    """兼容魔搭 OpenAPI 响应包络，并对异常成功响应给出明确错误。"""
    if payload.get("success") is False:
        raise ValueError(str(payload.get("message") or payload.get("error") or "魔搭请求失败"))
    data = payload.get("data") or payload.get("Data") or {}
    if isinstance(data, list):  # 旧接口仅用于兼容测试/历史缓存结构
        return data, int(payload.get("TotalCount") or len(data))
    if not isinstance(data, dict):
        raise ValueError("魔搭返回了无法识别的数据结构")
    items = data.get("datasets") or data.get("Datasets") or []
    if not isinstance(items, list):
        raise ValueError("魔搭 datasets 字段不是列表")
    total = data.get("total_count", data.get("TotalCount", len(items)))
    return items, int(total or 0)


def _dataset_id(item: dict) -> str:
    repo_id = item.get("id") or item.get("Path")
    if repo_id:
        return str(repo_id)
    namespace = item.get("namespace") or item.get("Namespace") or ""
    name = item.get("name") or item.get("Name") or ""
    return f"{namespace}/{name}".strip("/")


def _is_agriculture_candidate(item: dict) -> bool:
    searchable = " ".join(
        str(value)
        for value in (
            _dataset_id(item),
            item.get("display_name", ""),
            item.get("description", ""),
            " ".join(item.get("tags") or []),
        )
    ).lower()
    return any(term in searchable for term in AGRICULTURE_TERMS)


def search_modelscope(queries: tuple[str, ...] = DEFAULT_MODELSCOPE_QUERIES) -> dict:
    hits: list[str] = []
    totals: dict[str, object] = {}
    pages: dict[str, list[str]] = {}
    for q in queries:
        url = MODELSCOPE_DATASETS + "?" + urllib.parse.urlencode(
            {"search": q, "sort": "downloads", "page_size": 10, "page_number": 1}
        )
        try:
            data = _request_json(url, timeout=25)
            items, total = _modelscope_page(data)
        except Exception as exc:
            totals[q] = {"error": str(exc)}
            continue
        totals[q] = total
        pages[q] = [_dataset_id(item) for item in items if _dataset_id(item)]
        hits.extend(_dataset_id(item) for item in items if _dataset_id(item) and _is_agriculture_candidate(item))

    successful_pages = list(pages.values())
    # 至少两个成功查询且首页完全相同，才说明服务端可能忽略了检索词。
    collapsed = len(successful_pages) >= 2 and len({tuple(page) for page in successful_pages}) == 1
    if not successful_pages:
        note = "魔搭检索失败；请检查网络、代理或稍后重试。"
    elif collapsed:
        note = "魔搭返回了相同检索首页，检索词可能被服务端忽略。"
    elif hits:
        note = "魔搭检索已恢复；发现农业候选集，但尚未验证为可比的作物产量表。"
    else:
        note = "魔搭检索已恢复，但未找到可确认的农业产量表。"
    report = {
        "source": MODELSCOPE_DATASETS,
        "queries": list(queries),
        "totals": totals,
        "results_by_query": pages,
        "first_page": next(iter(pages.values()), []),
        "search_collapsed": collapsed,
        "agriculture_named_hits": sorted(set(hits)),
        "usable_yield_table": False,
        "note": note,
    }
    RAW.mkdir(parents=True, exist_ok=True)
    MS_SEARCH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace(",", " ").split())


def _map_crop(name: str) -> str | None:
    n = _norm(name)
    for crop, keys in CROP_MATCH.items():
        if any(k in n for k in keys):
            if crop == "水稻" and "wild" in n:
                return None
            return crop
    return None


def fetch_usda_psd() -> list[dict[str, object]]:
    """USDA FAS PSD 谷物：中国小麦/稻米/玉米的面积、单产、产量。与 FAO 不同机构。"""
    raw = _get(USDA_ZIP, timeout=90)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        text = zf.read(name).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, object]] = []
    for rec in reader:
        country = (rec.get("Country_Name") or rec.get("Country") or "").lower()
        if "china" not in country or "taiwan" in country or "hong kong" in country:
            continue
        crop = _map_crop(rec.get("Commodity_Description") or rec.get("Commodity") or "")
        if crop is None:
            continue
        attr = (rec.get("Attribute_Description") or rec.get("Attribute") or "").strip()
        year = rec.get("Market_Year") or rec.get("Year")
        val = rec.get("Value")
        if not year or val in (None, ""):
            continue
        try:
            year_i = int(float(year))
            value = float(val)
        except ValueError:
            continue
        if year_i < 2015 or year_i > 2026:
            continue
        unit = rec.get("Unit_Description") or rec.get("Unit") or ""
        rows.append(
            {
                "crop": crop,
                "year": year_i,
                "attribute": attr,
                "value": value,
                "unit": unit,
                "country": rec.get("Country_Name"),
                "commodity": rec.get("Commodity_Description"),
                "source": "USDA FAS PSD grains_pulses",
            }
        )
    USDA_RAW.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def _attr_kind(attr: str) -> str | None:
    a = attr.lower()
    if a == "yield":
        return "yield"
    if a == "production":
        return "production"
    if a in {"area harvested", "harvested area"}:
        return "area"
    return None


def to_year_table(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """折成 作物×年：单产吨/公顷、产量万吨、面积千公顷。"""
    bucket: dict[tuple[str, int], dict[str, float]] = {}
    units: dict[tuple[str, int, str], str] = {}
    for r in rows:
        kind = _attr_kind(str(r["attribute"]))
        if kind is None:
            continue
        key = (str(r["crop"]), int(r["year"]))
        bucket.setdefault(key, {})[kind] = float(r["value"])
        units[(key[0], key[1], kind)] = str(r.get("unit") or "")

    out = []
    for (crop, year), vals in sorted(bucket.items()):
        yld = vals.get("yield")
        prod = vals.get("production")
        area = vals.get("area")
        # PSD 常见：产量 1000 MT，面积 1000 HA，单产 MT/HA
        yu = units.get((crop, year, "yield"), "").lower()
        pu = units.get((crop, year, "production"), "").lower()
        au = units.get((crop, year, "area"), "").lower()
        yield_t_ha = yld
        if yld is not None and "kg" in yu and "ha" in yu:
            yield_t_ha = yld / 1000.0
        prod_kt = prod
        if prod is not None:
            if "1000" in pu or "thousand" in pu:
                prod_kt = prod
            elif "mt" in pu or "metric ton" in pu:
                prod_kt = prod / 1000.0
        area_kha = area
        if area is not None:
            if "1000" in au or "thousand" in au:
                area_kha = area
            elif au in {"ha", "hectares"}:
                area_kha = area / 1000.0
        out.append(
            {
                "crop": crop,
                "year": year,
                "yield_t_ha": yield_t_ha,
                "production_kt": prod_kt,
                "area_kha": area_kha,
                "source": "USDA FAS PSD",
            }
        )
    return out


def load_or_fetch(force: bool = False) -> dict[str, object]:
    RAW.mkdir(parents=True, exist_ok=True)
    if force or not MS_SEARCH.exists():
        ms = search_modelscope()
    else:
        try:
            ms = json.loads(MS_SEARCH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ms = search_modelscope()
    if force or not USDA_RAW.exists():
        rows = fetch_usda_psd()
    else:
        rows = json.loads(USDA_RAW.read_text(encoding="utf-8"))
    table = to_year_table(rows)
    meta = {
        "modelscope": {k: ms[k] for k in ("search_collapsed", "usable_yield_table", "note", "agriculture_named_hits")},
        "holdout": {
            "name": "USDA FAS PSD China grains",
            "why": "魔搭无产量表；USDA 与已用的 OWID/FAO 不是同一套数，可作新对照",
            "rows_raw": len(rows),
            "rows_year": len(table),
            "years": sorted({r["year"] for r in table}),
            "crops": sorted({r["crop"] for r in table}),
            "path": str(USDA_RAW),
        },
    }
    EVAL_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"meta": meta, "table": table}


def holdout_map(table: list[dict[str, object]] | None = None) -> dict[tuple[str, int], dict[str, object]]:
    table = table if table is not None else to_year_table(json.loads(USDA_RAW.read_text(encoding="utf-8")))
    return {(r["crop"], r["year"]): r for r in table}
