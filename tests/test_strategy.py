"""期货策略测试：方向约定、不重叠成交、成本扣减、随机零分布判定。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.eventstudy import Revision
from agro.futures import FuturesBar
from agro.strategy import (
    COST_BPS,
    Trade,
    _full_study,
    _max_drawdown,
    _sharpe,
    build_trades,
    evaluate,
    random_sign_null,
    revision_signal,
)


def bar(day: str, close: float) -> FuturesBar:
    return FuturesBar("X", day, close, close, close, close, None, 1000.0, 0.0, "test")


def rev(as_of: str, delta: float) -> Revision:
    return Revision("lay", "棉花", "r", int(as_of[:4]), as_of, 0.5, 0.9, delta)


def trade(gross: float, direction: int = 1) -> Trade:
    return Trade("2024-01-02", "2024-01-05", direction, -0.01, gross)


class TestSignal(unittest.TestCase):
    def test_down_revision_goes_long(self):
        """因子下修 = 作物变差 = 供给预期下降 = 做多。符号搞反整个测试就无意义。"""
        self.assertEqual(revision_signal(-0.01), 1)

    def test_up_revision_goes_short(self):
        self.assertEqual(revision_signal(0.01), -1)

    def test_below_threshold_stays_flat(self):
        self.assertEqual(revision_signal(0.0005), 0)
        self.assertEqual(revision_signal(-0.0005), 0)

    def test_threshold_is_inclusive(self):
        self.assertNotEqual(revision_signal(0.002, min_abs=0.002), 0)

    def test_zero_delta_is_flat(self):
        self.assertEqual(revision_signal(0.0), 0)


class TestBuildTrades(unittest.TestCase):
    def setUp(self):
        self.bars = [bar(f"2024-01-{d:02d}", 100.0) for d in range(1, 29)]

    def test_no_overlap_between_trades(self):
        """每 1 天一个强信号，持有 3 日，成交必须互不重叠。"""
        revs = [rev(f"2024-01-{d:02d}", -0.01) for d in range(1, 20)]
        trades = build_trades(revs, self.bars, hold=3)
        self.assertGreater(len(trades), 1)
        for a, b in zip(trades, trades[1:]):
            self.assertGreaterEqual(b.entry_date, a.exit_date)

    def test_overlap_would_have_produced_more_trades(self):
        revs = [rev(f"2024-01-{d:02d}", -0.01) for d in range(1, 20)]
        trades = build_trades(revs, self.bars, hold=3)
        self.assertLess(len(trades), len(revs), "不去重的话成交数会接近信号数")

    def test_entry_is_after_information_date(self):
        trades = build_trades([rev("2024-01-05", -0.01)], self.bars, hold=3)
        self.assertEqual(trades[0].entry_date, "2024-01-06")

    def test_weak_signals_produce_no_trades(self):
        revs = [rev(f"2024-01-{d:02d}", -0.0001) for d in range(1, 20)]
        self.assertEqual(build_trades(revs, self.bars, hold=3), [])

    def test_direction_applied_to_return(self):
        bars = [bar("2024-01-01", 100.0), bar("2024-01-02", 100.0), bar("2024-01-03", 110.0)]
        long_t = build_trades([rev("2024-01-01", -0.01)], bars, hold=1)
        self.assertAlmostEqual(long_t[0].gross_return, 0.1)
        short_t = build_trades([rev("2024-01-01", 0.01)], bars, hold=1)
        self.assertAlmostEqual(short_t[0].gross_return, -0.1)

    def test_skips_when_horizon_runs_past_history(self):
        self.assertEqual(build_trades([rev("2024-01-27", -0.01)], self.bars, hold=5), [])


class TestEvaluate(unittest.TestCase):
    def test_cost_is_subtracted_per_trade(self):
        trades = [trade(0.01) for _ in range(20)]
        free = evaluate(trades, 0.0, 3)
        costed = evaluate(trades, 100.0, 3)  # 100bp = 1%
        self.assertAlmostEqual(free["mean_net_bps"], 100.0)
        self.assertAlmostEqual(costed["mean_net_bps"], 0.0)

    def test_cost_can_flip_a_winner_to_loser(self):
        trades = [trade(0.0005) for _ in range(20)]
        self.assertGreater(evaluate(trades, 0.0, 3)["cum_net_return"], 0)
        self.assertLess(evaluate(trades, 10.0, 3)["cum_net_return"], 0)

    def test_hit_rate_is_computed_net_of_cost(self):
        trades = [trade(0.0005) for _ in range(20)]
        self.assertEqual(evaluate(trades, 0.0, 3)["hit_rate_net"], 1.0)
        self.assertEqual(evaluate(trades, 10.0, 3)["hit_rate_net"], 0.0)

    def test_refuses_under_ten_trades(self):
        got = evaluate([trade(0.01) for _ in range(5)], 0.0, 3)
        self.assertFalse(got["ok"])

    def test_long_share_reported(self):
        trades = [trade(0.01, 1) for _ in range(15)] + [trade(0.01, -1) for _ in range(5)]
        self.assertAlmostEqual(evaluate(trades, 0.0, 3)["long_share"], 0.75)


class TestRiskMetrics(unittest.TestCase):
    def test_max_drawdown_on_known_path(self):
        # +10% 后 -50%，峰值 1.1，谷底 0.55 → 回撤 -50%
        self.assertAlmostEqual(_max_drawdown([0.1, -0.5]), -0.5, places=4)

    def test_no_drawdown_on_monotone_gains(self):
        self.assertAlmostEqual(_max_drawdown([0.01] * 10), 0.0)

    def test_sharpe_none_when_returns_constant(self):
        self.assertIsNone(_sharpe([0.01] * 10, 10.0))

    def test_sharpe_none_on_tiny_sample(self):
        self.assertIsNone(_sharpe([0.01, 0.02], 10.0))

    def test_sharpe_sign_follows_mean(self):
        self.assertLess(_sharpe([-0.02, 0.01, -0.03, 0.005, -0.01], 10.0), 0)


class TestRandomSignNull(unittest.TestCase):
    def test_reproducible_with_fixed_seed(self):
        trades = [trade(0.01 * (-1) ** i, 1) for i in range(40)]
        a = random_sign_null(trades, 0.0, draws=200)
        b = random_sign_null(trades, 0.0, draws=200)
        self.assertEqual(a["sharpe_actual"], b["sharpe_actual"])
        self.assertEqual(a["percentile_of_actual"], b["percentile_of_actual"])

    def test_perfect_strategy_sits_at_top_of_null(self):
        """方向永远猜对 → 分位应当接近 1，且判定为超出零分布。"""
        trades = [trade(abs(0.01 + 0.001 * i), 1) for i in range(40)]
        got = random_sign_null(trades, 0.0, draws=500)
        self.assertGreater(got["percentile_of_actual"], 0.95)
        self.assertTrue(got["above_null_95"])
        self.assertFalse(got["below_null_05"])

    def test_systematically_wrong_direction_lands_in_lower_tail(self):
        """方向永远猜反 → 落在下尾，不能被「超出零分布」当成有边。"""
        trades = [trade(-abs(0.01 + 0.001 * i), 1) for i in range(40)]
        got = random_sign_null(trades, 0.0, draws=500)
        self.assertTrue(got["below_null_05"])
        self.assertFalse(got["above_null_95"], "下尾显著不等于有边")
        self.assertTrue(got["outside_null_95"], "双尾标记仍应为真")

    def test_null_distribution_is_centred_near_zero(self):
        trades = [trade(0.01 * (-1) ** i, 1) for i in range(60)]
        got = random_sign_null(trades, 0.0, draws=500)
        self.assertLess(abs(got["sharpe_null_median"]), 1.0)
        self.assertLess(got["sharpe_null_p05"], got["sharpe_null_p95"])

    def test_refuses_tiny_sample(self):
        self.assertFalse(random_sign_null([trade(0.01)] * 5, 0.0)["ok"])

    def test_equal_magnitude_returns_have_no_sharpe(self):
        """等幅收益的标准差为 0，夏普无从计算，必须明确拒绝而不是报个假数。"""
        got = random_sign_null([trade(0.05, 1) for _ in range(30)], 0.0, draws=50)
        self.assertFalse(got["ok"])
        self.assertIn("标准差", got["reason"])

    def test_null_uses_same_magnitudes_as_strategy(self):
        """零分布必须复用同一批标的收益，只换方向；否则检验的就不是方向选择。"""
        trades = [trade(0.01 * (i + 1), 1) for i in range(30)]
        got = random_sign_null(trades, 0.0, draws=400)
        # 策略全做多且标的全涨 → 必然站在零分布顶端
        self.assertGreater(got["sharpe_actual"], got["sharpe_null_p95"])


class TestOfficialDecision(unittest.TestCase):
    def test_full_study_keeps_cost_and_null(self):
        trades = [trade(0.01 * (-1) ** i, 1) for i in range(20)]
        bars = [bar(f"2024-01-{d:02d}", 100.0 + d) for d in range(1, 25)]
        got = _full_study(trades, bars, "CF0", 3)
        self.assertIn("1x", got["cost_sensitivity"])
        self.assertIn("random_sign_null", got)
        self.assertIn("reversed_strategy", got)


class TestCostModel(unittest.TestCase):
    def test_domestic_cost_higher_than_ice(self):
        """郑商所棉花名义小、最小变动价位占比大，滑点应比 ICE 贵。"""
        self.assertGreater(COST_BPS["CF0"], COST_BPS["CT=F"])

    def test_all_costs_positive_and_plausible(self):
        for sym, bps in COST_BPS.items():
            self.assertGreater(bps, 0, sym)
            self.assertLess(bps, 50, f"{sym} 双边成本 {bps}bp 不合常理")


if __name__ == "__main__":
    unittest.main()
