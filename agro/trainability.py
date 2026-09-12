"""训练可行性：先数能对齐的样本，再决定能不能训。

ModelScope 上 agriculture 检索出来的是语料、问答、病虫害图像和地块分割，
没有「地区 × 天气 × 产量 × 销量」对齐表。本模块只评估我们自己的图谱库。
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .store import DB_PATH


MIN_LINEAR = 20
MIN_GNN = 200


def _rows(sql: str, path: Path | None = None) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(sql))
    finally:
        conn.close()


def aligned_samples(path: Path | None = None) -> list[dict[str, object]]:
    """每个作物-年：主产区生长季天气均值 + 全国单产。这是当前唯一可监督样本。"""
    weather = _rows(
        "SELECT region_id, year, month, tmean, tmax, precip_mm FROM weather_month",
        path,
    )
    yields = _rows(
        "SELECT crop, year, yield_t_ha, source FROM yield_year WHERE crop != '谷物'",
        path,
    )
    region_crops = {
        "dongbei": {"玉米"},
        "huabei": {"小麦", "玉米"},
        "changjiang": {"水稻"},
        "huanan": {"水稻"},
    }
    by_ry: dict[tuple[str, int], list] = defaultdict(list)
    for w in weather:
        by_ry[(w["region_id"], w["year"])].append(w)

    samples = []
    for y in yields:
        months = []
        for rid, crops in region_crops.items():
            if y["crop"] not in crops:
                continue
            months.extend(by_ry.get((rid, y["year"]), []))
        if len(months) < 6:
            continue
        samples.append(
            {
                "crop": y["crop"],
                "year": y["year"],
                "yield_t_ha": y["yield_t_ha"],
                "tmean": sum(m["tmean"] for m in months) / len(months),
                "tmax": sum(m["tmax"] for m in months) / len(months),
                "precip_mm": sum(m["precip_mm"] for m in months),
                "weather_months": len(months),
                "source": y["source"],
            }
        )
    return samples


def verdict(samples: list[dict[str, object]] | None = None) -> dict[str, object]:
    samples = samples if samples is not None else aligned_samples()
    n = len(samples)
    by_crop: dict[str, int] = {}
    years = sorted({s["year"] for s in samples})
    for s in samples:
        by_crop[s["crop"]] = by_crop.get(s["crop"], 0) + 1
    sales_n = _rows("SELECT COUNT(*) AS n FROM sales_obs")[0]["n"] if DB_PATH.exists() else 0
    return {
        "aligned_n": n,
        "years": years,
        "by_crop": by_crop,
        "sales_n": sales_n,
        "can_train_linear": n >= MIN_LINEAR,
        "can_train_gnn": n >= MIN_GNN,
        "can_use_modelscope_agri_for_this_task": False,
        "reason": (
            "对齐样本不足，不能做图网络或深度学习训练"
            if n < MIN_LINEAR
            else "只够做线性/岭回归诊断，不够训 GNN"
            if n < MIN_GNN
            else "样本过门槛，可以做图模型，但仍缺县级标签"
        ),
        "modelscope_note": (
            "ModelScope 检索 agriculture/农业 无作物产量数据集；"
            "命中的是行业语料、农业问答、杂草/病虫害图像、地块分割。"
            "那些集不能验证本图谱的产量预测效果。"
        ),
        "samples": samples,
    }


def loo_ridge(samples: list[dict[str, object]]) -> dict[str, object] | None:
    """样本极少时的诊断回归，不是上线模型。纯标准库正规方程。"""
    if len(samples) < 6:
        return None
    crops = sorted({s["crop"] for s in samples})
    crop_idx = {c: i for i, c in enumerate(crops)}

    def feat(s):
        onehot = [1.0 if s["crop"] == c else 0.0 for c in crops]
        return onehot + [s["tmean"], s["tmax"], s["precip_mm"] / 1000.0]

    xs = [feat(s) for s in samples]
    ys = [float(s["yield_t_ha"]) for s in samples]
    preds = []
    for hold in range(len(samples)):
        train_x = [x for i, x in enumerate(xs) if i != hold]
        train_y = [y for i, y in enumerate(ys) if i != hold]
        w = _ridge(train_x, train_y, l2=1.0)
        preds.append(_dot(w, [1.0] + xs[hold]))
    mae = sum(abs(p - y) for p, y in zip(preds, ys)) / len(ys)
    baseline = sum(ys) / len(ys)
    base_mae = sum(abs(baseline - y) for y in ys) / len(ys)
    return {
        "n": len(samples),
        "loo_mae_t_ha": round(mae, 4),
        "mean_baseline_mae_t_ha": round(base_mae, 4),
        "beats_mean": mae < base_mae,
        "note": "诊断用。能打过总均值，主要是作物哑变量（水稻单产本就高于小麦），不能当成天气已经训准",
        "crop_features": crops,
    }


def _dot(w, x):
    return sum(a * b for a, b in zip(w, x))


def _ridge(X, y, l2: float):
    # 设计矩阵加截距
    n = len(X)
    d = len(X[0]) + 1
    xtx = [[0.0] * d for _ in range(d)]
    xty = [0.0] * d
    for row, yi in zip(X, y):
        xr = [1.0] + row
        for i in range(d):
            xty[i] += xr[i] * yi
            for j in range(d):
                xtx[i][j] += xr[i] * xr[j]
    for i in range(1, d):
        xtx[i][i] += l2
    return _solve(xtx, xty)


def _solve(A, b):
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for i in range(n):
        piv = max(range(i, n), key=lambda r: abs(M[r][i]))
        M[i], M[piv] = M[piv], M[i]
        div = M[i][i] or 1e-12
        for j in range(i, n + 1):
            M[i][j] /= div
        for r in range(n):
            if r == i:
                continue
            f = M[r][i]
            for j in range(i, n + 1):
                M[r][j] -= f * M[i][j]
    return [M[i][n] for i in range(n)]
