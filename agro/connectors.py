"""外部数据接入位。当前只有气候态种子实现，接口先钉死。"""

from __future__ import annotations

from dataclasses import dataclass

from .climate import Climatology, MonthClimate
from .datasets import bundled


@dataclass
class ConnectorStatus:
    name: str
    ready: bool
    kind: str
    note: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ready": self.ready, "kind": self.kind, "note": self.note}


class BundledClimateSource:
    """内置气候态。latest() 默认返回气候态本身，距平为零。

    要做动态预测演示时，传入 observed 覆盖最近几个月。
    """

    def __init__(self, observed: list[MonthClimate] | None = None) -> None:
        self._clim = bundled.climatology()
        self._observed = observed or []

    def climatology(self) -> Climatology:
        return self._clim

    def latest(self, region_id: str) -> list[MonthClimate]:
        return [c for c in self._observed if c.region_id == region_id]


def planned_connectors() -> list[ConnectorStatus]:
    return [
        ConnectorStatus("bundled-climate", True, "climate", "内置月气候态，可叠观测距平"),
        ConnectorStatus("open-meteo-era5", True, "climate", "公开再分析，按主产区锚点拉取"),
        ConnectorStatus("owid-fao-yield", True, "yield", "中国分作物单产，OWID/FAO"),
        ConnectorStatus("worldbank-cereal", True, "yield", "全国谷物单产与产量"),
        ConnectorStatus("cma-stations", False, "climate", "国家气象站逐日/逐月，待接入"),
        ConnectorStatus("dce-file", True, "market", "本地 CSV/JSON 日行情，字段对齐 DCEData/大商所表"),
        ConnectorStatus("dce-scraper", False, "market", "不内嵌 yuany3721/DCEData 爬虫，旧接口且 GPL"),
        ConnectorStatus("czce-file", False, "market", "小麦/籼稻在郑商所，DCE 覆盖不到"),
        ConnectorStatus("stats-yearbook", False, "yield", "县域单产与播种面积，待接入"),
        ConnectorStatus("remote-sensing", False, "canopy", "NDVI/LAI 物候校正，待接入"),
    ]


def demo_warm_anomaly(region_id: str, months: tuple[int, ...], d_tmax: float = 3.5, d_precip: float = -40.0) -> list[MonthClimate]:
    """构造偏暖偏干的最近观测，用来演示动态预报如何偏离气候态。"""
    clim = bundled.climatology()
    out: list[MonthClimate] = []
    for m in months:
        n = clim.get(region_id, m)
        out.append(
            MonthClimate(
                region_id=region_id,
                year=2026,
                month=m,
                tmean=n.tmean + d_tmax * 0.7,
                tmax=n.tmax + d_tmax,
                tmin=n.tmin + d_tmax * 0.4,
                precip_mm=max(0.0, n.precip_mm + d_precip),
                source="demo-observed",
            )
        )
    return out
