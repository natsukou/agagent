"""交易执行与风控结构的单元测试。

这里不测「策略赚不赚钱」，只测交易台的机械性质：
止损止盈在正确的价位成交、同一根K线两边触及按止损算、跳空按开盘价成交、
头寸由波动决定、账户净值有下界。
"""

from __future__ import annotations

import pytest

from agro.futures import FuturesBar
from agro.trader import (
    DEFAULT_RULES,
    Fill,
    TradeRules,
    account,
    atr_at,
    conviction_size,
    simulate,
)

ENTRY = 100.0


def _bar(i: int, o: float, h: float, l: float, c: float) -> FuturesBar:
    return FuturesBar(
        symbol="TEST",
        date=f"2020-01-{i + 1:02d}",
        open=o,
        high=h,
        low=l,
        close=c,
        settle=c,
        volume=1000.0,
        open_interest=1000.0,
    )


def _flat(n: int = 31) -> list[FuturesBar]:
    """n 根平盘K线：收盘 100，高低 ±1，真实波幅恒为 2，ATR = 2。"""
    return [_bar(i, ENTRY, ENTRY + 1, ENTRY - 1, ENTRY) for i in range(n)]


def test_atr_needs_full_window():
    bars = _flat(10)
    assert atr_at(bars, 9, 14) is None
    bars = _flat(31)
    assert atr_at(bars, 29, 14) == pytest.approx(2.0)


def test_target_exit_pays_payoff_ratio():
    bars = _flat(31) + [_bar(31, ENTRY, 107.0, ENTRY, 106.0)]
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "止盈"
    # 止损 2×ATR = 4，止盈 3×ATR = 6，赔率恰好 1.5R
    assert sim["r_multiple"] == pytest.approx(1.5, abs=1e-3)


def test_stop_exit_loses_one_r():
    bars = _flat(31) + [_bar(31, ENTRY, ENTRY, 95.0, 96.0)]
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "止损"
    assert sim["r_multiple"] == pytest.approx(-1.0, abs=1e-3)


def test_touching_both_sides_counts_as_stop():
    """同一根K线既到止盈也到止损，必须按止损算，不能按乐观侧。"""
    bars = _flat(31) + [_bar(31, ENTRY, 107.0, 95.0, 106.0)]
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "止损"
    assert sim["r_multiple"] < 0


def test_gap_through_stop_fills_at_open():
    """跳空穿过止损时按开盘价成交，亏损可以超过 1R。"""
    bars = _flat(31) + [_bar(31, 90.0, 91.0, 89.0, 90.0)]
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit"] == pytest.approx(90.0)
    assert sim["r_multiple"] == pytest.approx(-2.5, abs=1e-3)


def test_breakeven_stop_prevents_winner_turning_loser():
    """先冲到 +1R，再跌回成本价下方：应在成本价离场，不是吃满 1R 亏损。"""
    bars = _flat(31)
    bars.append(_bar(31, ENTRY, 105.0, ENTRY, 104.0))  # 触及 +1R（104）
    bars.append(_bar(32, ENTRY, ENTRY, 95.0, 96.0))  # 回落穿过成本价
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "保本离场"
    assert sim["r_multiple"] == pytest.approx(0.0, abs=1e-9)


def test_time_stop_exits_at_horizon():
    bars = _flat(31) + [_bar(31 + k, ENTRY, ENTRY + 1, ENTRY - 1, ENTRY) for k in range(40)]
    sim = simulate(bars, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "状态结束/时间止损"
    assert sim["exit_date"] == bars[30 + DEFAULT_RULES.max_hold].date


def test_short_direction_is_mirrored():
    bars = _flat(31) + [_bar(31, ENTRY, ENTRY, 93.0, 94.0)]
    sim = simulate(bars, 29, -1, DEFAULT_RULES, 0.0, 1.0)
    assert sim is not None
    assert sim["exit_reason"] == "止盈"
    assert sim["r_multiple"] == pytest.approx(1.5, abs=1e-3)


def test_notional_is_set_by_volatility_not_by_hand():
    """止损距离翻倍，名义敞口减半。这是波动定仓，不是固定手数。"""
    quiet = _flat(31) + [_bar(31, ENTRY, ENTRY + 1, ENTRY - 1, ENTRY)]
    loud = [_bar(i, ENTRY, ENTRY + 2, ENTRY - 2, ENTRY) for i in range(31)]
    loud.append(_bar(31, ENTRY, ENTRY + 1, ENTRY - 1, ENTRY))
    a = simulate(quiet, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    b = simulate(loud, 29, 1, DEFAULT_RULES, 0.0, 1.0)
    assert a is not None and b is not None
    assert float(b["notional"]) == pytest.approx(float(a["notional"]) / 2.0, abs=1e-4)


def test_notional_respects_cap():
    rules = TradeRules(risk_per_trade=0.5, max_notional=1.0)
    bars = _flat(31) + [_bar(31, ENTRY, ENTRY + 1, ENTRY - 1, ENTRY)]
    sim = simulate(bars, 29, 1, rules, 0.0, 1.0)
    assert sim is not None
    assert float(sim["notional"]) == pytest.approx(1.0)


def test_conviction_tiers():
    band = 0.03
    assert conviction_size(-0.07, band, DEFAULT_RULES) == 1.0
    assert conviction_size(0.04, band, DEFAULT_RULES) == DEFAULT_RULES.half_size


def _fill(ret: float, i: int) -> Fill:
    return Fill(
        link="x",
        year=2020,
        state="long",
        entry_date=f"2020-01-{i + 1:02d}",
        exit_date=f"2020-01-{i + 2:02d}",
        direction=1,
        entry=ENTRY,
        exit=ENTRY,
        stop=96.0,
        target=106.0,
        atr=2.0,
        risk_frac=0.0075,
        notional=0.2,
        r_multiple=ret / 0.2 / 0.04,
        equity_return=ret,
        exit_reason="止损",
    )


def test_account_return_cannot_break_minus_one():
    """连续 50 笔 −10% 只是趋近 −100%，不会像简单收益相加那样跌到 −500%。"""
    acc = account([_fill(-0.1, i) for i in range(50)])
    assert acc["ok"]
    assert -1.0 < float(acc["cum_return"]) < -0.99
    assert float(acc["max_drawdown"]) >= -1.0


def test_account_compounds_in_date_order():
    acc = account([_fill(0.1, 0), _fill(0.1, 1)])
    assert float(acc["final_equity"]) == pytest.approx(1.21, rel=1e-9)


def test_empty_account_is_not_ok():
    assert account([])["ok"] is False
