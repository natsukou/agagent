"""农业产量图谱命令行。

    python -m agro.cli graph-stats
    python -m agro.cli layouts
    python -m agro.cli climate --region changjiang --start-month 6 --horizon 4 --warm-demo
    python -m agro.cli yield --layout rice.changjiang.single --warm-demo
    python -m agro.cli yield-all
    python -m agro.cli connectors
"""

from __future__ import annotations

import argparse
import json
import sys

from .adapter import to_plan
from .climate import MonthClimate, forecast_horizon
from .connectors import BundledClimateSource, demo_warm_anomaly, planned_connectors
from .datasets import bundled, dce
from .graph import attach_dce_instruments, build_yield_graph
from .market import coverage_report, dominant_snapshots
from .predict import forecast_yield
from . import collect as collect_mod
from . import store as store_mod
from . import trainability as train_mod


def _out(obj) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _series(region_id: str, start_year: int, start_month: int, horizon: int, warm: bool, strict: bool) -> list[MonthClimate]:
    src = BundledClimateSource(demo_warm_anomaly(region_id, (start_month - 1 or 12,)) if warm else None)
    clim = src.climatology()
    recent = src.latest(region_id)
    forecast = forecast_horizon(region_id, start_year, start_month, horizon, clim, recent)
    if strict:
        return forecast
    filled = [clim.get(region_id, m) for m in range(1, 13)]
    overlay = {c.month: c for c in forecast}
    return [overlay.get(c.month, c) for c in filled]


def cmd_graph_stats(_args):
    g = build_yield_graph(bundled.layouts())
    attach_dce_instruments(g)
    _out(g.stats())


def cmd_layouts(_args):
    rows = []
    for lay in bundled.layouts():
        rows.append(
            {
                "id": lay.id,
                "crop": lay.crop,
                "system": lay.system,
                "region": lay.region_name,
                "baseline_t_ha": lay.baseline_t_ha,
                "area_kha": lay.area_kha,
                "irrigated_share": lay.irrigated_share,
                "stages": [s.name for s in lay.stages],
                "components": [c.name for c in lay.components],
                "season_months": lay.season_months,
            }
        )
    _out(rows)


def cmd_climate(args):
    series = _series(args.region, args.year, args.start_month, args.horizon, args.warm_demo, True)
    _out(
        [
            {
                "region": c.region_id,
                "year": c.year,
                "month": c.month,
                "tmean": round(c.tmean, 2),
                "tmax": round(c.tmax, 2),
                "tmin": round(c.tmin, 2),
                "precip_mm": round(c.precip_mm, 1),
                "source": c.source,
            }
            for c in series
        ]
    )


def cmd_yield(args):
    lay = bundled.layout_map()[args.layout]
    g = build_yield_graph(bundled.layouts())
    climate = _series(lay.region_id, args.year, args.start_month, args.horizon, args.warm_demo, args.strict)
    fc = forecast_yield(lay, climate, g)
    _out(to_plan(fc).to_dict())


def cmd_yield_all(args):
    g = build_yield_graph(bundled.layouts())
    rows = []
    for lay in bundled.layouts():
        climate = _series(lay.region_id, args.year, args.start_month, args.horizon, args.warm_demo, args.strict)
        rows.append(to_plan(forecast_yield(lay, climate, g)).to_dict())
    _out(rows)


def cmd_connectors(_args):
    _out([c.to_dict() for c in planned_connectors()])


def cmd_dce_map(_args):
    _out(coverage_report())


def cmd_collect(args):
    _out(collect_mod.collect_all(args.weather_start, args.weather_end))


def cmd_graph_build(_args):
    conn = store_mod._connect()
    stats = store_mod.build_graph(conn)
    export = store_mod.export_json(conn)
    conn.close()
    _out({"built": stats, "export": str(export), "summary": store_mod.summary(store_mod._connect())})


def cmd_train_check(_args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    v = train_mod.verdict()
    v["loo_ridge"] = train_mod.loo_ridge(v["samples"])
    v.pop("samples", None)
    _out(v)


def cmd_graph_summary(_args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行: python -m agro.cli collect && python -m agro.cli graph-build")
    _out(store_mod.summary(store_mod._connect()))


def cmd_dce_load(args):
    path = args.path or str(dce.sample_path())
    bars = dce.load(path)
    snaps = dominant_snapshots(bars)
    _out(
        {
            "path": path,
            "bars": len(bars),
            "dates": sorted({b.date for b in bars}),
            "varieties": sorted({b.variety_code for b in bars}),
            "snapshots": [s.to_dict() for s in snaps],
        }
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agro", description="农业产量图谱")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--start-month", type=int, default=6)
    p.add_argument("--horizon", type=int, default=6)
    p.add_argument("--warm-demo", action="store_true", help="叠加热干距平，演示动态预报")
    p.add_argument("--strict", action="store_true", help="不用气候态填补，暴露覆盖缺口")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("graph-stats").set_defaults(func=cmd_graph_stats)
    sub.add_parser("layouts").set_defaults(func=cmd_layouts)
    cl = sub.add_parser("climate")
    cl.add_argument("--region", default="changjiang")
    cl.set_defaults(func=cmd_climate)
    y = sub.add_parser("yield")
    y.add_argument("--layout", default="rice.changjiang.single")
    y.set_defaults(func=cmd_yield)
    sub.add_parser("yield-all").set_defaults(func=cmd_yield_all)
    sub.add_parser("connectors").set_defaults(func=cmd_connectors)
    sub.add_parser("dce-map").set_defaults(func=cmd_dce_map)
    dl = sub.add_parser("dce-load")
    dl.add_argument("--path", default="", help="本地 csv/json，默认用内置样例")
    dl.set_defaults(func=cmd_dce_load)
    col = sub.add_parser("collect")
    col.add_argument("--weather-start", default="2022-01-01")
    col.add_argument("--weather-end", default="2024-12-31")
    col.set_defaults(func=cmd_collect)
    sub.add_parser("graph-build").set_defaults(func=cmd_graph_build)
    sub.add_parser("graph-summary").set_defaults(func=cmd_graph_summary)
    sub.add_parser("train-check").set_defaults(func=cmd_train_check)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
