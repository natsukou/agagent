"""产量准确率总测试的测试：技能分定义、不可交付行的排除、计分口径。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.accuracy import _row, skill, to_markdown


def m(mae: float, mape: float, n: int = 6) -> dict:
    return {"n": n, "mae": mae, "rmse": mae, "mape": mape}


class TestSkill(unittest.TestCase):
    def test_model_half_the_error_gives_half_skill(self):
        self.assertAlmostEqual(skill(0.5, 1.0), 0.5)

    def test_equal_error_gives_zero_skill(self):
        self.assertAlmostEqual(skill(1.0, 1.0), 0.0)

    def test_worse_model_gives_negative_skill(self):
        self.assertLess(skill(2.0, 1.0), 0.0)

    def test_perfect_model_gives_skill_one(self):
        self.assertAlmostEqual(skill(0.0, 1.0), 1.0)

    def test_undefined_without_persist_baseline(self):
        self.assertIsNone(skill(1.0, None))
        self.assertIsNone(skill(None, 1.0))

    def test_undefined_when_persist_is_perfect(self):
        """持续基线误差为 0 时技能分无法定义，不能除零。"""
        self.assertIsNone(skill(0.5, 0.0))


class TestRow(unittest.TestCase):
    def test_beats_persist_flag_follows_mae_not_mape(self):
        # 故意让 MAPE 方向与 MAE 方向相反，确认判定用的是 MAE
        r = _row("t", "y", "src", m(0.5, 0.09), m(1.0, 0.01))
        self.assertTrue(r["beats_persist"])
        self.assertAlmostEqual(r["skill_vs_persist"], 0.5)

    def test_deliverable_defaults_true(self):
        self.assertTrue(_row("t", "y", "src", m(1.0, 0.01), m(1.0, 0.01))["deliverable"])

    def test_no_persist_baseline_leaves_skill_none(self):
        r = _row("t", "y", "src", m(1.0, 0.02), None)
        self.assertIsNone(r["skill_vs_persist"])
        self.assertFalse(r["beats_persist"])

    def test_carries_sample_size_from_model_metrics(self):
        self.assertEqual(_row("t", "y", "src", m(1.0, 0.01, n=1653), None)["n"], 1653)


class TestScoreboardSemantics(unittest.TestCase):
    """oracle 行用测试年实测挑方法，必须排除在「跑赢持续」的计数之外。"""

    def _report(self) -> dict:
        rows = [
            _row("good", "y", "s", m(0.5, 0.01), m(1.0, 0.02)),
            _row("bad", "y", "s", m(2.0, 0.04), m(1.0, 0.02)),
            _row("oracle", "y", "s", m(0.1, 0.005), m(1.0, 0.02), deliverable=False),
        ]
        scored = [r for r in rows if r["skill_vs_persist"] is not None and r["deliverable"]]
        winners = [r for r in scored if r["beats_persist"]]
        return {
            "task": "t",
            "test_years": [2024],
            "split_rule": "按年",
            "metric_note": "看技能分",
            "rows": rows,
            "scoreboard": {
                "tasks_total": len(rows),
                "tasks_non_deliverable": sum(1 for r in rows if not r["deliverable"]),
                "tasks_with_persist_baseline": len(scored),
                "tasks_beating_persist": len(winners),
                "beating_persist_names": [r["task"] for r in winners],
                "lowest_mape_task": {"task": "good", "mape": 0.01},
                "highest_skill_task": {"task": "good", "skill": 0.5},
                "median_skill": 0.0,
            },
            "guards": ["按年切分"],
            "known_limits": ["样本少"],
        }

    def test_oracle_excluded_from_winners(self):
        sb = self._report()["scoreboard"]
        self.assertEqual(sb["tasks_beating_persist"], 1)
        self.assertNotIn("oracle", sb["beating_persist_names"])
        self.assertEqual(sb["tasks_non_deliverable"], 1)

    def test_markdown_renders_all_rows_including_non_deliverable(self):
        md = to_markdown(self._report())
        self.assertIn("oracle", md)
        self.assertIn("可交付", md)
        self.assertIn("只计可交付路径", md)

    def test_markdown_shows_signed_skill(self):
        md = to_markdown(self._report())
        self.assertIn("+0.5000", md)
        self.assertIn("-1.0000", md)


if __name__ == "__main__":
    unittest.main()
