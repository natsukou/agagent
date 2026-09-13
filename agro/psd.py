"""USDA FAS PSD 多品类、多国产量标签。

`evalset.py` 只管中国三主粮的对照集；这里负责新作物结构的训练标签：
大豆（中/美/巴）、玉米（中/美）、棉花（中/美）、咖啡（巴/中）。

单位陷阱是这个模块存在的主要理由：
- 谷物、油籽：产量 1000 MT，面积 1000 HA，单产 MT/HA，直接可用。
- 棉花：单产 KG/HA（要 /1000），产量是 1000 包 480 磅（1 包 = 217.7243 kg）。
- 咖啡：只有产量，单位 1000 袋 60 kg，**没有面积、没有单产**。
  所以咖啡布局的目标口径是产量而不是单产，不能假装能报 t/ha。
"""

from __future__ import annotations

import csv
import io
import json
import zipfile

from .collect import RAW, _get

PSD_RAW = RAW / "psd_multi.json"

BALE_KG = 480 * 0.45359237  # 217.7243
BAG_KG = 60.0

PSD_FILES: dict[str, tuple[str, dict[str, str]]] = {
    "grains": (
        "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip",
        {"Corn": "玉米", "Wheat": "小麦", "Rice, Milled": "水稻"},
    ),
    "oilseeds": (
        "https://apps.fas.usda.gov/psdonline/downloads/psd_oilseeds_csv.zip",
        {"Oilseed, Soybean": "大豆"},
    ),
    "cotton": (
        "https://apps.fas.usda.gov/psdonline/downloads/psd_cotton_csv.zip",
        {"Cotton": "棉花"},
    ),
    "coffee": (
        "https://apps.fas.usda.gov/psdonline/downloads/psd_coffee_csv.zip",
        {"Coffee, Green": "咖啡"},
    ),
}

COUNTRIES = {"China": "CHN", "United States": "USA", "Brazil": "BRA"}

YEAR_FROM = 2015
YEAR_TO = 2026


def _attr_kind(attr: str) -> str | None:
    a = (attr or "").strip().lower()
    if a == "yield":
        return "yield"
    if a == "production":
        return "production"
    if a in {"area harvested", "harvested area"}:
        return "area"
    return None


def parse_psd_csv(text: str, commodity_map: dict[str, str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rec in csv.DictReader(io.StringIO(text)):
        country = (rec.get("Country_Name") or "").strip()
        if country not in COUNTRIES:
            continue
        crop = commodity_map.get((rec.get("Commodity_Description") or "").strip())
        if crop is None:
            continue
        kind = _attr_kind(rec.get("Attribute_Description") or "")
        if kind is None:
            continue
        try:
            year = int(float(rec.get("Market_Year") or 0))
            value = float(rec.get("Value") or 0)
        except ValueError:
            continue
        if year < YEAR_FROM or year > YEAR_TO:
            continue
        rows.append(
            {
                "crop": crop,
                "scope": COUNTRIES[country],
                "year": year,
                "kind": kind,
                "value": value,
                "unit": (rec.get("Unit_Description") or "").strip(),
                "commodity": (rec.get("Commodity_Description") or "").strip(),
                "source": "USDA FAS PSD",
            }
        )
    return rows


def fetch_psd(refresh: bool = False) -> list[dict[str, object]]:
    if PSD_RAW.exists() and not refresh:
        return json.loads(PSD_RAW.read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for _name, (url, commodity_map) in PSD_FILES.items():
        raw = _get(url, timeout=180)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            member = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            text = zf.read(member).decode("utf-8", errors="replace")
        rows.extend(parse_psd_csv(text, commodity_map))
    PSD_RAW.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def _convert(crop: str, kind: str, value: float, unit: str) -> float:
    u = unit.lower()
    if kind == "yield":
        return value / 1000.0 if "kg" in u else value
    if kind == "production":
        if "bale" in u:
            return value * BALE_KG / 1000.0  # 1000 包 -> kt
        if "bags" in u:
            return value * BAG_KG / 1000.0  # 1000 袋 -> kt
        return value  # 1000 MT == kt
    return value  # 面积已经是 1000 HA


def to_year_table(rows: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
    """折成 作物×国别×年。咖啡的 yield_t_ha 会是 None——PSD 没这个数。"""
    rows = rows if rows is not None else fetch_psd()
    bucket: dict[tuple[str, str, int], dict[str, float]] = {}
    for r in rows:
        key = (str(r["crop"]), str(r["scope"]), int(r["year"]))
        bucket.setdefault(key, {})[str(r["kind"])] = _convert(
            str(r["crop"]), str(r["kind"]), float(r["value"]), str(r.get("unit") or "")
        )
    out: list[dict[str, object]] = []
    for (crop, scope, year), vals in sorted(bucket.items()):
        out.append(
            {
                "crop": crop,
                "scope": scope,
                "year": year,
                "yield_t_ha": vals.get("yield"),
                "production_kt": vals.get("production"),
                "area_kha": vals.get("area"),
                "source": "USDA FAS PSD",
            }
        )
    return out


def label_map(table: list[dict[str, object]] | None = None) -> dict[tuple[str, str, int], dict[str, object]]:
    table = table if table is not None else to_year_table()
    return {(str(r["crop"]), str(r["scope"]), int(r["year"])): r for r in table}


def coverage(table: list[dict[str, object]] | None = None) -> dict[str, object]:
    table = table if table is not None else to_year_table()
    by_key: dict[str, dict[str, object]] = {}
    for r in table:
        key = f"{r['crop']}/{r['scope']}"
        slot = by_key.setdefault(
            key, {"years": [], "has_yield": 0, "has_area": 0, "has_production": 0}
        )
        slot["years"].append(r["year"])  # type: ignore[union-attr]
        if r["yield_t_ha"] is not None:
            slot["has_yield"] = int(slot["has_yield"]) + 1  # type: ignore[arg-type]
        if r["area_kha"] is not None:
            slot["has_area"] = int(slot["has_area"]) + 1  # type: ignore[arg-type]
        if r["production_kt"] is not None:
            slot["has_production"] = int(slot["has_production"]) + 1  # type: ignore[arg-type]
    for slot in by_key.values():
        years = sorted(slot["years"])  # type: ignore[arg-type]
        slot["years"] = f"{years[0]}-{years[-1]}"
        slot["n_years"] = len(years)
    return {"rows": len(table), "series": by_key}
