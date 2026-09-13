"""产量图谱：气候节点 → 物候胁迫 → 产量构成 → 预期产量。

复用知识组件图的邻接表与拓扑，但边语义换成农业因果，不再使用
「掌握前置」。混用两类边会把气候距平当成能力证据传播。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .crops import CropLayout


class NodeKind(str, Enum):
    REGION = "region"
    CLIMATE = "climate"
    CROP = "crop"
    STAGE = "stage"
    COMPONENT = "component"
    YIELD = "yield"
    MARKET = "market"
    COUNTY = "county"
    DEMAND = "demand"


class AgriEdgeType(str, Enum):
    HOSTS = "hosts"  # 区域承载布局
    OBSERVES = "observes"  # 区域观测气候
    STRESSES = "stresses"  # 气候胁迫物候
    SATISFIES = "satisfies"  # 气候满足窗口
    CONTRIBUTES = "contributes"  # 物候贡献产量构成
    AGGREGATES = "aggregates"  # 构成汇总为产量
    PRICES = "prices"  # 期货品种给作物布局定价信号
    CONTAINS = "contains"  # 主产区包含区县
    DEMANDS = "demands"  # 区县对作物场景的需求
    NEEDS = "needs"  # 作物需求对物候天气窗口的需要


@dataclass
class AgriNode:
    id: str
    name: str
    kind: NodeKind
    layout_id: str | None = None
    payload: dict[str, float | str] = field(default_factory=dict)


@dataclass
class AgriEdge:
    src: str
    dst: str
    type: AgriEdgeType
    confidence: float = 0.8
    weight: float = 1.0
    source: str = "bundled"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.src, self.dst, self.type.value)


class YieldGraph:
    def __init__(self, version: str = "agri-v1") -> None:
        self.version = version
        self.nodes: dict[str, AgriNode] = {}
        self.edges: dict[tuple[str, str, str], AgriEdge] = {}
        self._out: dict[str, list[AgriEdge]] = {}
        self._in: dict[str, list[AgriEdge]] = {}

    def add_node(self, node: AgriNode) -> None:
        self.nodes[node.id] = node
        self._out.setdefault(node.id, [])
        self._in.setdefault(node.id, [])

    def add_edge(self, edge: AgriEdge) -> None:
        if edge.src not in self.nodes or edge.dst not in self.nodes:
            raise KeyError(f"边引用了不存在的节点: {edge.src} -> {edge.dst}")
        self.edges[edge.key] = edge
        self._out.setdefault(edge.src, []).append(edge)
        self._in.setdefault(edge.dst, []).append(edge)

    def out_edges(self, nid: str, types: tuple[AgriEdgeType, ...] | None = None) -> list[AgriEdge]:
        edges = self._out.get(nid, [])
        return [e for e in edges if types is None or e.type in types]

    def in_edges(self, nid: str, types: tuple[AgriEdgeType, ...] | None = None) -> list[AgriEdge]:
        edges = self._in.get(nid, [])
        return [e for e in edges if types is None or e.type in types]

    def of_kind(self, kind: NodeKind) -> list[AgriNode]:
        return [n for n in self.nodes.values() if n.kind is kind]

    def ancestors(self, nid: str, max_hops: int = 4) -> dict[str, int]:
        seen: dict[str, int] = {}
        q: deque[tuple[str, int]] = deque([(nid, 0)])
        while q:
            cur, hops = q.popleft()
            if hops >= max_hops:
                continue
            for e in self._in.get(cur, []):
                if e.src not in seen:
                    seen[e.src] = hops + 1
                    q.append((e.src, hops + 1))
        return seen

    def stats(self) -> dict[str, object]:
        by_kind: dict[str, int] = {}
        for n in self.nodes.values():
            by_kind[n.kind.value] = by_kind.get(n.kind.value, 0) + 1
        by_type: dict[str, int] = {}
        for e in self.edges.values():
            by_type[e.type.value] = by_type.get(e.type.value, 0) + 1
        return {
            "version": self.version,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "nodes_by_kind": by_kind,
            "edges_by_type": by_type,
        }


def build_yield_graph(layouts: list[CropLayout]) -> YieldGraph:
    g = YieldGraph()
    regions: set[str] = set()

    for lay in layouts:
        if lay.region_id not in regions:
            regions.add(lay.region_id)
            g.add_node(AgriNode(f"region:{lay.region_id}", lay.region_name, NodeKind.REGION))
            for var, name in (("tmax", "最高温"), ("tmin", "最低温"), ("precip", "降水")):
                cid = f"climate:{lay.region_id}:{var}"
                g.add_node(AgriNode(cid, f"{lay.region_name}{name}", NodeKind.CLIMATE, payload={"var": var}))
                g.add_edge(AgriEdge(f"region:{lay.region_id}", cid, AgriEdgeType.OBSERVES, 1.0, 1.0))

        g.add_node(AgriNode(f"crop:{lay.id}", f"{lay.crop}/{lay.system}", NodeKind.CROP, lay.id))
        g.add_edge(AgriEdge(f"region:{lay.region_id}", f"crop:{lay.id}", AgriEdgeType.HOSTS, 1.0, 1.0))

        for st in lay.stages:
            sid = f"stage:{lay.id}:{st.id}"
            g.add_node(
                AgriNode(
                    sid,
                    f"{lay.crop}-{st.name}",
                    NodeKind.STAGE,
                    lay.id,
                    {"yield_weight": st.yield_weight, "t_heat": st.t_heat, "precip_need": st.precip_need_mm},
                )
            )
            # 高温与干旱是胁迫，适温窗口是满足边
            g.add_edge(
                AgriEdge(
                    f"climate:{lay.region_id}:tmax",
                    sid,
                    AgriEdgeType.STRESSES,
                    0.9,
                    st.yield_weight,
                    source="物候热害窗口",
                )
            )
            g.add_edge(
                AgriEdge(
                    f"climate:{lay.region_id}:precip",
                    sid,
                    AgriEdgeType.STRESSES,
                    0.85,
                    st.yield_weight,
                    source="需水窗口",
                )
            )
            g.add_edge(
                AgriEdge(
                    f"climate:{lay.region_id}:tmin",
                    sid,
                    AgriEdgeType.SATISFIES,
                    0.75,
                    st.yield_weight,
                    source="低温下限",
                )
            )

        for comp in lay.components:
            cid = f"comp:{lay.id}:{comp.id}"
            g.add_node(AgriNode(cid, comp.name, NodeKind.COMPONENT, lay.id, {"weight": comp.weight}))
            # 物候阶段按产量权重贡献到构成
            for st in lay.stages:
                g.add_edge(
                    AgriEdge(
                        f"stage:{lay.id}:{st.id}",
                        cid,
                        AgriEdgeType.CONTRIBUTES,
                        0.8,
                        st.yield_weight * comp.weight,
                    )
                )

        yid = f"yield:{lay.id}"
        g.add_node(AgriNode(yid, f"{lay.crop}预期产量", NodeKind.YIELD, lay.id, {"baseline": lay.baseline_t_ha}))
        for comp in lay.components:
            g.add_edge(
                AgriEdge(
                    f"comp:{lay.id}:{comp.id}",
                    yid,
                    AgriEdgeType.AGGREGATES,
                    1.0,
                    comp.weight,
                )
            )
    return g


def attach_dce_instruments(graph: YieldGraph) -> int:
    """把已映射到布局的大商所品种挂到产量图谱上。期货是定价信号，不是产量。"""
    from .market import DCE_INSTRUMENTS

    added = 0
    for ins in DCE_INSTRUMENTS.values():
        if not ins.layouts:
            continue
        nid = f"market:dce:{ins.code}"
        if nid not in graph.nodes:
            graph.add_node(
                AgriNode(
                    nid,
                    f"DCE {ins.name}",
                    NodeKind.MARKET,
                    payload={"code": ins.code, "role": ins.role, "exchange": "DCE"},
                )
            )
            added += 1
        for layout_id in ins.layouts:
            crop_id = f"crop:{layout_id}"
            if crop_id not in graph.nodes:
                continue
            key = (nid, crop_id, AgriEdgeType.PRICES.value)
            if key not in graph.edges:
                conf = 0.85 if ins.role == "spot_proxy" else 0.45
                graph.add_edge(AgriEdge(nid, crop_id, AgriEdgeType.PRICES, conf, 1.0, source="dce-map"))
    return added


def attach_counties(graph: YieldGraph) -> int:
    """把种子区县和作物需求节点挂上。天气暂继承主产区，待换区县坐标拉取。"""
    from .county import COUNTIES

    added = 0
    for c in COUNTIES:
        nid = f"county:{c.id}"
        if nid not in graph.nodes:
            graph.add_node(
                AgriNode(
                    nid,
                    c.name,
                    NodeKind.COUNTY,
                    payload={"region_id": c.region_id, "lat": c.lat, "lon": c.lon, "area_kha": c.area_kha},
                )
            )
            added += 1
        region_id = f"region:{c.region_id}"
        if region_id in graph.nodes:
            graph.add_edge(AgriEdge(region_id, nid, AgriEdgeType.CONTAINS, 1.0, 1.0, source="county-seed"))
        for crop in c.crops:
            did = f"demand:{c.id}:{crop}"
            if did not in graph.nodes:
                graph.add_node(AgriNode(did, f"{c.name}-{crop}需求", NodeKind.DEMAND, payload={"crop": crop}))
                added += 1
            graph.add_edge(AgriEdge(nid, did, AgriEdgeType.DEMANDS, 1.0, 1.0, source="county-seed"))
            for n in graph.nodes.values():
                if n.kind is NodeKind.CROP and n.name.startswith(crop + "/"):
                    graph.add_edge(AgriEdge(did, n.id, AgriEdgeType.DEMANDS, 0.7, 1.0, source="county-seed"))
                    break
    return added
