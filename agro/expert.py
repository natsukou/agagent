"""期货专家深度决策 + 产业轻决策二次调整。

现有 `strategy.py` 把方向压成 -sign(delta)，只有一条硬规则。
这里拆成两段，且两段都不能看见建仓日及之后的行情：

1. **期货专家**：只用信息日及之前的价格/成交量特征，训练一个两层 MLP，
   预测随后持有期的标的收益符号。这是「交易员看盘」那一层。
2. **产业轻决策**：再用当季产量展望相对开局基线的偏离（与 `intervene.py`
   同一口径）做一次轻量调整——同意则保留，冲突则改 hold，专家空仓时
   才允许产业信号补位。不把产业层再训成第二个深度模型。

训练按年切分：测试年之前的主链截面才进训练集，禁止打乱、禁止用安慰剂链训练。
判定仍走 `strategy.py` 的带成本回测与随机符号零分布，不另立尺子。
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

from .collect import ROOT
from .eventstudy import LAYOUT_SYMBOLS, Revision, build_revisions, load_bars
from .futures import FuturesBar
from .intervene import INTERVENE_BAND, outlook_state, price_direction
from .strategy import (
    COST_BPS,
    DEFAULT_COST_BPS,
    HOLD_DAYS,
    Trade,
    _bar_index,
    _px,
    evaluate,
    random_sign_null,
)

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "expert_report.json"
TABLE_PATH = MODEL_DIR / "expert_table.md"

FEATURE_NAMES = ("ret_1", "ret_3", "ret_5", "ret_10", "vol_10", "mom_20", "ma_gap", "vol_ratio")
EXPERT_TAU = 0.08
CLIP_Y = 0.08
TRAIN_SEED = 20260913


@dataclass
class ExpertSample:
    key: str
    layout_id: str
    symbol: str
    kind: str
    role: str
    year: int
    as_of: str
    features: list[float]
    y: float
    industry_state: str
    industry_dir: int
    rule_dir: int


@dataclass
class MLP:
    w1: list[list[float]]
    b1: list[float]
    w2: list[float]
    b2: float

    def forward(self, x: list[float]) -> float:
        h = self._hidden(x)
        z = sum(self.w2[i] * h[i] for i in range(len(h))) + self.b2
        return _tanh(z)

    def _hidden(self, x: list[float]) -> list[float]:
        return [_relu(sum(row[j] * x[j] for j in range(len(x))) + b) for row, b in zip(self.w1, self.b1)]

    def predict_dir(self, x: list[float], tau: float = EXPERT_TAU) -> int:
        score = self.forward(x)
        if score > tau:
            return 1
        if score < -tau:
            return -1
        return 0


def _relu(v: float) -> float:
    return v if v > 0.0 else 0.0


def _tanh(z: float) -> float:
    if z > 20:
        return 1.0
    if z < -20:
        return -1.0
    e = math.exp(2.0 * z)
    return (e - 1.0) / (e + 1.0)


def _ret(closes: list[float], n: int) -> float:
    if len(closes) <= n or closes[-1 - n] <= 0:
        return 0.0
    return closes[-1] / closes[-1 - n] - 1.0


def price_features(bars: list[FuturesBar], as_of: str) -> list[float] | None:
    """只用 date <= as_of 的K线。建仓在 as_of 之后，这里看不见。"""
    hist = [b for b in bars if b.date <= as_of]
    if len(hist) < 21:
        return None
    window = hist[-21:]
    closes = [_px(b) for b in window]
    if any(c <= 0 for c in closes):
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    vol10 = statistics.pstdev(rets[-10:]) if len(rets) >= 10 else 0.0
    sma20 = sum(closes[-20:]) / 20.0
    volumes = [float(b.volume or 0.0) for b in window]
    v_sma = sum(volumes[-20:]) / 20.0
    v_ratio = (volumes[-1] / v_sma - 1.0) if v_sma > 0 else 0.0
    return [
        _ret(closes, 1),
        _ret(closes, 3),
        _ret(closes, 5),
        _ret(closes, 10),
        vol10,
        _ret(closes, 20),
        closes[-1] / sma20 - 1.0,
        v_ratio,
    ]


def industry_path(revisions: list[Revision], band: float = INTERVENE_BAND) -> dict[str, tuple[str, int]]:
    """每个 as_of → (产业状态, 方向)。基线是当季第一条展望。"""
    open_by_year: dict[int, float] = {}
    out: dict[str, tuple[str, int]] = {}
    for rev in sorted(revisions, key=lambda r: (r.year, r.as_of)):
        baseline = open_by_year.setdefault(rev.year, rev.factor)
        state = outlook_state(rev.factor, baseline, band)
        out[rev.as_of] = (state, price_direction(state))
    return out


def light_adjust(expert_dir: int, industry_dir: int) -> tuple[int, str]:
    """产业层只做轻调整：同意保留，冲突空仓，专家空仓才允许产业补位。"""
    if industry_dir == 0:
        return expert_dir, "产业中性，保留专家"
    if expert_dir == 0:
        return industry_dir, "专家空仓，产业补位"
    if expert_dir == industry_dir:
        return expert_dir, "专家与产业同向，保留"
    return 0, "专家与产业冲突，轻决策改为 hold"


def rule_dir(delta: float, min_abs: float = 0.002) -> int:
    if abs(delta) < min_abs:
        return 0
    return 1 if delta < 0 else -1


def _label(bars: list[FuturesBar], as_of: str, hold: int) -> float | None:
    i = _bar_index(bars, as_of)
    if i is None or i + hold >= len(bars):
        return None
    entry, exit_ = _px(bars[i]), _px(bars[i + hold])
    if entry <= 0 or exit_ <= 0:
        return None
    raw = exit_ / entry - 1.0
    return max(-CLIP_Y, min(CLIP_Y, raw)) / CLIP_Y


def collect_samples(
    hold: int = HOLD_DAYS,
    band: float = INTERVENE_BAND,
    layout_ids: tuple[str, ...] | None = None,
) -> list[ExpertSample]:
    packed = build_revisions(layout_ids)
    samples: list[ExpertSample] = []
    for lid, revs in packed.items():
        industry = industry_path(revs, band)
        for symbol, kind, role in LAYOUT_SYMBOLS.get(lid, ()):
            bars = load_bars(symbol, kind)
            if not bars:
                continue
            for rev in revs:
                feat = price_features(bars, rev.as_of)
                y = _label(bars, rev.as_of, hold)
                if feat is None or y is None:
                    continue
                state, i_dir = industry[rev.as_of]
                samples.append(
                    ExpertSample(
                        f"{lid}→{symbol}",
                        lid,
                        symbol,
                        kind,
                        role,
                        rev.year,
                        rev.as_of,
                        feat,
                        y,
                        state,
                        i_dir,
                        rule_dir(rev.delta),
                    )
                )
    return samples


def split_by_year(samples: list[ExpertSample], test_years: tuple[int, ...]) -> tuple[list[ExpertSample], list[ExpertSample]]:
    test = set(test_years)
    train = [s for s in samples if s.year not in test]
    hold = [s for s in samples if s.year in test]
    return train, hold


def _standardize(train: list[ExpertSample], xs: list[list[float]]) -> tuple[list[list[float]], list[float], list[float]]:
    dim = len(FEATURE_NAMES)
    means = []
    scales = []
    for j in range(dim):
        col = [s.features[j] for s in train] or [0.0]
        mu = statistics.fmean(col)
        sd = statistics.pstdev(col)
        means.append(mu)
        scales.append(sd if sd > 1e-8 else 1.0)
    out = [[(row[j] - means[j]) / scales[j] for j in range(dim)] for row in xs]
    return out, means, scales


def init_mlp(din: int = 8, hidden: int = 8, seed: int = TRAIN_SEED) -> MLP:
    rng = random.Random(seed)
    scale = 0.35
    return MLP(
        [[rng.uniform(-scale, scale) for _ in range(din)] for _ in range(hidden)],
        [0.0] * hidden,
        [rng.uniform(-scale, scale) for _ in range(hidden)],
        0.0,
    )


def train_mlp(
    xs: list[list[float]],
    ys: list[float],
    epochs: int = 60,
    lr: float = 0.03,
    seed: int = TRAIN_SEED,
) -> MLP:
    model = init_mlp(len(xs[0]) if xs else 8, 8, seed)
    if not xs:
        return model
    rng = random.Random(seed)
    order = list(range(len(xs)))
    for _ in range(epochs):
        rng.shuffle(order)
        for idx in order:
            x, y = xs[idx], ys[idx]
            h_pre = [sum(row[j] * x[j] for j in range(len(x))) + b for row, b in zip(model.w1, model.b1)]
            h = [_relu(z) for z in h_pre]
            z = sum(model.w2[i] * h[i] for i in range(len(h))) + model.b2
            yhat = _tanh(z)
            d_z = (yhat - y) * (1.0 - yhat * yhat)
            w2 = model.w2[:]
            for i in range(len(h)):
                model.w2[i] -= lr * d_z * h[i]
                if h_pre[i] <= 0:
                    continue
                d_h = d_z * w2[i]
                for j in range(len(x)):
                    model.w1[i][j] -= lr * d_h * x[j]
                model.b1[i] -= lr * d_h
            model.b2 -= lr * d_z
    return model


def trades_from_dirs(samples: list[ExpertSample], bars: list[FuturesBar], hold: int, dirs: list[int]) -> list[Trade]:
    packed = sorted(zip(samples, dirs), key=lambda item: item[0].as_of)
    trades: list[Trade] = []
    busy_until = -1
    for sample, direction in packed:
        if direction == 0:
            continue
        i = _bar_index(bars, sample.as_of)
        if i is None or i + hold >= len(bars) or i <= busy_until:
            continue
        entry, exit_ = _px(bars[i]), _px(bars[i + hold])
        if entry <= 0 or exit_ <= 0:
            continue
        raw = exit_ / entry - 1.0
        trades.append(Trade(bars[i].date, bars[i + hold].date, direction, sample.y, direction * raw))
        busy_until = i + hold
    return trades


def _study(trades: list[Trade], symbol: str, hold: int) -> dict[str, object]:
    cost = COST_BPS.get(symbol, DEFAULT_COST_BPS)
    return {
        "cost_1x": evaluate(trades, cost, hold),
        "random_sign_null": random_sign_null(trades, cost),
    }


@dataclass
class PreparedLink:
    key: str
    layout_id: str
    symbol: str
    kind: str
    role: str
    samples: list[ExpertSample]
    dirs_rule: list[int]
    dirs_expert: list[int]
    dirs_fused: list[int]
    reasons: list[str]


@dataclass
class PreparedExpert:
    test_years: tuple[int, ...]
    hold: int
    band: float
    tau: float
    n_train_primary: int
    train_years: list[int]
    n_test: int
    links: dict[str, PreparedLink]


def prepare(
    test_years: tuple[int, ...] = (2023, 2024),
    hold: int = HOLD_DAYS,
    band: float = INTERVENE_BAND,
    tau: float = EXPERT_TAU,
    layout_ids: tuple[str, ...] | None = None,
) -> PreparedExpert:
    """训练专家并给出测试年每条链的规则/专家/融合方向。策略层只消费这个结果。"""
    samples = collect_samples(hold, band, layout_ids)
    train_all, test_all = split_by_year(samples, test_years)
    train = [s for s in train_all if s.role == "primary"]
    xs, means, scales = _standardize(train, [s.features for s in train])
    model = train_mlp(xs, [s.y for s in train])

    def norm(feat: list[float]) -> list[float]:
        return [(feat[j] - means[j]) / scales[j] for j in range(len(feat))]

    links: dict[str, PreparedLink] = {}
    for key in sorted({s.key for s in test_all}):
        rows = [s for s in test_all if s.key == key]
        expert_dirs = [model.predict_dir(norm(s.features), tau) for s in rows]
        fused: list[int] = []
        reasons: list[str] = []
        for sample, e_dir in zip(rows, expert_dirs):
            d, why = light_adjust(e_dir, sample.industry_dir)
            fused.append(d)
            reasons.append(why)
        links[key] = PreparedLink(
            key,
            rows[0].layout_id,
            rows[0].symbol,
            rows[0].kind,
            rows[0].role,
            rows,
            [s.rule_dir for s in rows],
            expert_dirs,
            fused,
            reasons,
        )
    return PreparedExpert(
        test_years,
        hold,
        band,
        tau,
        len(train),
        sorted({s.year for s in train}),
        len(test_all),
        links,
    )


def run(
    test_years: tuple[int, ...] = (2023, 2024),
    hold: int = HOLD_DAYS,
    band: float = INTERVENE_BAND,
    tau: float = EXPERT_TAU,
    layout_ids: tuple[str, ...] | None = None,
) -> dict[str, object]:
    packed = prepare(test_years, hold, band, tau, layout_ids)
    results: dict[str, object] = {}
    for key, link in packed.links.items():
        bars = load_bars(link.symbol, link.kind)
        variants = {
            "rule": link.dirs_rule,
            "expert": link.dirs_expert,
            "expert_industry": link.dirs_fused,
        }
        block: dict[str, object] = {
            "layout": link.layout_id,
            "symbol": link.symbol,
            "role": link.role,
            "n_test": len(link.samples),
            "expert_nonzero": sum(1 for d in link.dirs_expert if d != 0),
            "industry_nonzero": sum(1 for s in link.samples if s.industry_dir != 0),
            "fused_nonzero": sum(1 for d in link.dirs_fused if d != 0),
            "agree_share": round(
                sum(1 for s, d in zip(link.samples, link.dirs_expert) if s.industry_dir != 0 and s.industry_dir == d)
                / max(1, sum(1 for s in link.samples if s.industry_dir != 0)),
                4,
            ),
            "adjust_reasons": {
                "产业中性，保留专家": link.reasons.count("产业中性，保留专家"),
                "专家空仓，产业补位": link.reasons.count("专家空仓，产业补位"),
                "专家与产业同向，保留": link.reasons.count("专家与产业同向，保留"),
                "专家与产业冲突，轻决策改为 hold": link.reasons.count("专家与产业冲突，轻决策改为 hold"),
            },
        }
        for name, dirs in variants.items():
            block[name] = _study(trades_from_dirs(link.samples, bars, hold, dirs), link.symbol, hold)
        results[key] = block

    verdicts = []
    for key, res in results.items():
        fused = (res.get("expert_industry") or {}).get("cost_1x") or {}
        null = (res.get("expert_industry") or {}).get("random_sign_null") or {}
        if not fused.get("ok") or not null.get("ok"):
            continue
        verdicts.append(
            {
                "link": key,
                "role": res.get("role"),
                "n_trades": fused["n_trades"],
                "sharpe_net": fused["sharpe_net"],
                "percentile_vs_random": null["percentile_of_actual"],
                "beats_random_95": null["above_null_95"],
            }
        )

    report = {
        "task": "期货专家 MLP + 产业轻决策二次调整",
        "test_years": list(packed.test_years),
        "hold_days": packed.hold,
        "industry_band": packed.band,
        "expert_tau": packed.tau,
        "features": list(FEATURE_NAMES),
        "n_train_primary": packed.n_train_primary,
        "n_test": packed.n_test,
        "train_years": packed.train_years,
        "rule": {
            "expert": "两层 MLP，输入信息日及之前的价格/量特征，输出 tanh 分数；|score|≥tau 才出手",
            "industry": "展望相对当季开局基线的偏离，与 intervene.py 同一带宽",
            "fuse": "同意保留；冲突改 hold；专家空仓才允许产业补位。不翻转专家方向。",
        },
        "results": results,
        "verdicts": verdicts,
        "scoreboard": {
            "links_tested": len(verdicts),
            "beats_random_95": sum(1 for v in verdicts if v["beats_random_95"]),
        },
        "caveats": [
            "专家层只学价格形态，不声称理解基本面",
            "产业层是规则调整，不是第二个深度模型",
            "测试年截面不得进入训练；安慰剂链不参与训练",
            "判定仍看随机符号零分布上尾，不看收益正负",
            "不生成当前交易建议",
        ],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    TABLE_PATH.write_text(to_markdown(report), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    report["table"] = str(TABLE_PATH)
    return report


def to_markdown(report: dict) -> str:
    lines = [
        "# 期货专家与产业轻决策",
        "",
        f"- 测试年：{report['test_years']}",
        f"- 专家：{report['rule']['expert']}",
        f"- 产业：{report['rule']['industry']}",
        f"- 融合：{report['rule']['fuse']}",
        f"- 训练主链截面：{report['n_train_primary']}；测试截面：{report['n_test']}",
        "",
        "## 一、三套方向对照",
        "",
        "| 链 | 角色 | 测试截面 | 规则成交 | 专家成交 | 融合成交 | 规则夏普 | 专家夏普 | 融合夏普 | 融合分位 | 上尾显著 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, res in report["results"].items():
        def _n(name: str) -> object:
            block = (res.get(name) or {}).get("cost_1x") or {}
            return block.get("n_trades", "—")

        def _s(name: str) -> object:
            block = (res.get(name) or {}).get("cost_1x") or {}
            return block.get("sharpe_net", "—")

        null = (res.get("expert_industry") or {}).get("random_sign_null") or {}
        pct = null.get("percentile_of_actual", "—")
        beat = "是" if null.get("above_null_95") else "否"
        lines.append(
            f"| {key} | {res.get('role')} | {res.get('n_test')} | {_n('rule')} | {_n('expert')} | {_n('expert_industry')} | {_s('rule')} | {_s('expert')} | {_s('expert_industry')} | {pct} | {beat} |"
        )
    lines.extend(["", "## 二、轻决策调整次数", ""])
    lines.append("| 链 | 产业中性保留专家 | 专家空仓产业补位 | 同向保留 | 冲突改 hold |")
    lines.append("| --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        why = res.get("adjust_reasons") or {}
        lines.append(
            f"| {key} | {why.get('产业中性，保留专家', 0)} | {why.get('专家空仓，产业补位', 0)} | {why.get('专家与产业同向，保留', 0)} | {why.get('专家与产业冲突，轻决策改为 hold', 0)} |"
        )
    board = report.get("scoreboard") or {}
    lines.extend(
        [
            "",
            f"上尾显著链数：{board.get('beats_random_95', 0)} / {board.get('links_tested', 0)}",
            "",
            "产业层不翻转专家方向。冲突时选择空仓，是为了避免把两套弱信号叠成一套假强信号。",
        ]
    )
    return "\n".join(lines) + "\n"


def run_with_table(**kwargs) -> dict[str, object]:
    return run(**kwargs)
