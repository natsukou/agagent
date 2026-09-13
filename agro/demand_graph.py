"""区县气象 + 作物场景需求图谱。

空间：主产区 ⊃ 区县。场景：区县 × 作物 × 物候窗口。
需求边表示「这个县种这个作物时，需要满足哪些天气窗口」，再汇总到产量目标。
这是产量积分的骨架，不是掌握前置图。
"""

from __future__ import annotations

from .county import COUNTIES
from .datasets import bundled
from .graph import AgriEdge, AgriEdgeType, AgriNode, NodeKind, YieldGraph, attach_counties, attach_dce_instruments, build_yield_graph


def layout_for(crop: str, region_id: str):
    for lay in bundled.layouts():
        if lay.crop == crop and lay.region_id == region_id:
            return lay
    for lay in bundled.layouts():
        if lay.crop == crop:
            return lay
    return None


def build_demand_graph() -> YieldGraph:
    g = build_yield_graph(bundled.layouts())
    attach_dce_instruments(g)
    attach_counties(g)

    for county in COUNTIES:
        cid = f"county:{county.id}"
        for crop in county.crops:
            lay = layout_for(crop, county.region_id)
            if lay is None:
                continue
            for stage in lay.stages:
                wid = f"need:{county.id}:{crop}:{stage.id}"
                if wid not in g.nodes:
                    g.add_node(
                        AgriNode(
                            wid,
                            f"{county.name}-{crop}-{stage.name}",
                            NodeKind.CLIMATE,
                            lay.id,
                            payload={
                                "kind": "weather_need",
                                "t_opt_min": stage.t_opt_min,
                                "t_opt_max": stage.t_opt_max,
                                "precip_need_mm": stage.precip_need_mm,
                                "start_month": stage.start_month,
                                "end_month": stage.end_month,
                            },
                        )
                    )
                g.add_edge(
                    AgriEdge(
                        f"demand:{county.id}:{crop}",
                        wid,
                        AgriEdgeType.NEEDS,
                        0.8,
                        stage.yield_weight,
                        source="demand-graph",
                    )
                )
            yid = f"yield-target:{county.id}:{crop}"
            if yid not in g.nodes:
                g.add_node(
                    AgriNode(
                        yid,
                        f"{county.name}-{crop}预期单产",
                        NodeKind.YIELD,
                        lay.id,
                        payload={"crop": crop, "area_kha": county.area_kha},
                    )
                )
            g.add_edge(
                AgriEdge(
                    f"demand:{county.id}:{crop}",
                    yid,
                    AgriEdgeType.AGGREGATES,
                    0.9,
                    1.0,
                    source="demand-graph",
                )
            )
    return g


def snapshot() -> dict[str, object]:
    g = build_demand_graph()
    stats = g.stats()
    stats["counties"] = len(COUNTIES)
    stats["demand_nodes"] = len(g.of_kind(NodeKind.DEMAND))
    stats["yield_targets"] = sum(1 for n in g.nodes if n.startswith("yield-target:"))
    stats["weather_needs"] = sum(
        1 for n in g.nodes.values() if n.payload.get("kind") == "weather_need"
    )
    stats["note"] = "种子 12 县，天气仍继承主产区日序列，不是全国区县全表"
    return stats
