"""大商所日行情的规范结构。

字段对齐 DCE 日行情表 / yuany3721/DCEData 爬下来的列，但本仓库不内嵌
该爬虫：它是 GPL 工具、打的是 2022 年旧 HTTP 接口，且不是现成数据集。
接入方式：把已导出的 CSV/JSON 喂给 loader。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Instrument:
    code: str
    name: str
    exchange: str
    crop: str | None
    layouts: tuple[str, ...]
    role: str  # spot_proxy / downstream / livestock / industrial
    unit: str
    notes: str = ""


# DCEData varietyDict 的农业相关子集。化工、黑色、林产不进产量图谱。
DCE_INSTRUMENTS: dict[str, Instrument] = {
    "c": Instrument("c", "玉米", "DCE", "玉米", ("maize.dongbei.spring", "maize.huabei.summer"), "spot_proxy", "元/吨"),
    "cs": Instrument("cs", "玉米淀粉", "DCE", "玉米", ("maize.dongbei.spring", "maize.huabei.summer"), "downstream", "元/吨", "加工品，只作玉米需求侧信号"),
    "rr": Instrument("rr", "粳米", "DCE", "水稻", ("rice.changjiang.single", "rice.huanan.early"), "spot_proxy", "元/吨", "只覆盖粳稻，籼稻在郑商所"),
    "a": Instrument("a", "豆一", "DCE", "大豆", (), "spot_proxy", "元/吨", "产量图谱尚无大豆布局"),
    "b": Instrument("b", "豆二", "DCE", "大豆", (), "spot_proxy", "元/吨"),
    "m": Instrument("m", "豆粕", "DCE", "大豆", (), "downstream", "元/吨"),
    "y": Instrument("y", "豆油", "DCE", "大豆", (), "downstream", "元/吨"),
    "p": Instrument("p", "棕榈油", "DCE", None, (), "industrial", "元/吨", "进口油脂，不映射国内作物布局"),
    "jd": Instrument("jd", "鸡蛋", "DCE", None, (), "livestock", "元/500千克"),
    "lh": Instrument("lh", "生猪", "DCE", None, (), "livestock", "元/吨"),
}

# 产量图谱已有、但大商所没有的品种，避免误接到 DCE。
NOT_ON_DCE: dict[str, str] = {
    "小麦": "郑商所 WH / PM，不在 DCEData 品种表",
    "籼稻": "郑商所 RI / LR，DCE 只有粳米 RR",
}


@dataclass
class DailyBar:
    date: str  # YYYY-MM-DD
    variety_code: str
    variety_name: str
    contract: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    pre_settle: float | None
    settle: float | None
    volume: float | None
    open_interest: float | None
    turnover: float | None
    trade_type: str = "期货"
    source: str = "dce-file"

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "variety_code": self.variety_code,
            "variety_name": self.variety_name,
            "contract": self.contract,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "pre_settle": self.pre_settle,
            "settle": self.settle,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "turnover": self.turnover,
            "trade_type": self.trade_type,
            "source": self.source,
        }


@dataclass
class VarietySnapshot:
    """某日某品种的主力合约（持仓量最大）。"""

    date: str
    variety_code: str
    variety_name: str
    dominant_contract: str
    settle: float | None
    close: float | None
    volume: float
    open_interest: float
    layouts: tuple[str, ...]
    role: str
    crop: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date,
            "variety_code": self.variety_code,
            "variety_name": self.variety_name,
            "dominant_contract": self.dominant_contract,
            "settle": self.settle,
            "close": self.close,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "crop": self.crop,
            "role": self.role,
            "layouts": list(self.layouts),
        }


def instrument(code: str) -> Instrument | None:
    return DCE_INSTRUMENTS.get(code.lower())


def bars_for_crop(bars: list[DailyBar], crop: str) -> list[DailyBar]:
    codes = {k for k, ins in DCE_INSTRUMENTS.items() if ins.crop == crop}
    return [b for b in bars if b.variety_code in codes]


def dominant_snapshots(bars: list[DailyBar]) -> list[VarietySnapshot]:
    buckets: dict[tuple[str, str], list[DailyBar]] = {}
    for b in bars:
        buckets.setdefault((b.date, b.variety_code), []).append(b)

    out: list[VarietySnapshot] = []
    for (date, code), group in sorted(buckets.items()):
        pick = max(group, key=lambda x: (x.open_interest or 0.0, x.volume or 0.0))
        ins = DCE_INSTRUMENTS.get(code)
        out.append(
            VarietySnapshot(
                date=date,
                variety_code=code,
                variety_name=pick.variety_name,
                dominant_contract=pick.contract,
                settle=pick.settle,
                close=pick.close,
                volume=sum(x.volume or 0.0 for x in group),
                open_interest=pick.open_interest or 0.0,
                layouts=ins.layouts if ins else (),
                role=ins.role if ins else "unknown",
                crop=ins.crop if ins else None,
            )
        )
    return out


def coverage_report() -> dict[str, object]:
    mapped = [i for i in DCE_INSTRUMENTS.values() if i.layouts]
    unmapped = [i for i in DCE_INSTRUMENTS.values() if i.crop and not i.layouts]
    skip = [i for i in DCE_INSTRUMENTS.values() if i.crop is None]
    return {
        "exchange": "DCE",
        "source_repo": "https://github.com/yuany3721/DCEData",
        "use": "只复用品种表与日行情字段，不内嵌爬虫",
        "mapped_to_layouts": [i.code for i in mapped],
        "crop_without_layout": [i.code for i in unmapped],
        "not_for_yield_graph": [i.code for i in skip],
        "crops_not_on_dce": NOT_ON_DCE,
    }
