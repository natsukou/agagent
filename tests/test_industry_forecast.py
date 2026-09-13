"""产业预测测试：对照集折算 + 完整产出表列齐全。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.evalset import holdout_map, to_year_table
from agro.industry import STAGE_YIELD_MAPE, build_table, select_method
from agro.store import DB_PATH
from agro.train import Sample

FIXTURE = Path(__file__).resolve().parents[1] / "agro" / "datasets" / "usda_psd_fixture.json"
REQUIRED = {
    "level",
    "crop",
    "unit_id",
    "unit_name",
    "year",
    "chosen_yield_t_ha",
    "chosen_prod_kt",
    "method",
    "area_kha",
    "actual_yield_t_ha",
    "actual_prod_kt",
    "yield_mape",
    "prod_mape",
    "note",
}


class TestEvalset(unittest.TestCase):
    def test_year_table_units(self):
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
        table = to_year_table(rows)
        wheat = next(r for r in table if r["crop"] == "小麦" and r["year"] == 2024)
        self.assertAlmostEqual(wheat["yield_t_ha"], 5.90)
        self.assertAlmostEqual(wheat["production_kt"], 140000.0)
        self.assertAlmostEqual(wheat["area_kha"], 23730.0)
        self.assertEqual(len(holdout_map(table)), len(table))


class TestIndustryTable(unittest.TestCase):
    def test_complete_output_columns(self):
        if not DB_PATH.exists():
            self.skipTest("需要先 collect + graph-build")
        holdout = holdout_map(to_year_table(json.loads(FIXTURE.read_text(encoding="utf-8"))))
        report = build_table(test_years=(2023, 2024), holdout=holdout)
        self.assertGreaterEqual(report["counts"]["national"], 6)
        self.assertGreaterEqual(report["counts"]["layout"], 10)
        self.assertGreaterEqual(report["counts"]["county"], 20)
        for row in report["rows"]:
            missing = REQUIRED - set(row)
            self.assertFalse(missing, missing)
        crops = {r["crop"] for r in report["rows"] if r["level"] == "全国"}
        self.assertEqual(crops, {"小麦", "水稻", "玉米"})
        national = [r for r in report["rows"] if r["level"] == "全国"]
        self.assertTrue(all(r["actual_yield_t_ha"] is not None for r in national))
        self.assertIn("national_yield_ok", report["stage"])
        self.assertLessEqual(STAGE_YIELD_MAPE, 0.05)

    def test_no_oracle_selection(self):
        """全国行只能用一种方法，且误差不得优于两个基线中更好的那个。"""
        if not DB_PATH.exists():
            self.skipTest("需要先 collect + graph-build")
        holdout = holdout_map(to_year_table(json.loads(FIXTURE.read_text(encoding="utf-8"))))
        report = build_table(test_years=(2023, 2024), holdout=holdout)
        national = [r for r in report["rows"] if r["level"] == "全国"]
        self.assertEqual(len({r["method"] for r in national}), 1)
        pm = report["metrics"]["per_method_yield"]
        chosen = report["metrics"]["national_yield"]["mae"]
        best_single = min(pm[k]["mae"] for k in ("persist", "ridge") if k in pm)
        self.assertGreaterEqual(chosen, best_single - 1e-9)

    def test_selection_uses_train_only(self):
        rows = [
            Sample("玉米", y, 6.0 + 0.1 * i, [1.0], ["x"], {"lag": 6.0 + 0.1 * (i - 1)})
            for i, y in enumerate(range(2016, 2023))
        ]
        sel = select_method(rows)
        self.assertIn(sel["method"], {"persist", "ridge"})
        self.assertNotIn("test", str(sel["reason"]))

    def test_production_uses_lagged_area(self):
        if not DB_PATH.exists():
            self.skipTest("需要先 collect + graph-build")
        holdout = holdout_map(to_year_table(json.loads(FIXTURE.read_text(encoding="utf-8"))))
        report = build_table(test_years=(2024,), holdout=holdout)
        wheat = next(r for r in report["rows"] if r["level"] == "全国" and r["crop"] == "小麦")
        self.assertEqual(wheat["area_kha"], 23550.0)  # 2023 面积
        self.assertEqual(wheat["actual_area_kha"], 23730.0)  # 2024 实测面积


if __name__ == "__main__":
    unittest.main()
