"""期货策略预计测试：把决策做成可下单规则，带成本回测。

`eventstudy.py` 回答的是「修正量与收益率有没有相关性」。
这里回答更硬的问题：**照这个决策真去下单，账户会变成什么样。**

现行决策不再只用 `-sign(delta)`，而是两段：
1. 期货专家 MLP：信息日及之前的价格/量特征；
2. 产业轻决策：产量展望相对开局基线的偏离，只做保留 / 补位 / 冲突改 hold。

旧规则仍在同一测试窗上对照。判定「有没有边」看随机符号零分布上尾。

成交约束：
- 持有固定 h 个交易日，**持仓期间不接新信号**（不重叠）；
- 建仓价取信息日之后第一个交易日收盘，平仓取其后 h 个交易日收盘。

成本按双边基点计，默认值的来源写在 `COST_BPS` 注释里。
同时报 0 成本、标称成本、2 倍成本三档，看结论对成本假设有多敏感。

判定「有没有边」不看收益率正负，看**随机符号分布的分位**：
用同样的建仓日期、同样的持有期，把方向换成固定种子的随机 ±1，跑 2000 次，
得到夏普的零分布。策略夏普落在这个分布中间，就说明方向选择没有贡献。
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

from .collect import ROOT
from .eventstudy import LAYOUT_SYMBOLS, Revision, build_revisions, load_bars
from .futures import FuturesBar

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "strategy_report.json"

HOLD_DAYS = 3
# 修正量阈值：中位 |delta| 在 0.002–0.003 量级，取 0.002 大致筛掉一半噪声截面
MIN_ABS_DELTA = 0.002

# 双边成本（基点）。推算依据：
# - CF0 郑商所棉花：5 吨/手 × ~15000 元/吨 = 7.5 万名义，手续费约 4.3 元/手 ≈ 0.6bp 单边；
#   最小变动价位 5 元/吨 = 3.3bp，按吃掉半个价位算 1.7bp 滑点。双边约 5bp。
# - ICE CT：50000 磅 × ~0.70 美元 = 3.5 万名义，佣金约 3 美元 ≈ 0.9bp 单边；
#   最小变动 0.01 美分/磅 = 5 美元 ≈ 1.4bp，半个价位 0.7bp。双边约 3bp。
# 这里统一取偏保守的一档，并在报告里给 0×/1×/2× 三个口径。
COST_BPS = {"CF0": 8.0, "CT=F": 5.0, "ZS=F": 5.0, "KC=F": 6.0}
DEFAULT_COST_BPS = 8.0

PERM_DRAWS = 2000
PERM_SEED = 20260913
TRADING_DAYS_PER_YEAR = 244


@dataclass
class Trade:
    entry_date: str
    exit_date: str
    direction: int  # +1 做多，-1 做空
    delta: float
    gross_return: float  # 已带方向


def revision_signal(delta: float, min_abs: float = MIN_ABS_DELTA) -> int:
    """修正量 → 方向。因子下修做多，上修做空，幅度不够不动手。"""
    if abs(delta) < min_abs:
        return 0
    return 1 if delta < 0 else -1


def _bar_index(bars: list[FuturesBar], as_of: str) -> int | None:
    return next((i for i, b in enumerate(bars) if b.date > as_of), None)


def _px(bar: FuturesBar) -> float:
    return float(bar.close or bar.settle or 0.0)


def build_trades(
    revisions: list[Revision],
    bars: list[FuturesBar],
    hold: int = HOLD_DAYS,
    min_abs: float = MIN_ABS_DELTA,
) -> list[Trade]:
    """不重叠地遍历截面。持仓未平之前的信号一律跳过。"""
    trades: list[Trade] = []
    busy_until = -1  # 已占用到的 bar 下标
    for rev in sorted(revisions, key=lambda r: r.as_of):
        direction = revision_signal(rev.delta, min_abs)
        if direction == 0:
            continue
        i = _bar_index(bars, rev.as_of)
        if i is None or i + hold >= len(bars):
            continue
        if i <= busy_until:
            continue  # 仓位还没平，不接新信号
        entry, exit_ = _px(bars[i]), _px(bars[i + hold])
        if entry <= 0 or exit_ <= 0:
            continue
        raw = exit_ / entry - 1.0
        trades.append(
            Trade(
                entry_date=bars[i].date,
                exit_date=bars[i + hold].date,
                direction=direction,
                delta=rev.delta,
                gross_return=direction * raw,
            )
        )
        busy_until = i + hold
    return trades


def _sharpe(rets: list[float], trades_per_year: float) -> float | None:
    if len(rets) < 3:
        return None
    sd = statistics.stdev(rets)
    if sd <= 0:
        return None
    return round(statistics.fmean(rets) / sd * (trades_per_year ** 0.5), 4)


def _max_drawdown(rets: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for r in rets:
        equity *= 1.0 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return round(worst, 5)


def evaluate(trades: list[Trade], cost_bps: float, hold: int) -> dict[str, object]:
    if len(trades) < 10:
        return {"ok": False, "n": len(trades), "reason": "成交不足 10 笔"}
    cost = cost_bps / 10000.0
    net = [t.gross_return - cost for t in trades]
    gross = [t.gross_return for t in trades]
    span_years = max(
        (int(trades[-1].exit_date[:4]) - int(trades[0].entry_date[:4]) + 1), 1
    )
    trades_per_year = len(trades) / span_years
    equity = 1.0
    for r in net:
        equity *= 1.0 + r
    return {
        "ok": True,
        "n_trades": len(trades),
        "hold_days": hold,
        "cost_bps_round_trip": cost_bps,
        "span": [trades[0].entry_date, trades[-1].exit_date],
        "trades_per_year": round(trades_per_year, 2),
        "hit_rate_net": round(sum(1 for r in net if r > 0) / len(net), 4),
        "mean_gross_bps": round(statistics.fmean(gross) * 10000, 2),
        "mean_net_bps": round(statistics.fmean(net) * 10000, 2),
        "cum_net_return": round(equity - 1.0, 5),
        "sharpe_net": _sharpe(net, trades_per_year),
        "max_drawdown_net": _max_drawdown(net),
        "long_share": round(sum(1 for t in trades if t.direction > 0) / len(trades), 4),
    }


def buy_and_hold(bars: list[FuturesBar], trades: list[Trade]) -> dict[str, object]:
    """同一区间单纯持有多头，作为最低门槛的对照。"""
    if not trades:
        return {"ok": False, "reason": "无成交区间"}
    start, end = trades[0].entry_date, trades[-1].exit_date
    window = [b for b in bars if start <= b.date <= end]
    if len(window) < 2:
        return {"ok": False, "reason": "区间内行情不足"}
    total = _px(window[-1]) / _px(window[0]) - 1.0
    return {
        "ok": True,
        "span": [start, end],
        "bars": len(window),
        "cum_return": round(total, 5),
        "note": "连续主力/近月拼接，含换月跳空，不是可持有的真实收益",
    }


def random_sign_null(
    trades: list[Trade],
    cost_bps: float,
    draws: int = PERM_DRAWS,
    seed: int = PERM_SEED,
) -> dict[str, object]:
    """保留建仓日期与持有期，只把方向换成随机 ±1，得到夏普的零分布。

    这是判定「方向选择有没有贡献」的关键检验：策略夏普落在零分布中间，
    说明赚的亏的都来自标的自身的波动，和我们的因子无关。
    """
    if len(trades) < 10:
        return {"ok": False, "reason": "成交不足 10 笔"}
    # 去掉方向，拿回标的原始收益
    raw = [t.gross_return * t.direction for t in trades]
    span_years = max(
        (int(trades[-1].exit_date[:4]) - int(trades[0].entry_date[:4]) + 1), 1
    )
    tpy = len(trades) / span_years
    cost = cost_bps / 10000.0

    actual = _sharpe([t.gross_return - cost for t in trades], tpy)
    rng = random.Random(seed)
    null: list[float] = []
    for _ in range(draws):
        s = _sharpe([rng.choice((1, -1)) * r - cost for r in raw], tpy)
        if s is not None:
            null.append(s)
    if actual is None or not null:
        return {"ok": False, "reason": "夏普无法计算（收益标准差为 0）"}
    null.sort()
    below = sum(1 for s in null if s <= actual)
    return {
        "ok": True,
        "draws": len(null),
        "sharpe_actual": actual,
        "sharpe_null_median": round(statistics.median(null), 4),
        "sharpe_null_p05": round(null[int(0.05 * len(null))], 4),
        "sharpe_null_p95": round(null[int(0.95 * len(null))], 4),
        "percentile_of_actual": round(below / len(null), 4),
        # 判定「有边」只能看上尾。显著变差（下尾）说明方向系统性选反了，
        # 或者干脆是噪声碰巧走到另一侧，都不构成可用策略。
        "above_null_95": bool(below / len(null) > 0.95),
        "below_null_05": bool(below / len(null) < 0.05),
        "outside_null_95": bool(below / len(null) > 0.95 or below / len(null) < 0.05),
    }


def study_symbol(
    revisions: list[Revision],
    bars: list[FuturesBar],
    symbol: str,
    hold: int = HOLD_DAYS,
    min_abs: float = MIN_ABS_DELTA,
) -> dict[str, object]:
    trades = build_trades(revisions, bars, hold, min_abs)
    base_cost = COST_BPS.get(symbol, DEFAULT_COST_BPS)
    out: dict[str, object] = {
        "signal": {
            "min_abs_delta": min_abs,
            "sections": len(revisions),
            "sections_above_threshold": sum(
                1 for r in revisions if revision_signal(r.delta, min_abs) != 0
            ),
            "trades_after_no_overlap": len(trades),
        },
        "cost_sensitivity": {
            "0x": evaluate(trades, 0.0, hold),
            "1x": evaluate(trades, base_cost, hold),
            "2x": evaluate(trades, base_cost * 2, hold),
        },
        "buy_and_hold": buy_and_hold(bars, trades),
        "random_sign_null": random_sign_null(trades, base_cost),
    }
    # 反向策略：若正向没边，反向也不该有，否则说明符号约定搞反了
    flipped = [
        Trade(t.entry_date, t.exit_date, -t.direction, t.delta, -t.gross_return)
        for t in trades
    ]
    out["reversed_strategy"] = evaluate(flipped, base_cost, hold)
    return out


def _full_study(trades: list[Trade], bars: list[FuturesBar], symbol: str, hold: int) -> dict[str, object]:
    base_cost = COST_BPS.get(symbol, DEFAULT_COST_BPS)
    out: dict[str, object] = {
        "signal": {
            "trades_after_no_overlap": len(trades),
        },
        "cost_sensitivity": {
            "0x": evaluate(trades, 0.0, hold),
            "1x": evaluate(trades, base_cost, hold),
            "2x": evaluate(trades, base_cost * 2, hold),
        },
        "buy_and_hold": buy_and_hold(bars, trades),
        "random_sign_null": random_sign_null(trades, base_cost),
    }
    flipped = [
        Trade(t.entry_date, t.exit_date, -t.direction, t.delta, -t.gross_return)
        for t in trades
    ]
    out["reversed_strategy"] = evaluate(flipped, base_cost, hold)
    return out


def run(
    hold: int = HOLD_DAYS,
    min_abs: float = MIN_ABS_DELTA,
    layout_ids: tuple[str, ...] | None = None,
    test_years: tuple[int, ...] = (2023, 2024),
    band: float = 0.03,
) -> dict[str, object]:
    from . import expert as ex_mod

    packed = ex_mod.prepare(test_years, hold, band, layout_ids=layout_ids)
    results: dict[str, object] = {}
    for key, link in packed.links.items():
        bars = load_bars(link.symbol, link.kind)
        if not bars:
            results[key] = {"ok": False, "reason": f"{link.symbol} 无行情"}
            continue
        fused_trades = ex_mod.trades_from_dirs(link.samples, bars, hold, link.dirs_fused)
        rule_trades = ex_mod.trades_from_dirs(link.samples, bars, hold, link.dirs_rule)
        expert_trades = ex_mod.trades_from_dirs(link.samples, bars, hold, link.dirs_expert)
        results[key] = {
            "layout": link.layout_id,
            "symbol": link.symbol,
            "role": link.role,
            "n_test": len(link.samples),
            "decision": "expert_industry",
            "adjust_reasons": {
                "产业中性，保留专家": link.reasons.count("产业中性，保留专家"),
                "专家空仓，产业补位": link.reasons.count("专家空仓，产业补位"),
                "专家与产业同向，保留": link.reasons.count("专家与产业同向，保留"),
                "专家与产业冲突，轻决策改为 hold": link.reasons.count("专家与产业冲突，轻决策改为 hold"),
            },
            **_full_study(fused_trades, bars, link.symbol, hold),
            "signal": {
                "min_abs_delta": min_abs,
                "sections": len(link.samples),
                "sections_above_threshold": sum(1 for d in link.dirs_fused if d != 0),
                "trades_after_no_overlap": len(fused_trades),
            },
            "baselines": {
                "rule": _full_study(rule_trades, bars, link.symbol, hold),
                "expert": _full_study(expert_trades, bars, link.symbol, hold),
            },
        }

    verdicts = []
    for key, res in results.items():
        one = (res.get("cost_sensitivity") or {}).get("1x") or {}
        null = res.get("random_sign_null") or {}
        if not one.get("ok") or not null.get("ok"):
            continue
        verdicts.append(
            {
                "link": key,
                "role": res.get("role"),
                "n_trades": one["n_trades"],
                "cum_net_return": one["cum_net_return"],
                "sharpe_net": one["sharpe_net"],
                "percentile_vs_random": null["percentile_of_actual"],
                "beats_random_95": null["above_null_95"],
                "significantly_worse_than_random": null["below_null_05"],
                "net_positive": bool(one["cum_net_return"] > 0),
            }
        )

    report = {
        "task": "期货策略预计测试：专家 MLP + 产业轻决策的带成本回测",
        "test_years": list(test_years),
        "n_train_primary": packed.n_train_primary,
        "n_test": packed.n_test,
        "rule": {
            "direction": "官方决策=期货专家 MLP + 产业轻决策；旧规则 -sign(delta) 仅作对照",
            "threshold": f"专家 |score|≥{packed.tau}；产业带宽 {band}；旧规则 |delta|≥{min_abs}",
            "hold_days": hold,
            "overlap": "持仓期间不接新信号，成交不重叠",
            "entry": "信息日之后第一个交易日收盘",
            "fuse": "同意保留；冲突改 hold；专家空仓才允许产业补位",
        },
        "cost_model": {
            "unit": "双边基点",
            "by_symbol": COST_BPS,
            "derivation": "见 strategy.py 中 COST_BPS 注释：手续费 + 半个最小变动价位滑点",
            "reported_tiers": ["0x（无成本）", "1x（标称）", "2x（悲观）"],
        },
        "decision_rule": (
            "判定有没有边不看收益正负，看策略夏普是否落在随机符号零分布之外（分位 >0.95）。"
            "落在分布中间就说明方向选择没有贡献，盈亏来自标的自身波动。"
        ),
        "results": results,
        "verdicts": verdicts,
        "scoreboard": {
            "links_tested": len(verdicts),
            "net_positive": sum(1 for v in verdicts if v["net_positive"]),
            "beats_random_95": sum(1 for v in verdicts if v["beats_random_95"]),
            "worse_than_random_05": sum(
                1 for v in verdicts if v["significantly_worse_than_random"]
            ),
            "median_percentile_vs_random": (
                None
                if not verdicts
                else round(
                    statistics.median(v["percentile_vs_random"] for v in verdicts), 4
                )
            ),
        },
        "caveats": [
            "连续主力序列含换月跳空，真实可交易收益会更差",
            "未建模保证金、强平、涨跌停与流动性冲击",
            "单点锚点代表整个产区",
            "不重叠约束让成交数大幅下降，样本本身就不多",
            "因子来自 ERA5 再分析，与市场实时看到的预报不是同一信息集",
            "专家只学价格形态；产业层是轻规则，不是第二个深度模型",
            "测试年截面不进训练；安慰剂链不参与训练",
        ],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    return report


def to_markdown(report: dict) -> str:
    lines = ["# 期货策略预计测试", ""]
    rule = report["rule"]
    lines.append(f"- 方向：{rule['direction']}")
    lines.append(f"- 阈值：{rule['threshold']}")
    lines.append(f"- 持有：{rule['hold_days']} 个交易日，{rule['overlap']}")
    lines.append(f"- 建仓：{rule['entry']}")
    lines.append(f"- 成本：{report['cost_model']['derivation']}")
    lines.append(f"- 判定：{report['decision_rule']}")
    if report.get("test_years"):
        lines.append(f"- 测试年：{report['test_years']}；训练主链截面 {report.get('n_train_primary')}，测试截面 {report.get('n_test')}")
    lines.append("")
    lines.append("## 一、信号与成交（官方决策：专家+产业）")
    lines.append("")
    lines.append("| 链 | 角色 | 截面数 | 过阈值 | 不重叠成交 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        sig = res.get("signal")
        if not sig:
            continue
        lines.append(
            f"| {key} | {res.get('role')} | {sig['sections']} | "
            f"{sig['sections_above_threshold']} | {sig['trades_after_no_overlap']} |"
        )
    lines.append("")
    lines.append("## 二、成本敏感性（累计净收益 / 净夏普 / 净胜率）")
    lines.append("")
    lines.append("| 链 | 成交 | 0x 成本 | 1x 标称 | 2x 悲观 | 净夏普(1x) | 净胜率(1x) | 最大回撤(1x) |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        cs = res.get("cost_sensitivity") or {}
        one = cs.get("1x") or {}
        if not one.get("ok"):
            lines.append(f"| {key} | {one.get('n', 0)} | - | - | - | - | - | {one.get('reason', '')} |")
            continue
        lines.append(
            f"| {key} | {one['n_trades']} | {cs['0x']['cum_net_return']} | "
            f"{one['cum_net_return']} | {cs['2x']['cum_net_return']} | "
            f"{one['sharpe_net']} | {one['hit_rate_net']} | {one['max_drawdown_net']} |"
        )
    lines.append("")
    lines.append("## 三、随机符号零分布（决定性检验）")
    lines.append("")
    lines.append("| 链 | 策略净夏普 | 零分布中位 | 零分布 5% | 零分布 95% | 策略分位 | 上尾显著（有边） |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        nl = res.get("random_sign_null") or {}
        if not nl.get("ok"):
            continue
        verdict = "是" if nl["above_null_95"] else ("否（下尾显著变差）" if nl["below_null_05"] else "否")
        lines.append(
            f"| {key} | {nl['sharpe_actual']} | {nl['sharpe_null_median']} | "
            f"{nl['sharpe_null_p05']} | {nl['sharpe_null_p95']} | "
            f"{nl['percentile_of_actual']} | {verdict} |"
        )
    lines.append("")
    lines.append("## 四、对照：反向策略与单纯持有")
    lines.append("")
    lines.append("| 链 | 正向净收益 | 反向净收益 | 同区间单纯持有 |")
    lines.append("| --- | --- | --- | --- |")
    for key, res in report["results"].items():
        one = ((res.get("cost_sensitivity") or {}).get("1x")) or {}
        rev = res.get("reversed_strategy") or {}
        bh = res.get("buy_and_hold") or {}
        if not one.get("ok"):
            continue
        lines.append(
            f"| {key} | {one['cum_net_return']} | "
            f"{rev.get('cum_net_return', '-')} | {bh.get('cum_return', '-')} |"
        )
    sb = report["scoreboard"]
    lines.append("")
    lines.append("## 五、对照：同一测试窗上的旧规则与专家单独")
    lines.append("")
    lines.append("| 链 | 旧规则成交 | 旧规则夏普 | 专家成交 | 专家夏普 | 官方融合成交 | 官方融合夏普 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for key, res in report["results"].items():
        def _row(block: dict) -> tuple[object, object]:
            one = ((block.get("cost_sensitivity") or {}).get("1x")) or {}
            return one.get("n_trades", "—"), one.get("sharpe_net", "—")

        rn, rs = _row(res.get("baselines", {}).get("rule") or {})
        en, es = _row(res.get("baselines", {}).get("expert") or {})
        fn, fs = _row(res)
        lines.append(f"| {key} | {rn} | {rs} | {en} | {es} | {fn} | {fs} |")
    lines.append("")
    lines.append("## 六、计分板")
    lines.append("")
    lines.append(f"- 检验链数：{sb['links_tested']}")
    lines.append(f"- 净收益为正的：{sb['net_positive']}")
    lines.append(f"- **夏普超出随机零分布上尾 95%（真有边）的：{sb['beats_random_95']}**")
    lines.append(f"- 反而显著差于随机（下尾 5%）的：{sb['worse_than_random_05']}")
    lines.append(f"- 相对随机的分位中位数：{sb['median_percentile_vs_random']}")
    lines.append("")
    lines.append("## 七、口径限制")
    lines.append("")
    for c in report["caveats"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def run_with_table(**kwargs) -> dict[str, object]:
    report = run(**kwargs)
    md = MODEL_DIR / "strategy_table.md"
    md.write_text(to_markdown(report), encoding="utf-8")
    report["artifact_markdown"] = str(md)
    return report
