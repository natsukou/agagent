"""多作物、多国的产量训练器。

和 `train.py` 的区别在于不再硬编码三主粮：
训练单元由布局自己推导——(作物, 国别) 一组，组内布局给出区域、生长季月份、
物候胁迫权重。气候框架统一（同一套 ERA5 日要素 + 同一套胁迫函数），
但每个作物的区域集合与影响图谱不同，这正是新增大豆/棉花/咖啡要测的东西。

跨年由布局的 `prev_year_months` 声明：华北冬小麦是 10-12 月，
巴西大豆是 10-12 月，巴西咖啡是 9-12 月（南半球）。取这些月份的天气时
用自然年 Y-1，其余月份用 Y。

咖啡目标是产量（千吨）而非单产——USDA PSD 没有咖啡面积和单产。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .climate import MonthClimate
from .collect import ROOT
from .crops import CropLayout
from .datasets import bundled
from .graph import build_yield_graph
from .predict import forecast_yield
from .store import CHN_YIELD_FILTER, DB_PATH, PSD_SCOPE_FILTER, _connect
from .train import metrics, split_by_year
from .trainability import _dot, _ridge

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "multicrop_report.json"

TEST_YEARS = (2023, 2024)
NEW_CROPS = ("大豆", "玉米", "棉花", "咖啡")


@dataclass
class GroupSample:
    crop: str
    scope: str
    year: int
    y: float
    features: list[float]
    names: list[str]
    factor: float


def layout_groups(crops: tuple[str, ...] = NEW_CROPS) -> dict[tuple[str, str], list[CropLayout]]:
    groups: dict[tuple[str, str], list[CropLayout]] = {}
    for lay in bundled.layouts():
        if lay.crop not in crops:
            continue
        groups.setdefault((lay.crop, lay.country), []).append(lay)
    return groups


def season_month_years(layout: CropLayout, market_year: int) -> list[tuple[int, int]]:
    """返回 (自然年, 月) 列表：跨年月份落在 market_year - 1。"""
    months: list[int] = []
    for st in layout.stages:
        m = st.start_month
        while True:
            if m not in months:
                months.append(m)
            if m == st.end_month:
                break
            m = 1 if m == 12 else m + 1
    return [
        (market_year - 1 if m in layout.prev_year_months else market_year, m)
        for m in sorted(months)
    ]


def load_weather(conn) -> dict[tuple[str, int, int], dict]:
    return {
        (r["region_id"], r["year"], r["month"]): dict(r)
        for r in conn.execute("SELECT * FROM weather_month")
    }


def load_psd_labels(conn) -> dict[tuple[str, str, int], dict]:
    out: dict[tuple[str, str, int], dict] = {}
    for r in conn.execute(
        f"SELECT scope, crop, year, yield_t_ha, production_t FROM yield_year WHERE {PSD_SCOPE_FILTER}"
    ):
        country = r["scope"].split(":", 1)[1]
        out[(r["crop"], country, r["year"])] = dict(r)
    return out


def layout_series(layout: CropLayout, market_year: int, weather) -> list[MonthClimate]:
    series: list[MonthClimate] = []
    for cal_year, month in season_month_years(layout, market_year):
        rec = weather.get((layout.region_id, cal_year, month))
        if rec is None:
            continue
        series.append(
            MonthClimate(
                layout.region_id,
                cal_year,
                month,
                rec["tmean"],
                rec["tmax"],
                rec["tmin"],
                rec["precip_mm"],
                rec.get("source") or "obs",
            )
        )
    return series


def group_features(
    layouts: list[CropLayout], market_year: int, weather, graph
) -> tuple[list[float], list[str], float] | None:
    """面积加权的生长季天气 + 图谱胁迫因子。任一布局天气不全就整组跳过。"""
    tmeans: list[tuple[float, float]] = []
    tmaxes: list[tuple[float, float]] = []
    precips: list[tuple[float, float]] = []
    factors: list[tuple[float, float]] = []
    for lay in layouts:
        series = layout_series(lay, market_year, weather)
        expect = len(season_month_years(lay, market_year))
        if len(series) < expect:
            return None
        w = lay.area_kha
        tmeans.append((sum(c.tmean for c in series) / len(series), w))
        tmaxes.append((sum(c.tmax for c in series) / len(series), w))
        precips.append((sum(c.precip_mm for c in series), w))
        factors.append((forecast_yield(lay, series, graph).detail["factor"], w))

    def wavg(pairs: list[tuple[float, float]]) -> float:
        tw = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / tw if tw else 0.0

    factor = wavg(factors)
    feats = [wavg(tmeans), wavg(tmaxes), wavg(precips) / 1000.0, factor]
    names = ["tmean", "tmax", "precip_m", "graph_factor"]
    return feats, names, factor


def build_samples(
    crops: tuple[str, ...] = NEW_CROPS, db_path: Path | None = None
) -> dict[tuple[str, str], list[GroupSample]]:
    conn = _connect(db_path or DB_PATH)
    try:
        weather = load_weather(conn)
        labels = load_psd_labels(conn)
    finally:
        conn.close()
    graph = build_yield_graph(bundled.layouts())

    out: dict[tuple[str, str], list[GroupSample]] = {}
    for (crop, country), layouts in sorted(layout_groups(crops).items()):
        target = layouts[0].target
        years = sorted({y for (c, s, y) in labels if c == crop and s == country})
        samples: list[GroupSample] = []
        for year in years:
            rec = labels.get((crop, country, year))
            prev = labels.get((crop, country, year - 1))
            if rec is None or prev is None:
                continue
            if target == "yield_t_ha":
                y_now, y_lag = rec.get("yield_t_ha"), prev.get("yield_t_ha")
            else:
                # 库里按吨存，报表用千吨，量纲小一点岭回归更稳
                y_now = None if rec.get("production_t") is None else rec["production_t"] / 1000.0
                y_lag = None if prev.get("production_t") is None else prev["production_t"] / 1000.0
            if y_now is None or y_lag is None:
                continue
            built = group_features(layouts, year, weather, graph)
            if built is None:
                continue
            feats, names, factor = built
            samples.append(
                GroupSample(
                    crop=crop,
                    scope=country,
                    year=year,
                    y=float(y_now),
                    features=feats + [float(y_lag)],
                    names=names + ["lag_target"],
                    factor=factor,
                )
            )
        if samples:
            out[(crop, country)] = samples
    return out


def eval_group(samples: list[GroupSample], test_years: tuple[int, ...], l2: float = 0.5) -> dict:
    train, test = split_by_year(samples, test_years)  # type: ignore[arg-type]
    if len(train) < 4 or not test:
        return {"ok": False, "reason": f"train={len(train)} test={len(test)}，年份太少，不做评估"}
    w = _ridge([s.features for s in train], [s.y for s in train], l2)
    pred = [_dot(w, [1.0] + s.features) for s in test]
    truth = [s.y for s in test]

    # 持续基线：上一年实测（lag_target 是特征最后一维）
    persist = [s.features[-1] for s in test]
    # 只用图谱：上一年实测 × 当年胁迫因子 / 上一年胁迫因子
    lag_factor = {s.year: s.factor for s in samples}
    graph_only = []
    for s in test:
        f_prev = lag_factor.get(s.year - 1)
        graph_only.append(s.features[-1] * (s.factor / f_prev) if f_prev else s.features[-1])

    model_m = metrics(truth, pred)
    pers_m = metrics(truth, persist)
    graph_m = metrics(truth, graph_only)
    return {
        "ok": True,
        "target": "production_kt" if samples[0].crop == "咖啡" else "yield_t_ha",
        "train_years": sorted(s.year for s in train),
        "test_years": sorted(s.year for s in test),
        "model_ridge": model_m,
        "baseline_persist": pers_m,
        "graph_only": graph_m,
        "beats_persist": model_m["mae"] < pers_m["mae"],
        "graph_beats_persist": graph_m["mae"] < pers_m["mae"],
        "best": min(
            (("ridge", model_m["mae"]), ("persist", pers_m["mae"]), ("graph_only", graph_m["mae"])),
            key=lambda kv: kv[1],
        )[0],
        "rows": [
            {
                "year": s.year,
                "actual": round(s.y, 4),
                "ridge": round(p, 4),
                "persist": round(q, 4),
                "graph_only": round(g, 4),
                "graph_factor": round(s.factor, 4),
            }
            for s, p, q, g in zip(test, pred, persist, graph_only)
        ],
        "weights": {
            ("bias" if i == 0 else train[0].names[i - 1]): round(v, 5) for i, v in enumerate(w)
        },
    }


def to_markdown(report: dict) -> str:
    lines: list[str] = []
    lines.append("# 新作物结构训练产出表")
    lines.append("")
    lines.append(f"- 标签：{report['labels']}")
    lines.append(f"- 天气：{report['weather']}")
    split = report["split"]
    lines.append(f"- 切分：测试年 {split['test_years']}，{split['rule']}")
    it = report.get("information_timing") or {}
    if it:
        lines.append(f"- 信息口径：岭回归与仅图谱 {it.get('ridge_and_graph_only')}")
        lines.append(f"- 信息口径：持续基线 {it.get('persist')}")
    ofr = report.get("overfit_risk") or {}
    if ofr:
        lines.append(f"- 过拟合风险：{ofr.get('note')}")
    lines.append("")
    lines.append("## 一、训练单元（作物 × 国别）")
    lines.append("")
    lines.append("| 组 | 布局 | 区域 | 目标 | 单位 | 跨年 | 样本年数 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key, g in report["groups"].items():
        lines.append(
            f"| {key} | {', '.join(g['layouts'])} | {', '.join(g['regions'])} | "
            f"{g['target']} | {g['unit']} | {'是' if g['crosses_year'] else '否'} | {g['n_samples']} |"
        )
    lines.append("")
    lines.append("## 二、精度对照（MAPE，越小越好）")
    lines.append("")
    lines.append("| 组 | 目标 | 岭回归 | 持续基线 | 仅图谱 | 最优 | 岭胜持续 | 图谱胜持续 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, r in report["results"].items():
        if not r.get("ok"):
            lines.append(f"| {key} | - | - | - | - | 未评估 | - | {r.get('reason', '')} |")
            continue
        lines.append(
            f"| {key} | {r['target']} | {r['model_ridge']['mape']:.4f} | "
            f"{r['baseline_persist']['mape']:.4f} | {r['graph_only']['mape']:.4f} | "
            f"{r['best']} | {'是' if r['beats_persist'] else '否'} | "
            f"{'是' if r['graph_beats_persist'] else '否'} |"
        )
    lines.append("")
    lines.append("## 三、逐年逐组预测明细")
    lines.append("")
    lines.append("| 组 | 市场年 | 实测 | 岭回归 | 持续 | 仅图谱 | 图谱胁迫因子 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key, r in report["results"].items():
        if not r.get("ok"):
            continue
        for row in r["rows"]:
            lines.append(
                f"| {key} | {row['year']} | {row['actual']} | {row['ridge']} | "
                f"{row['persist']} | {row['graph_only']} | {row['graph_factor']} |"
            )
    sb = report["scoreboard"]
    lines.append("")
    lines.append("## 四、结论计分")
    lines.append("")
    lines.append(f"- 已评估组数：{sb['groups_evaluated']}")
    lines.append(f"- 岭回归胜持续基线：{sb['ridge_beats_persist']}")
    lines.append(f"- 仅图谱胜持续基线：{sb['graph_beats_persist']}")
    lines.append(f"- 各方法夺冠次数：{sb['best_counts']}")
    lines.append("")
    return "\n".join(lines)


def run(
    test_years: tuple[int, ...] = TEST_YEARS,
    crops: tuple[str, ...] = NEW_CROPS,
    db_path: Path | None = None,
) -> dict:
    groups = build_samples(crops, db_path)
    results = {}
    for (crop, country), samples in groups.items():
        results[f"{crop}/{country}"] = eval_group(samples, test_years)

    ok = [r for r in results.values() if r.get("ok")]
    report = {
        "task": "新作物结构训练：大豆/玉米/棉花/咖啡 × 国别",
        "split": {"test_years": list(test_years), "rule": "按市场年切分，不随机打乱"},
        "labels": "USDA FAS PSD（大豆 MT/HA、棉花 KG/HA→t/ha、咖啡仅产量 1000×60kg→kt）",
        "weather": "Open-Meteo ERA5 archive，按布局锚点取，时区 auto",
        "information_timing": {
            "ridge_and_graph_only": "用整个生长季已发生天气，属季末估产（hindcast），不是播种前预报",
            "persist": "只用上一年实测标签，是真正的事前预报",
            "why_it_matters": "三者可用信息不同，图谱赢持续不等于能提前预报",
        },
        "overfit_risk": {
            "features": 5,
            "train_years_per_group": 7,
            "note": "每组只有 7 个训练年、5 个特征加截距，岭回归接近饱和，单组名次不稳",
        },
        "groups": {
            f"{c}/{s}": {
                "layouts": [lay.id for lay in lays],
                "regions": sorted({lay.region_id for lay in lays}),
                "target": lays[0].target,
                "unit": lays[0].unit,
                "crosses_year": any(lay.crosses_year for lay in lays),
                "n_samples": len(groups[(c, s)]),
            }
            for (c, s), lays in sorted(layout_groups(crops).items())
            if (c, s) in groups
        },
        "results": results,
        "scoreboard": {
            "groups_evaluated": len(ok),
            "ridge_beats_persist": sum(1 for r in ok if r["beats_persist"]),
            "graph_beats_persist": sum(1 for r in ok if r["graph_beats_persist"]),
            "best_counts": {
                k: sum(1 for r in ok if r["best"] == k) for k in ("ridge", "persist", "graph_only")
            },
        },
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = MODEL_DIR / "multicrop_table.md"
    md_path.write_text(to_markdown(report), encoding="utf-8")
    report["artifacts"] = {"json": str(REPORT_PATH), "markdown": str(md_path)}
    return report
