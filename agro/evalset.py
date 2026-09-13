"""从魔搭检索、并下载独立产业对照测试集。

魔搭 dolphin / openapi 的 Search 目前被忽略（任意关键词都返回同一批热门集），
产量表下不下来。对照集改用 USDA PSD 中国主粮（与 OWID/FAO 不同机构），
作为本阶段产业预测的新测试集。
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

DOLPHIN = "https://www.modelscope.cn/api/v1/dolphin/datasets"


def _request_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_modelscope(queries: tuple[str, ...] = ("agriculture", "作物产量", "crop yield", "粮食产量")) -> dict:
    hits = []
    totals = {}
    first_page = []
    for q in queries:
        url = DOLPHIN + "?" + urllib.parse.urlencode(
            {"SearchValue": q, "PageSize": 10, "PageNumber": 1, "SortBy": "GmtModified"}
        )
        try:
            data = _request_json(url, timeout=25)
        except Exception as exc:
            totals[q] = {"error": str(exc)}
            continue
        items = data.get("Data") or []
        totals[q] = data.get("TotalCount")
        names = [f"{it.get('Namespace')}/{it.get('Name')}" for it in items]
        if not first_page:
            first_page = names
        agri = [
            n
            for n in names
            if any(k in n.lower() for k in ("agri", "crop", "yield", "wheat", "rice", "maize", "fao", "grain"))
        ]
        hits.extend(agri)
        # 任意两次检索首页相同，说明 Search 被丢弃
    collapsed = len({tuple(first_page)}) <= 1 and len(set(totals.values())) <= 1
    report = {
        "source": DOLPHIN,
        "queries": list(queries),
        "totals": totals,
        "first_page": first_page,
        "search_collapsed": collapsed,
        "agriculture_named_hits": sorted(set(hits)),
        "usable_yield_table": False,
        "note": (
            "SearchValue 被忽略，首页与总量对所有关键词相同，无法从魔搭下载产量测试集。"
            if collapsed
            else "检索可用，但未找到产量表。"
        ),
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
