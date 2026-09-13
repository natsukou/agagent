"""期货接入测试：解析器与流动性判定必须离线可测。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agro.futures import (
    CN_EXCHANGES,
    CROP_SYMBOLS,
    INTL_SYMBOLS,
    MIN_RECENT_VOLUME,
    SYMBOLS,
    THIN_VOLUME,
    FuturesBar,
    annual_price,
    liquidity,
    parse_czce_daily,
    parse_sina_daily,
    parse_yahoo_chart,
    price_without_layout,
)

CZCE_SAMPLE = Path(__file__).resolve().parents[1] / "agro" / "datasets" / "czce_sample.txt"


def _bars(symbol: str, days: int, volume: float, start_year: int = 2024) -> list[FuturesBar]:
    out = []
    for i in range(days):
        month = i // 28 + 1
        day = i % 28 + 1
        out.append(
            FuturesBar(
                symbol=symbol,
                date=f"{start_year}-{month:02d}-{day:02d}",
                open=2000.0,
                high=2010.0,
                low=1990.0,
                close=2005.0,
                settle=2004.0,
                volume=volume,
                open_interest=10000.0,
            )
        )
    return out


class TestCzceParser(unittest.TestCase):
    def test_parses_contracts_and_skips_totals(self):
        rows = parse_czce_daily(CZCE_SAMPLE.read_text(encoding="utf-8"))
        codes = [r["contract"] for r in rows]
        self.assertEqual(codes, ["WH409", "JR409", "SR409"])
        self.assertNotIn("小计", codes)
        sugar = next(r for r in rows if r["contract"] == "SR409")
        self.assertEqual(sugar["date"], "2024-07-15")
        self.assertEqual(sugar["variety"], "SR")
        self.assertEqual(sugar["settle"], 6142.0)
        self.assertEqual(sugar["volume"], 298412.0)
        self.assertEqual(sugar["open_interest"], 412880.0)
        wheat = next(r for r in rows if r["contract"] == "WH409")
        self.assertEqual(wheat["volume"], 2.0)


class TestSinaParser(unittest.TestCase):
    def test_jsonp_to_bars(self):
        payload = (
            "/*<script>location.href='//sina.com';</script>*/\nvar _=("
            + json.dumps(
                [
                    {"d": "2026-09-10", "o": "2260.000", "h": "2270.000", "l": "2250.000",
                     "c": "2265.000", "v": "400000", "p": "1100000", "s": "2262.000"},
                    {"d": "bad", "o": "1", "h": "1", "l": "1", "c": "1", "v": "1", "p": "1", "s": "1"},
                ]
            )
            + ");"
        )
        bars = parse_sina_daily("C0", payload)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].date, "2026-09-10")
        self.assertEqual(bars[0].settle, 2262.0)
        self.assertEqual(bars[0].open_interest, 1100000.0)

    def test_empty_payload(self):
        self.assertEqual(parse_sina_daily("RR0", 'var hq_str_RR0="";'), [])


class TestLiquidityTiers(unittest.TestCase):
    def test_dormant_when_stale(self):
        info = liquidity(_bars("JR0", 100, 1.0, start_year=2020))
        self.assertEqual(info["tier"], "dormant")
        self.assertFalse(info["usable"])
        self.assertIn("停更", info["reason"])

    def test_dormant_when_volume_below_min(self):
        info = liquidity(_bars("WH0", 200, MIN_RECENT_VOLUME - 1))
        self.assertEqual(info["tier"], "dormant")
        self.assertFalse(info["usable"])

    def test_thin_between_thresholds(self):
        info = liquidity(_bars("RR0", 200, (MIN_RECENT_VOLUME + THIN_VOLUME) / 2))
        self.assertEqual(info["tier"], "thin")
        self.assertTrue(info["usable"])

    def test_liquid_above_thin(self):
        info = liquidity(_bars("C0", 200, THIN_VOLUME * 2))
        self.assertEqual(info["tier"], "liquid")


class TestAnnualPrice(unittest.TestCase):
    def test_drops_short_years(self):
        rows = _bars("C0", 50, 100000.0)
        self.assertEqual(annual_price(rows), {})

    def test_aggregates_full_year(self):
        rows = _bars("C0", 200, 100000.0)
        agg = annual_price(rows)
        self.assertIn(2024, agg)
        self.assertEqual(agg[2024]["days"], 200)
        self.assertAlmostEqual(agg[2024]["mean_price"], 2004.0)


class TestYahooParser(unittest.TestCase):
    def test_chart_to_bars_skips_null_close(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "timestamp": [1757548800, 1757635200],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [1030.0, 1040.0],
                                    "high": [1050.0, 1045.0],
                                    "low": [1025.0, 1035.0],
                                    "close": [1045.0, None],
                                    "volume": [88000, 90000],
                                }
                            ]
                        },
                    }
                ]
            }
        }
        bars = parse_yahoo_chart("ZS=F", payload)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, 1045.0)
        self.assertEqual(bars[0].open_interest, 0.0)  # Yahoo 不给持仓量
        self.assertEqual(bars[0].source, "yahoo-chart")

    def test_empty_result(self):
        self.assertEqual(parse_yahoo_chart("ZS=F", {"chart": {"result": []}}), [])


class TestSymbolRegistry(unittest.TestCase):
    def test_soybean_crush_chain_present(self):
        self.assertEqual(CROP_SYMBOLS["大豆"], ("A0", "B0", "M0", "Y0"))
        self.assertEqual(SYMBOLS["A0"].note, "国产非转基因，食用为主")
        self.assertEqual(SYMBOLS["B0"].role, "import_proxy")
        self.assertEqual(SYMBOLS["M0"].role, "downstream")

    def test_four_cn_exchanges_no_gfex(self):
        self.assertEqual(CN_EXCHANGES, ("DCE", "CZCE", "SHFE", "INE"))
        self.assertNotIn("GFEX", {s.exchange for s in SYMBOLS.values()})

    def test_every_crop_symbol_registered(self):
        for crop, codes in CROP_SYMBOLS.items():
            for code in codes:
                self.assertIn(code, SYMBOLS, f"{crop}:{code} 未注册")
                self.assertEqual(SYMBOLS[code].crop, crop)

    def test_intl_covers_dormant_domestic_crops(self):
        ref_crops = {s.crop for s in INTL_SYMBOLS.values() if s.crop}
        self.assertIn("小麦", ref_crops)  # 国内强麦停更后的替代
        self.assertIn("水稻", ref_crops)

    def test_flags_priced_crops_without_layout(self):
        gap = price_without_layout()
        # 大豆与棉花已补上产量布局，不再算缺口；油菜籽/甘蔗/苹果等仍只有价格
        for covered in ("小麦", "水稻", "玉米", "大豆", "棉花"):
            self.assertIn(covered, gap["layout_crops"])
            self.assertNotIn(covered, gap["missing_layout"])
        self.assertIn("油菜籽", gap["missing_layout"])


if __name__ == "__main__":
    unittest.main()
