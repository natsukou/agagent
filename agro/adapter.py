"""产量图谱对外挂 / Agent 的受限输出。通路由覆盖缺口决定，模型不能改。"""

from __future__ import annotations

from dataclasses import dataclass

from .predict import YieldForecast


@dataclass
class AgriPlan:
    path: str
    allowed_actions: list[str]
    layout_id: str
    missing: list[str]
    prompt_frame: str
    tuples: list[tuple[str, str, str]]
    forecast: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "allowed_actions": self.allowed_actions,
            "layout_id": self.layout_id,
            "missing": self.missing,
            "prompt_frame": self.prompt_frame,
            "tuples": [{"head": h, "relation": r, "tail": t} for h, r, t in self.tuples],
            "forecast": self.forecast,
        }


def to_plan(fc: YieldForecast) -> AgriPlan:
    tuples = [
        (fc.layout_id, "in_region", fc.region_id),
        (fc.layout_id, "expected_t_ha", f"{fc.expected_t_ha:.3f}"),
    ]
    for s in fc.stages:
        tuples.append((s.stage_id, "factor", f"{s.factor:.3f}"))

    if fc.missing_climate:
        return AgriPlan(
            path="索取通路",
            allowed_actions=["refuse_point_forecast", "list_missing_months", "suggest_connector"],
            layout_id=fc.layout_id,
            missing=fc.missing_climate,
            prompt_frame="气候覆盖不足，禁止给出点估计当作实测。只列缺失月份与建议接入源。",
            tuples=tuples,
            forecast=fc.to_dict(),
        )
    if any(c.startswith(("HEAT_", "DROUGHT_", "COLD_")) for c in fc.reason_codes):
        return AgriPlan(
            path="协同通路",
            allowed_actions=["publish_interval", "flag_stress_stages", "request_agronomist"],
            layout_id=fc.layout_id,
            missing=[],
            prompt_frame="窗口内有明显胁迫。必须带区间与胁迫阶段，禁止只报单点产量。",
            tuples=tuples,
            forecast=fc.to_dict(),
        )
    return AgriPlan(
        path="快速通路",
        allowed_actions=["publish_expected_yield", "cite_baseline_and_climate"],
        layout_id=fc.layout_id,
        missing=[],
        prompt_frame="气候窗口内、无明显胁迫。可发布预期产量，必须同时给出基线与气候来源。",
        tuples=tuples,
        forecast=fc.to_dict(),
    )
