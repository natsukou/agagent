"""日 / 三日产量预估：更新的是季末预期单产，不是当天收割吨数。

主粮在灌浆结束前没有日收获。短周期输出定义为：
  已发生天气 + 未来 1/3 日预报 → 对本季期末单产的修正。
标签暂用全国分作物年单产（弱监督）。区县标签到位后只换 y，不改图。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .collect import RAW
from .county import COUNTIES
from .store import CHN_YIELD_FILTER, DB_PATH, PSD_SCOPE_FILTER, _connect
from .train import Sample, eval_task, split_by_year
from .trainability import _ridge, _dot

STEP_DAYS = 3

SEASON_SPAN = {
    ("玉米", "dongbei"): ((5, 1), (9, 30)),
    ("玉米", "huabei"): ((6, 1), (9, 30)),
    ("水稻", "dongbei"): ((5, 1), (9, 30)),
    ("水稻", "changjiang"): ((5, 1), (9, 30)),
    ("水稻", "huanan"): ((3, 1), (7, 31)),
    ("小麦", "huabei"): ((10, 1), (6, 15)),  # 起点在上一年
}


@dataclass
class DailyWx:
    day: date
    tmean: float
    tmax: float
    tmin: float
    precip: float


def load_daily(region_id: str) -> list[DailyWx]:
    path = RAW / "weather" / f"{region_id}.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    d = payload["daily"]
    out = []
    for i, t in enumerate(d["time"]):
        if d["temperature_2m_mean"][i] is None:
            continue
        y, m, dd = (int(x) for x in t.split("-"))
        out.append(
            DailyWx(
                date(y, m, dd),
                d["temperature_2m_mean"][i],
                d["temperature_2m_max"][i],
                d["temperature_2m_min"][i],
                d["precipitation_sum"][i] or 0.0,
            )
        )
    return out


def season_window(crop: str, region_id: str, year: int) -> tuple[date, date] | None:
    key = (crop, region_id)
    if key not in SEASON_SPAN:
        return None
    (sm, sd), (em, ed) = SEASON_SPAN[key]
    if crop == "小麦":
        return date(year - 1, sm, sd), date(year, em, ed)
    return date(year, sm, sd), date(year, em, ed)


def _slice(days: list[DailyWx], start: date, end: date) -> list[DailyWx]:
    return [d for d in days if start <= d.day <= end]


def window_features(history: list[DailyWx], horizon: list[DailyWx], progress: float) -> list[float]:
    def agg(rows: list[DailyWx]) -> tuple[float, float, float, float]:
        if not rows:
            return 0.0, 0.0, 0.0, 0.0
        n = len(rows)
        return (
            sum(r.tmean for r in rows) / n,
            sum(r.tmax for r in rows) / n,
            sum(r.precip for r in rows),
            sum(1.0 for r in rows if r.tmax >= 32.0) / n,
        )

    ht, hx, hp, hh = agg(history)
    ft, fx, fp, fh = agg(horizon)
    return [progress, ht, hx, hp / 100.0, hh, ft, fx, fp / 30.0, fh]


def load_yield_map(db_path: Path | None = None) -> dict[tuple[str, int], float]:
    conn = _connect(db_path or DB_PATH)
    try:
        return {
            (r["crop"], r["year"]): float(r["yield_t_ha"])
            for r in conn.execute(
                "SELECT crop, year, yield_t_ha FROM yield_year "
                f"WHERE {CHN_YIELD_FILTER} AND crop != '谷物'"
            )
        }
    finally:
        conn.close()


def build_nowcast_samples(step: int = STEP_DAYS, db_path: Path | None = None) -> list[Sample]:
    """每个区县-作物-三年窗一条样本。天气暂用所属主产区日序列。"""
    cache = {rid: load_daily(rid) for rid in {c.region_id for c in COUNTIES}}
    ymap = load_yield_map(db_path)
    crops = ["小麦", "水稻", "玉米"]
    names = [f"crop:{c}" for c in crops] + [
        "progress",
        "hist_tmean",
        "hist_tmax",
        "hist_precip",
        "hist_heat",
        "h3_tmean",
        "h3_tmax",
        "h3_precip",
        "h3_heat",
        "area",
    ]
    samples: list[Sample] = []
    years = sorted({y for _c, y in ymap})
    for county in COUNTIES:
        series = cache.get(county.region_id) or []
        if not series:
            continue
        for crop in county.crops:
            onehot = [1.0 if crop == c else 0.0 for c in crops]
            for year in years:
                label = ymap.get((crop, year))
                if label is None:
                    continue
                span = season_window(crop, county.region_id, year)
                if span is None:
                    continue
                start, end = span
                season = _slice(series, start, end)
                if len(season) < 20:
                    continue
                length = (end - start).days or 1
                t = start + timedelta(days=step)
                while t <= end:
                    hist = _slice(season, start, t)
                    fut = _slice(series, t + timedelta(days=1), t + timedelta(days=step))
                    progress = (t - start).days / length
                    feats = onehot + window_features(hist, fut, progress) + [county.area_kha / 200.0]
                    samples.append(
                        Sample(
                            crop=crop,
                            year=year,
                            y=label,
                            features=feats,
                            names=names,
                            extras={"county": county.id, "day": t.isoformat(), "progress": progress},
                        )
                    )
                    t += timedelta(days=step)
    return samples


def run_nowcast(test_years: tuple[int, ...] = (2023, 2024), step: int = STEP_DAYS) -> dict[str, object]:
    samples = build_nowcast_samples(step=step)
    train, test = split_by_year(samples, test_years)
    report = eval_task(train, test, l2=0.8)
    report["task"] = f"{step}日产量预估（季末单产修正）"
    report["definition"] = "预测的是本季期末单产，不是当天收割量"
    report["label"] = "全国分作物年单产（弱监督，区县共享同年标签）"
    report["sample_counts"] = {"all": len(samples), "train": len(train), "test": len(test)}
    # 报告太长时只留首尾预测
    preds = report.get("predictions") or []
    if len(preds) > 12:
        report["predictions"] = preds[:6] + preds[-6:]
        report["predictions_truncated"] = True
    return report
