"""三种交易者：无干预 vs 有干预的模拟收益。

初学者：没有产量基线，无干预时生长季全程做多。
熟悉者：已有「沿用去年」的平均基线，无干预时空仓，不拿天气单。
尖端：开季就会按绝对胁迫（因子相对 1.0）定价，无干预时已经在场。

有干预：一律改走「相对当季开局」的 hold / long / short。
收益变化 = 有干预累计净收益 − 无干预累计净收益。
"""

from __future__ import annotations

import json
from collections import defaultdict

from .collect import ROOT
from .eventstudy import LAYOUT_SYMBOLS, Revision, build_revisions, load_bars
from .futures import FuturesBar
from .intervene import INTERVENE_BAND, decide_path, outlook_state, price_direction
from .strategy import COST_BPS, DEFAULT_COST_BPS, _bar_index, _px

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "persona_report.json"

PRIMARY = [
    (lid, sym, kind)
    for lid, pairs in LAYOUT_SYMBOLS.items()
    for sym, kind, role in pairs
    if role == "primary"
]


def decide_absolute(revisions: list[Revision], band: float) -> list[str]:
    """尖端无干预：相对因子=1 的绝对胁迫，开季就会进场。"""
    return [outlook_state(r.factor, 1.0, band) for r in sorted(revisions, key=lambda x: (x.year, x.as_of))]


def _segment_return(bars: list[FuturesBar], start: str, end: str, direction: int) -> float | None:
    if direction == 0:
        return 0.0
    i = _bar_index(bars, start)
    j = _bar_index(bars, end)
    if i is None:
        return None
    if j is None or j <= i:
        j = i + 1 if i + 1 < len(bars) else None
    if j is None:
        return None
    a, b = _px(bars[i]), _px(bars[j])
    if a <= 0 or b <= 0:
        return None
    return direction * (b / a - 1.0)


def path_pnl(
    revisions: list[Revision],
    states: list[str],
    bars: list[FuturesBar],
    cost_bps: float,
) -> dict[str, float]:
    """按截面状态走完一条季内路径。方向变化时扣一笔双边成本。"""
    ordered = sorted(revisions, key=lambda r: (r.year, r.as_of))
    if len(ordered) != len(states) or not ordered:
        return {"gross": 0.0, "net": 0.0, "n_turns": 0, "n_sections": 0}
    gross = 0.0
    turns = 0
    prev = "hold"
    for k, rev in enumerate(ordered):
        end = ordered[k + 1].as_of if k + 1 < len(ordered) and ordered[k + 1].year == rev.year else rev.as_of
        if k + 1 >= len(ordered) or ordered[k + 1].year != rev.year:
            # 最后一截：向后再找一个交易日，避免 start==end 丢掉
            pass
        ret = _segment_return(bars, rev.as_of, end, price_direction(states[k]))
        if ret is None:
            continue
        if states[k] != "hold" and states[k] != prev:
            turns += 1
        gross += ret
        prev = states[k] if states[k] != "hold" else prev
        if states[k] == "hold":
            prev = "hold"
    net = gross - turns * (cost_bps / 10000.0)
    return {"gross": round(gross, 5), "net": round(net, 5), "n_turns": turns, "n_sections": len(ordered)}


def persona_states(revisions: list[Revision], band: float) -> dict[str, list[str]]:
    overlay = [d.state for d in decide_path(revisions, band)]
    n = len(overlay)
    return {
        "beginner_off": ["long"] * n,
        "beginner_on": overlay,
        "familiar_off": ["hold"] * n,
        "familiar_on": overlay,
        "expert_off": decide_absolute(revisions, band),
        "expert_on": overlay,
    }


def run(band: float = INTERVENE_BAND) -> dict[str, object]:
    revisions = build_revisions()
    by_link: dict[str, dict] = {}
    acc = {
        "beginner": {"off": 0.0, "on": 0.0},
        "familiar": {"off": 0.0, "on": 0.0},
        "expert": {"off": 0.0, "on": 0.0},
    }
    for lid, symbol, kind in PRIMARY:
        revs = revisions.get(lid)
        if not revs:
            continue
        bars = load_bars(symbol, kind)
        cost = COST_BPS.get(symbol, DEFAULT_COST_BPS)
        states = persona_states(revs, band)
        row = {}
        for name, st in states.items():
            who, switch = name.split("_")
            pnl = path_pnl(revs, st, bars, cost)
            row[name] = pnl
            acc[who][switch] += pnl["net"]
        row["beginner_delta"] = round(row["beginner_on"]["net"] - row["beginner_off"]["net"], 5)
        row["familiar_delta"] = round(row["familiar_on"]["net"] - row["familiar_off"]["net"], 5)
        row["expert_delta"] = round(row["expert_on"]["net"] - row["expert_off"]["net"], 5)
        by_link[f"{lid}→{symbol}"] = row

    summary = {
        who: {
            "off": round(v["off"], 5),
            "on": round(v["on"], 5),
            "delta": round(v["on"] - v["off"], 5),
        }
        for who, v in acc.items()
    }
    report = {
        "task": "三种交易者：无干预 vs 有干预模拟净收益",
        "unit": "各主链净收益相加（不是组合复利），口径是合约价格涨跌 × 方向 − 换仓成本",
        "band": band,
        "definitions": {
            "beginner_off": "生长季全程做多，没有产量信息",
            "beginner_on": "改走开局相对干预（hold/long/short）",
            "familiar_off": "沿用去年，空仓，不拿天气单",
            "familiar_on": "只在季内相对开局越界时介入",
            "expert_off": "开季已按因子相对 1.0 进场（绝对胁迫）",
            "expert_on": "改为只交易相对开局的增量（与我们框架相同）",
        },
        "summary": summary,
        "by_link": by_link,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    return report
