"""气候状态与动态预测。

没有外部气象源时，用「气候态 + 距平持续性」做可替换的基线预报。
真实 NWP / 再分析数据通过 ClimateConnector 接入后，只替换观测，不改图结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MonthClimate:
    """某区域某月的气候要素，单位统一为摄氏度与毫米。"""

    region_id: str
    year: int
    month: int  # 1-12
    tmean: float
    tmax: float
    tmin: float
    precip_mm: float
    source: str = "climatology"

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.region_id, self.year, self.month)


@dataclass
class Climatology:
    """区域月气候态。key = (region_id, month)。"""

    normals: dict[tuple[str, int], MonthClimate] = field(default_factory=dict)

    def get(self, region_id: str, month: int) -> MonthClimate:
        return self.normals[(region_id, month)]


@dataclass
class ForecastParams:
    persist: float = 0.65
    decay: float = 0.75
    # 距平衰减到该阈值后改回纯气候态
    floor: float = 0.08


class ClimateConnector(Protocol):
    def latest(self, region_id: str) -> list[MonthClimate]:
        """最近若干月的观测，用于计算距平。"""

    def climatology(self) -> Climatology:
        ...


def anomaly(obs: MonthClimate, normal: MonthClimate) -> dict[str, float]:
    return {
        "tmean": obs.tmean - normal.tmean,
        "tmax": obs.tmax - normal.tmax,
        "tmin": obs.tmin - normal.tmin,
        "precip_mm": obs.precip_mm - normal.precip_mm,
    }


def persist_anomaly(recent: list[MonthClimate], clim: Climatology) -> dict[str, float]:
    """用最近一个月相对气候态的距平作为持续性信号。"""
    if not recent:
        return {"tmean": 0.0, "tmax": 0.0, "tmin": 0.0, "precip_mm": 0.0}
    last = recent[-1]
    return anomaly(last, clim.get(last.region_id, last.month))


def forecast_month(
    region_id: str,
    year: int,
    month: int,
    clim: Climatology,
    anom: dict[str, float],
    lead: int,
    params: ForecastParams | None = None,
) -> MonthClimate:
    """超前 lead 个月的动态预报：气候态 + 衰减距平。"""
    p = params or ForecastParams()
    scale = p.persist * (p.decay ** lead)
    if scale < p.floor:
        scale = 0.0
    n = clim.get(region_id, month)
    return MonthClimate(
        region_id=region_id,
        year=year,
        month=month,
        tmean=n.tmean + scale * anom["tmean"],
        tmax=n.tmax + scale * anom["tmax"],
        tmin=n.tmin + scale * anom["tmin"],
        precip_mm=max(0.0, n.precip_mm + scale * anom["precip_mm"]),
        source="persist+climatology",
    )


def advance_ym(year: int, month: int, steps: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + steps
    return total // 12, total % 12 + 1


def forecast_horizon(
    region_id: str,
    start_year: int,
    start_month: int,
    horizon: int,
    clim: Climatology,
    recent: list[MonthClimate],
    params: ForecastParams | None = None,
) -> list[MonthClimate]:
    anom = persist_anomaly(recent, clim)
    out: list[MonthClimate] = []
    for lead in range(horizon):
        y, m = advance_ym(start_year, start_month, lead)
        out.append(forecast_month(region_id, y, m, clim, anom, lead, params))
    return out
