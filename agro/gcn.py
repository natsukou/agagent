"""两层图卷积，作用在区县需求图上。

节点是区县-作物需求，边是同主产区或同作物。
标签仍是全国年单产：GCN 只能学到区域共享信号，不能声称有县级技巧。
没有县级标签时，GCN 不应替代上年持续基线。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from .county import COUNTIES
from .nowcast import build_nowcast_samples
from .train import Sample, metrics, split_by_year

COUNTY_INDEX = {c.id: c for c in COUNTIES}


def _zeros(n: int, m: int) -> list[list[float]]:
    return [[0.0] * m for _ in range(n)]


def _mm(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    n, k, m = len(a), len(a[0]), len(b[0])
    out = _zeros(n, m)
    for i in range(n):
        ai = a[i]
        oi = out[i]
        for t in range(k):
            ait = ai[t]
            if ait == 0.0:
                continue
            bt = b[t]
            for j in range(m):
                oi[j] += ait * bt[j]
    return out


def _mv(a: list[list[float]], v: list[float]) -> list[float]:
    return [sum(ai[j] * v[j] for j in range(len(v))) for ai in a]


def _relu(x: float) -> float:
    return x if x > 0.0 else 0.0


def _norm_adj(n: int, edges: list[tuple[int, int]]) -> list[list[float]]:
    a = _zeros(n, n)
    for i in range(n):
        a[i][i] = 1.0
    for i, j in edges:
        if i != j:
            a[i][j] = 1.0
            a[j][i] = 1.0
    deg = [sum(row) for row in a]
    inv = [1.0 / math.sqrt(d) if d > 0 else 0.0 for d in deg]
    out = _zeros(n, n)
    for i in range(n):
        for j in range(n):
            out[i][j] = inv[i] * a[i][j] * inv[j]
    return out


@dataclass
class Snapshot:
    year: int
    day: str
    crop: str
    ids: list[str]
    x: list[list[float]]
    y: float
    adj: list[list[float]]


def _edges_for(ids: list[str]) -> list[tuple[int, int]]:
    edges = []
    for i, a in enumerate(ids):
        ca = COUNTY_INDEX[a]
        for j in range(i + 1, len(ids)):
            cb = COUNTY_INDEX[ids[j]]
            if ca.region_id == cb.region_id:
                edges.append((i, j))
    return edges


def snapshots_from(samples: list[Sample]) -> list[Snapshot]:
    groups: dict[tuple[int, str, str], list[Sample]] = defaultdict(list)
    for s in samples:
        day = str(s.extras.get("day") or "")
        cid = str(s.extras.get("county") or "")
        if not day or not cid:
            continue
        groups[(s.year, day, s.crop)].append(s)
    out = []
    for (year, day, crop), rows in sorted(groups.items()):
        ids = [str(r.extras["county"]) for r in rows]
        x = [r.features for r in rows]
        adj = _norm_adj(len(ids), _edges_for(ids))
        out.append(Snapshot(year, day, crop, ids, x, rows[0].y, adj))
    return out


class GCN:
    def __init__(self, in_dim: int, hid: int = 8) -> None:
        self.in_dim = in_dim
        self.hid = hid
        self.w0 = [[((i * 17 + j * 13) % 100) / 100.0 - 0.5 for j in range(hid)] for i in range(in_dim)]
        self.w1 = [[((i * 11 + 7) % 100) / 100.0 - 0.5] for i in range(hid)]

    def forward(self, x: list[list[float]], adj: list[list[float]]) -> list[float]:
        h = _mm(adj, _mm(x, self.w0))
        h = [[_relu(v) for v in row] for row in h]
        y = _mm(adj, _mm(h, self.w1))
        return [row[0] for row in y]

    def _backward(self, s: Snapshot) -> tuple[list[list[float]], list[list[float]], float]:
        ax = _mm(s.adj, s.x)
        pre = _mm(ax, self.w0)
        h = [[_relu(v) for v in row] for row in pre]
        ah = _mm(s.adj, h)
        pred = [row[0] for row in _mm(ah, self.w1)]
        n = len(pred)
        dy = [[2.0 * (pred[i] - s.y) / n] for i in range(n)]
        loss = sum((p - s.y) ** 2 for p in pred) / n
        g1 = _mm(_T(ah), dy)
        dah = _mm(dy, _T(self.w1))
        dh = _mm(_T(s.adj), dah)
        dpre = [[dh[i][j] if pre[i][j] > 0 else 0.0 for j in range(self.hid)] for i in range(n)]
        g0 = _mm(_T(ax), dpre)
        return g0, g1, loss

    def train(self, snaps: list[Snapshot], steps: int = 40, lr: float = 0.02) -> float:
        last = 0.0
        for _ in range(steps):
            g0 = _zeros(self.in_dim, self.hid)
            g1 = _zeros(self.hid, 1)
            loss = 0.0
            for s in snaps:
                d0, d1, sl = self._backward(s)
                loss += sl
                for i in range(self.in_dim):
                    for j in range(self.hid):
                        g0[i][j] += d0[i][j]
                for i in range(self.hid):
                    g1[i][0] += d1[i][0]
            last = loss / max(len(snaps), 1)
            scale = lr / max(len(snaps), 1)
            for i in range(self.in_dim):
                for j in range(self.hid):
                    self.w0[i][j] -= scale * g0[i][j]
            for i in range(self.hid):
                self.w1[i][0] -= scale * g1[i][0]
        return last


def _T(a: list[list[float]]) -> list[list[float]]:
    return [list(col) for col in zip(*a)] if a else []


def run_gcn(test_years: tuple[int, ...] = (2023, 2024), max_train_snaps: int = 80) -> dict[str, object]:
    samples = build_nowcast_samples()
    if not samples:
        return {"ok": False, "reason": "没有三日样本：需要 raw/weather 与产量库"}
    train_s, test_s = split_by_year(samples, test_years)
    train_g = snapshots_from(train_s)
    test_g = snapshots_from(test_s)
    if len(train_g) < 4 or not test_g:
        return {"ok": False, "reason": f"snapshots train={len(train_g)} test={len(test_g)}"}

    # 训练子集：每年均匀抽样，避免数值梯度扫完全季
    by_year: dict[int, list[Snapshot]] = defaultdict(list)
    for s in train_g:
        by_year[s.year].append(s)
    picked: list[Snapshot] = []
    per = max(1, max_train_snaps // max(len(by_year), 1))
    for rows in by_year.values():
        step = max(1, len(rows) // per)
        picked.extend(rows[::step][:per])

    model = GCN(in_dim=len(samples[0].features), hid=6)
    train_loss = model.train(picked, steps=25, lr=0.01)

    pred, truth, persist = [], [], []
    mean_y = sum(s.y for s in train_s) / len(train_s)
    last_by_crop = {}
    for s in train_s:
        last_by_crop[s.crop] = s.y
    for snap in test_g:
        ys = model.forward(snap.x, snap.adj)
        pred.append(sum(ys) / len(ys))
        truth.append(snap.y)
        persist.append(last_by_crop.get(snap.crop, mean_y))

    return {
        "ok": True,
        "task": "区县需求图 GCN：三日窗 → 季末单产（弱监督）",
        "train_snapshots": len(train_g),
        "used_for_fit": len(picked),
        "test_snapshots": len(test_g),
        "train_loss": round(train_loss, 5),
        "model": metrics(truth, pred),
        "baseline_persist": metrics(truth, persist),
        "beats_persist": metrics(truth, pred)["mae"] < metrics(truth, persist)["mae"],
        "warning": "标签是全国年单产，GCN 没有区县监督信号，默认不应压过上年持续",
    }
