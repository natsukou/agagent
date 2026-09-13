"""信息干预框架：在平均基线上决定是否介入，以及介入时做多还是做空。

产品不是每天报一个可交易点数。有知识的交易者已经有平均基线
（沿用去年产量预期，因子 = 1.0）。我们只在季内展望明显偏离这条基线时
给出一次干预建议，并配一条模拟决策：

    展望差于基线（factor ≤ 1 − band）→ 供给趋紧 → 做多
    展望好于基线（factor ≥ 1 + band）→ 供给趋松 → 做空
    落在带宽内 → 不干预，继续沿用基线

这和 `strategy.py` 的区别：那里每个 3 日截面只要 delta 超过 0.002 就翻仓，
是直接决策回测。这里按「展望相对基线的状态」合并成干预事件，
一次干旱年通常只有一段做多，而不是几十次短线。

模拟决策用来把信息翻译成可核对的方向，不是自动下单系统。
产量侧看干预方向是否与年末相对去年的惊喜同号；
价格侧看介入后到退出（或季末）的合约方向是否同号。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .collect import ROOT
from .eventstudy import LAYOUT_SYMBOLS, Revision, build_revisions, load_bars
from .futures import FuturesBar
from .strategy import COST_BPS, DEFAULT_COST_BPS, _bar_index, _px

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "intervene_report.json"

# 相对平均基线（factor=1）偏离多少才干预。3% 对应「正常人会改口的产量展望」。
INTERVENE_BAND = 0.03

# 产量惊喜：年末实测相对上年，超过这个比例才算「基线被打破」。
YIELD_SURPRISE_BAND = 0.015


@dataclass(frozen=True)
class Decision:
    """一条模拟决策。state 只能是 long / short / hold。"""

    as_of: str
    year: int
    progress: float
    factor: float
    state: str  # hold | long | short
    reason: str


def outlook_state(factor: float, baseline: float = 1.0, band: float = INTERVENE_BAND) -> str:
    """相对给定基线（默认当季开局展望）决定干预状态。"""
    gap = factor - baseline
    if gap <= -band:
        return "long"
    if gap >= band:
        return "short"
    return "hold"


def price_direction(state: str) -> int:
    """干预状态 → 模拟仓位。hold = 0，不持仓。"""
    if state == "long":
        return 1
    if state == "short":
        return -1
    return 0


def decide_path(revisions: list[Revision], band: float = INTERVENE_BAND) -> list[Decision]:
    """把逐日因子收成干预状态。基线是**当季第一条展望**，不是绝对的 1.0。

    知情交易者开季已经有一个产量预期。我们只在季内展望相对开局
    明显变差或变好时才干预；带宽内 hold。跨年重置基线。
    """
    out: list[Decision] = []
    open_by_year: dict[int, float] = {}
    for rev in sorted(revisions, key=lambda r: (r.year, r.as_of)):
        baseline = open_by_year.setdefault(rev.year, rev.factor)
        state = outlook_state(rev.factor, baseline, band)
        lo, hi = baseline - band, baseline + band
        if state == "hold":
            reason = (
                f"展望 {rev.factor:.3f} 相对开局 {baseline:.3f} "
                f"落在 [{lo:.3f}, {hi:.3f}]，不干预"
            )
        elif state == "long":
            reason = (
                f"展望 {rev.factor:.3f} 低于开局 {baseline:.3f}−{band:.2f}，"
                "季内恶化，减产预期，模拟做多"
            )
        else:
            reason = (
                f"展望 {rev.factor:.3f} 高于开局 {baseline:.3f}+{band:.2f}，"
                "季内好转，增产预期，模拟做空"
            )
        out.append(
            Decision(rev.as_of, rev.year, rev.progress, rev.factor, state, reason)
        )
    return out


@dataclass
class Episode:
    """一段连续的非 hold 状态 = 一次干预事件。"""

    year: int
    state: str
    start: str
    end: str
    factor_start: float
    factor_end: float
    n_sections: int


def episodes(decisions: list[Decision]) -> list[Episode]:
    evs: list[Episode] = []
    cur: list[Decision] = []
    for d in decisions:
        if d.state == "hold":
            if cur:
                evs.append(_close(cur))
                cur = []
            continue
        if cur and (cur[-1].state != d.state or cur[-1].year != d.year):
            evs.append(_close(cur))
            cur = [d]
        else:
            cur.append(d)
    if cur:
        evs.append(_close(cur))
    return evs


def _close(cur: list[Decision]) -> Episode:
    a, b = cur[0], cur[-1]
    return Episode(a.year, a.state, a.as_of, b.as_of, a.factor, b.factor, len(cur))


def simulate_episode(ep: Episode, bars: list[FuturesBar], cost_bps: float) -> dict[str, object]:
    """介入后第一个交易日建仓，退出日（状态结束）之后第一个交易日平仓。"""
    i = _bar_index(bars, ep.start)
    j = _bar_index(bars, ep.end)
    if i is None:
        return {"ok": False, "reason": "找不到介入后的交易日"}
    if j is None or j <= i:
        j = i + 1 if i + 1 < len(bars) else None
    if j is None:
        return {"ok": False, "reason": "找不到退出后的交易日"}
    entry, exit_ = _px(bars[i]), _px(bars[j])
    if entry <= 0 or exit_ <= 0:
        return {"ok": False, "reason": "价格无效"}
    raw = exit_ / entry - 1.0
    d = price_direction(ep.state)
    net = d * raw - cost_bps / 10000.0
    return {
        "ok": True,
        "entry_date": bars[i].date,
        "exit_date": bars[j].date,
        "direction": d,
        "price_move": round(raw, 5),
        "signed_gross": round(d * raw, 5),
        "signed_net": round(net, 5),
        "price_agree": bool(d * raw > 0),
    }


def load_year_surprise() -> dict[tuple[str, int], float]:
    """年末实测相对上年。正数 = 实际好于去年。"""
    from .multicrop import NEW_CROPS, build_samples

    groups = build_samples(NEW_CROPS)
    out: dict[tuple[str, int], float] = {}
    for (crop, country), samples in groups.items():
        by_year = {s.year: s for s in samples}
        for year, s in by_year.items():
            lag = s.features[-1]
            if lag:
                out[(f"{crop}/{country}", year)] = (s.y - lag) / abs(lag)
    return out


LAYOUT_YIELD_KEY = {
    "cotton.xinjiang": "棉花/CHN",
    "cotton.us.belt": "棉花/USA",
    "soy.us.belt": "大豆/USA",
    "coffee.br.arabica": "咖啡/BRA",
}


def yield_agree(ep: Episode, layout_id: str, surprise: dict[tuple[str, int], float]) -> dict[str, object]:
    key = LAYOUT_YIELD_KEY.get(layout_id)
    if key is None:
        return {"ok": False, "reason": "该布局没有年末产量标签"}
    sur = surprise.get((key, ep.year))
    if sur is None:
        return {"ok": False, "reason": "缺该年产量标签"}
    implied = -1 if ep.state == "long" else 1  # 做多 = 预期减产 = 惊喜应为负
    material = abs(sur) >= YIELD_SURPRISE_BAND
    return {
        "ok": True,
        "yield_key": key,
        "surprise": round(sur, 4),
        "material": material,
        "yield_agree": bool(implied * sur > 0) if material else None,
        "note": "惊喜不显著时不评分，基线本就够用" if not material else "",
    }


def study_link(
    layout_id: str,
    revisions: list[Revision],
    symbol: str,
    kind: str,
    role: str,
    surprise: dict[tuple[str, int], float],
    band: float,
) -> dict[str, object]:
    decs = decide_path(revisions, band)
    evs = [e for e in episodes(decs) if e.n_sections >= 2]
    bars = load_bars(symbol, kind)
    cost = COST_BPS.get(symbol, DEFAULT_COST_BPS)
    n_hold = sum(1 for d in decs if d.state == "hold")
    n_long = sum(1 for d in decs if d.state == "long")
    n_short = sum(1 for d in decs if d.state == "short")

    priced = []
    ylds = []
    for ep in evs:
        sim = simulate_episode(ep, bars, cost) if bars else {"ok": False, "reason": "无行情"}
        yld = yield_agree(ep, layout_id, surprise)
        priced.append({"episode": ep.__dict__, "price": sim, "yield": yld})
        if sim.get("ok"):
            ylds.append(sim)

    y_scored = [p["yield"] for p in priced if p["yield"].get("ok") and p["yield"].get("yield_agree") is not None]
    p_scored = [p["price"] for p in priced if p["price"].get("ok")]

    return {
        "layout": layout_id,
        "symbol": symbol,
        "role": role,
        "band": band,
        "sections": len(decs),
        "hold_share": round(n_hold / len(decs), 4) if decs else None,
        "long_share": round(n_long / len(decs), 4) if decs else None,
        "short_share": round(n_short / len(decs), 4) if decs else None,
        "n_episodes": len(evs),
        "n_long_episodes": sum(1 for e in evs if e.state == "long"),
        "n_short_episodes": sum(1 for e in evs if e.state == "short"),
        "yield_scored": len(y_scored),
        "yield_agree_n": sum(1 for y in y_scored if y["yield_agree"]),
        "price_scored": len(p_scored),
        "price_agree_n": sum(1 for p in p_scored if p["price_agree"]),
        "sim_cum_net": round(sum(float(p["signed_net"]) for p in p_scored), 5) if p_scored else None,
        "episodes": priced,
    }


def run(band: float = INTERVENE_BAND, layout_ids: tuple[str, ...] | None = None) -> dict[str, object]:
    revisions = build_revisions(layout_ids)
    surprise = load_year_surprise()
    results: dict[str, object] = {}
    for lid, revs in revisions.items():
        for symbol, kind, role in LAYOUT_SYMBOLS.get(lid, ()):
            if role != "primary":
                continue
            results[f"{lid}→{symbol}"] = study_link(
                lid, revs, symbol, kind, role, surprise, band
            )

    prim = [r for r in results.values() if isinstance(r, dict)]
    y_n = sum(int(r["yield_scored"]) for r in prim)
    y_ok = sum(int(r["yield_agree_n"]) for r in prim)
    p_n = sum(int(r["price_scored"]) for r in prim)
    p_ok = sum(int(r["price_agree_n"]) for r in prim)

    report = {
        "task": "信息干预框架：在平均基线上决定不干预 / 做多 / 做空",
        "framework": {
            "baseline": "当季第一条物候展望 = 开局基线。知情交易者开季已有产量预期，开局当天不干预",
            "band": band,
            "long": f"factor ≤ 开局 − {band:.2f} → 季内恶化 → 减产预期 → 模拟做多",
            "short": f"factor ≥ 开局 + {band:.2f} → 季内好转 → 增产预期 → 模拟做空",
            "hold": "相对开局落在带宽内则不干预、不持仓",
            "episode": "连续同一状态合并为一次干预，不是每个 3 日截面翻仓",
            "not": "不是自动交易系统；模拟决策只用来核对方向",
        },
        "results": results,
        "scoreboard": {
            "primary_links": len(prim),
            "episodes": sum(int(r["n_episodes"]) for r in prim),
            "yield_direction_hit": f"{y_ok}/{y_n}" if y_n else "无显著惊喜可评",
            "price_direction_hit": f"{p_ok}/{p_n}" if p_n else "无",
            "yield_hit_rate": round(y_ok / y_n, 4) if y_n else None,
            "price_hit_rate": round(p_ok / p_n, 4) if p_n else None,
        },
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    return report


def to_markdown(report: dict) -> str:
    fw = report["framework"]
    sb = report["scoreboard"]
    lines = [
        "# 信息干预框架与模拟决策",
        "",
        f"- 平均基线：{fw['baseline']}",
        f"- 做多：{fw['long']}",
        f"- 做空：{fw['short']}",
        f"- 不干预：{fw['hold']}",
        f"- 事件：{fw['episode']}",
        f"- {fw['not']}",
        "",
        "## 计分（只评干预事件，不评 hold）",
        "",
        f"- 主链数：{sb['primary_links']}",
        f"- 干预事件：{sb['episodes']}",
        f"- 产量方向命中：{sb['yield_direction_hit']}",
        f"- 价格方向命中：{sb['price_direction_hit']}",
        "",
        "| 链 | 截面 | hold占比 | 做多占比 | 做空占比 | 事件 | 产量同号 | 价格同号 | 模拟净收益加总 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, r in report["results"].items():
        if not isinstance(r, dict):
            continue
        y = f"{r['yield_agree_n']}/{r['yield_scored']}"
        p = f"{r['price_agree_n']}/{r['price_scored']}"
        lines.append(
            f"| {key} | {r['sections']} | {r['hold_share']} | {r['long_share']} | "
            f"{r['short_share']} | {r['n_episodes']} | {y} | {p} | {r['sim_cum_net']} |"
        )
    lines.append("")
    lines.append("## 事件明细（主链）")
    lines.append("")
    lines.append("| 链 | 年 | 状态 | 起 | 止 | 产量惊喜 | 产量同号 | 价格变动 | 价格同号 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for key, r in report["results"].items():
        if not isinstance(r, dict):
            continue
        for item in r.get("episodes") or []:
            ep = item["episode"]
            yld = item["yield"]
            pr = item["price"]
            ys = yld.get("surprise", "—")
            ya = yld.get("yield_agree")
            ya_s = "—" if ya is None else ("是" if ya else "否")
            pm = pr.get("price_move", "—")
            pa = "是" if pr.get("price_agree") else ("否" if pr.get("ok") else pr.get("reason", "—"))
            lines.append(
                f"| {key} | {ep['year']} | {ep['state']} | {ep['start']} | {ep['end']} | "
                f"{ys} | {ya_s} | {pm} | {pa} |"
            )
    lines.append("")
    return "\n".join(lines)


def run_with_table(band: float = INTERVENE_BAND) -> dict[str, object]:
    report = run(band=band)
    md = MODEL_DIR / "intervene_table.md"
    md.write_text(to_markdown(report), encoding="utf-8")
    report["artifact_markdown"] = str(md)
    return report
