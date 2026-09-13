from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.eventstudy import Revision
from agro.expert import (
    MLP,
    ExpertSample,
    industry_path,
    light_adjust,
    price_features,
    split_by_year,
    train_mlp,
)
from agro.futures import FuturesBar


def bar(day: str, close: float, volume: float = 1000.0) -> FuturesBar:
    return FuturesBar("X", day, close, close, close, close, None, volume, 0.0, "test")


def rev(as_of: str, factor: float, delta: float = -0.01) -> Revision:
    return Revision("lay", "棉花", "r", int(as_of[:4]), as_of, 0.5, factor, delta)


class TestPriceFeatures(unittest.TestCase):
    def test_features_ignore_bars_after_as_of(self):
        bars = [bar(f"2024-01-{d:02d}", 100.0 + d) for d in range(1, 28)]
        early = price_features(bars, "2024-01-22")
        leaked = price_features(bars[:22], "2024-01-22")
        self.assertIsNotNone(early)
        self.assertEqual(early, leaked)

    def test_too_short_history_is_none(self):
        bars = [bar(f"2024-01-{d:02d}", 100.0) for d in range(1, 10)]
        self.assertIsNone(price_features(bars, "2024-01-09"))


class TestLightAdjust(unittest.TestCase):
    def test_neutral_industry_keeps_expert(self):
        self.assertEqual(light_adjust(1, 0)[0], 1)

    def test_agreement_keeps_expert(self):
        self.assertEqual(light_adjust(-1, -1)[0], -1)

    def test_conflict_goes_flat(self):
        direction, why = light_adjust(1, -1)
        self.assertEqual(direction, 0)
        self.assertIn("冲突", why)

    def test_does_not_flip_expert(self):
        self.assertNotEqual(light_adjust(1, -1)[0], -1)

    def test_empty_expert_can_take_industry(self):
        self.assertEqual(light_adjust(0, 1)[0], 1)


class TestIndustryPath(unittest.TestCase):
    def test_season_open_is_baseline(self):
        path = industry_path(
            [
                rev("2023-06-01", 1.00),
                rev("2023-07-01", 0.96),
                rev("2024-06-01", 1.02),
            ],
            band=0.03,
        )
        self.assertEqual(path["2023-06-01"][0], "hold")
        self.assertEqual(path["2023-07-01"][0], "long")
        self.assertEqual(path["2024-06-01"][0], "hold")


class TestSplitAndTrain(unittest.TestCase):
    def test_split_is_by_year_not_shuffle(self):
        rows = [
            ExpertSample("a→X", "a", "X", "cn", "primary", year, f"{year}-06-01", [0.0] * 8, 0.1, "hold", 0, 0)
            for year in (2021, 2022, 2023, 2024)
        ]
        train, test = split_by_year(rows, (2023, 2024))
        self.assertEqual([s.year for s in train], [2021, 2022])
        self.assertEqual([s.year for s in test], [2023, 2024])

    def test_mlp_fits_separable_sign(self):
        xs = [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(20)]
        xs += [[-1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(20)]
        ys = [1.0] * 20 + [-1.0] * 20
        model = train_mlp(xs, ys, epochs=40, lr=0.08)
        self.assertIsInstance(model, MLP)
        self.assertGreater(model.forward(xs[0]), 0.2)
        self.assertLess(model.forward(xs[-1]), -0.2)
        self.assertEqual(model.predict_dir(xs[0]), 1)
        self.assertEqual(model.predict_dir(xs[-1]), -1)


if __name__ == "__main__":
    unittest.main()
