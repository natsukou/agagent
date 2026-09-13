"""生长季内胁迫因子修正 → 期货收益率的事件研究。

为什么只对棉花值得做：`multicrop.py` 的结论里，棉花是唯一一个「仅图谱」
打赢持续基线的作物（美棉 MAPE 0.83% vs 3.38%）。谷物有灌溉和政策托底把
年际波动压平，物候图谱没有可用信号；棉花旱作比例高，单产对水热敏感。

信息口径是这个模块的全部难点。`multicrop.py` 的岭回归用整季已发生天气，
属季末估产，不能拿去交易。这里改成严格的事前构造：

- 截至日 t 的预期单产因子，只用 t 当天及之前的日天气；
- t 之后的月份用**剔除当年**的多年日均气候态补齐；
- 当月只观测到一部分时，按天数把已观测与气候态混合，避免月初
  只有两三天数据时降水累计接近 0、把干旱胁迫打出假尖峰；
- 修正量 = factor(t) - factor(t - step)，是 t 日收盘后才知道的信息；
- 建仓价取 t 之后第一个交易日收盘，平仓取其后 h 个交易日收盘。

预期符号为负：因子下修（作物变差）→ 供给预期下降 → 价格上行。

还做两个证伪检查：
- 对照组：玉米 / C0，图谱在玉米上没有技能，这里也不该出现相关性；
- 安慰剂：把修正量与**下一年同期**的收益率配对，相关性应当消失。
"""

from __future__ import annotations

import json
import random
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .climate import MonthClimate
from .collect import ROOT
from .crops import CropLayout
from .datasets import bundled
from .futures import FuturesBar, fetch_intl, fetch_symbol
from .graph import build_yield_graph
from .nowcast import DailyWx, load_daily
from .predict import forecast_yield

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "eventstudy_report.json"

STEP_DAYS = 3
HORIZONS = (1, 3, 5, 10)
PERM_DRAWS = 2000
PERM_SEED = 20260913

# 布局 → (合约, 取数方式, 角色)。国内合约走新浪连续主力，国际基准走 Yahoo。
#
# 角色说明：
# - primary：产区与合约标的对应，是真正要检验的链条；
# - cross_placebo：拿棉花产区的修正量去打大豆合约。棉花因子若真含供给信息，
#   应当只对棉价有解释力，对豆价没有。这比「换一个作物当对照」更严格。
#
# 不用玉米做对照组：东北春玉米灌溉充分，胁迫函数几乎恒等于 1，
# 450 个截面里只有 11 个非零修正，样本退化到无法检验（见 zero_share）。
LAYOUT_SYMBOLS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "cotton.xinjiang": (("CF0", "cn", "primary"),),
    "cotton.us.belt": (
        ("CT=F", "intl", "primary"),
        ("ZS=F", "intl", "cross_placebo"),
    ),
    "soy.us.belt": (("ZS=F", "intl", "primary"),),
    "coffee.br.arabica": (("KC=F", "intl", "primary"),),
}

# 非零修正少于这个数就不做检验：胁迫函数是阈值铰链，作物落在舒适区时
# 各阶段因子恒为 1.0，修正量恒为 0，此时相关性没有意义。
MIN_NONZERO = 40


@dataclass
class Revision:
    layout_id: str
    crop: str
    region_id: str
    year: int
    as_of: str
    progress: float
    factor: float
    delta: float


def monthly_normals(region_id: str, exclude_year: int) -> dict[int, MonthClimate]:
    """剔除目标年的多年月气候态。留着当年会把当年信息漏进「未来月份」的预期。"""
    days = load_daily(region_id)
    buckets: dict[int, list[DailyWx]] = {}
    for d in days:
        if d.day.year == exclude_year:
            continue
        buckets.setdefault(d.day.month, []).append(d)
    out: dict[int, MonthClimate] = {}
    for month, rows in buckets.items():
        n = len(rows)
        years = len({r.day.year for r in rows}) or 1
        out[month] = MonthClimate(
            region_id,
            0,
            month,
            sum(r.tmean for r in rows) / n,
            sum(r.tmax for r in rows) / n,
            sum(r.tmin for r in rows) / n,
            sum(r.precip for r in rows) / years,  # 月累计降水的多年平均
            "era5-normal-exyear",
        )
    return out


def blended_month(
    days: list[DailyWx],
    cal_year: int,
    month: int,
    as_of: date,
    normal: MonthClimate,
) -> MonthClimate:
    """当月已观测部分 + 剩余天数按气候态补齐，按天数加权。"""
    total = monthrange(cal_year, month)[1]
    obs = [d for d in days if d.day.year == cal_year and d.day.month == month and d.day <= as_of]
    if not obs:
        return normal
    n = len(obs)
    if n >= total:
        return MonthClimate(
            normal.region_id,
            cal_year,
            month,
            sum(d.tmean for d in obs) / n,
            sum(d.tmax for d in obs) / n,
            sum(d.tmin for d in obs) / n,
            sum(d.precip for d in obs),
            "obs",
        )
    rest = total - n
    daily_normal_precip = normal.precip_mm / total
    return MonthClimate(
        normal.region_id,
        cal_year,
        month,
        (sum(d.tmean for d in obs) + normal.tmean * rest) / total,
        (sum(d.tmax for d in obs) + normal.tmax * rest) / total,
        (sum(d.tmin for d in obs) + normal.tmin * rest) / total,
        sum(d.precip for d in obs) + daily_normal_precip * rest,
        "obs+normal",
    )


def _season_bounds(layout: CropLayout, year: int) -> tuple[date, date]:
    months = layout.season_months
    first, last = months[0], months[-1]
    start_year = year - 1 if first in layout.prev_year_months else year
    end_year = year - 1 if last in layout.prev_year_months else year
    return date(start_year, first, 1), date(end_year, last, monthrange(end_year, last)[1])


def factor_path(
    layout: CropLayout, year: int, graph, step: int = STEP_DAYS
) -> list[Revision]:
    """季内每 step 天一个截面，只用截至日已发生的天气。"""
    days = load_daily(layout.region_id)
    if not days:
        return []
    normals = monthly_normals(layout.region_id, year)
    months = layout.season_months
    if any(m not in normals for m in months):
        return []
    start, end = _season_bounds(layout, year)
    span = (end - start).days or 1

    out: list[Revision] = []
    prev: float | None = None
    as_of = start
    while as_of <= end:
        series = [
            blended_month(
                days,
                year - 1 if m in layout.prev_year_months else year,
                m,
                as_of,
                normals[m],
            )
            for m in months
        ]
        factor = forecast_yield(layout, series, graph).detail["factor"]
        if prev is not None:
            out.append(
                Revision(
                    layout_id=layout.id,
                    crop=layout.crop,
                    region_id=layout.region_id,
                    year=year,
                    as_of=as_of.isoformat(),
                    progress=round((as_of - start).days / span, 4),
                    factor=round(factor, 6),
                    delta=round(factor - prev, 6),
                )
            )
        prev = factor
        as_of = as_of + timedelta(days=step)
    return out


def load_bars(symbol: str, kind: str) -> list[FuturesBar]:
    bars = fetch_intl(symbol) if kind == "intl" else fetch_symbol(symbol)
    usable = [b for b in bars if (b.close or b.settle)]
    return sorted(usable, key=lambda b: b.date)


def _px(bar: FuturesBar) -> float:
    return float(bar.close or bar.settle or 0.0)


def forward_return(bars: list[FuturesBar], as_of: str, horizon: int) -> float | None:
    """建仓在 as_of 之后第一个交易日收盘，持有 horizon 个交易日。"""
    idx = next((i for i, b in enumerate(bars) if b.date > as_of), None)
    if idx is None or idx + horizon >= len(bars):
        return None
    entry, exit_ = _px(bars[idx]), _px(bars[idx + horizon])
    if entry <= 0 or exit_ <= 0:
        return None
    return exit_ / entry - 1.0


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


def permutation_p(xs: list[float], ys: list[float], draws: int = PERM_DRAWS) -> float:
    """打乱修正量与收益率的配对，看 |r| 被超过的频率。固定种子，可复现。

    这个检验处理的是「无关系」这个零假设，**不处理**重叠持有期带来的
    序列相关，所以 horizon 大于 step 时仍会高估显著性。
    """
    if len(xs) < 5:
        return 1.0
    obs = abs(pearson(xs, ys))
    rng = random.Random(PERM_SEED)
    shuffled = list(xs)
    hits = 0
    for _ in range(draws):
        rng.shuffle(shuffled)
        if abs(pearson(shuffled, ys)) >= obs:
            hits += 1
    return (hits + 1) / (draws + 1)


def _tercile_split(pairs: list[tuple[float, float]]) -> dict[str, object]:
    """按修正量分三档，看最差档与最好档的平均收益差。"""
    if len(pairs) < 9:
        return {"ok": False, "reason": f"样本 {len(pairs)} 条，不足以分档"}
    ordered = sorted(pairs, key=lambda kv: kv[0])
    k = len(ordered) // 3
    low = [r for _d, r in ordered[:k]]      # 下修最狠
    high = [r for _d, r in ordered[-k:]]    # 上修最多
    mean_low = sum(low) / len(low)
    mean_high = sum(high) / len(high)
    return {
        "ok": True,
        "n_per_tercile": k,
        "mean_return_worst_revision": round(mean_low, 5),
        "mean_return_best_revision": round(mean_high, 5),
        "spread": round(mean_low - mean_high, 5),
        "spread_sign_as_expected": mean_low > mean_high,
    }


def study_pair(
    revisions: list[Revision], bars: list[FuturesBar], horizon: int
) -> dict[str, object]:
    pairs: list[tuple[float, float]] = []
    for rev in revisions:
        if rev.delta == 0.0:
            continue  # 因子没动的截面不构成事件
        ret = forward_return(bars, rev.as_of, horizon)
        if ret is None:
            continue
        pairs.append((rev.delta, ret))
    if len(pairs) < 10:
        return {"ok": False, "n": len(pairs), "reason": "有效事件不足 10 条"}

    deltas = [d for d, _ in pairs]
    rets = [r for _, r in pairs]
    r = pearson(deltas, rets)
    agree = sum(1 for d, ret in pairs if (d < 0 and ret > 0) or (d > 0 and ret < 0))
    return {
        "ok": True,
        "n": len(pairs),
        "horizon_trading_days": horizon,
        "pearson_r": round(r, 4),
        "expected_sign": "negative",
        "sign_as_expected": r < 0,
        "permutation_p": round(permutation_p(deltas, rets), 4),
        "direction_hit_rate": round(agree / len(pairs), 4),
        "terciles": _tercile_split(pairs),
        "overlap_warning": horizon > 2,
    }


def placebo_next_year(
    revisions: list[Revision], bars: list[FuturesBar], horizon: int = 3
) -> dict[str, object]:
    """把修正量和一年后同期的收益率配对。真信号在这里应当消失。"""
    pairs: list[tuple[float, float]] = []
    for rev in revisions:
        if rev.delta == 0.0:
            continue
        y, m, d = (int(x) for x in rev.as_of.split("-"))
        try:
            shifted = date(y + 1, m, d).isoformat()
        except ValueError:
            continue
        ret = forward_return(bars, shifted, horizon)
        if ret is None:
            continue
        pairs.append((rev.delta, ret))
    if len(pairs) < 10:
        return {"ok": False, "n": len(pairs), "reason": "有效事件不足 10 条"}
    deltas = [d for d, _ in pairs]
    rets = [r for _, r in pairs]
    r = pearson(deltas, rets)
    return {
        "ok": True,
        "n": len(pairs),
        "pearson_r": round(r, 4),
        "permutation_p": round(permutation_p(deltas, rets), 4),
        "should_be_near_zero": True,
    }


def build_revisions(
    layout_ids: tuple[str, ...] | None = None,
    years: tuple[int, ...] = tuple(range(2016, 2025)),
    step: int = STEP_DAYS,
) -> dict[str, list[Revision]]:
    graph = build_yield_graph(bundled.layouts())
    lay_map = bundled.layout_map()
    ids = layout_ids or tuple(LAYOUT_SYMBOLS)
    out: dict[str, list[Revision]] = {}
    for lid in ids:
        lay = lay_map.get(lid)
        if lay is None:
            continue
        rows: list[Revision] = []
        for year in years:
            rows.extend(factor_path(lay, year, graph, step))
        if rows:
            out[lid] = rows
    return out


def run(
    layout_ids: tuple[str, ...] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    step: int = STEP_DAYS,
) -> dict[str, object]:
    revisions = build_revisions(layout_ids, step=step)
    results: dict[str, object] = {}
    for lid, revs in revisions.items():
        nonzero = sum(1 for r in revs if r.delta != 0.0)
        factors = [r.factor for r in revs]
        for symbol, kind, role in LAYOUT_SYMBOLS.get(lid, ()):
            bars = load_bars(symbol, kind)
            key = f"{lid}→{symbol}"
            base = {
                "layout": lid,
                "crop": revs[0].crop,
                "symbol": symbol,
                "role": role,
                "revisions": len(revs),
                "nonzero_revisions": nonzero,
                "zero_share": round(1.0 - nonzero / len(revs), 4),
                "factor_range": [round(min(factors), 4), round(max(factors), 4)],
            }
            if not bars:
                results[key] = {**base, "ok": False, "reason": f"{symbol} 无行情"}
                continue
            if nonzero < MIN_NONZERO:
                results[key] = {
                    **base,
                    "ok": False,
                    "reason": f"非零修正仅 {nonzero} 条，胁迫函数在该布局上几乎不动，不做检验",
                }
                continue
            results[key] = {
                **base,
                "ok": True,
                "bars": len(bars),
                "bar_span": [bars[0].date, bars[-1].date],
                "by_horizon": {str(h): study_pair(revs, bars, h) for h in horizons},
                "placebo_next_year": placebo_next_year(revs, bars),
            }

    report = {
        "task": "季内胁迫因子修正 → 期货收益率事件研究",
        "step_days": step,
        "horizons_trading_days": list(horizons),
        "construction": {
            "factor_at_t": "只用 t 及之前的日天气；未来月份用剔除当年的多年气候态补齐",
            "partial_month": "当月按天数混合已观测与气候态，避免月初降水累计假尖峰",
            "entry": "t 之后第一个交易日收盘",
            "exit": "建仓后 h 个交易日收盘",
            "expected_sign": "负：因子下修 → 供给预期下降 → 价格上行",
        },
        "caveats": [
            "置换检验只否证「无关系」，不处理重叠持有期的序列相关，h>2 时高估显著性",
            "每个布局用单点锚点代表整个产区",
            f"同时检验 {sum(len(v) for v in LAYOUT_SYMBOLS.values())} 组 × {len(horizons)} 个持有期，多重比较未做校正",
            "相关性不等于可交易：未计手续费、滑点、主力换月跳空",
            "胁迫函数是阈值铰链，阶段落在舒适区时因子恒为 1.0，修正量为 0；"
            "灌溉充分的布局（如东北春玉米）零占比可达 0.98，检验无意义",
        ],
        "results": results,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    return report


def to_markdown(report: dict) -> str:
    lines = ["# 季内因子修正与期货收益率事件研究", ""]
    lines.append(f"- 截面步长：{report['step_days']} 天")
    con = report["construction"]
    lines.append(f"- 因子构造：{con['factor_at_t']}")
    lines.append(f"- 当月处理：{con['partial_month']}")
    lines.append(f"- 建仓/平仓：{con['entry']} → {con['exit']}")
    lines.append(f"- 预期符号：{con['expected_sign']}")
    lines.append("")
    lines.append("## 一、修正序列本身")
    lines.append("")
    lines.append("| 布局→合约 | 角色 | 截面数 | 非零修正 | 零占比 | 因子区间 | 可检验 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        if not isinstance(res, dict) or "revisions" not in res:
            continue
        lines.append(
            f"| {key} | {res['role']} | {res['revisions']} | {res['nonzero_revisions']} | "
            f"{res['zero_share']} | {res['factor_range'][0]}–{res['factor_range'][1]} | "
            f"{'是' if res.get('ok') else res.get('reason', '否')} |"
        )
    lines.append("")
    lines.append("## 二、相关性")
    lines.append("")
    lines.append("| 布局→合约 | 角色 | 持有期 | 事件数 | 相关系数 | 符号符合 | 置换 p | 方向命中率 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        if not isinstance(res, dict) or "by_horizon" not in res:
            continue
        role = res["role"]
        for h, st in res["by_horizon"].items():
            if not st.get("ok"):
                lines.append(f"| {key} | {role} | {h} | {st.get('n', 0)} | - | - | - | {st.get('reason', '')} |")
                continue
            lines.append(
                f"| {key} | {role} | {h} | {st['n']} | {st['pearson_r']} | "
                f"{'是' if st['sign_as_expected'] else '否'} | {st['permutation_p']} | "
                f"{st['direction_hit_rate']} |"
            )
    lines.append("")
    lines.append("## 三、分档收益（下修最狠档 减 上修最多档，持有 3 日）")
    lines.append("")
    lines.append("| 布局→合约 | 每档样本 | 下修档均收益 | 上修档均收益 | 价差 | 符号符合 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        if not isinstance(res, dict) or "by_horizon" not in res:
            continue
        st = res["by_horizon"].get("3") or {}
        tc = st.get("terciles") or {}
        if not tc.get("ok"):
            continue
        lines.append(
            f"| {key} | {tc['n_per_tercile']} | {tc['mean_return_worst_revision']} | "
            f"{tc['mean_return_best_revision']} | {tc['spread']} | "
            f"{'是' if tc['spread_sign_as_expected'] else '否'} |"
        )
    lines.append("")
    lines.append("## 四、时移安慰剂（修正量配一年后同期收益，应接近 0）")
    lines.append("")
    lines.append("| 布局→合约 | 事件数 | 相关系数 | 置换 p |")
    lines.append("| --- | --- | --- | --- |")
    for key, res in report["results"].items():
        if not isinstance(res, dict) or "placebo_next_year" not in res:
            continue
        pb = res["placebo_next_year"]
        if not pb.get("ok"):
            continue
        lines.append(f"| {key} | {pb['n']} | {pb['pearson_r']} | {pb['permutation_p']} |")
    lines.append("")
    lines.append("## 五、口径限制")
    lines.append("")
    for c in report["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def run_with_table(**kwargs) -> dict[str, object]:
    report = run(**kwargs)
    md_path = MODEL_DIR / "eventstudy_table.md"
    md_path.write_text(to_markdown(report), encoding="utf-8")
    report["artifact_markdown"] = str(md_path)
    return report
