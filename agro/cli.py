"""农业产量图谱命令行。

    python -m agro.cli graph-stats
    python -m agro.cli layouts
    python -m agro.cli climate --region changjiang --start-month 6 --horizon 4 --warm-demo
    python -m agro.cli yield --layout rice.changjiang.single --warm-demo
    python -m agro.cli yield-all
    python -m agro.cli demand-graph
    python -m agro.cli nowcast --step 3
    python -m agro.cli gcn
    python -m agro.cli industry-eval --test-years 2023,2024
    python -m agro.cli multicrop --table
    python -m agro.cli event-study --table
"""

from __future__ import annotations

import argparse
import json
import sys

from .adapter import to_plan
from .climate import MonthClimate, forecast_horizon
from .connectors import BundledClimateSource, demo_warm_anomaly, planned_connectors
from .datasets import bundled, dce
from .demand_graph import snapshot as demand_snapshot
from .graph import attach_counties, attach_dce_instruments, build_yield_graph
from .market import coverage_report, dominant_snapshots
from .predict import forecast_yield
from . import collect as collect_mod
from . import store as store_mod
from . import trainability as train_mod
from . import train as fit_mod


def _utf8() -> None:
    """Windows 控制台默认 GBK，产出表里的中文与数学符号会直接抛 UnicodeEncodeError。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def _out(obj) -> None:
    _utf8()
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _text(s: str) -> None:
    _utf8()
    print(s)


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
    attach_counties(g)
    _out(g.stats())


def cmd_demand(_args):
    _out(demand_snapshot())


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


def cmd_train(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    _out(fit_mod.run(test_years=years))


def cmd_nowcast(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import nowcast as nowcast_mod

    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    _out(nowcast_mod.run_nowcast(test_years=years, step=args.step))


def cmd_gcn(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import gcn as gcn_mod

    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    _out(gcn_mod.run_gcn(test_years=years))


def cmd_futures(args):
    from . import futures as fut_mod

    syms = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip()) or None
    _out(fut_mod.availability(refresh=args.refresh, symbols=syms))


def cmd_futures_align(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import futures as fut_mod

    _out(fut_mod.align_with_yield(symbol=args.symbol.upper()))


def cmd_multicrop(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import multicrop as mc_mod

    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    crops = tuple(x.strip() for x in args.crops.split(",") if x.strip())
    report = mc_mod.run(test_years=years, crops=crops)
    if args.table:
        _text(mc_mod.to_markdown(report))
    else:
        _out({k: v for k, v in report.items() if k != "results"})


def cmd_strategy(args):
    from . import strategy as st_mod

    report = st_mod.run_with_table(hold=args.hold, min_abs=args.min_delta)
    if args.table:
        _text(st_mod.to_markdown(report))
    else:
        _out({k: v for k, v in report.items() if k != "results"})


def cmd_accuracy(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import accuracy as acc_mod

    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    report = acc_mod.run_with_table(test_years=years)
    if args.table:
        _text(acc_mod.to_markdown(report))
    else:
        _out({k: v for k, v in report.items() if k != "rows"})


def cmd_eventstudy(args):
    from . import eventstudy as es_mod

    ids = tuple(x.strip() for x in args.layouts.split(",") if x.strip()) or None
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    report = es_mod.run_with_table(layout_ids=ids, horizons=horizons, step=args.step)
    if args.table:
        _text(es_mod.to_markdown(report))
    else:
        _out({k: v for k, v in report.items() if k != "results"})


def cmd_psd(args):
    from . import psd as psd_mod

    if args.refresh:
        psd_mod.fetch_psd(refresh=True)
    _out(psd_mod.coverage())


def cmd_industry(args):
    if not store_mod.DB_PATH.exists():
        raise SystemExit("尚未建库，先运行 collect 与 graph-build")
    from . import industry as industry_mod

    years = tuple(int(x) for x in args.test_years.split(",") if x.strip())
    report = industry_mod.run(test_years=years, force_download=args.refresh)
    _out(report)


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


def cmd_ai_serve(args):
    from .ai.server import serve

    serve(args.host, args.port)


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
    col.add_argument("--weather-start", default="2015-01-01")
    col.add_argument("--weather-end", default="2024-12-31")
    col.set_defaults(func=cmd_collect)
    sub.add_parser("graph-build").set_defaults(func=cmd_graph_build)
    sub.add_parser("graph-summary").set_defaults(func=cmd_graph_summary)
    sub.add_parser("train-check").set_defaults(func=cmd_train_check)
    tr = sub.add_parser("train")
    tr.add_argument("--test-years", default="2023,2024", help="逗号分隔的测试年份")
    tr.set_defaults(func=cmd_train)
    sub.add_parser("demand-graph").set_defaults(func=cmd_demand)
    nc = sub.add_parser("nowcast")
    nc.add_argument("--test-years", default="2023,2024")
    nc.add_argument("--step", type=int, default=3, help="1=每日修正，3=每三日修正")
    nc.set_defaults(func=cmd_nowcast)
    gc = sub.add_parser("gcn")
    gc.add_argument("--test-years", default="2023,2024")
    gc.set_defaults(func=cmd_gcn)
    fu = sub.add_parser("futures")
    fu.add_argument("--symbols", default="", help="逗号分隔，默认全部")
    fu.add_argument("--refresh", action="store_true", help="重新拉取行情")
    fu.set_defaults(func=cmd_futures)
    fa = sub.add_parser("futures-align")
    fa.add_argument("--symbol", default="C0")
    fa.set_defaults(func=cmd_futures_align)
    mc = sub.add_parser("multicrop")
    mc.add_argument("--test-years", default="2023,2024")
    mc.add_argument("--crops", default="大豆,玉米,棉花,咖啡")
    mc.add_argument("--table", action="store_true", help="打印 Markdown 产出表")
    mc.set_defaults(func=cmd_multicrop)
    st = sub.add_parser("strategy")
    st.add_argument("--hold", type=int, default=3, help="持有交易日数")
    st.add_argument("--min-delta", type=float, default=0.002, help="修正量阈值")
    st.add_argument("--table", action="store_true", help="打印 Markdown 产出表")
    st.set_defaults(func=cmd_strategy)
    ac = sub.add_parser("accuracy")
    ac.add_argument("--test-years", default="2023,2024")
    ac.add_argument("--table", action="store_true", help="打印 Markdown 产出表")
    ac.set_defaults(func=cmd_accuracy)
    es = sub.add_parser("event-study")
    es.add_argument("--layouts", default="", help="留空则用默认棉花两地 + 玉米对照组")
    es.add_argument("--horizons", default="1,3,5,10", help="持有交易日数")
    es.add_argument("--step", type=int, default=3, help="季内截面步长（日）")
    es.add_argument("--table", action="store_true", help="打印 Markdown 产出表")
    es.set_defaults(func=cmd_eventstudy)
    ps = sub.add_parser("psd")
    ps.add_argument("--refresh", action="store_true", help="重新下载 USDA PSD 四类 zip")
    ps.set_defaults(func=cmd_psd)
    ind = sub.add_parser("industry-eval")
    ind.add_argument("--test-years", default="2023,2024")
    ind.add_argument("--refresh", action="store_true", help="重新检索魔搭并下载 USDA 对照集")
    ind.set_defaults(func=cmd_industry)
    ai = sub.add_parser("ai-serve", help="启动本地 DeepSeek 解释助手 API")
    ai.add_argument("--host", default="127.0.0.1")
    ai.add_argument("--port", type=int, default=8787)
    ai.set_defaults(func=cmd_ai_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
