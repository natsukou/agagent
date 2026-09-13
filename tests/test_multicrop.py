"""新作物结构测试：单位换算、跨年生长季、分组与目标口径。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.datasets import bundled
from agro.multicrop import NEW_CROPS, layout_groups, season_month_years
from agro.psd import BAG_KG, BALE_KG, _convert, parse_psd_csv, to_year_table
from agro.regions import REGIONS, country_of, is_southern

CSV_HEAD = (
    "Commodity_Code,Commodity_Description,Country_Code,Country_Name,Market_Year,"
    "Calendar_Year,Month,Attribute_ID,Attribute_Description,Unit_ID,Unit_Description,Value\n"
)


class TestPsdUnits(unittest.TestCase):
    def test_cotton_yield_kg_to_t(self):
        self.assertAlmostEqual(_convert("棉花", "yield", 2160.0, "(KG/HA)"), 2.16)

    def test_grain_yield_already_t(self):
        self.assertAlmostEqual(_convert("玉米", "yield", 6.53, "(MT/HA)"), 6.53)

    def test_cotton_production_bales_to_kt(self):
        # 1000 包 480 磅 = 217.7243 吨 → 0.2177 kt
        self.assertAlmostEqual(_convert("棉花", "production", 1.0, "1000 480 lb. Bales"), BALE_KG / 1000.0, places=4)

    def test_coffee_production_bags_to_kt(self):
        self.assertAlmostEqual(_convert("咖啡", "production", 1.0, "(1000 60 KG BAGS)"), BAG_KG / 1000.0)

    def test_grain_production_passthrough(self):
        self.assertAlmostEqual(_convert("玉米", "production", 288842.0, "(1000 MT)"), 288842.0)


class TestPsdParse(unittest.TestCase):
    def test_filters_country_and_attribute(self):
        rows = parse_psd_csv(
            CSV_HEAD
            + "0813100,Cotton,CH,China,2024,2024,1,4,Yield,28,(KG/HA),2160.0000\n"
            + "0813100,Cotton,IN,India,2024,2024,1,4,Yield,28,(KG/HA),443.0000\n"
            + "0813100,Cotton,CH,China,2024,2024,1,20,Exports,88,1000 480 lb. Bales,50.0000\n"
            + "0813100,Cotton,CH,China,1999,1999,1,4,Yield,28,(KG/HA),900.0000\n",
            {"Cotton": "棉花"},
        )
        self.assertEqual(len(rows), 1)  # 印度被过滤、出口属性被过滤、1999 超出年窗
        self.assertEqual(rows[0]["scope"], "CHN")
        self.assertEqual(rows[0]["kind"], "yield")

    def test_year_table_leaves_coffee_yield_none(self):
        rows = parse_psd_csv(
            CSV_HEAD
            + "0711100,\"Coffee, Green\",BR,Brazil,2024,2024,1,20,Production,24,(1000 60 KG BAGS),66300.0000\n",
            {"Coffee, Green": "咖啡"},
        )
        table = to_year_table(rows)
        self.assertEqual(len(table), 1)
        self.assertIsNone(table[0]["yield_t_ha"])
        self.assertIsNone(table[0]["area_kha"])
        self.assertAlmostEqual(float(table[0]["production_kt"]), 66300.0 * BAG_KG / 1000.0)


class TestSeasonAcrossYears(unittest.TestCase):
    def test_brazil_soy_uses_previous_calendar_year(self):
        lay = bundled.layout_map()["soy.br.matogrosso"]
        self.assertTrue(lay.crosses_year)
        pairs = season_month_years(lay, 2024)
        prev = {m for (y, m) in pairs if y == 2023}
        self.assertTrue(prev, "南半球大豆必须有落在上一自然年的月份")
        self.assertTrue(prev.issubset(set(lay.prev_year_months)))

    def test_northern_maize_stays_in_year(self):
        lay = bundled.layout_map()["maize.dongbei.spring"]
        self.assertFalse(lay.crosses_year)
        self.assertEqual({y for (y, _m) in season_month_years(lay, 2024)}, {2024})

    def test_winter_wheat_crosses_year(self):
        lay = bundled.layout_map()["wheat.huabei.winter"]
        self.assertTrue(lay.crosses_year)
        self.assertIn(2023, {y for (y, _m) in season_month_years(lay, 2024)})


class TestGroups(unittest.TestCase):
    def test_four_new_crops_grouped_by_country(self):
        groups = layout_groups(NEW_CROPS)
        self.assertEqual({c for c, _s in groups}, set(NEW_CROPS))
        self.assertIn(("大豆", "BRA"), groups)
        self.assertIn(("大豆", "USA"), groups)
        self.assertIn(("棉花", "USA"), groups)
        self.assertIn(("咖啡", "BRA"), groups)

    def test_china_maize_has_two_layouts(self):
        self.assertEqual(len(layout_groups(("玉米",))[("玉米", "CHN")]), 2)

    def test_coffee_target_is_production(self):
        for (crop, _country), lays in layout_groups(("咖啡",)).items():
            self.assertEqual(crop, "咖啡")
            for lay in lays:
                self.assertEqual(lay.target, "production")

    def test_layout_region_country_consistent(self):
        for lay in bundled.layouts():
            self.assertEqual(lay.country, country_of(lay.region_id), lay.id)

    def test_southern_layouts_declare_prev_months(self):
        for lay in bundled.layouts():
            if is_southern(lay.region_id):
                self.assertTrue(lay.prev_year_months, f"{lay.id} 在南半球但没声明跨年月份")

    def test_every_region_has_anchor(self):
        for rid, meta in REGIONS.items():
            self.assertIn("lat", meta, rid)
            self.assertIn("country", meta, rid)
            self.assertIn(meta["hemisphere"], ("N", "S"), rid)


if __name__ == "__main__":
    unittest.main()
