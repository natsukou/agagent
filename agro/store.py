"""把采集结果收成清晰的四层图谱库：地区、天气、产量、销量。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .collect import RAW, ROOT, weather_monthly
from .datasets import bundled, dce
from .market import dominant_snapshots
from .regions import REGIONS

DB_PATH = ROOT / "agri_graph.db"
EXPORT_PATH = ROOT / "graph_export.json"

# yield_year 里同时存两套口径：OWID/FAO 与世界银行的中国序列（scope='CHN'），
# 以及 USDA PSD 的多国序列（scope='PSD:<ISO3>'）。
#
# 凡是按 (crop, year) 建索引、不带 scope 的查询都是错的，有两种坏法：
# 1. PSD 的咖啡没有单产，yield_t_ha 是 NULL，float() 直接抛异常；
# 2. 同一 (crop, year) 会有 CHN/PSD:CHN/PSD:USA/PSD:BRA 多行，
#    字典推导式里后来的行静默覆盖前面的——这种更危险，不报错但数就串了。
#
# 所以只认中国单一机构口径的查询统一用这个片段；要多国标签的走 PSD_SCOPE_FILTER。
CHN_YIELD_FILTER = "scope = 'CHN' AND yield_t_ha IS NOT NULL"
PSD_SCOPE_FILTER = "scope LIKE 'PSD:%'"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS edges;
        DROP TABLE IF EXISTS nodes;
        DROP TABLE IF EXISTS weather_month;
        DROP TABLE IF EXISTS yield_year;
        DROP TABLE IF EXISTS sales_obs;
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            attrs TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE edges (
            src TEXT NOT NULL,
            dst TEXT NOT NULL,
            type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            attrs TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (src, dst, type)
        );
        CREATE TABLE weather_month (
            region_id TEXT NOT NULL,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            tmean REAL, tmax REAL, tmin REAL, precip_mm REAL,
            source TEXT,
            PRIMARY KEY (region_id, year, month)
        );
        CREATE TABLE yield_year (
            scope TEXT NOT NULL,
            crop TEXT NOT NULL,
            year INTEGER NOT NULL,
            yield_t_ha REAL,
            production_t REAL,
            source TEXT,
            PRIMARY KEY (scope, crop, year)
        );
        CREATE TABLE sales_obs (
            date TEXT NOT NULL,
            crop TEXT NOT NULL,
            variety_code TEXT,
            volume REAL,
            settle REAL,
            source TEXT,
            PRIMARY KEY (date, crop, variety_code)
        );
        """
    )


def _node(conn: sqlite3.Connection, nid: str, kind: str, name: str, **attrs) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO nodes(id, kind, name, attrs) VALUES (?,?,?,?)",
        (nid, kind, name, json.dumps(attrs, ensure_ascii=False)),
    )


def _edge(conn: sqlite3.Connection, src: str, dst: str, etype: str, weight: float = 1.0, **attrs) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO edges(src, dst, type, weight, attrs) VALUES (?,?,?,?,?)",
        (src, dst, etype, weight, json.dumps(attrs, ensure_ascii=False)),
    )


def build_graph(conn: sqlite3.Connection) -> dict[str, int]:
    init_schema(conn)
    country_names = {"CHN": "中国", "USA": "美国", "BRA": "巴西"}
    for iso3, cname in country_names.items():
        _node(conn, f"country:{iso3}", "country", cname, iso3=iso3)

    for rid, meta in REGIONS.items():
        _node(
            conn,
            f"region:{rid}",
            "region",
            str(meta["name"]),
            lat=meta["lat"],
            lon=meta["lon"],
            anchor=meta["anchor"],
            country=meta["country"],
            hemisphere=meta["hemisphere"],
        )
        _edge(conn, f"region:{rid}", f"country:{meta['country']}", "in_country")
        for crop in meta["crops"]:
            _node(conn, f"crop:{crop}", "crop", crop)
            _edge(conn, f"crop:{crop}", f"region:{rid}", "grown_in")

    for lay in bundled.layouts():
        _node(
            conn,
            f"layout:{lay.id}",
            "layout",
            f"{lay.crop}/{lay.system}",
            crop=lay.crop,
            area_kha=lay.area_kha,
            country=lay.country,
            target=lay.target,
            unit=lay.unit,
        )
        _node(conn, f"crop:{lay.crop}", "crop", lay.crop)
        _edge(conn, f"layout:{lay.id}", f"region:{lay.region_id}", "located_in")
        _edge(conn, f"layout:{lay.id}", f"crop:{lay.crop}", "of_crop")
        _edge(conn, f"layout:{lay.id}", f"country:{lay.country}", "in_country")

    weather_n = 0
    weather_dir = RAW / "weather"
    if weather_dir.exists():
        for path in weather_dir.glob("*.json"):
            for row in weather_monthly(path):
                conn.execute(
                    """INSERT OR REPLACE INTO weather_month
                       (region_id, year, month, tmean, tmax, tmin, precip_mm, source)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (row["region_id"], row["year"], row["month"], row["tmean"], row["tmax"], row["tmin"], row["precip_mm"], row["source"]),
                )
                nid = f"weather:{row['region_id']}:{row['year']}-{row['month']:02d}"
                _node(conn, nid, "weather", f"{row['region_id']} {row['year']}-{row['month']:02d}", **row)
                _edge(conn, f"region:{row['region_id']}", nid, "has_weather")
                weather_n += 1

    yield_n = 0
    owid_path = RAW / "owid_yields_chn.json"
    if owid_path.exists():
        for row in json.loads(owid_path.read_text(encoding="utf-8")):
            conn.execute(
                """INSERT OR REPLACE INTO yield_year(scope, crop, year, yield_t_ha, production_t, source)
                   VALUES (?,?,?,?,?,?)""",
                (row["scope"], row["crop"], row["year"], row["yield_t_ha"], None, row["source"]),
            )
            nid = f"yield:{row['scope']}:{row['crop']}:{row['year']}"
            _node(conn, nid, "yield", f"{row['crop']} {row['year']} 单产", **row)
            _edge(conn, nid, f"crop:{row['crop']}", "of_crop")
            _edge(conn, nid, "country:CHN", "in_scope")
            for rid, meta in REGIONS.items():
                if row["crop"] in meta["crops"]:
                    _edge(conn, nid, f"region:{rid}", "observed_for", 0.6, note="全国单产挂到主产区，不是县级统计")
                    # 同年天气影响产量
                    for month in range(1, 13):
                        wid = f"weather:{rid}:{row['year']}-{month:02d}"
                        if conn.execute("SELECT 1 FROM nodes WHERE id=?", (wid,)).fetchone():
                            _edge(conn, wid, nid, "affects", 0.4)
            yield_n += 1

    # USDA PSD 多国标签。scope 前缀 PSD: 与 OWID/FAO 的 CHN series 分开存，
    # 两家机构同一作物同一年数不一样，不能互相覆盖。
    psd_path = RAW / "psd_multi.json"
    if psd_path.exists():
        from .psd import to_year_table

        for row in to_year_table(json.loads(psd_path.read_text(encoding="utf-8"))):
            scope = f"PSD:{row['scope']}"
            prod_t = None if row["production_kt"] is None else float(row["production_kt"]) * 1000.0
            conn.execute(
                """INSERT OR REPLACE INTO yield_year(scope, crop, year, yield_t_ha, production_t, source)
                   VALUES (?,?,?,?,?,?)""",
                (scope, row["crop"], row["year"], row["yield_t_ha"], prod_t, row["source"]),
            )
            nid = f"yield:{scope}:{row['crop']}:{row['year']}"
            _node(
                conn,
                nid,
                "yield",
                f"{row['crop']} {row['scope']} {row['year']}",
                scope=scope,
                crop=row["crop"],
                year=row["year"],
                yield_t_ha=row["yield_t_ha"],
                production_kt=row["production_kt"],
                area_kha=row["area_kha"],
                source=row["source"],
            )
            _node(conn, f"crop:{row['crop']}", "crop", str(row["crop"]))
            _edge(conn, nid, f"crop:{row['crop']}", "of_crop")
            _edge(conn, nid, f"country:{row['scope']}", "in_scope")
            for lay in bundled.layouts():
                if lay.crop == row["crop"] and lay.country == row["scope"]:
                    _edge(conn, nid, f"layout:{lay.id}", "observed_for", 0.6, note="国别产量挂到该国布局")
            yield_n += 1

    wb_path = RAW / "worldbank_chn.json"
    if wb_path.exists():
        by_year: dict[int, dict[str, float]] = {}
        for row in json.loads(wb_path.read_text(encoding="utf-8")):
            by_year.setdefault(row["year"], {})[row["indicator"]] = row["value"]
        for year, vals in by_year.items():
            prod = vals.get("cereal_production_t")
            yld = vals.get("cereal_yield_kg_ha")
            if yld is not None:
                yld_t = yld / 1000.0
                conn.execute(
                    """INSERT OR REPLACE INTO yield_year(scope, crop, year, yield_t_ha, production_t, source)
                       VALUES (?,?,?,?,?,?)""",
                    ("CHN", "谷物", year, yld_t, prod, "World Bank WDI"),
                )
                nid = f"yield:CHN:谷物:{year}"
                _node(conn, nid, "yield", f"谷物 {year} 单产", yield_t_ha=yld_t, production_t=prod, source="World Bank WDI")
                _edge(conn, nid, "country:CHN", "in_scope")
                yield_n += 1

    sales_n = 0
    bars = dce.load(dce.sample_path())
    for snap in dominant_snapshots(bars):
        if not snap.crop:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO sales_obs(date, crop, variety_code, volume, settle, source)
               VALUES (?,?,?,?,?,?)""",
            (snap.date, snap.crop, snap.variety_code, snap.volume, snap.settle, "DCE sample"),
        )
        nid = f"sales:DCE:{snap.variety_code}:{snap.date}"
        _node(
            conn,
            nid,
            "sales",
            f"{snap.variety_name} {snap.date} 成交",
            volume=snap.volume,
            settle=snap.settle,
            contract=snap.dominant_contract,
        )
        _edge(conn, nid, f"crop:{snap.crop}", "of_crop")
        _edge(conn, nid, "country:CHN", "in_scope")
        for layout_id in snap.layouts:
            _edge(conn, nid, f"layout:{layout_id}", "prices")
        sales_n += 1

    from .county import COUNTIES

    county_n = 0
    for c in COUNTIES:
        _node(
            conn,
            f"county:{c.id}",
            "county",
            c.name,
            region_id=c.region_id,
            lat=c.lat,
            lon=c.lon,
            area_kha=c.area_kha,
        )
        _edge(conn, f"region:{c.region_id}", f"county:{c.id}", "contains")
        county_n += 1
        for crop in c.crops:
            did = f"demand:{c.id}:{crop}"
            _node(conn, did, "demand", f"{c.name}-{crop}需求", crop=crop)
            _edge(conn, f"county:{c.id}", did, "demands")
            _edge(conn, did, f"crop:{crop}", "demands", 0.7)
            yid = f"yield-target:{c.id}:{crop}"
            _node(conn, yid, "yield_target", f"{c.name}-{crop}预期单产", crop=crop, area_kha=c.area_kha)
            _edge(conn, did, yid, "aggregates")

    conn.commit()
    return {
        "nodes": conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0],
        "weather_months": weather_n,
        "yield_rows": yield_n,
        "sales_rows": sales_n,
        "counties": county_n,
    }


def export_json(conn: sqlite3.Connection, path: Path | None = None) -> Path:
    path = path or EXPORT_PATH
    payload = {
        "nodes": [dict(r) for r in conn.execute("SELECT id, kind, name, attrs FROM nodes ORDER BY kind, id")],
        "edges": [dict(r) for r in conn.execute("SELECT src, dst, type, weight FROM edges ORDER BY type, src")],
        "weather": [dict(r) for r in conn.execute("SELECT * FROM weather_month ORDER BY region_id, year, month")],
        "yields": [dict(r) for r in conn.execute("SELECT * FROM yield_year ORDER BY crop, year")],
        "sales": [dict(r) for r in conn.execute("SELECT * FROM sales_obs ORDER BY date, crop")],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summary(conn: sqlite3.Connection) -> dict[str, object]:
    kinds = {r[0]: r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM nodes GROUP BY kind")}
    etypes = {r[0]: r[1] for r in conn.execute("SELECT type, COUNT(*) FROM edges GROUP BY type")}
    latest_yield = [
        dict(r)
        for r in conn.execute(
            "SELECT crop, year, yield_t_ha, source FROM yield_year "
            f"WHERE {CHN_YIELD_FILTER} AND crop != '谷物' ORDER BY year DESC, crop LIMIT 12"
        )
    ]
    weather_span = conn.execute("SELECT MIN(year||'-'||printf('%02d',month)), MAX(year||'-'||printf('%02d',month)), COUNT(*) FROM weather_month").fetchone()
    return {
        "db": str(DB_PATH),
        "nodes_by_kind": kinds,
        "edges_by_type": etypes,
        "weather_span": {"min": weather_span[0], "max": weather_span[1], "n": weather_span[2]},
        "latest_crop_yields": latest_yield,
        "sales_rows": conn.execute("SELECT COUNT(*) FROM sales_obs").fetchone()[0],
    }
