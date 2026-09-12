"""针对性作物的完整布局：熟制、主产区、物候、气候窗口、产量构成。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PhenologyStage:
    id: str
    name: str
    start_month: int
    end_month: int
    t_opt_min: float
    t_opt_max: float
    t_heat: float
    t_cold: float
    precip_need_mm: float
    yield_weight: float  # 对最终产量的权重，同作物内和为 1


@dataclass(frozen=True)
class YieldComponent:
    id: str
    name: str
    weight: float


@dataclass
class CropLayout:
    """一个「作物 × 熟制 × 主产区」的完整布局，不是品种名本身。"""

    id: str
    crop: str
    system: str
    region_id: str
    region_name: str
    baseline_t_ha: float
    area_kha: float
    irrigated_share: float
    stages: list[PhenologyStage]
    components: list[YieldComponent]
    notes: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def season_months(self) -> list[int]:
        months: list[int] = []
        for s in self.stages:
            m = s.start_month
            while True:
                if m not in months:
                    months.append(m)
                if m == s.end_month:
                    break
                m = 1 if m == 12 else m + 1
        return months

    def stage(self, stage_id: str) -> PhenologyStage:
        for s in self.stages:
            if s.id == stage_id:
                return s
        raise KeyError(stage_id)


def months_overlap(start: int, end: int, month: int) -> bool:
    if start <= end:
        return start <= month <= end
    return month >= start or month <= end
