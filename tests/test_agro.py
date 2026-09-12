from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.adapter import to_plan
from agro.climate import MonthClimate, forecast_horizon
from agro.connectors import demo_warm_anomaly
from agro.datasets import bundled, dce
from agro.graph import AgriEdgeType, NodeKind, attach_dce_instruments, build_yield_graph
from agro.market import dominant_snapshots
from agro.predict import forecast_yield


class TestLayouts(unittest.TestCase):
    def test_five_targeted_systems(self):
        lays = bundled.layouts()
        self.assertEqual(len(lays), 5)
        ids = {x.id for x in lays}
        self.assertIn("rice.changjiang.single", ids)
        self.assertIn("wheat.huabei.winter", ids)
        self.assertIn("maize.dongbei.spring", ids)

    def test_weights_sum_to_one(self):
        for lay in bundled.layouts():
            self.assertAlmostEqual(sum(s.yield_weight for s in lay.stages), 1.0, places=6)
            self.assertAlmostEqual(sum(c.weight for c in lay.components), 1.0, places=6)

    def test_winter_wheat_wraps_year(self):
        lay = bundled.layout_map()["wheat.huabei.winter"]
        self.assertIn(12, lay.season_months)
        self.assertIn(1, lay.season_months)
        self.assertIn(5, lay.season_months)


class TestClimateForecast(unittest.TestCase):
    def test_zero_anomaly_stays_on_climatology(self):
        clim = bundled.climatology()
        fc = forecast_horizon("huabei", 2026, 6, 3, clim, [])
        for i, c in enumerate(fc):
            n = clim.get("huabei", c.month)
            self.assertAlmostEqual(c.tmax, n.tmax)
            self.assertEqual(c.source, "persist+climatology")

    def test_warm_anomaly_decays(self):
        clim = bundled.climatology()
        recent = demo_warm_anomaly("changjiang", (5,), d_tmax=4.0)
        fc = forecast_horizon("changjiang", 2026, 6, 4, clim, recent)
        deltas = [c.tmax - clim.get("changjiang", c.month).tmax for c in fc]
        self.assertGreater(deltas[0], deltas[-1])
        self.assertGreater(deltas[0], 1.0)


class TestYieldGraph(unittest.TestCase):
    def test_every_layout_has_yield_node(self):
        g = build_yield_graph(bundled.layouts())
        for lay in bundled.layouts():
            self.assertIn(f"yield:{lay.id}", g.nodes)
            self.assertEqual(g.nodes[f"yield:{lay.id}"].kind, NodeKind.YIELD)

    def test_stress_edges_from_climate(self):
        g = build_yield_graph(bundled.layouts())
        stresses = [e for e in g.edges.values() if e.type is AgriEdgeType.STRESSES]
        self.assertGreaterEqual(len(stresses), 20)


class TestYieldModel(unittest.TestCase):
    def _climate(self, region: str) -> list[MonthClimate]:
        clim = bundled.climatology()
        return [clim.get(region, m) for m in range(1, 13)]

    def test_climatology_near_baseline(self):
        g = build_yield_graph(bundled.layouts())
        lay = bundled.layout_map()["rice.changjiang.single"]
        fc = forecast_yield(lay, self._climate("changjiang"), g)
        self.assertTrue(fc.ready)
        self.assertGreater(fc.expected_t_ha, lay.baseline_t_ha * 0.75)
        self.assertLess(fc.expected_t_ha, lay.baseline_t_ha * 1.15)

    def test_heat_at_heading_cuts_rice(self):
        g = build_yield_graph(bundled.layouts())
        lay = bundled.layout_map()["rice.changjiang.single"]
        series = self._climate("changjiang")
        hot = []
        for c in series:
            if c.month in (7, 8):
                hot.append(MonthClimate(c.region_id, c.year, c.month, 32, 38, 27, c.precip_mm, "hot"))
            else:
                hot.append(c)
        base = forecast_yield(lay, series, g)
        stressed = forecast_yield(lay, hot, g)
        self.assertLess(stressed.expected_t_ha, base.expected_t_ha)
        self.assertTrue(any(c.startswith("HEAT_") for c in stressed.reason_codes))

class TestDCESchema(unittest.TestCase):
    def test_sample_loads_and_picks_dominant(self):
        bars = dce.load(dce.sample_path())
        self.assertGreaterEqual(len(bars), 6)
        snaps = { (s.date, s.variety_code): s for s in dominant_snapshots(bars) }
        maize = snaps[("2024-07-15", "c")]
        self.assertEqual(maize.dominant_contract, "c2409")
        self.assertIn("maize.dongbei.spring", maize.layouts)

    def test_chinese_headers(self):
        bar = dce.row_to_bar(
            {
                "日期": "20240715",
                "品种": "玉米",
                "合约": "c2409",
                "开盘价": "2380",
                "最高价": "2396",
                "最低价": "2372",
                "收盘价": "2388",
                "前结算价": "2375",
                "结算价": "2,386",
                "成交量": "412000",
                "持仓量": "865000",
                "成交额": "9840000000",
            }
        )
        self.assertEqual(bar.variety_code, "c")
        self.assertEqual(bar.date, "2024-07-15")
        self.assertEqual(bar.settle, 2386.0)

    def test_wheat_not_on_dce(self):
        from agro.market import NOT_ON_DCE

        self.assertIn("小麦", NOT_ON_DCE)

    def test_graph_gets_price_edges(self):
        g = build_yield_graph(bundled.layouts())
        n = attach_dce_instruments(g)
        self.assertGreaterEqual(n, 3)
        self.assertIn("market:dce:c", g.nodes)
        priced = [e for e in g.edges.values() if e.type is AgriEdgeType.PRICES]
        self.assertTrue(any(e.src == "market:dce:c" and "maize.dongbei.spring" in e.dst for e in priced))


class TestYieldModelContinued(unittest.TestCase):
    def test_strict_gap_goes_to_ask(self):
        g = build_yield_graph(bundled.layouts())
        lay = bundled.layout_map()["maize.dongbei.spring"]
        # 只有 6 月，春玉米季节缺月
        only = [bundled.climatology().get("dongbei", 6)]
        fc = forecast_yield(lay, only, g)
        plan = to_plan(fc)
        self.assertTrue(fc.missing_climate)
        self.assertEqual(plan.path, "索取通路")
        self.assertIn("refuse_point_forecast", plan.allowed_actions)


class TestGraphStore(unittest.TestCase):
    def test_build_without_remote_raw(self):
        import tempfile
        from pathlib import Path

        from agro import store

        tmp = Path(tempfile.mkdtemp())
        conn = store._connect(tmp / "t.db")
        stats = store.build_graph(conn)
        self.assertGreater(stats["nodes"], 10)
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM nodes")}
        self.assertIn("region", kinds)
        self.assertIn("crop", kinds)
        self.assertIn("sales", kinds)
        export = store.export_json(conn, tmp / "g.json")
        self.assertTrue(export.exists())


if __name__ == "__main__":
    unittest.main()
