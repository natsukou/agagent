from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.adapter import to_plan
from agro.climate import MonthClimate, forecast_horizon
from agro.connectors import demo_warm_anomaly
from agro.datasets import bundled, dce
from agro.graph import AgriEdgeType, NodeKind, attach_counties, attach_dce_instruments, build_yield_graph
from agro.market import dominant_snapshots
from agro.predict import forecast_yield


class TestLayouts(unittest.TestCase):
    def test_targeted_systems(self):
        lays = bundled.layouts()
        ids = {x.id for x in lays}
        # 中国三主粮的原始五套布局
        for base in (
            "rice.changjiang.single",
            "rice.huanan.early",
            "wheat.huabei.winter",
            "maize.dongbei.spring",
            "maize.huabei.summer",
        ):
            self.assertIn(base, ids)
        # 新作物结构：大豆/棉花/咖啡各有自己的产区，不共用三主粮产区
        for added in (
            "soy.dongbei.spring",
            "soy.us.belt",
            "soy.br.matogrosso",
            "maize.us.belt",
            "cotton.xinjiang",
            "cotton.us.belt",
            "coffee.br.arabica",
            "coffee.yunnan",
        ):
            self.assertIn(added, ids)
        self.assertEqual(len(lays), 13)
        self.assertEqual(len(ids), len(lays), "布局 id 必须唯一")

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
        self.assertIn("county", kinds)
        self.assertIn("demand", kinds)
        export = store.export_json(conn, tmp / "g.json")
        self.assertTrue(export.exists())


class TestDemandGraph(unittest.TestCase):
    def test_counties_and_weather_needs(self):
        from agro.county import COUNTIES
        from agro.demand_graph import build_demand_graph

        g = build_demand_graph()
        self.assertGreaterEqual(len(g.of_kind(NodeKind.COUNTY)), len(COUNTIES))
        self.assertTrue(g.of_kind(NodeKind.DEMAND))
        self.assertTrue(any(e.type is AgriEdgeType.NEEDS for e in g.edges.values()))
        self.assertTrue(any(e.type is AgriEdgeType.CONTAINS for e in g.edges.values()))
        self.assertEqual(g.nodes["county:230184"].name, "五常")
        self.assertIn("水稻", next(c.crops for c in COUNTIES if c.id == "230184"))

    def test_attach_counties_idempotent(self):
        g = build_yield_graph(bundled.layouts())
        n1 = attach_counties(g)
        n2 = attach_counties(g)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)


class TestNowcastAndGcn(unittest.TestCase):
    def test_wheat_season_crosses_year(self):
        from datetime import date

        from agro.nowcast import season_window, window_features, DailyWx

        start, end = season_window("小麦", "huabei", 2024)
        self.assertEqual(start, date(2023, 10, 1))
        self.assertEqual(end, date(2024, 6, 15))
        feats = window_features(
            [DailyWx(date(2024, 5, 1), 20, 28, 12, 4)],
            [DailyWx(date(2024, 5, 2), 22, 30, 14, 0)],
            0.5,
        )
        self.assertEqual(len(feats), 9)

    def test_gcn_forward_shape(self):
        from agro.gcn import GCN, _norm_adj

        gcn = GCN(in_dim=3, hid=4)
        x = [[1.0, 0.0, 0.2], [0.0, 1.0, 0.3]]
        adj = _norm_adj(2, [(0, 1)])
        y = gcn.forward(x, adj)
        self.assertEqual(len(y), 2)

    def test_gcn_train_reduces_loss_on_toy(self):
        from agro.gcn import GCN, Snapshot, _norm_adj

        adj = _norm_adj(2, [(0, 1)])
        snaps = [
            Snapshot(2020, "2020-07-01", "玉米", ["a", "b"], [[1.0, 0.0], [1.0, 0.0]], 6.0, adj),
            Snapshot(2021, "2021-07-01", "玉米", ["a", "b"], [[1.1, 0.1], [1.0, 0.0]], 6.2, adj),
        ]
        # 伪造县索引不会被 train 用到
        model = GCN(in_dim=2, hid=3)
        before = sum((p - snaps[0].y) ** 2 for p in model.forward(snaps[0].x, adj))
        model.train(snaps, steps=30, lr=0.05)
        after = sum((p - snaps[0].y) ** 2 for p in model.forward(snaps[0].x, adj))
        self.assertLessEqual(after, before + 1e-6)


class TestTrainSplit(unittest.TestCase):
    def test_no_year_leak(self):
        from agro.train import Sample, metrics, split_by_year

        rows = [
            Sample("玉米", 2020, 6.0, [1], ["x"]),
            Sample("玉米", 2021, 6.1, [1], ["x"]),
            Sample("玉米", 2023, 6.5, [1], ["x"]),
            Sample("水稻", 2023, 7.1, [1], ["x"]),
        ]
        train, test = split_by_year(rows, (2023, 2024))
        self.assertEqual({s.year for s in train}, {2020, 2021})
        self.assertEqual({s.year for s in test}, {2023})
        m = metrics([1.0, 3.0], [1.0, 5.0])
        self.assertEqual(m["n"], 2)
        self.assertGreater(m["mae"], 0)


if __name__ == "__main__":
    unittest.main()
