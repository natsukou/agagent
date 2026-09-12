"""由气候预报与产量图谱计算预期产量。

模型刻意保持可解释：每个物候阶段给出热害 / 冷害 / 干旱胁迫，再按图谱权重汇总。
缺气候月份时不编造产量，只标覆盖缺口，供后续接入真实数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .climate import MonthClimate
from .crops import CropLayout, months_overlap
from .graph import AgriEdgeType, YieldGraph


@dataclass
class StageStress:
    stage_id: str
    stage_name: str
    heat: float
    cold: float
    drought: float
    factor: float
    months_used: list[int]
    missing_months: list[int]


@dataclass
class YieldForecast:
    layout_id: str
    crop: str
    region_id: str
    baseline_t_ha: float
    expected_t_ha: float
    low_t_ha: float
    high_t_ha: float
    area_kha: float
    expected_kt: float
    stages: list[StageStress]
    missing_climate: list[str]
    reason_codes: list[str]
    graph_version: str
    detail: dict[str, float] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return not self.missing_climate

    def to_dict(self) -> dict[str, object]:
        return {
            "layout_id": self.layout_id,
            "crop": self.crop,
            "region_id": self.region_id,
            "baseline_t_ha": self.baseline_t_ha,
            "expected_t_ha": round(self.expected_t_ha, 3),
            "interval_t_ha": [round(self.low_t_ha, 3), round(self.high_t_ha, 3)],
            "area_kha": self.area_kha,
            "expected_kt": round(self.expected_kt, 2),
            "ready": self.ready,
            "missing_climate": self.missing_climate,
            "reason_codes": self.reason_codes,
            "graph_version": self.graph_version,
            "stages": [
                {
                    "id": s.stage_id,
                    "name": s.stage_name,
                    "heat": round(s.heat, 3),
                    "cold": round(s.cold, 3),
                    "drought": round(s.drought, 3),
                    "factor": round(s.factor, 3),
                    "months_used": s.months_used,
                    "missing_months": s.missing_months,
                }
                for s in self.stages
            ],
            "detail": {k: round(v, 4) for k, v in self.detail.items()},
        }


def _stage_months(start: int, end: int) -> list[int]:
    out = []
    m = start
    while True:
        out.append(m)
        if m == end:
            return out
        m = 1 if m == 12 else m + 1


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def stage_stress(
    layout: CropLayout,
    stage_id: str,
    climate: dict[int, MonthClimate],
    irrig_relief: float,
) -> StageStress:
    st = layout.stage(stage_id)
    months = _stage_months(st.start_month, st.end_month)
    used: list[MonthClimate] = []
    missing: list[int] = []
    for m in months:
        c = climate.get(m)
        if c is None:
            missing.append(m)
        else:
            used.append(c)

    if not used:
        return StageStress(st.id, st.name, 0.0, 0.0, 0.0, 1.0, [], missing)

    tmax = sum(c.tmax for c in used) / len(used)
    tmin = sum(c.tmin for c in used) / len(used)
    precip = sum(c.precip_mm for c in used)

    heat = _clip((tmax - st.t_heat) / 6.0, 0.0, 1.0) if tmax > st.t_heat else 0.0
    cold = _clip((st.t_cold - tmin) / 6.0, 0.0, 1.0) if tmin < st.t_cold else 0.0
    drought_raw = _clip((st.precip_need_mm - precip) / max(st.precip_need_mm, 1.0), 0.0, 1.0)
    drought = drought_raw * (1.0 - irrig_relief)

    factor = _clip(1.0 - 0.45 * heat - 0.25 * cold - 0.35 * drought, 0.35, 1.12)
    return StageStress(st.id, st.name, heat, cold, drought, factor, [c.month for c in used], missing)


def forecast_yield(
    layout: CropLayout,
    climate_series: list[MonthClimate],
    graph: YieldGraph,
    uncertainty: float = 0.08,
) -> YieldForecast:
    by_month = {c.month: c for c in climate_series if c.region_id == layout.region_id}
    irrig = 0.55 * layout.irrigated_share
    stresses = [stage_stress(layout, st.id, by_month, irrig) for st in layout.stages]

    missing = []
    reasons = []
    for s in stresses:
        if s.missing_months:
            missing.append(f"{layout.region_id}:{s.stage_id}:{s.missing_months}")
            reasons.append("CLIMATE_COVERAGE_GAP")
        if s.heat >= 0.25:
            reasons.append(f"HEAT_{s.stage_id}")
        if s.drought >= 0.25:
            reasons.append(f"DROUGHT_{s.stage_id}")
        if s.cold >= 0.25:
            reasons.append(f"COLD_{s.stage_id}")

    # 图谱权重：物候 → 构成 → 产量；缺边时退回物候自身权重
    weighted = 0.0
    wsum = 0.0
    for st, ss in zip(layout.stages, stresses):
        sid = f"stage:{layout.id}:{st.id}"
        w = 0.0
        for e in graph.out_edges(sid, (AgriEdgeType.CONTRIBUTES,)):
            w += e.weight
        if w == 0.0:
            w = st.yield_weight
        weighted += w * ss.factor
        wsum += w
    factor = weighted / wsum if wsum else 1.0

    expected = layout.baseline_t_ha * factor
    band = max(uncertainty, 0.04 + 0.06 * (1.0 if missing else 0.0))
    reasons = list(dict.fromkeys(reasons or ["WITHIN_CLIMATE_WINDOW"]))

    return YieldForecast(
        layout_id=layout.id,
        crop=layout.crop,
        region_id=layout.region_id,
        baseline_t_ha=layout.baseline_t_ha,
        expected_t_ha=expected,
        low_t_ha=expected * (1.0 - band),
        high_t_ha=expected * (1.0 + band),
        area_kha=layout.area_kha,
        expected_kt=expected * layout.area_kha,
        stages=stresses,
        missing_climate=missing,
        reason_codes=reasons,
        graph_version=graph.version,
        detail={"factor": factor, "irrig_relief": irrig},
    )
