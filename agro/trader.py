"""交易专家的执行与风控结构：把一个方向信号变成一笔有边界的交易。

前面几层只给方向。`personas.py` 把方向直接乘上整段价格涨跌，
结果是「各链简单收益相加」这种没有账户的口径：可以跌破 −100%，
一条咖啡链就能主导合计。真实交易员不是这么持仓的。

这里补上交易台上真正的那套结构，每一条都对应一个具体约束：

1. **风险单位**：每笔只赌账户的固定比例（默认 0.75%），不是「满仓一个方向」。
2. **止损定位**：止损放在 2×ATR 之外，由波动决定，不是拍一个百分比。
3. **头寸规模**：名义敞口 = 风险预算 ÷ 止损距离。波动大的品种自动减仓，
   这直接解决了咖啡把合计吃掉的问题。
4. **非对称赔率**：止盈 3×ATR，赔率 1.5R，命中率低于 50% 也能有正期望。
5. **保本移动**：浮盈到 +1R 把止损移到成本价，不让赢单变亏单。
6. **时间止损**：最多持有 15 个交易日，或状态结束即离场，不无限期挂着。
7. **信念分档**：展望偏离超过两倍带宽才给满档风险，否则半档。
8. **账户复利**：所有链的成交按时间排到同一个账户上复利，敞口有上限，
   净值有下界。这才是「收益」两个字能用的口径。

同一根K线同时触及止损与止盈时，一律按止损成交；跳空穿越止损按开盘价成交。
这是保守侧，不是乐观侧。

**这一层不创造边。** 风控只改变收益的形状（尾部、方差、破产概率），
不改变方向的信息含量。所以判定仍然走随机符号零分布，
而且零分布必须跑同一套止损止盈引擎——否则非对称赔率会被误读成 alpha。
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field

from .collect import ROOT
from .eventstudy import LAYOUT_SYMBOLS, Revision, build_revisions, load_bars
from .futures import FuturesBar
from .intervene import INTERVENE_BAND, Episode, decide_path, episodes, outlook_state, price_direction
from .strategy import COST_BPS, DEFAULT_COST_BPS, TRADING_DAYS_PER_YEAR, _bar_index, _px

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "trader_report.json"
TABLE_PATH = MODEL_DIR / "trader_table.md"

PERM_DRAWS = 2000
PERM_SEED = 20260913

PRIMARY = [
    (lid, sym, kind)
    for lid, pairs in LAYOUT_SYMBOLS.items()
    for sym, kind, role in pairs
    if role == "primary"
]


@dataclass(frozen=True)
class TradeRules:
    """交易台参数。每一项都是一个可以被质疑的假设，所以全部显式写出来。"""

    risk_per_trade: float = 0.0075  # 每笔风险占账户比例
    stop_atr: float = 2.0
    target_atr: float = 3.0
    breakeven_at_r: float = 1.0
    max_hold: int = 15
    max_notional: float = 1.0  # 单笔名义敞口上限（账户倍数）
    atr_window: int = 14
    conviction_full_band: float = 2.0  # 偏离 ≥ 该倍数带宽才给满档风险
    half_size: float = 0.5


DEFAULT_RULES = TradeRules()


@dataclass
class Fill:
    """一笔走完全程的成交。r_multiple 以初始止损距离为 1R。"""

    link: str
    year: int
    state: str
    entry_date: str
    exit_date: str
    direction: int
    entry: float
    exit: float
    stop: float
    target: float
    atr: float
    risk_frac: float
    notional: float
    r_multiple: float
    equity_return: float
    exit_reason: str


def _hl(bar: FuturesBar) -> tuple[float, float]:
    px = _px(bar)
    hi = float(bar.high) if bar.high else px
    lo = float(bar.low) if bar.low else px
    return hi, lo


def _true_range(prev_close: float, bar: FuturesBar) -> float:
    hi, lo = _hl(bar)
    return max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))


def atr_at(bars: list[FuturesBar], i: int, window: int) -> float | None:
    """截至第 i 根K线（含）的 ATR。建仓在 i 之后，这里不看未来。"""
    if i < window or i >= len(bars):
        return None
    trs = []
    for k in range(i - window + 1, i + 1):
        prev = _px(bars[k - 1])
        if prev <= 0:
            return None
        trs.append(_true_range(prev, bars[k]))
    if not trs:
        return None
    value = statistics.fmean(trs)
    return value if value > 0 else None


def conviction_size(deviation: float, band: float, rules: TradeRules) -> float:
    """偏离越过两倍带宽才给满档。信念分档，不是每次都梭哈。"""
    if abs(deviation) >= rules.conviction_full_band * band:
        return 1.0
    return rules.half_size


def simulate(
    bars: list[FuturesBar],
    signal_i: int,
    direction: int,
    rules: TradeRules,
    cost_bps: float,
    size_mult: float,
    last_i: int | None = None,
) -> dict[str, object] | None:
    """从信号日之后第一根K线建仓，按 止损 / 止盈 / 状态结束 / 时间止损 走完。

    signal_i 是信息日所在（或之前最后一根）K线下标，建仓价取 signal_i+1 的收盘。
    ATR 只用 signal_i 及之前的数据。
    """
    entry_i = signal_i + 1
    if entry_i >= len(bars):
        return None
    atr = atr_at(bars, signal_i, rules.atr_window)
    if atr is None:
        return None
    entry = _px(bars[entry_i])
    if entry <= 0:
        return None

    stop_dist = rules.stop_atr * atr
    stop = entry - direction * stop_dist
    target = entry + direction * rules.target_atr * atr
    if stop <= 0:
        return None

    stop_pct = stop_dist / entry
    notional = min(rules.max_notional, rules.risk_per_trade * size_mult / stop_pct)

    horizon = entry_i + rules.max_hold
    if last_i is not None:
        horizon = min(horizon, last_i)
    horizon = min(horizon, len(bars) - 1)
    if horizon <= entry_i:
        horizon = min(entry_i + 1, len(bars) - 1)

    exit_i = horizon
    exit_px = _px(bars[horizon])
    reason = "状态结束/时间止损"
    moved = False

    for k in range(entry_i + 1, horizon + 1):
        bar = bars[k]
        hi, lo = _hl(bar)
        adverse = lo if direction > 0 else hi
        favorable = hi if direction > 0 else lo
        # 保守顺序：先判止损。同一根K线两边都触及时按止损算。
        if direction * (stop - adverse) >= 0:
            op = float(bar.open) if bar.open else _px(bar)
            exit_px = op if direction * (stop - op) >= 0 else stop
            exit_i = k
            reason = "保本离场" if moved else "止损"
            break
        if direction * (favorable - target) >= 0:
            exit_px = target
            exit_i = k
            reason = "止盈"
            break
        if not moved and direction * (favorable - (entry + direction * stop_dist * rules.breakeven_at_r)) >= 0:
            stop = entry
            moved = True
    else:
        exit_px = _px(bars[horizon])

    if exit_px <= 0:
        return None
    raw = direction * (exit_px / entry - 1.0)
    cost = cost_bps / 10000.0
    equity_return = notional * (raw - cost)
    return {
        "entry_date": bars[entry_i].date,
        "exit_date": bars[exit_i].date,
        "entry": round(entry, 4),
        "exit": round(exit_px, 4),
        "stop": round(stop, 4),
        "target": round(target, 4),
        "atr": round(atr, 4),
        "notional": round(notional, 4),
        "r_multiple": round(raw / stop_pct, 4),
        "equity_return": round(equity_return, 6),
        "exit_reason": reason,
    }


# ---------------------------------------------------------------- 成交簿


def overlay_fills(
    link: str,
    revisions: list[Revision],
    bars: list[FuturesBar],
    band: float,
    rules: TradeRules,
    cost_bps: float,
    flip: dict[tuple[str, int], int] | None = None,
) -> list[Fill]:
    """把干预事件（连续同状态）走成一笔笔有边界的成交。

    flip 用于零分布：按 (起始日, 年) 把方向换成随机 ±1，其余结构完全不动。
    """
    decs = decide_path(revisions, band)
    open_by_year: dict[int, float] = {}
    for d in decs:
        open_by_year.setdefault(d.year, d.factor)
    evs = [e for e in episodes(decs) if e.n_sections >= 2]
    out: list[Fill] = []
    for ep in evs:
        signal_i = _last_index_on_or_before(bars, ep.start)
        if signal_i is None:
            continue
        direction = price_direction(ep.state)
        if flip is not None:
            direction = flip.get((ep.start, ep.year), direction)
        if direction == 0:
            continue
        baseline = open_by_year.get(ep.year, ep.factor_start)
        deviation = ep.factor_end - baseline
        size = conviction_size(deviation, band, rules)
        last_i = _bar_index(bars, ep.end)
        sim = simulate(bars, signal_i, direction, rules, cost_bps, size, last_i)
        if sim is None:
            continue
        out.append(
            Fill(
                link=link,
                year=ep.year,
                state=ep.state,
                direction=direction,
                risk_frac=round(rules.risk_per_trade * size, 5),
                entry_date=str(sim["entry_date"]),
                exit_date=str(sim["exit_date"]),
                entry=float(sim["entry"]),
                exit=float(sim["exit"]),
                stop=float(sim["stop"]),
                target=float(sim["target"]),
                atr=float(sim["atr"]),
                notional=float(sim["notional"]),
                r_multiple=float(sim["r_multiple"]),
                equity_return=float(sim["equity_return"]),
                exit_reason=str(sim["exit_reason"]),
            )
        )
    return out


def absolute_fills(
    link: str,
    revisions: list[Revision],
    bars: list[FuturesBar],
    band: float,
    rules: TradeRules,
    cost_bps: float,
) -> list[Fill]:
    """尖端无干预：按因子相对 1.0 的绝对胁迫进场，同样走风控引擎。"""
    ordered = sorted(revisions, key=lambda r: (r.year, r.as_of))
    fake = [
        _Dec(r.as_of, r.year, outlook_state(r.factor, 1.0, band), r.factor)
        for r in ordered
    ]
    evs = [e for e in _episodes_from(fake) if e.n_sections >= 2]
    out: list[Fill] = []
    for ep in evs:
        signal_i = _last_index_on_or_before(bars, ep.start)
        if signal_i is None:
            continue
        direction = price_direction(ep.state)
        size = conviction_size(ep.factor_end - 1.0, band, rules)
        last_i = _bar_index(bars, ep.end)
        sim = simulate(bars, signal_i, direction, rules, cost_bps, size, last_i)
        if sim is None:
            continue
        out.append(
            Fill(
                link=link,
                year=ep.year,
                state=ep.state,
                direction=direction,
                risk_frac=round(rules.risk_per_trade * size, 5),
                entry_date=str(sim["entry_date"]),
                exit_date=str(sim["exit_date"]),
                entry=float(sim["entry"]),
                exit=float(sim["exit"]),
                stop=float(sim["stop"]),
                target=float(sim["target"]),
                atr=float(sim["atr"]),
                notional=float(sim["notional"]),
                r_multiple=float(sim["r_multiple"]),
                equity_return=float(sim["equity_return"]),
                exit_reason=str(sim["exit_reason"]),
            )
        )
    return out


@dataclass
class _Dec:
    as_of: str
    year: int
    state: str
    factor: float


def _episodes_from(decs: list[_Dec]) -> list[Episode]:
    evs: list[Episode] = []
    cur: list[_Dec] = []

    def close(group: list[_Dec]) -> Episode:
        a, b = group[0], group[-1]
        return Episode(a.year, a.state, a.as_of, b.as_of, a.factor, b.factor, len(group))

    for d in decs:
        if d.state == "hold":
            if cur:
                evs.append(close(cur))
                cur = []
            continue
        if cur and (cur[-1].state != d.state or cur[-1].year != d.year):
            evs.append(close(cur))
            cur = [d]
        else:
            cur.append(d)
    if cur:
        evs.append(close(cur))
    return evs


def _last_index_on_or_before(bars: list[FuturesBar], as_of: str) -> int | None:
    idx = None
    for i, b in enumerate(bars):
        if b.date <= as_of:
            idx = i
        else:
            break
    return idx


def buy_and_hold_fills(
    link: str,
    revisions: list[Revision],
    bars: list[FuturesBar],
    weight: float,
    cost_bps: float,
) -> list[Fill]:
    """初学者无干预：每个生长季开局满上多头，季末才走。没有止损，这是他的原状。"""
    by_year: dict[int, list[Revision]] = {}
    for r in revisions:
        by_year.setdefault(r.year, []).append(r)
    out: list[Fill] = []
    for year, revs in sorted(by_year.items()):
        revs = sorted(revs, key=lambda r: r.as_of)
        i = _bar_index(bars, revs[0].as_of)
        j = _bar_index(bars, revs[-1].as_of)
        if i is None:
            continue
        if j is None or j <= i:
            j = min(i + 1, len(bars) - 1)
        entry, exit_ = _px(bars[i]), _px(bars[j])
        if entry <= 0 or exit_ <= 0:
            continue
        raw = exit_ / entry - 1.0
        out.append(
            Fill(
                link=link,
                year=year,
                state="long",
                direction=1,
                risk_frac=weight,
                entry_date=bars[i].date,
                exit_date=bars[j].date,
                entry=round(entry, 4),
                exit=round(exit_, 4),
                stop=0.0,
                target=0.0,
                atr=0.0,
                notional=weight,
                r_multiple=0.0,
                equity_return=round(weight * (raw - cost_bps / 10000.0), 6),
                exit_reason="季末离场（无止损）",
            )
        )
    return out


# ---------------------------------------------------------------- 账户与统计


def account(fills: list[Fill]) -> dict[str, object]:
    """把所有链的成交排到同一个账户上复利。净值有下界，这才叫收益。"""
    if not fills:
        return {"ok": False, "reason": "无成交"}
    ordered = sorted(fills, key=lambda f: (f.entry_date, f.link))
    equity = 1.0
    peak = 1.0
    worst = 0.0
    curve = []
    for f in ordered:
        equity *= 1.0 + f.equity_return
        equity = max(equity, 0.0)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
        curve.append({"date": f.exit_date, "equity": round(equity, 5)})
    rs = [f.r_multiple for f in fills if f.atr > 0]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    rets = [f.equity_return for f in ordered]
    span_years = max(int(ordered[-1].exit_date[:4]) - int(ordered[0].entry_date[:4]) + 1, 1)
    sd = statistics.stdev(rets) if len(rets) > 2 else 0.0
    per_year = len(rets) / span_years
    return {
        "ok": True,
        "n_trades": len(ordered),
        "span": [ordered[0].entry_date, ordered[-1].exit_date],
        "final_equity": round(equity, 5),
        "cum_return": round(equity - 1.0, 5),
        "max_drawdown": round(worst, 5),
        "hit_rate": round(len(wins) / len(rs), 4) if rs else None,
        "avg_win_r": round(statistics.fmean(wins), 4) if wins else None,
        "avg_loss_r": round(statistics.fmean(losses), 4) if losses else None,
        "expectancy_r": round(statistics.fmean(rs), 4) if rs else None,
        "payoff_ratio": (
            round(statistics.fmean(wins) / abs(statistics.fmean(losses)), 3)
            if wins and losses and statistics.fmean(losses) != 0
            else None
        ),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else None
        ),
        "sharpe": (
            round(statistics.fmean(rets) / sd * (per_year ** 0.5), 4) if sd > 0 else None
        ),
        "exit_mix": _mix(ordered),
        "equity_curve": curve,
    }


def _mix(fills: list[Fill]) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in fills:
        out[f.exit_reason] = out.get(f.exit_reason, 0) + 1
    return out


def random_sign_null(
    links: dict[str, tuple[list[Revision], list[FuturesBar], float]],
    band: float,
    rules: TradeRules,
    draws: int = PERM_DRAWS,
    seed: int = PERM_SEED,
) -> dict[str, object]:
    """零分布必须跑同一套止损止盈引擎。

    只把事件方向换成随机 ±1，建仓日、ATR、头寸规模、止损止盈、时间止损全部不动。
    这样非对称赔率带来的那部分收益在零分布里同样存在，
    剩下的差值才是方向选择的贡献。
    """
    keys: list[tuple[str, tuple[str, int]]] = []
    for link, (revs, bars, _cost) in links.items():
        decs = decide_path(revs, band)
        for ep in episodes(decs):
            if ep.n_sections >= 2:
                keys.append((link, (ep.start, ep.year)))
    if not keys:
        return {"ok": False, "reason": "无干预事件"}

    rng = random.Random(seed)
    exp_null: list[float] = []
    ret_null: list[float] = []
    for _ in range(draws):
        flips = {link: {} for link in links}
        for link, key in keys:
            flips[link][key] = rng.choice((1, -1))
        fills: list[Fill] = []
        for link, (revs, bars, cost) in links.items():
            fills.extend(overlay_fills(link, revs, bars, band, rules, cost, flips[link]))
        acc = account(fills)
        if not acc.get("ok"):
            continue
        if acc.get("expectancy_r") is not None:
            exp_null.append(float(acc["expectancy_r"]))
        ret_null.append(float(acc["cum_return"]))
    if not exp_null:
        return {"ok": False, "reason": "零分布为空"}

    actual_fills: list[Fill] = []
    for link, (revs, bars, cost) in links.items():
        actual_fills.extend(overlay_fills(link, revs, bars, band, rules, cost))
    actual = account(actual_fills)
    a_exp = float(actual["expectancy_r"]) if actual.get("expectancy_r") is not None else None
    a_ret = float(actual["cum_return"])
    exp_null.sort()
    ret_null.sort()

    def pct(values: list[float], v: float | None) -> float | None:
        if v is None or not values:
            return None
        return round(sum(1 for x in values if x <= v) / len(values), 4)

    p_exp = pct(exp_null, a_exp)
    return {
        "ok": True,
        "draws": len(exp_null),
        "statistic": "全账户期望 R（每笔以初始止损距离为 1R）",
        "expectancy_actual": a_exp,
        "expectancy_null_median": round(statistics.median(exp_null), 4),
        "expectancy_null_p05": round(exp_null[int(0.05 * len(exp_null))], 4),
        "expectancy_null_p95": round(exp_null[int(0.95 * len(exp_null))], 4),
        "percentile_of_actual": p_exp,
        "beats_null_95": bool(p_exp is not None and p_exp > 0.95),
        "worse_than_null_05": bool(p_exp is not None and p_exp < 0.05),
        "return_actual": a_ret,
        "return_null_median": round(statistics.median(ret_null), 5),
        "return_null_p95": round(ret_null[int(0.95 * len(ret_null))], 5),
        "return_percentile": pct(ret_null, a_ret),
        "null_median_positive": bool(statistics.median(ret_null) > 0),
    }


# ---------------------------------------------------------------- 主流程


def run(
    band: float = INTERVENE_BAND,
    rules: TradeRules = DEFAULT_RULES,
    draws: int = PERM_DRAWS,
) -> dict[str, object]:
    revisions = build_revisions()
    links: dict[str, tuple[list[Revision], list[FuturesBar], float]] = {}
    for lid, symbol, kind in PRIMARY:
        revs = revisions.get(lid)
        if not revs:
            continue
        bars = load_bars(symbol, kind)
        if not bars:
            continue
        links[f"{lid}→{symbol}"] = (revs, bars, COST_BPS.get(symbol, DEFAULT_COST_BPS))

    weight = 1.0 / max(len(links), 1)
    per_link: dict[str, object] = {}
    books: dict[str, list[Fill]] = {
        "beginner_off": [],
        "beginner_on": [],
        "familiar_off": [],
        "familiar_on": [],
        "expert_off": [],
        "expert_on": [],
    }
    for link, (revs, bars, cost) in links.items():
        overlay = overlay_fills(link, revs, bars, band, rules, cost)
        absolute = absolute_fills(link, revs, bars, band, rules, cost)
        bh = buy_and_hold_fills(link, revs, bars, weight, cost)
        books["beginner_off"].extend(bh)
        books["beginner_on"].extend(overlay)
        books["familiar_on"].extend(overlay)
        books["expert_off"].extend(absolute)
        books["expert_on"].extend(overlay)
        per_link[link] = {
            "overlay": _slim(account(overlay)),
            "absolute": _slim(account(absolute)),
            "buy_and_hold": _slim(account(bh)),
            "fills": [f.__dict__ for f in overlay],
        }

    accounts = {name: _slim(account(fills)) for name, fills in books.items()}
    accounts["familiar_off"] = {"ok": True, "n_trades": 0, "cum_return": 0.0, "max_drawdown": 0.0}
    personas = {}
    for who in ("beginner", "familiar", "expert"):
        off = accounts[f"{who}_off"]
        on = accounts[f"{who}_on"]
        personas[who] = {
            "off_return": off.get("cum_return"),
            "on_return": on.get("cum_return"),
            "delta": (
                round(float(on.get("cum_return", 0.0)) - float(off.get("cum_return", 0.0)), 5)
            ),
            "off_max_drawdown": off.get("max_drawdown"),
            "on_max_drawdown": on.get("max_drawdown"),
        }

    null = random_sign_null(links, band, rules, draws=draws)
    overlay_acc = accounts["familiar_on"]
    report = {
        "task": "交易专家结构：风险单位 + 止损 + 赔率 + 账户复利，再验有没有边",
        "rules": {
            "risk_per_trade": rules.risk_per_trade,
            "stop": f"{rules.stop_atr}×ATR({rules.atr_window})",
            "target": f"{rules.target_atr}×ATR，赔率 {rules.target_atr / rules.stop_atr:.1f}R",
            "breakeven": f"浮盈 +{rules.breakeven_at_r}R 后止损移到成本价",
            "time_stop": f"最多 {rules.max_hold} 个交易日，状态结束即离场",
            "sizing": "名义敞口 = 风险预算 ÷ 止损距离，单笔上限 "
            f"{rules.max_notional}× 账户",
            "conviction": f"偏离 ≥ {rules.conviction_full_band}×带宽 给满档，否则 {rules.half_size} 档",
            "account": "所有链成交按时间排入同一账户复利，不是简单收益相加",
            "conservative": "同一根K线两边触及按止损算；跳空穿越按开盘价成交",
        },
        "band": band,
        "accounts": accounts,
        "personas": personas,
        "by_link": per_link,
        "edge_test": null,
        "verdict": _verdict(overlay_acc, null, personas),
        "caveats": [
            "风控只改变收益形状，不创造方向信息；零分布跑同一套止损止盈就是为了验这一点",
            "连续主力序列含换月跳空，ATR 与止损都会被跳空污染",
            "未建模保证金追缴、涨跌停无法离场、以及止损单被穿的滑点",
            "日内只有高低价，无法确定止损与止盈的先后，一律按止损优先",
            "初学者基线没有止损，是刻意保留的原状，不是我们推荐的做法",
        ],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    TABLE_PATH.write_text(to_markdown(report), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    report["table"] = str(TABLE_PATH)
    return report


def _slim(acc: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in acc.items() if k != "equity_curve"}


def _verdict(overlay: dict, null: dict, personas: dict) -> dict[str, object]:
    bounded = overlay.get("ok") and float(overlay.get("cum_return", -9)) > -1.0
    has_edge = bool(null.get("beats_null_95"))
    return {
        "bounded_loss": bool(bounded),
        "worst_case": overlay.get("max_drawdown"),
        "overlay_return": overlay.get("cum_return"),
        "expectancy_r": overlay.get("expectancy_r"),
        "solves_unbounded_accounting": bool(bounded),
        "solves_no_edge": has_edge,
        "percentile_vs_null": null.get("percentile_of_actual"),
        "positive_feedback": bool(
            has_edge and overlay.get("cum_return") is not None and float(overlay["cum_return"]) > 0
        ),
        "note": (
            "口径问题（收益可以跌破 −100%、一条链主导合计）由账户复利与波动定仓解决；"
            "方向有没有边只由零分布分位决定。两件事不能互相替代。"
        ),
    }


def to_markdown(report: dict) -> str:
    r = report["rules"]
    lines = [
        "# 交易专家结构与边的复验",
        "",
        "## 一、交易台结构（每条都是一个显式假设）",
        "",
        f"- 每笔风险：账户的 {r['risk_per_trade']:.2%}",
        f"- 止损：{r['stop']}",
        f"- 止盈：{r['target']}",
        f"- 保本：{r['breakeven']}",
        f"- 时间止损：{r['time_stop']}",
        f"- 头寸：{r['sizing']}",
        f"- 信念分档：{r['conviction']}",
        f"- 账户：{r['account']}",
        f"- 保守侧：{r['conservative']}",
        "",
        "## 二、三种交易者（账户复利口径）",
        "",
        "| 角色 | 无干预 | 有干预 | 收益变化 | 无干预最大回撤 | 有干预最大回撤 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    label = {"beginner": "初学者", "familiar": "熟悉者", "expert": "尖端"}
    for who, p in report["personas"].items():
        lines.append(
            f"| {label.get(who, who)} | {_pct(p['off_return'])} | {_pct(p['on_return'])} | "
            f"{_pct(p['delta'])} | {_pct(p['off_max_drawdown'])} | {_pct(p['on_max_drawdown'])} |"
        )
    acc = report["accounts"]["familiar_on"]
    lines += [
        "",
        "## 三、干预账簿的交易质量",
        "",
        f"- 成交笔数：{acc.get('n_trades')}",
        f"- 命中率：{acc.get('hit_rate')}",
        f"- 平均盈利：{acc.get('avg_win_r')} R；平均亏损：{acc.get('avg_loss_r')} R",
        f"- 赔率：{acc.get('payoff_ratio')}；盈亏比：{acc.get('profit_factor')}",
        f"- 期望：{acc.get('expectancy_r')} R/笔",
        f"- 账户累计：{_pct(acc.get('cum_return'))}；最大回撤：{_pct(acc.get('max_drawdown'))}",
        f"- 离场构成：{acc.get('exit_mix')}",
        "",
        "## 四、有没有边（零分布跑同一套止损止盈）",
        "",
    ]
    null = report["edge_test"]
    if null.get("ok"):
        lines += [
            f"- 统计量：{null['statistic']}",
            f"- 实际：{null['expectancy_actual']} R",
            f"- 随机方向中位：{null['expectancy_null_median']} R，5%：{null['expectancy_null_p05']}，95%：{null['expectancy_null_p95']}",
            f"- 实际所处分位：{null['percentile_of_actual']}",
            f"- **上尾显著（真有边）：{'是' if null['beats_null_95'] else '否'}**",
            f"- 随机方向的账户收益中位：{_pct(null['return_null_median'])}，实际 {_pct(null['return_actual'])}（分位 {null['return_percentile']}）",
        ]
        if null.get("null_median_positive"):
            lines.append(
                "- 注意：随机方向的中位收益也为正，说明这部分来自 1.5R 赔率结构本身，不是方向。"
            )
    else:
        lines.append(f"- 无法检验：{null.get('reason')}")

    v = report["verdict"]
    lines += [
        "",
        "## 五、两个问题分别回答",
        "",
        f"- 口径无界（收益跌破 −100%、单链主导合计）：{'已解决' if v['solves_unbounded_accounting'] else '未解决'}"
        f"（账户复利 + 波动定仓，最坏回撤 {_pct(v['worst_case'])}）",
        f"- 方向无边（没有 alpha）：{'已解决' if v['solves_no_edge'] else '未解决'}"
        f"（分位 {v['percentile_vs_null']}）",
        f"- 真正的正向回馈：{'是' if v['positive_feedback'] else '否'}",
        "",
        v["note"],
        "",
        "## 六、按链",
        "",
        "| 链 | 干预成交 | 干预账户收益 | 期望R | 绝对胁迫收益 | 死拿多头收益 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for key, blk in report["by_link"].items():
        ov = blk["overlay"]
        ab = blk["absolute"]
        bh = blk["buy_and_hold"]
        lines.append(
            f"| {key} | {ov.get('n_trades', 0)} | {_pct(ov.get('cum_return'))} | "
            f"{ov.get('expectancy_r')} | {_pct(ab.get('cum_return'))} | {_pct(bh.get('cum_return'))} |"
        )
    lines += ["", "## 七、口径限制", ""]
    for c in report["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def _pct(v: object) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(v)


def run_with_table(**kwargs) -> dict[str, object]:
    return run(**kwargs)
