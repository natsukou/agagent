"""本阶段产业预测：全国 / 布局 / 区县完整产出表，对照新测试集。"""

from __future__ import annotations

import json
from pathlib import Path

from .climate import MonthClimate
from .collect import ROOT
from .county import COUNTIES
from .datasets import bundled
from .evalset import holdout_map, load_or_fetch
from .graph import build_yield_graph
from .predict import forecast_yield
from .store import CHN_YIELD_FILTER, DB_PATH, PSD_SCOPE_FILTER, _connect
from .train import Sample, fit, load_weather, metrics, predict, split_by_year, yield_samples  # noqa: F401

TABLE_PATH = ROOT / "models" / "industry_table.json"
MD_PATH = ROOT / "models" / "industry_table.md"

TEST_YEARS = (2023, 2024)
STAGE_YIELD_MAPE = 0.05  # 全国单产 MAPE 5% 以内视为本阶段可用
RICE_MILLING = 0.70  # USDA Rice, Milled 是精米；单产×面积是稻谷，对照时折精米


def _weather_series(weather: dict, region_id: str, year: int) -> list[MonthClimate]:
    series = []
    for month in range(1, 13):
        rec = weather.get((region_id, year, month))
        if not rec:
            continue
        series.append(
            MonthClimate(
                region_id,
                year,
                month,
                rec["tmean"],
                rec["tmax"],
                rec["tmin"],
                rec["precip_mm"],
                rec.get("source") or "obs",
            )
        )
    return series


def _err(pred: float | None, actual: float | None) -> float | None:
    if pred is None or actual is None or actual == 0:
        return None
    return round(pred - actual, 4)


def _mape(pred: float | None, actual: float | None) -> float | None:
    if pred is None or actual is None or actual == 0:
        return None
    return round(abs(pred - actual) / abs(actual), 4)


def select_method(train: list[Sample]) -> dict[str, object]:
    """只在训练年份上决定用 persist 还是 ridge，禁止用测试年实测值挑。

    做法：训练集内留一年，用其余年份拟合岭回归，与「上年单产」比 MAE。
    """
    years = sorted({s.year for s in train})
    ridge_err: list[float] = []
    persist_err: list[float] = []
    for year in years:
        inner_train = [s for s in train if s.year != year]
        inner_test = [s for s in train if s.year == year]
        if len(inner_train) < 4 or not inner_test:
            continue
        w = fit(inner_train, 0.5)
        for s in inner_test:
            ridge_err.append(abs(predict(w, s) - s.y))
            lag = s.extras.get("lag")
            if lag is not None:
                persist_err.append(abs(float(lag) - s.y))
    if not ridge_err or not persist_err:
        return {"method": "persist", "reason": "训练集不足以比较，默认上年持续"}
    r_mae = sum(ridge_err) / len(ridge_err)
    p_mae = sum(persist_err) / len(persist_err)
    return {
        "method": "ridge" if r_mae < p_mae else "persist",
        "train_loo_ridge_mae": round(r_mae, 4),
        "train_loo_persist_mae": round(p_mae, 4),
        "reason": "训练集留一年比较，未使用测试年实测",
    }


def build_table(
    test_years: tuple[int, ...] = TEST_YEARS,
    db_path: Path | None = None,
    holdout: dict[tuple[str, int], dict] | None = None,
) -> dict[str, object]:
    fetched = None
    if holdout is None:
        fetched = load_or_fetch()
        holdout = holdout_map(fetched["table"])

    conn = _connect(db_path or DB_PATH)
    try:
        weather = load_weather(conn)
        samples = yield_samples(conn)
        # 这张表报的是中国三主粮，标签口径固定为 OWID/FAO。库里还有 USDA PSD
        # 多国标签（scope 前缀 PSD:），其中咖啡没有单产、yield_t_ha 是 NULL，
        # 混进来会直接把 float() 撞崩，也会把口径搞混。
        owid = {
            (r["crop"], r["year"]): float(r["yield_t_ha"])
            for r in conn.execute(
                f"SELECT crop, year, yield_t_ha FROM yield_year WHERE {CHN_YIELD_FILTER} AND crop != '谷物'"
            )
        }
    finally:
        conn.close()

    train, _ = split_by_year(samples, test_years)
    weights = fit(train, 0.5) if len(train) >= 4 else None
    selection = select_method(train)
    chosen_method = str(selection["method"])
    by_crop_year = {(s.crop, s.year): s for s in samples}
    persist_src = {}
    for s in train:
        persist_src[s.crop] = s.y

    g = build_yield_graph(bundled.layouts())
    rows: list[dict[str, object]] = []
    # 这张表的对照集是 USDA 中国三主粮，所以只走中国的三主粮布局；
    # 大豆/棉花/咖啡与美巴布局走 multicrop 的产出表。
    staple_layouts = [lay for lay in bundled.layouts_for("CHN") if lay.crop in ("小麦", "水稻", "玉米")]

    for crop in ("小麦", "水稻", "玉米"):
        for year in test_years:
            act = holdout.get((crop, year), {})
            sample = by_crop_year.get((crop, year))
            persist = persist_src.get(crop)
            model_y = predict(weights, sample) if weights and sample else None
            graph_factor = float(sample.extras["graph_factor"]) if sample and "graph_factor" in sample.extras else None
            graph_y = (persist * graph_factor) if persist and graph_factor else None
            actual_y = act.get("yield_t_ha")
            actual_p = act.get("production_kt")
            actual_area = act.get("area_kha")
            # 预报时点拿不到当年收获面积，用上一年面积
            area = (holdout.get((crop, year - 1)) or {}).get("area_kha")

            def prod(y):
                if y is None or area is None:
                    return None
                kt = y * float(area)
                if crop == "水稻":
                    kt *= RICE_MILLING
                return round(kt, 1)

            method = chosen_method if model_y is not None else "persist"
            pick = model_y if method == "ridge" else persist
            rice_note = "；水稻产量已按 0.70 折精米对齐 USDA" if crop == "水稻" else ""
            rows.append(
                {
                    "level": "全国",
                    "crop": crop,
                    "unit_id": "CHN",
                    "unit_name": "中国",
                    "year": year,
                    "persist_yield_t_ha": None if persist is None else round(persist, 4),
                    "graph_yield_t_ha": None if graph_y is None else round(graph_y, 4),
                    "model_yield_t_ha": None if model_y is None else round(model_y, 4),
                    "chosen_yield_t_ha": None if pick is None else round(pick, 4),
                    "method": method,
                    "area_kha": area,
                    "persist_prod_kt": prod(persist),
                    "graph_prod_kt": prod(graph_y),
                    "model_prod_kt": prod(model_y),
                    "chosen_prod_kt": prod(pick),
                    "actual_yield_t_ha": actual_y,
                    "actual_prod_kt": actual_p,
                    "actual_area_kha": actual_area,
                    "yield_err": _err(pick, actual_y),
                    "yield_mape": _mape(pick, actual_y),
                    "prod_err": _err(prod(pick), actual_p),
                    "prod_mape": _mape(prod(pick), actual_p),
                    "label_source": act.get("source") or "",
                    "note": "对照 USDA；训练标签是 OWID/FAO" + rice_note,
                }
            )

    for lay in staple_layouts:
        for year in test_years:
            series = _weather_series(weather, lay.region_id, year)
            fc = forecast_yield(lay, series, g)
            act = holdout.get((lay.crop, year), {})
            persist = persist_src.get(lay.crop)
            rows.append(
                {
                    "level": "布局",
                    "crop": lay.crop,
                    "unit_id": lay.id,
                    "unit_name": lay.system,
                    "year": year,
                    "persist_yield_t_ha": None if persist is None else round(persist, 4),
                    "graph_yield_t_ha": round(fc.expected_t_ha, 4),
                    "model_yield_t_ha": None,
                    "chosen_yield_t_ha": round(fc.expected_t_ha, 4),
                    "method": "graph_phenology",
                    "area_kha": lay.area_kha,
                    "persist_prod_kt": None if persist is None else round(persist * lay.area_kha, 1),
                    "graph_prod_kt": round(fc.expected_kt, 1),
                    "model_prod_kt": None,
                    "chosen_prod_kt": round(fc.expected_kt, 1),
                    "actual_yield_t_ha": None,
                    "actual_prod_kt": None,
                    "actual_area_kha": None,
                    "yield_err": None,
                    "yield_mape": None,
                    "prod_err": None,
                    "prod_mape": None,
                    "label_source": "",
                    "note": f"布局种子面积；全国实测单产 {act.get('yield_t_ha') or '—'}，无布局实测",
                }
            )

    layout_factor = {}
    for lay in staple_layouts:
        for year in test_years:
            series = _weather_series(weather, lay.region_id, year)
            fc = forecast_yield(lay, series, g)
            layout_factor[(lay.region_id, lay.crop, year)] = fc.detail.get("factor", 1.0)

    for county in COUNTIES:
        for crop in county.crops:
            for year in test_years:
                persist = persist_src.get(crop)
                factor = layout_factor.get((county.region_id, crop, year))
                if factor is None:
                    # 五常水稻挂最近水稻布局
                    factor = next(
                        (layout_factor[k] for k in layout_factor if k[1] == crop and k[2] == year),
                        1.0,
                    )
                y = persist * factor if persist else None
                act = holdout.get((crop, year), {})
                rows.append(
                    {
                        "level": "区县",
                        "crop": crop,
                        "unit_id": county.id,
                        "unit_name": county.name,
                        "year": year,
                        "persist_yield_t_ha": None if persist is None else round(persist, 4),
                        "graph_yield_t_ha": None if y is None else round(y, 4),
                        "model_yield_t_ha": None,
                        "chosen_yield_t_ha": None if y is None else round(y, 4),
                        "method": "persist×region_factor",
                        "area_kha": county.area_kha,
                        "persist_prod_kt": None if persist is None else round(persist * county.area_kha, 1),
                        "graph_prod_kt": None if y is None else round(y * county.area_kha, 1),
                        "model_prod_kt": None,
                        "chosen_prod_kt": None if y is None else round(y * county.area_kha, 1),
                        "actual_yield_t_ha": None,
                        "actual_prod_kt": None,
                        "actual_area_kha": None,
                        "yield_err": None,
                        "yield_mape": None,
                        "prod_err": None,
                        "prod_mape": None,
                        "label_source": "",
                        "note": f"天气继承{county.region_id}；无县级实测，全国单产参考 {act.get('yield_t_ha') or '—'}",
                    }
                )

    national = [r for r in rows if r["level"] == "全国" and r["yield_mape"] is not None]
    y_true = [float(r["actual_yield_t_ha"]) for r in national]
    y_pred = [float(r["chosen_yield_t_ha"]) for r in national]
    p_true = [float(r["actual_prod_kt"]) for r in national if r["prod_mape"] is not None]
    p_pred = [float(r["chosen_prod_kt"]) for r in national if r["prod_mape"] is not None]
    yield_m = metrics(y_true, y_pred) if y_true else {"n": 0}
    prod_m = metrics(p_true, p_pred) if p_true else {"n": 0}
    yield_ok = bool(y_true) and yield_m["mape"] <= STAGE_YIELD_MAPE
    per_method = {}
    for key, label in (
        ("persist_yield_t_ha", "persist"),
        ("model_yield_t_ha", "ridge"),
        ("graph_yield_t_ha", "graph_only"),
    ):
        vals = [(float(r["actual_yield_t_ha"]), float(r[key])) for r in national if r.get(key) is not None]
        if vals:
            per_method[label] = metrics([a for a, _ in vals], [b for _, b in vals])
    oracle = [
        min(
            (abs(float(r[k]) - float(r["actual_yield_t_ha"])), float(r[k]))
            for k in ("persist_yield_t_ha", "model_yield_t_ha")
            if r.get(k) is not None
        )[1]
        for r in national
    ]
    if oracle:
        per_method["oracle_upper_bound"] = metrics(y_true, oracle)
    report = {
        "task": "本阶段产业预测对照（全国主粮单产/产量）",
        "test_years": list(test_years),
        "holdout": fetched["meta"]["holdout"] if fetched else {"name": "injected"},
        "modelscope": fetched["meta"]["modelscope"] if fetched else {},
        "metrics": {
            "national_yield": yield_m,
            "national_production": prod_m,
            "per_method_yield": per_method,
        },
        "method_selection": selection,
        "leakage_guards": [
            "方法选择只用训练年留一年比较，不看测试年实测",
            "产量用上一年 USDA 收获面积，不用当年实测面积",
            "oracle_upper_bound 仅作上界参考，不是可交付精度",
        ],
        "stage": {
            "need": "全国小麦/水稻/玉米能出完整单产与产量表，单产 MAPE ≤ 5%",
            "national_yield_ok": yield_ok,
            "national_production_ok": bool(p_true) and prod_m["mape"] <= 0.08,
            "county_ok": False,
            "verdict": (
                "本阶段全国产业预测可以出表"
                if yield_ok
                else "全国表能出，但对照 USDA 的单产误差超过本阶段 5% 门槛"
            ),
            "county_note": "区县只有结构产量，没有县级标签，不计入本阶段验收",
        },
        "counts": {
            "national": sum(1 for r in rows if r["level"] == "全国"),
            "layout": sum(1 for r in rows if r["level"] == "布局"),
            "county": sum(1 for r in rows if r["level"] == "区县"),
            "all": len(rows),
        },
        "rows": rows,
        "owid_last_train": persist_src,
    }
    return report


def _md_table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    head = "| " + " | ".join(c[1] for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    lines = [head, sep]
    for r in rows:
        cells = []
        for key, _ in cols:
            v = r.get(key)
            if v is None or v == "":
                cells.append("—")
            elif isinstance(v, float):
                cells.append(f"{v:.4f}" if abs(v) < 100 else f"{v:.1f}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def to_markdown(report: dict[str, object]) -> str:
    rows = report["rows"]
    nat = [r for r in rows if r["level"] == "全国"]
    lay = [r for r in rows if r["level"] == "布局"]
    cty = [r for r in rows if r["level"] == "区县"]
    st = report["stage"]
    ym = report["metrics"]["national_yield"]
    pm = report["metrics"]["national_production"]
    ms = report.get("modelscope") or {}
    hd = report.get("holdout") or {}
    pm_all = report["metrics"].get("per_method_yield") or {}
    sel = report.get("method_selection") or {}
    cols = [
        ("crop", "作物"),
        ("unit_name", "单元"),
        ("year", "年"),
        ("chosen_yield_t_ha", "预测单产 t/ha"),
        ("method", "方法"),
        ("area_kha", "面积 kha"),
        ("chosen_prod_kt", "预测产量 kt"),
        ("actual_yield_t_ha", "实测单产"),
        ("actual_prod_kt", "实测产量 kt"),
        ("yield_mape", "单产MAPE"),
        ("prod_mape", "产量MAPE"),
        ("note", "说明"),
    ]
    return "\n".join(
        [
            "# 产业预测完整产出表",
            "",
            f"对照集：{hd.get('name', 'USDA FAS PSD')}。魔搭：{ms.get('note', '未检索')}",
            "",
            f"全国单产 {ym}；全国产量 {pm}",
            "",
            f"选定方法：{sel.get('method')}（{sel.get('reason')}）",
            "",
            "各方法单独误差（同一测试集）：",
            "",
            "\n".join(f"- {k}: {v}" for k, v in pm_all.items()),
            "",
            "面积用上一年 USDA 收获面积，不用当年实测面积。",
            "",
            f"**判定：{st['verdict']}** 区县：{st['county_note']}",
            "",
            "## 全国",
            "",
            _md_table(nat, cols),
            "",
            "## 布局（结构产量，无布局实测）",
            "",
            _md_table(lay, cols),
            "",
            "## 区县（结构产量，无县级实测）",
            "",
            _md_table(cty, cols),
            "",
        ]
    )


def run(test_years: tuple[int, ...] = TEST_YEARS, force_download: bool = False) -> dict[str, object]:
    if force_download:
        load_or_fetch(force=True)
    report = build_table(test_years=test_years)
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    MD_PATH.write_text(to_markdown(report), encoding="utf-8")
    report["written"] = {"json": str(TABLE_PATH), "markdown": str(MD_PATH)}
    return report
