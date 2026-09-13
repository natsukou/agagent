"""产业图谱 → 产量 / 销量估计：按年份切分训练与测试。

产量目标：OWID/FAO 全国分作物单产（吨/公顷）。
销量目标：世界银行全国谷物产量（吨），作为供给/销量代理；
同时给出作物级结构产量 = 单产 × 布局面积。

切分必须按时间：训练集用较早年份，测试集用最近两年，禁止随机打乱。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .climate import MonthClimate
from .collect import ROOT
from .datasets import bundled
from .predict import forecast_yield
from .store import CHN_YIELD_FILTER, DB_PATH, PSD_SCOPE_FILTER, _connect
from .trainability import _ridge, _dot

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "train_report.json"

CROP_REGIONS = {
    "玉米": ("dongbei", "huabei"),
    "水稻": ("changjiang", "huanan"),
    "小麦": ("huabei",),
}

# 生长季月份；小麦跨年，用当年 1–6 与上年 10–12
SEASON = {
    "玉米": ((5, 6, 7, 8, 9), ()),
    "水稻": ((5, 6, 7, 8, 9), ()),
    "小麦": ((1, 2, 3, 4, 5, 6), (10, 11, 12)),
}


@dataclass
class Sample:
    crop: str
    year: int
    y: float
    features: list[float]
    names: list[str]
    extras: dict[str, float | str] = field(default_factory=dict)


def load_weather(conn) -> dict[tuple[str, int, int], dict]:
    out = {}
    for r in conn.execute("SELECT * FROM weather_month"):
        out[(r["region_id"], r["year"], r["month"])] = dict(r)
    return out


def load_yields(conn) -> list[dict]:
    """只取这个训练器认识的作物与标签口径。

    库里现在还有大豆/棉花/咖啡的 USDA PSD 多国标签（scope 前缀 PSD:），
    它们的产区、生长季、目标口径都不同，由 `multicrop.py` 负责；
    这里混进来只会拿 SEASON/CROP_REGIONS 撞 KeyError。
    """
    placeholders = ",".join("?" for _ in SEASON)
    return [
        dict(r)
        for r in conn.execute(
            "SELECT crop, year, yield_t_ha, source FROM yield_year "
            f"WHERE {CHN_YIELD_FILTER} AND crop IN ({placeholders}) "
            "ORDER BY crop, year",
            tuple(SEASON),
        )
    ]


def load_cereal(conn) -> dict[int, dict]:
    rows = {}
    for r in conn.execute("SELECT year, yield_t_ha, production_t FROM yield_year WHERE crop='谷物'"):
        rows[r["year"]] = {"yield_t_ha": r["yield_t_ha"], "production_t": r["production_t"]}
    return rows


def crop_area_kha(crop: str) -> float:
    """只汇总中国布局：这里的标签是 OWID/FAO 中国单产，不能混入美/巴面积。"""
    return sum(lay.area_kha for lay in bundled.layouts_for("CHN", crop))


def season_rows(weather, crop: str, year: int) -> list[dict]:
    cur, prev = SEASON[crop]
    rows = []
    for rid in CROP_REGIONS[crop]:
        for m in cur:
            rec = weather.get((rid, year, m))
            if rec:
                rows.append(rec)
        for m in prev:
            rec = weather.get((rid, year - 1, m))
            if rec:
                rows.append(rec)
    return rows


def graph_stress(crop: str, year: int, weather) -> float:
    """用产量图谱物候胁迫，把产业图接到产量特征上。"""
    from .graph import build_yield_graph

    g = build_yield_graph(bundled.layouts())
    factors = []
    for lay in bundled.layouts_for("CHN", crop):
        series = []
        for m in range(1, 13):
            rec = weather.get((lay.region_id, year, m))
            if rec:
                series.append(
                    MonthClimate(
                        lay.region_id,
                        year,
                        m,
                        rec["tmean"],
                        rec["tmax"],
                        rec["tmin"],
                        rec["precip_mm"],
                        rec.get("source") or "obs",
                    )
                )
        if len(series) < 4:
            continue
        fc = forecast_yield(lay, series, g)
        factors.append(fc.detail.get("factor", 1.0))
    return sum(factors) / len(factors) if factors else 1.0


def yield_samples(conn) -> list[Sample]:
    weather = load_weather(conn)
    yields = load_yields(conn)
    by_crop_year = {(r["crop"], r["year"]): r["yield_t_ha"] for r in yields}
    crops = ["小麦", "水稻", "玉米"]
    names = [f"crop:{c}" for c in crops] + ["tmean", "tmax", "precip_m", "graph_factor", "lag_yield"]
    out = []
    for row in yields:
        crop, year = row["crop"], row["year"]
        months = season_rows(weather, crop, year)
        if len(months) < 4:
            continue
        tmean = sum(m["tmean"] for m in months) / len(months)
        tmax = sum(m["tmax"] for m in months) / len(months)
        precip = sum(m["precip_mm"] for m in months) / max(len(CROP_REGIONS[crop]), 1)
        factor = graph_stress(crop, year, weather)
        lag = by_crop_year.get((crop, year - 1))
        if lag is None:
            continue
        onehot = [1.0 if crop == c else 0.0 for c in crops]
        feats = onehot + [tmean, tmax, precip / 1000.0, factor, lag]
        out.append(
            Sample(
                crop=crop,
                year=year,
                y=float(row["yield_t_ha"]),
                features=feats,
                names=names,
                extras={"tmean": tmean, "tmax": tmax, "precip": precip, "graph_factor": factor, "lag": lag},
            )
        )
    return out


def sales_samples(conn) -> list[Sample]:
    """全国谷物产量（吨）作销量/供给代理。特征：谷物单产、面积、主产区年天气。"""
    weather = load_weather(conn)
    cereal = load_cereal(conn)
    years = sorted(y for y, v in cereal.items() if v.get("production_t"))
    names = ["cereal_yield", "area_mha", "tmean", "tmax", "precip_m", "lag_prod_mt"]
    out = []
    for year in years:
        prev = cereal.get(year - 1)
        cur = cereal[year]
        if not prev or not prev.get("production_t") or not cur.get("yield_t_ha"):
            continue
        months = []
        for rid in CROP_REGIONS["玉米"] + CROP_REGIONS["水稻"] + CROP_REGIONS["小麦"]:
            for m in (5, 6, 7, 8, 9):
                rec = weather.get((rid, year, m))
                if rec:
                    months.append(rec)
        if len(months) < 8:
            continue
        area_ha = None
        # 产量吨 / (单产吨/公顷) = 公顷
        if cur["yield_t_ha"]:
            area_ha = cur["production_t"] / cur["yield_t_ha"]
        tmean = sum(m["tmean"] for m in months) / len(months)
        tmax = sum(m["tmax"] for m in months) / len(months)
        precip = sum(m["precip_mm"] for m in months) / 4.0
        feats = [
            cur["yield_t_ha"],
            (area_ha or 0.0) / 1e6,
            tmean,
            tmax,
            precip / 1000.0,
            prev["production_t"] / 1e8,
        ]
        out.append(
            Sample(
                crop="谷物",
                year=year,
                y=cur["production_t"] / 1e8,  # 亿吨，数值稳定
                features=feats,
                names=names,
                extras={"production_t": cur["production_t"], "area_ha": area_ha or 0.0},
            )
        )
    return out


def split_by_year(samples: list[Sample], test_years: tuple[int, ...]) -> tuple[list[Sample], list[Sample]]:
    train = [s for s in samples if s.year not in test_years]
    test = [s for s in samples if s.year in test_years]
    return train, test


def fit(samples: list[Sample], l2: float = 0.5) -> list[float]:
    return _ridge([s.features for s in samples], [s.y for s in samples], l2)


def predict(weights: list[float], sample: Sample) -> float:
    return _dot(weights, [1.0] + sample.features)


def metrics(y_true: list[float], y_pred: list[float]) -> dict[str, float]:
    n = len(y_true)
    mae = sum(abs(a - b) for a, b in zip(y_true, y_pred)) / n
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(y_true, y_pred)) / n)
    mape = sum(abs(a - b) / abs(a) for a, b in zip(y_true, y_pred) if a) / n
    return {"n": n, "mae": round(mae, 4), "rmse": round(rmse, 4), "mape": round(mape, 4)}


def eval_task(train: list[Sample], test: list[Sample], l2: float = 0.5) -> dict[str, object]:
    if len(train) < 4 or not test:
        return {"ok": False, "reason": f"train={len(train)} test={len(test)}，不足以切分评估"}
    w = fit(train, l2)
    pred = [predict(w, s) for s in test]
    truth = [s.y for s in test]
    mean_b = sum(s.y for s in train) / len(train)
    persist = []
    for s in test:
        lags = [t.y for t in train if t.crop == s.crop]
        persist.append(lags[-1] if lags else mean_b)
    model_m = metrics(truth, pred)
    mean_m = metrics(truth, [mean_b] * len(test))
    pers_m = metrics(truth, persist)
    return {
        "ok": True,
        "train_n": len(train),
        "test_n": len(test),
        "test_years": sorted({s.year for s in test}),
        "model": model_m,
        "baseline_mean": mean_m,
        "baseline_persist": pers_m,
        "beats_mean": model_m["mae"] < mean_m["mae"],
        "beats_persist": model_m["mae"] < pers_m["mae"],
        "predictions": [
            {
                "crop": s.crop,
                "year": s.year,
                "actual": round(s.y, 4),
                "pred": round(p, 4),
                "persist": round(q, 4),
            }
            for s, p, q in zip(test, pred, persist)
        ],
        "weights": {("bias" if i == 0 else train[0].names[i - 1]): round(v, 5) for i, v in enumerate(w)},
    }


def crop_production_table(yield_rows: list[Sample]) -> list[dict[str, object]]:
    """结构销量：单产 × 布局面积。单位万吨。"""
    rows = []
    for s in yield_rows:
        kt = s.y * crop_area_kha(s.crop)  # t/ha * kha = 千吨
        rows.append(
            {
                "crop": s.crop,
                "year": s.year,
                "yield_t_ha": round(s.y, 4),
                "area_kha": crop_area_kha(s.crop),
                "struct_production_kt": round(kt, 1),
            }
        )
    return rows


def run(test_years: tuple[int, ...] = (2023, 2024), db_path: Path | None = None) -> dict[str, object]:
    conn = _connect(db_path or DB_PATH)
    try:
        y_all = yield_samples(conn)
        s_all = sales_samples(conn)
    finally:
        conn.close()

    y_train, y_test = split_by_year(y_all, test_years)
    s_train, s_test = split_by_year(s_all, test_years)
    report = {
        "task": "产业图谱端到端：产量与销量估计",
        "split": {"test_years": list(test_years), "rule": "按年份切分，不用随机"},
        "yield": eval_task(y_train, y_test),
        "sales_cereal_1e8t": eval_task(s_train, s_test),
        "structural_sales": crop_production_table(y_all),
        "sample_counts": {
            "yield_all": len(y_all),
            "yield_train": len(y_train),
            "yield_test": len(y_test),
            "sales_all": len(s_all),
            "sales_train": len(s_train),
            "sales_test": len(s_test),
        },
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
