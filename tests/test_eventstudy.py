"""事件研究测试。重点是两条前视守卫：因子不能看未来天气，收益不能用建仓日之前的价。"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.climate import MonthClimate
from agro.eventstudy import (
    LAYOUT_SYMBOLS,
    MIN_NONZERO,
    Revision,
    _tercile_split,
    blended_month,
    forward_return,
    monthly_normals,
    pearson,
    permutation_p,
    placebo_next_year,
    study_pair,
)
from agro.futures import FuturesBar
from agro.nowcast import DailyWx


def wx(d: date, tmean=20.0, tmax=28.0, tmin=12.0, precip=2.0) -> DailyWx:
    return DailyWx(d, tmean, tmax, tmin, precip)


def bar(day: str, close: float) -> FuturesBar:
    return FuturesBar("X", day, close, close, close, close, None, 1000.0, 0.0, "test")


NORMAL = MonthClimate("r", 0, 6, 20.0, 28.0, 12.0, 60.0, "normal")


class TestBlendedMonth(unittest.TestCase):
    def test_no_observation_falls_back_to_normal(self):
        got = blended_month([], 2024, 6, date(2024, 5, 31), NORMAL)
        self.assertIs(got, NORMAL)

    def test_full_month_uses_observation_only(self):
        days = [wx(date(2024, 6, d), tmax=35.0, precip=1.0) for d in range(1, 31)]
        got = blended_month(days, 2024, 6, date(2024, 6, 30), NORMAL)
        self.assertEqual(got.source, "obs")
        self.assertAlmostEqual(got.tmax, 35.0)
        self.assertAlmostEqual(got.precip_mm, 30.0)

    def test_partial_month_blends_by_day_count(self):
        # 前 15 天极热无雨，后 15 天用气候态补
        days = [wx(date(2024, 6, d), tmax=40.0, precip=0.0) for d in range(1, 16)]
        got = blended_month(days, 2024, 6, date(2024, 6, 15), NORMAL)
        self.assertEqual(got.source, "obs+normal")
        self.assertAlmostEqual(got.tmax, (40.0 * 15 + 28.0 * 15) / 30)
        # 降水 = 已观测 0 + 剩余 15 天 × 日均气候态 2mm
        self.assertAlmostEqual(got.precip_mm, 60.0 / 30 * 15)

    def test_partial_month_does_not_starve_precipitation(self):
        """月初只有 2 天数据时，不能让降水累计接近 0 而把干旱胁迫打出假尖峰。"""
        days = [wx(date(2024, 6, d), precip=0.0) for d in (1, 2)]
        got = blended_month(days, 2024, 6, date(2024, 6, 2), NORMAL)
        self.assertGreater(got.precip_mm, NORMAL.precip_mm * 0.8)

    def test_ignores_days_after_as_of(self):
        """前视守卫：as_of 之后的天气无论多极端，都不能改变当期因子输入。"""
        observed = [wx(date(2024, 6, d), tmax=30.0, precip=1.0) for d in range(1, 11)]
        future = [wx(date(2024, 6, d), tmax=50.0, precip=500.0) for d in range(11, 31)]
        a = blended_month(observed, 2024, 6, date(2024, 6, 10), NORMAL)
        b = blended_month(observed + future, 2024, 6, date(2024, 6, 10), NORMAL)
        self.assertAlmostEqual(a.tmax, b.tmax)
        self.assertAlmostEqual(a.precip_mm, b.precip_mm)

    def test_ignores_other_years_same_month(self):
        days = [wx(date(2023, 6, d), tmax=45.0) for d in range(1, 31)]
        got = blended_month(days, 2024, 6, date(2024, 6, 30), NORMAL)
        self.assertIs(got, NORMAL)


class TestMonthlyNormals(unittest.TestCase):
    def test_excludes_target_year(self):
        """气候态留着当年，等于把当年信息漏进「未来月份」的预期。"""
        import agro.eventstudy as es

        days = [wx(date(2022, 6, d), tmax=28.0) for d in range(1, 31)]
        days += [wx(date(2023, 6, d), tmax=28.0) for d in range(1, 31)]
        days += [wx(date(2024, 6, d), tmax=48.0) for d in range(1, 31)]
        orig = es.load_daily
        es.load_daily = lambda _rid: days
        try:
            got = monthly_normals("r", exclude_year=2024)
        finally:
            es.load_daily = orig
        self.assertAlmostEqual(got[6].tmax, 28.0)
        self.assertEqual(got[6].source, "era5-normal-exyear")

    def test_precip_is_monthly_total_not_daily(self):
        import agro.eventstudy as es

        days = [wx(date(2022, 6, d), precip=3.0) for d in range(1, 31)]
        days += [wx(date(2023, 6, d), precip=3.0) for d in range(1, 31)]
        orig = es.load_daily
        es.load_daily = lambda _rid: days
        try:
            got = monthly_normals("r", exclude_year=2024)
        finally:
            es.load_daily = orig
        self.assertAlmostEqual(got[6].precip_mm, 90.0)  # 月累计，不是日均


class TestForwardReturn(unittest.TestCase):
    def setUp(self):
        self.bars = [bar(f"2024-01-{d:02d}", 100.0 + d) for d in range(1, 21)]

    def test_entry_is_strictly_after_as_of(self):
        """前视守卫：as_of 当天的收盘不能当建仓价，那是信息生成日。"""
        r = forward_return(self.bars, "2024-01-05", 1)
        # 建仓 01-06 收盘 106，平仓 01-07 收盘 107
        self.assertAlmostEqual(r, 107.0 / 106.0 - 1.0)

    def test_skips_non_trading_days(self):
        bars = [bar("2024-01-02", 100.0), bar("2024-01-08", 110.0), bar("2024-01-09", 121.0)]
        r = forward_return(bars, "2024-01-03", 1)
        self.assertAlmostEqual(r, 121.0 / 110.0 - 1.0)

    def test_none_when_horizon_exceeds_history(self):
        self.assertIsNone(forward_return(self.bars, "2024-01-19", 5))

    def test_none_when_as_of_after_last_bar(self):
        self.assertIsNone(forward_return(self.bars, "2025-01-01", 1))


class TestStats(unittest.TestCase):
    def test_pearson_perfect_positive(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]), 1.0)

    def test_pearson_perfect_negative(self):
        self.assertAlmostEqual(pearson([1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]), -1.0)

    def test_pearson_zero_variance_is_zero_not_crash(self):
        self.assertEqual(pearson([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]), 0.0)

    def test_permutation_p_reproducible(self):
        xs = [float(i) for i in range(30)]
        ys = [float(i % 7) for i in range(30)]
        self.assertEqual(permutation_p(xs, ys, draws=200), permutation_p(xs, ys, draws=200))

    def test_permutation_p_small_sample_returns_one(self):
        self.assertEqual(permutation_p([1.0, 2.0], [1.0, 2.0]), 1.0)

    def test_permutation_p_detects_perfect_relation(self):
        xs = [float(i) for i in range(40)]
        self.assertLess(permutation_p(xs, xs, draws=500), 0.01)


class TestTerciles(unittest.TestCase):
    def test_expected_sign_when_down_revision_pays(self):
        # 修正量越负，收益越高 → 价差为正，符号符合
        pairs = [(float(i - 10), -0.01 * (i - 10)) for i in range(21)]
        got = _tercile_split(pairs)
        self.assertTrue(got["ok"])
        self.assertGreater(got["spread"], 0)
        self.assertTrue(got["spread_sign_as_expected"])

    def test_wrong_sign_flagged(self):
        pairs = [(float(i - 10), 0.01 * (i - 10)) for i in range(21)]
        got = _tercile_split(pairs)
        self.assertLess(got["spread"], 0)
        self.assertFalse(got["spread_sign_as_expected"])

    def test_small_sample_refused(self):
        self.assertFalse(_tercile_split([(1.0, 0.1)] * 5)["ok"])


class TestStudyPair(unittest.TestCase):
    def _revs(self, n: int) -> list[Revision]:
        return [
            Revision("lay", "棉花", "r", 2024, f"2024-01-{d:02d}", 0.1, 0.9, -0.001)
            for d in range(1, n + 1)
        ]

    def test_refuses_under_ten_events(self):
        bars = [bar(f"2024-01-{d:02d}", 100.0 + d) for d in range(1, 40)]
        got = study_pair(self._revs(5), bars, 1)
        self.assertFalse(got["ok"])
        self.assertIn("不足", got["reason"])

    def test_zero_delta_sections_are_not_events(self):
        revs = self._revs(20)
        for r in revs[:15]:
            r.delta = 0.0
        bars = [bar(f"2024-01-{d:02d}", 100.0 + d) for d in range(1, 40)]
        got = study_pair(revs, bars, 1)
        self.assertFalse(got["ok"], "只剩 5 条非零修正，应当拒绝检验")

    def test_reports_expected_sign_is_negative(self):
        revs = self._revs(25)
        bars = [bar(f"2024-01-{d:02d}", 100.0 + d * (-1) ** d) for d in range(1, 40)]
        got = study_pair(revs, bars, 1)
        self.assertEqual(got["expected_sign"], "negative")
        self.assertEqual(got["horizon_trading_days"], 1)

    def test_placebo_shifts_a_full_year(self):
        revs = self._revs(25)
        bars = [bar(f"2025-01-{d:02d}", 100.0 + d) for d in range(1, 40)]
        got = placebo_next_year(revs, bars)
        self.assertTrue(got["ok"], "2024 的修正应当能配到 2025 的行情")
        self.assertTrue(got["should_be_near_zero"])


class TestWiring(unittest.TestCase):
    def test_registry_covers_cotton_both_sides_and_a_cross_placebo(self):
        self.assertIn("cotton.xinjiang", LAYOUT_SYMBOLS)
        self.assertIn("cotton.us.belt", LAYOUT_SYMBOLS)
        roles = {role for pairs in LAYOUT_SYMBOLS.values() for _s, _k, role in pairs}
        self.assertIn("primary", roles)
        self.assertIn("cross_placebo", roles)

    def test_cross_placebo_uses_unrelated_contract(self):
        pairs = LAYOUT_SYMBOLS["cotton.us.belt"]
        placebo = [s for s, _k, role in pairs if role == "cross_placebo"]
        primary = [s for s, _k, role in pairs if role == "primary"]
        self.assertTrue(placebo and primary)
        self.assertFalse(set(placebo) & set(primary), "安慰剂合约不能和主合约相同")

    def test_symbols_declare_known_fetch_kind(self):
        for pairs in LAYOUT_SYMBOLS.values():
            for _sym, kind, _role in pairs:
                self.assertIn(kind, ("cn", "intl"))

    def test_min_nonzero_threshold_is_enforced_upstream(self):
        self.assertGreaterEqual(MIN_NONZERO, 10)


if __name__ == "__main__":
    unittest.main()
