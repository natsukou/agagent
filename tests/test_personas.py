from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.eventstudy import Revision
from agro.personas import decide_absolute, persona_states


def R(day: str, factor: float, year: int = 2024) -> Revision:
    return Revision("lay", "棉花", "r", year, day, 0.5, factor, 0.0)


class TestPersonaStates(unittest.TestCase):
    def test_beginner_off_is_always_long(self):
        revs = [R("2024-06-01", 1.0), R("2024-06-10", 0.80)]
        st = persona_states(revs, 0.03)
        self.assertEqual(st["beginner_off"], ["long", "long"])

    def test_familiar_off_is_always_hold(self):
        revs = [R("2024-06-01", 0.80), R("2024-06-10", 0.70)]
        st = persona_states(revs, 0.03)
        self.assertEqual(st["familiar_off"], ["hold", "hold"])

    def test_on_rules_identical_across_personas(self):
        revs = [R("2024-06-01", 0.95), R("2024-06-10", 0.80)]
        st = persona_states(revs, 0.03)
        self.assertEqual(st["beginner_on"], st["familiar_on"])
        self.assertEqual(st["familiar_on"], st["expert_on"])
        self.assertEqual(st["beginner_on"], ["hold", "long"])

    def test_expert_off_uses_absolute_baseline(self):
        revs = [R("2024-06-01", 0.90), R("2024-06-10", 0.90)]
        # 相对开局不变 → 我们的框架 hold；相对 1.0 已低于 0.97 → 尖端无干预做多
        self.assertEqual(decide_absolute(revs, 0.03), ["long", "long"])
        self.assertEqual(persona_states(revs, 0.03)["expert_on"], ["hold", "hold"])


if __name__ == "__main__":
    unittest.main()
