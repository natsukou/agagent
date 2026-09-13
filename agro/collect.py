"""从公开接口拉取天气、产量、销量，写入 data/raw。

只使用开放 API / 开放数据文件，不爬交易所或统计局登录页。
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

from .regions import REGIONS

UA = "brain-agri-graph/0.1 (research; +local)"
ROOT = Path(__file__).resolve().parents[1] / "data"
RAW = ROOT / "raw"

OWID = {
    "玉米": "https://ourworldindata.org/grapher/maize-yields.csv",
    "水稻": "https://ourworldindata.org/grapher/rice-yields.csv",
    "小麦": "https://ourworldindata.org/grapher/wheat-yields.csv",
}

WB_INDICATORS = {
    "cereal_yield_kg_ha": "AG.YLD.CREL.KG",
    "cereal_production_t": "AG.PRD.CREL.MT",
    "cereal_area_ha": "AG.LND.CREL.HA",
}


def _get(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_weather(
    start: str = "2015-01-01",
    end: str = "2024-12-31",
    only: tuple[str, ...] | None = None,
    refresh: bool = False,
) -> dict[str, Path]:
    """按锚点坐标拉 ERA5 日要素。时区用 auto，否则美/巴区域的日界会被上海时区切错。"""
    RAW.joinpath("weather").mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    daily = "temperature_2m_mean,temperature_2m_max,temperature_2m_min,precipitation_sum"
    for rid, meta in REGIONS.items():
        if only and rid not in only:
            continue
        path = RAW / "weather" / f"{rid}.json"
        if path.exists() and not refresh:
            written[rid] = path
            continue
        url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={meta['lat']}&longitude={meta['lon']}"
            f"&start_date={start}&end_date={end}"
            f"&daily={daily}&timezone=auto"
        )
        payload = json.loads(_get(url).decode("utf-8"))
        payload["_meta"] = {
            "region_id": rid,
            "region_name": meta["name"],
            "anchor": meta["anchor"],
            "country": meta["country"],
            "hemisphere": meta["hemisphere"],
            "source": "Open-Meteo ERA5 archive, CC BY 4.0",
            "url": url,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        written[rid] = path
    return written


def fetch_owid_yields(year_from: int = 2015, year_to: int = 2024) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for crop, url in OWID.items():
        text = _get(url).decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        value_key = next(k for k in (reader.fieldnames or []) if k not in ("Entity", "Code", "Year"))
        for row in reader:
            if row.get("Code") != "CHN":
                continue
            year = int(row["Year"])
            if year < year_from or year > year_to:
                continue
            val = row.get(value_key)
            if not val:
                continue
            rows.append(
                {
                    "crop": crop,
                    "year": year,
                    "yield_t_ha": float(val),
                    "scope": "CHN",
                    "source": "OWID/FAO",
                    "series": value_key,
                }
            )
    path = RAW / "owid_yields_chn.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def fetch_worldbank(year_from: int = 2015, year_to: int = 2024) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for name, code in WB_INDICATORS.items():
        url = (
            f"https://api.worldbank.org/v2/country/CHN/indicator/{code}"
            f"?format=json&date={year_from}:{year_to}&per_page=100"
        )
        data = json.loads(_get(url).decode("utf-8"))
        for item in data[1] or []:
            if item.get("value") is None:
                continue
            rows.append(
                {
                    "indicator": name,
                    "code": code,
                    "year": int(item["date"]),
                    "value": float(item["value"]),
                    "scope": "CHN",
                    "source": "World Bank WDI",
                }
            )
    path = RAW / "worldbank_chn.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def weather_monthly(raw_path: Path) -> list[dict[str, object]]:
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    daily = payload["daily"]
    rid = payload["_meta"]["region_id"]
    buckets: dict[tuple[int, int], dict[str, list[float]]] = {}
    for i, day in enumerate(daily["time"]):
        year, month = int(day[:4]), int(day[5:7])
        slot = buckets.setdefault((year, month), {"tmean": [], "tmax": [], "tmin": [], "precip": []})
        if daily["temperature_2m_mean"][i] is not None:
            slot["tmean"].append(daily["temperature_2m_mean"][i])
        if daily["temperature_2m_max"][i] is not None:
            slot["tmax"].append(daily["temperature_2m_max"][i])
        if daily["temperature_2m_min"][i] is not None:
            slot["tmin"].append(daily["temperature_2m_min"][i])
        if daily["precipitation_sum"][i] is not None:
            slot["precip"].append(daily["precipitation_sum"][i])
    out = []
    for (year, month), vals in sorted(buckets.items()):
        if not vals["tmean"]:
            continue
        out.append(
            {
                "region_id": rid,
                "year": year,
                "month": month,
                "tmean": round(sum(vals["tmean"]) / len(vals["tmean"]), 2),
                "tmax": round(sum(vals["tmax"]) / len(vals["tmax"]), 2),
                "tmin": round(sum(vals["tmin"]) / len(vals["tmin"]), 2),
                "precip_mm": round(sum(vals["precip"]), 1),
                "source": "Open-Meteo ERA5",
            }
        )
    return out


def collect_all(start: str = "2015-01-01", end: str = "2024-12-31") -> dict[str, object]:
    weather = fetch_weather(start, end)
    yields = fetch_owid_yields()
    wb = fetch_worldbank()
    return {
        "weather": {k: str(v) for k, v in weather.items()},
        "owid_yields": str(yields),
        "worldbank": str(wb),
        "dce_sample": str(Path(__file__).resolve().parent / "datasets" / "dce_sample.json"),
    }
