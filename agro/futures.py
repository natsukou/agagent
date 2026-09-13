"""期货行情接入：只用公开、无需登录的接口。

可达性实测（本机）：
  新浪连续主力日线    可用，玉米回溯到 2005
  郑商所公开日报 txt  可用，2018 起，含 WH/PM/RI/LR/JR 真实合约
  大商所 dayQuotesCh  返回 412，不绕过
  CBOT（Yahoo）      可用，作国际基准参照

重要：连续主力是拼接序列，不是单一合约。小麦与稻谷的国内合约近三年
近乎零成交，价格是挂牌残值，不能当现货信号用。流动性判定写在
`liquidity()`，不合格的品种一律标 usable=False。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import statistics
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .collect import RAW

FUT_RAW = RAW / "futures"
UA = "Mozilla/5.0 (brain-agri-graph; research)"

SINA_DAILY = (
    "https://stock.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
    "InnerFuturesNewService.getDailyKLine?symbol={symbol}"
)
CZCE_DAILY = "http://www.czce.com.cn/cn/DFSStaticFiles/Future/{year}/{day}/FutureDataDaily.txt"

# 近三年日均成交量门槛（手）。低于 MIN 判为僵尸合约，低于 THIN 只能当薄市参考。
MIN_RECENT_VOLUME = 1000
THIN_VOLUME = 50000
RECENT_FROM = "2023-01-01"


@dataclass(frozen=True)
class Symbol:
    code: str  # 新浪连续主力代码，如 C0
    name: str
    exchange: str
    crop: str | None
    role: str  # spot_proxy / downstream / reference
    note: str = ""


SYMBOLS: dict[str, Symbol] = {
    # 谷物：政策收储品种普遍僵尸，玉米因市场化改革活跃
    "C0": Symbol("C0", "玉米连续", "DCE", "玉米", "spot_proxy"),
    "CS0": Symbol("CS0", "玉米淀粉连续", "DCE", "玉米", "downstream", "加工品，需求侧信号"),
    "WH0": Symbol("WH0", "强麦连续", "CZCE", "小麦", "spot_proxy", "2023-02 后停更"),
    "PM0": Symbol("PM0", "普麦连续", "CZCE", "小麦", "spot_proxy", "2022 后停更"),
    "JR0": Symbol("JR0", "粳稻连续", "CZCE", "水稻", "spot_proxy", "2022 后零成交"),
    "RI0": Symbol("RI0", "早籼稻连续", "CZCE", "水稻", "spot_proxy", "2022 后零成交"),
    "LR0": Symbol("LR0", "晚籼稻连续", "CZCE", "水稻", "spot_proxy", "2022 后零成交"),
    "RR0": Symbol("RR0", "粳米连续", "DCE", "水稻", "spot_proxy", "薄市"),
    # 大豆：压榨链完整，原料与两个产出都活跃
    "A0": Symbol("A0", "豆一连续", "DCE", "大豆", "spot_proxy", "国产非转基因，食用为主"),
    "B0": Symbol("B0", "豆二连续", "DCE", "大豆", "import_proxy", "进口转基因，压榨口径"),
    "M0": Symbol("M0", "豆粕连续", "DCE", "大豆", "downstream", "国内最活跃农产品合约"),
    "Y0": Symbol("Y0", "豆油连续", "DCE", "大豆", "downstream"),
    # 油脂油料
    "P0": Symbol("P0", "棕榈油连续", "DCE", None, "import_proxy", "进口油脂，不映射国内布局"),
    "RS0": Symbol("RS0", "菜籽连续", "CZCE", "油菜籽", "spot_proxy", "近乎零成交"),
    "RM0": Symbol("RM0", "菜粕连续", "CZCE", "油菜籽", "downstream"),
    "OI0": Symbol("OI0", "菜油连续", "CZCE", "油菜籽", "downstream"),
    # 经济作物
    "SR0": Symbol("SR0", "白糖连续", "CZCE", "甘蔗", "downstream", "糖是加工品，非田间单产"),
    "CF0": Symbol("CF0", "棉花连续", "CZCE", "棉花", "spot_proxy"),
    "CY0": Symbol("CY0", "棉纱连续", "CZCE", "棉花", "downstream", "薄市"),
    "AP0": Symbol("AP0", "苹果连续", "CZCE", "苹果", "spot_proxy"),
    "CJ0": Symbol("CJ0", "红枣连续", "CZCE", "红枣", "spot_proxy"),
    "PK0": Symbol("PK0", "花生连续", "CZCE", "花生", "spot_proxy"),
    # 养殖与工业农产品
    "JD0": Symbol("JD0", "鸡蛋连续", "DCE", None, "livestock"),
    "LH0": Symbol("LH0", "生猪连续", "DCE", None, "livestock", "2021 上市，薄市"),
    "RU0": Symbol("RU0", "天然橡胶连续", "SHFE", "天然橡胶", "spot_proxy"),
    "NR0": Symbol("NR0", "20号胶连续", "INE", "天然橡胶", "import_proxy"),
}

CROP_SYMBOLS = {
    "玉米": ("C0", "CS0"),
    "小麦": ("WH0", "PM0"),
    "水稻": ("JR0", "RI0", "LR0", "RR0"),
    "大豆": ("A0", "B0", "M0", "Y0"),
    "油菜籽": ("RS0", "RM0", "OI0"),
    "棉花": ("CF0", "CY0"),
    "苹果": ("AP0",),
    "红枣": ("CJ0",),
    "花生": ("PK0",),
    "甘蔗": ("SR0",),
    "天然橡胶": ("RU0", "NR0"),
}

# 国内四家：DCE 大商所、CZCE 郑商所、SHFE 上期所、INE 上海国际能源中心。
# 广期所 GFEX 目前挂牌工业硅 / 碳酸锂 / 多晶硅，无农产品，故不列。
CN_EXCHANGES = ("DCE", "CZCE", "SHFE", "INE")

# 国际农产品基准，Yahoo chart 接口。只作参照，不是中国现货。
INTL_SYMBOLS: dict[str, Symbol] = {
    "ZS=F": Symbol("ZS=F", "CBOT 大豆", "CBOT", "大豆", "reference"),
    "ZM=F": Symbol("ZM=F", "CBOT 豆粕", "CBOT", "大豆", "reference"),
    "ZL=F": Symbol("ZL=F", "CBOT 豆油", "CBOT", "大豆", "reference"),
    "ZC=F": Symbol("ZC=F", "CBOT 玉米", "CBOT", "玉米", "reference"),
    "ZW=F": Symbol("ZW=F", "CBOT 软红冬麦", "CBOT", "小麦", "reference", "国内强麦停更后的替代参照"),
    "KE=F": Symbol("KE=F", "KC 硬红冬麦", "CBOT", "小麦", "reference"),
    "ZR=F": Symbol("ZR=F", "CBOT 糙米", "CBOT", "水稻", "reference", "国内稻谷合约僵尸后的替代参照"),
    "CT=F": Symbol("CT=F", "ICE 棉花", "ICE", "棉花", "reference"),
    "SB=F": Symbol("SB=F", "ICE 原糖", "ICE", "甘蔗", "reference"),
    "KC=F": Symbol("KC=F", "ICE 咖啡", "ICE", "咖啡", "reference"),
    "CC=F": Symbol("CC=F", "ICE 可可", "ICE", "可可", "reference"),
    "LE=F": Symbol("LE=F", "CME 活牛", "CME", None, "livestock"),
    "HE=F": Symbol("HE=F", "CME 瘦肉猪", "CME", None, "livestock"),
}

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"


@dataclass(frozen=True)
class FuturesBar:
    symbol: str
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    settle: float | None
    volume: float
    open_interest: float
    source: str = "sina-continuous"

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "settle": self.settle,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "source": self.source,
        }


def _get(url: str, timeout: int = 45) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn", "Accept": "*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _f(v: object) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def parse_sina_daily(symbol: str, text: str) -> list[FuturesBar]:
    """新浪返回 jsonp，字段 d/o/h/l/c/v/p/s：日期、开高低收、成交量、持仓量、结算价。"""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    rows = json.loads(m.group(0))
    out: list[FuturesBar] = []
    for r in rows:
        day = str(r.get("d") or "")
        if len(day) != 10:
            continue
        out.append(
            FuturesBar(
                symbol=symbol,
                date=day,
                open=_f(r.get("o")),
                high=_f(r.get("h")),
                low=_f(r.get("l")),
                close=_f(r.get("c")),
                settle=_f(r.get("s")) or None,
                volume=_f(r.get("v")) or 0.0,
                open_interest=_f(r.get("p")) or 0.0,
            )
        )
    return out


def fetch_symbol(symbol: str, refresh: bool = False) -> list[FuturesBar]:
    FUT_RAW.mkdir(parents=True, exist_ok=True)
    path = FUT_RAW / f"{symbol}.json"
    if path.exists() and not refresh:
        return [FuturesBar(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
    bars = parse_sina_daily(symbol, _get(SINA_DAILY.format(symbol=symbol)))
    path.write_text(
        json.dumps([b.to_dict() for b in bars], ensure_ascii=False), encoding="utf-8"
    )
    return bars


def parse_czce_daily(text: str) -> list[dict[str, object]]:
    """郑商所公开日报：竖线分隔，含真实合约的结算价、成交量、持仓量。"""
    out: list[dict[str, object]] = []
    date = ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        date = m.group(0)
    for line in text.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        code = cells[0]
        if not code or not code[0].isalpha() or code.startswith("合约"):
            continue
        prefix = "".join(ch for ch in code if ch.isalpha()).upper()
        if prefix in {"", "小计", "总计"} or len(cells) < 12:
            continue
        out.append(
            {
                "date": date,
                "contract": code,
                "variety": prefix,
                "pre_settle": _f(cells[1]),
                "open": _f(cells[2]),
                "high": _f(cells[3]),
                "low": _f(cells[4]),
                "close": _f(cells[5]),
                "settle": _f(cells[6]),
                "volume": _f(cells[9]),
                "open_interest": _f(cells[10]),
                "source": "CZCE daily txt",
            }
        )
    return out


def fetch_czce_day(day: str, refresh: bool = False) -> list[dict[str, object]]:
    """day 形如 2024-07-15。2018 年以前返回 404。"""
    compact = day.replace("-", "")
    FUT_RAW.mkdir(parents=True, exist_ok=True)
    path = FUT_RAW / f"czce_{compact}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    rows = parse_czce_daily(_get(CZCE_DAILY.format(year=compact[:4], day=compact)))
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def parse_yahoo_chart(symbol: str, payload: dict) -> list[FuturesBar]:
    res = (payload.get("chart") or {}).get("result") or []
    if not res:
        return []
    node = res[0]
    stamps = node.get("timestamp") or []
    quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
    out: list[FuturesBar] = []
    for i, ts in enumerate(stamps):
        close = _f((quote.get("close") or [None] * len(stamps))[i])
        if close is None:
            continue
        day = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")
        out.append(
            FuturesBar(
                symbol=symbol,
                date=day,
                open=_f((quote.get("open") or [None] * len(stamps))[i]),
                high=_f((quote.get("high") or [None] * len(stamps))[i]),
                low=_f((quote.get("low") or [None] * len(stamps))[i]),
                close=close,
                settle=None,
                volume=_f((quote.get("volume") or [None] * len(stamps))[i]) or 0.0,
                open_interest=0.0,
                source="yahoo-chart",
            )
        )
    return out


def fetch_intl(symbol: str, rng: str = "10y", refresh: bool = False) -> list[FuturesBar]:
    """国际基准日线。持仓量 Yahoo 不提供，统一填 0。"""
    FUT_RAW.mkdir(parents=True, exist_ok=True)
    path = FUT_RAW / f"intl_{symbol.replace('=', '_')}.json"
    if path.exists() and not refresh:
        return [FuturesBar(**row) for row in json.loads(path.read_text(encoding="utf-8"))]
    payload = json.loads(_get(YAHOO_CHART.format(symbol=symbol, rng=rng)))
    bars = parse_yahoo_chart(symbol, payload)
    path.write_text(json.dumps([b.to_dict() for b in bars], ensure_ascii=False), encoding="utf-8")
    return bars


def liquidity(bars: list[FuturesBar], recent_from: str = RECENT_FROM) -> dict[str, object]:
    if not bars:
        return {"n": 0, "usable": False, "reason": "无数据"}
    recent = [b for b in bars if b.date >= recent_from]
    vols = [b.volume for b in recent]
    median_v = statistics.median(vols) if vols else 0.0
    zero_share = (sum(1 for v in vols if v == 0) / len(vols)) if vols else 1.0
    usable = bool(vols) and median_v >= MIN_RECENT_VOLUME
    if not vols:
        tier, reason = "dormant", f"{recent_from} 之后无行情，合约已停更"
    elif not usable:
        tier = "dormant"
        reason = f"近三年成交中位数 {median_v:.0f} 手，低于 {MIN_RECENT_VOLUME}，僵尸合约"
    elif median_v < THIN_VOLUME:
        tier = "thin"
        reason = f"近三年成交中位数 {median_v:.0f} 手，薄市；价格可参考，不宜做高频信号"
    else:
        tier, reason = "liquid", f"近三年成交中位数 {median_v:.0f} 手，流动性达标"
    return {
        "n": len(bars),
        "span": [bars[0].date, bars[-1].date],
        "recent_days": len(recent),
        "recent_median_volume": round(median_v, 1),
        "recent_zero_volume_share": round(zero_share, 4),
        "tier": tier,
        "usable": usable,
        "reason": reason,
    }


def annual_price(bars: list[FuturesBar], min_days: int = 120) -> dict[int, dict[str, float]]:
    """按自然年聚合结算/收盘价。只保留有足够交易日的年份。"""
    buckets: dict[int, list[FuturesBar]] = {}
    for b in bars:
        buckets.setdefault(int(b.date[:4]), []).append(b)
    out: dict[int, dict[str, float]] = {}
    for year, rows in sorted(buckets.items()):
        prices = [b.settle or b.close for b in rows]
        prices = [p for p in prices if p]
        if len(rows) < min_days or not prices:
            continue
        out[year] = {
            "days": len(rows),
            "mean_price": round(sum(prices) / len(prices), 2),
            "year_end_price": round(prices[-1], 2),
            "mean_volume": round(sum(b.volume for b in rows) / len(rows), 1),
            "mean_open_interest": round(sum(b.open_interest for b in rows) / len(rows), 1),
        }
    return out


def availability(refresh: bool = False, symbols: tuple[str, ...] | None = None) -> dict[str, object]:
    codes = symbols or tuple(SYMBOLS)
    per: dict[str, object] = {}
    for code in codes:
        meta = SYMBOLS.get(code)
        try:
            bars = fetch_symbol(code, refresh=refresh)
        except Exception as exc:
            per[code] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        info = liquidity(bars)
        info["crop"] = meta.crop if meta else None
        info["exchange"] = meta.exchange if meta else None
        info["role"] = meta.role if meta else None
        info["note"] = meta.note if meta else ""
        per[code] = info

    by_crop: dict[str, object] = {}
    verdicts = []
    for crop, codes_for_crop in CROP_SYMBOLS.items():
        tiers = {c: per[c].get("tier") for c in codes_for_crop if isinstance(per.get(c), dict)}
        if not tiers:
            continue
        liquid = [c for c, t in tiers.items() if t == "liquid"]
        thin = [c for c, t in tiers.items() if t == "thin"]
        by_crop[crop] = {
            "symbols": list(codes_for_crop),
            "liquid": liquid,
            "thin": thin,
            "dormant": [c for c, t in tiers.items() if t == "dormant"],
            "has_tradable_price": bool(liquid or thin),
        }
        if liquid:
            verdicts.append(f"{crop}: 可用（{'/'.join(liquid)}）")
        elif thin:
            verdicts.append(f"{crop}: 仅薄市（{'/'.join(thin)}）")
        else:
            verdicts.append(f"{crop}: 无有效行情")

    by_exchange: dict[str, dict[str, list[str]]] = {}
    for code, info in per.items():
        if not isinstance(info, dict) or "tier" not in info:
            continue
        ex = str(info.get("exchange") or "?")
        by_exchange.setdefault(ex, {"liquid": [], "thin": [], "dormant": []})
        by_exchange[ex][str(info["tier"])].append(code)

    return {
        "source": "新浪连续主力（jsonp）+ 郑商所公开日报 txt",
        "blocked": {"DCE dayQuotesCh": "HTTP 412，未绕过"},
        "thresholds": {"min_recent_volume": MIN_RECENT_VOLUME, "thin_below": THIN_VOLUME},
        "cn_exchanges": list(CN_EXCHANGES),
        "gfex_note": "广期所目前挂牌工业硅/碳酸锂/多晶硅，无农产品",
        "symbols": per,
        "by_crop": by_crop,
        "by_exchange": by_exchange,
        "price_without_layout": price_without_layout(),
        "intl_reference": {k: v.name for k, v in INTL_SYMBOLS.items()},
        "conclusion": "；".join(verdicts),
    }


def price_without_layout() -> dict[str, object]:
    """有期货价格、但产量图谱里没有作物布局的品种。这是接产业预测的缺口。"""
    from .datasets import bundled

    have = {lay.crop for lay in bundled.layouts()}
    priced = {s.crop for s in SYMBOLS.values() if s.crop}
    missing = sorted(priced - have)
    return {
        "layout_crops": sorted(have),
        "priced_crops": sorted(priced),
        "missing_layout": missing,
        "note": "这些品种能拿到价格，但没有物候布局，产量侧还接不上",
    }


def align_with_yield(db_path: Path | None = None, symbol: str = "C0") -> dict[str, object]:
    """把年度均价与年度单产对齐，供后续做供给-价格研究。不含价格模型。"""
    from .store import CHN_YIELD_FILTER, DB_PATH, _connect

    meta = SYMBOLS.get(symbol)
    crop = meta.crop if meta else None
    bars = fetch_symbol(symbol)
    prices = annual_price(bars)
    conn = _connect(db_path or DB_PATH)
    try:
        # 必须锁 scope：同一 (crop, year) 有 CHN 与 PSD:CHN/USA/BRA 多行，
        # 不锁的话字典推导式会让后来的国别静默覆盖中国口径，价格-单产就对错了。
        yields = {
            int(r["year"]): float(r["yield_t_ha"])
            for r in conn.execute(
                f"SELECT year, yield_t_ha FROM yield_year WHERE {CHN_YIELD_FILTER} AND crop = ?",
                (crop,),
            )
        }
    finally:
        conn.close()
    rows = []
    for year in sorted(set(prices) & set(yields)):
        rows.append(
            {
                "year": year,
                "crop": crop,
                "yield_t_ha": round(yields[year], 4),
                "mean_price": prices[year]["mean_price"],
                "year_end_price": prices[year]["year_end_price"],
                "trading_days": prices[year]["days"],
            }
        )
    return {
        "symbol": symbol,
        "crop": crop,
        "liquidity": liquidity(bars),
        "aligned_years": len(rows),
        "rows": rows,
        "caveat": "仅对齐，未建价格模型；年度 n 太小，不足以做供给弹性估计",
    }
