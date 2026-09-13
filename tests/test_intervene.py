"""干预框架：带宽内必须 hold，状态合并成事件，方向约定锁死。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.eventstudy import Revision
from agro.intervene import Decision, decide_path, episodes, outlook_state, price_direction


def R(day: str, factor: float, year: int = 2024) -> Revision:
    return Revision("lay", "棉花", "r", year, day, 0.5, factor, 0.0)


class TestOutlookState(unittest.TestCase):
    def test_inside_band_is_hold(self):
        self.assertEqual(outlook_state(1.0), "hold")
        self.assertEqual(outlook_state(0.98, band=0.03), "hold")
        self.assertEqual(outlook_state(1.02, band=0.03), "hold")

    def test_below_band_is_long(self):
        """相对开局恶化 → 供给趋紧 → 做多。这是信息到仓位的唯一约定。"""
        self.assertEqual(outlook_state(0.87, baseline=1.0), "long")
        self.assertEqual(outlook_state(0.86, baseline=0.90, band=0.03), "long")
        self.assertEqual(price_direction("long"), 1)

    def test_above_band_is_short(self):
        self.assertEqual(outlook_state(1.05, baseline=1.0), "short")
        self.assertEqual(outlook_state(0.96, baseline=0.90, band=0.03), "short")
        self.assertEqual(price_direction("short"), -1)

    def test_hold_has_no_position(self):
        self.assertEqual(price_direction("hold"), 0)

    def test_boundary_is_inclusive_on_intervene_side(self):
        self.assertEqual(outlook_state(0.97, band=0.03), "long")
        self.assertEqual(outlook_state(1.03, band=0.03), "short")


class TestEpisodes(unittest.TestCase):
    def test_quiet_season_has_no_episode(self):
        revs = [R(f"2024-06-{d:02d}", 0.99) for d in range(1, 10)]
        self.assertEqual(episodes(decide_path(revs)), [])

    def test_flat_stressed_season_does_not_intervene(self):
        """开局已经是 0.87、整季不变：知情者开季已计入，不再干预。"""
        revs = [R(f"2024-07-{d:02d}", 0.87) for d in range(1, 12)]
        self.assertEqual(episodes(decide_path(revs)), [])

    def test_worsening_merges_into_one_long_episode(self):
        revs = [R("2024-07-01", 0.95)] + [R(f"2024-07-{d:02d}", 0.87) for d in range(2, 12)]
        evs = episodes(decide_path(revs))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].state, "long")
        self.assertEqual(evs[0].n_sections, 10)

    def test_return_to_open_closes_episode(self):
        revs = [
            R("2024-06-01", 0.95),
            R("2024-06-04", 0.88),
            R("2024-06-07", 0.88),
            R("2024-06-10", 0.95),
            R("2024-06-13", 0.95),
        ]
        evs = episodes(decide_path(revs))
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0].end, "2024-06-07")

    def test_improve_from_open_is_short(self):
        revs = [R("2024-06-01", 0.90), R("2024-06-10", 0.96), R("2024-06-13", 0.97)]
        evs = episodes(decide_path(revs))
        self.assertEqual([e.state for e in evs], ["short"])

    def test_year_open_resets_baseline(self):
        revs = [R("2023-09-01", 0.90, 2023), R("2024-05-01", 0.90, 2024)]
        self.assertEqual(episodes(decide_path(revs)), [])


class TestDecidePathReasons(unittest.TestCase):
    def test_every_section_gets_a_state(self):
        decs = decide_path([R("2024-06-01", 1.0), R("2024-06-04", 0.80)])
        self.assertEqual([d.state for d in decs], ["hold", "long"])  # 开局 1.0 为基线，0.80 恶化做多
        self.assertTrue(all(isinstance(d, Decision) and d.reason for d in decs))


if __name__ == "__main__":
    unittest.main()
