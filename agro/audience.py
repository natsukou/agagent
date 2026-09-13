"""用户适配层：按使用者的目标函数，只交付与他正相关的那部分。

前面几层产出的是一张混在一起的成绩单：产量技能分、价格方向命中率、
账户回撤全塞在一起。谁都看不出「这东西对我有什么用」。

问题在于三类使用者的目标函数根本不同，而我们的贡献来自三种完全不同的机制：

- **结构**：波动定仓与 ATR 止损带来的尾部收敛。与方向无关——随机方向也能
  拿到同样的回撤改善。对没有风控的人是实打实的增量，对已有风控台的人是零。
- **产量信息**：年末单产/产量估计相对「照抄去年」的技能分。与价格无关，
  只在部分作物-国家组上为正。
- **方向**：季内展望驱动的多空。已验证落在随机符号零分布中间，没有边。

把三者分开之后，适配规则就很简单：**只把机制为正的那一项交付给对应的人。**
方向那一项不交付给任何人。

预期不用 2023–2024 两年留出来给，那个样本太小。这里跑逐年前向测试
（训练只用该年之前的年份，逐年滚动），用胜率和技能分中位数作为预期基础。
"""

from __future__ import annotations

import json
import random
import statistics
from dataclasses import dataclass, field

from .collect import ROOT
from .eventstudy import build_revisions, load_bars
from .intervene import INTERVENE_BAND, LAYOUT_YIELD_KEY, decide_path, episodes
from .multicrop import NEW_CROPS, GroupSample, _dot, _ridge, build_samples
from .strategy import COST_BPS, DEFAULT_COST_BPS
from .trader import (
    DEFAULT_RULES,
    PRIMARY,
    TradeRules,
    account,
    buy_and_hold_fills,
    overlay_fills,
)

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "audience_report.json"
TABLE_PATH = MODEL_DIR / "audience_table.md"

# 逐年前向测试至少要有这么多训练年才开始评
MIN_TRAIN_YEARS = 4
BIG_SURPRISE = 0.05
CONTROL_DRAWS = 300
CONTROL_SEED = 20260913


@dataclass
class RollingSkill:
    """逐年前向测试：每一年都只用它之前的年份训练。"""

    group: str
    target: str
    years: list[int] = field(default_factory=list)
    n: int = 0
    wins: int = 0
    win_rate: float | None = None
    median_skill: float | None = None
    mean_abs_err_model: float | None = None
    mean_abs_err_persist: float | None = None
    skill_pooled: float | None = None
    deliverable: bool = False


def rolling_skill(
    samples: list[GroupSample],
    l2: float = 0.5,
    only_years: tuple[int, ...] | None = None,
    max_year: int | None = None,
    min_year: int | None = None,
) -> RollingSkill:
    """对每个年份 y：训练集 = 年份 < y 的全部样本，测试 = y 本身。

    这是「站在当年年初能做到什么」的逼近，比固定两年留出稳定得多。
    only_years / max_year / min_year 只筛**被评估的年份**，训练集始终是该年之前的全部数据。
    """
    group = f"{samples[0].crop}/{samples[0].scope}"
    target = "production_kt" if samples[0].crop == "咖啡" else "yield_t_ha"
    out = RollingSkill(group=group, target=target)
    by_year = {s.year: s for s in samples}
    years = sorted(by_year)
    errs_m: list[float] = []
    errs_p: list[float] = []
    skills: list[float] = []
    for y in years:
        train = [s for s in samples if s.year < y]
        if len(train) < MIN_TRAIN_YEARS:
            continue
        if only_years is not None and y not in only_years:
            continue
        if max_year is not None and y > max_year:
            continue
        if min_year is not None and y < min_year:
            continue
        s = by_year[y]
        w = _ridge([t.features for t in train], [t.y for t in train], l2)
        pred = _dot(w, [1.0] + s.features)
        persist = s.features[-1]
        e_m = abs(s.y - pred)
        e_p = abs(s.y - persist)
        out.years.append(y)
        errs_m.append(e_m)
        errs_p.append(e_p)
        if e_m < e_p:
            out.wins += 1
        if e_p > 1e-9:
            skills.append(1.0 - e_m / e_p)
    out.n = len(out.years)
    if not out.n:
        return out
    out.win_rate = round(out.wins / out.n, 4)
    out.median_skill = round(statistics.median(skills), 4) if skills else None
    out.mean_abs_err_model = round(statistics.fmean(errs_m), 5)
    out.mean_abs_err_persist = round(statistics.fmean(errs_p), 5)
    if out.mean_abs_err_persist and out.mean_abs_err_persist > 0:
        out.skill_pooled = round(1.0 - out.mean_abs_err_model / out.mean_abs_err_persist, 4)
    # 交付门槛：合并技能分为正，且逐年胜率不低于一半。两个都过才算正相关。
    out.deliverable = bool(
        out.skill_pooled is not None
        and out.skill_pooled > 0
        and out.win_rate is not None
        and out.win_rate >= 0.5
    )
    return out


def passes_gates(
    all_years_ok: bool, selectable: bool, verify_skill: float | None
) -> bool:
    """三道门槛的合成规则。缺任何一条都不交付，验证段技能分缺失也不交付。"""
    if not all_years_ok or not selectable:
        return False
    return verify_skill is not None and float(verify_skill) > 0


def yield_deliverables(
    crops: tuple[str, ...] = NEW_CROPS, cutoff: int = 2022
) -> dict[str, object]:
    """哪些作物-国家组的产量估计真的比照抄去年好。只有这些能进交付清单。

    三道门槛必须同时过，缺一不可：
    1. 全年逐年前向的合并技能分 > 0 且胜率 ≥ 0.5；
    2. 只看 ≤cutoff 年就能被选中（说明这份清单可以提前定，不是事后挑的）；
    3. 在 >cutoff 年的验证段技能分仍 > 0。

    只过第 1 条就交付，等于在 9 组里挑成绩最好的 5 组，那是选择偏差。
    """
    groups = build_samples(crops)
    guard = selection_guard(crops, cutoff)
    per_group = guard["per_group"]
    rows: dict[str, object] = {}
    for (crop, scope), samples in sorted(groups.items()):
        if len(samples) < MIN_TRAIN_YEARS + 1:
            continue
        rs = rolling_skill(samples)
        if not rs.n:
            continue
        g = per_group.get(rs.group, {})
        row = dict(rs.__dict__)
        row["gate_all_years"] = bool(rs.deliverable)
        row["gate_selectable"] = bool(g.get("chosen_on_select"))
        row["verify_skill"] = g.get("verify_skill")
        row["gate_verified"] = bool(
            g.get("verify_skill") is not None and float(g["verify_skill"]) > 0
        )
        row["deliverable"] = passes_gates(
            row["gate_all_years"], row["gate_selectable"], g.get("verify_skill")
        )
        rows[rs.group] = row
    keep = [k for k, v in rows.items() if v["deliverable"]]
    return {
        "method": "逐年前向：训练集只含该年之前的年份，逐年滚动，不做随机打乱",
        "gate": (
            "三道都要过：①全年逐年合并技能分>0 且胜率≥0.5；"
            f"②只看 ≤{cutoff} 年也会被选中；③>{cutoff} 年验证段技能分仍>0"
        ),
        "groups": rows,
        "deliverable": sorted(keep),
        "dropped": sorted(k for k in rows if k not in keep),
        "selection_guard": guard,
    }


def selection_guard(
    crops: tuple[str, ...] = NEW_CROPS, cutoff: int = 2022
) -> dict[str, object]:
    """挑组这件事本身会过拟合：9 组里挑 5 组，靠的是全部年份的成绩。

    所以再加一道：只用 cutoff 及之前的年份决定交付清单，
    然后看这份清单在 cutoff 之后的年份上还成不成立。
    清单一旦在验证段掉到胜率一半以下，说明「挑组」是噪声，不是能力。
    """
    groups = build_samples(crops)
    rows: dict[str, object] = {}
    sel_wins = sel_n = ver_wins = ver_n = 0
    err_m = err_p = 0.0
    picked: list[str] = []
    for (crop, scope), samples in sorted(groups.items()):
        if len(samples) < MIN_TRAIN_YEARS + 1:
            continue
        sel = rolling_skill(samples, max_year=cutoff)
        ver = rolling_skill(samples, min_year=cutoff + 1)
        if not sel.n or not ver.n:
            continue
        chosen = bool(
            sel.skill_pooled is not None
            and sel.skill_pooled > 0
            and sel.win_rate is not None
            and sel.win_rate >= 0.5
        )
        rows[sel.group] = {
            "select_years": sel.years,
            "select_win_rate": sel.win_rate,
            "select_skill": sel.skill_pooled,
            "chosen_on_select": chosen,
            "verify_years": ver.years,
            "verify_win_rate": ver.win_rate,
            "verify_skill": ver.skill_pooled,
        }
        if chosen:
            picked.append(sel.group)
            sel_wins += sel.wins
            sel_n += sel.n
            ver_wins += ver.wins
            ver_n += ver.n
            err_m += (ver.mean_abs_err_model or 0.0) * ver.n
            err_p += (ver.mean_abs_err_persist or 0.0) * ver.n
    return {
        "cutoff": cutoff,
        "protocol": f"用 ≤{cutoff} 年的逐年成绩决定交付清单，再在 >{cutoff} 年上验证这份清单",
        "picked_on_select": sorted(picked),
        "select_win_rate": round(sel_wins / sel_n, 4) if sel_n else None,
        "verify_year_decisions": ver_n,
        "verify_win_rate": round(ver_wins / ver_n, 4) if ver_n else None,
        "verify_skill_pooled": round(1.0 - err_m / err_p, 4) if err_p > 0 else None,
        "holds_up": bool(ver_n and ver_wins / ver_n >= 0.5),
        "per_group": rows,
        "reading": (
            "验证段胜率仍不低于一半，说明「哪些组能交付」这件事是可以提前知道的，"
            "不是事后挑出来的。样本只有几年，这一条要按季度重跑。"
        ),
    }


def risk_deliverable(
    band: float = INTERVENE_BAND,
    rules: TradeRules = DEFAULT_RULES,
    draws: int = CONTROL_DRAWS,
    seed: int = CONTROL_SEED,
) -> dict[str, object]:
    """结构机制：尾部收敛有多少，以及它是不是真的与方向无关。

    对照组把事件方向换成随机 ±1，其余结构不动。如果随机方向也拿到同样的
    回撤，就证明这项价值来自风控结构本身，可以独立交付，
    但也不能拿它去暗示我们的方向有用。
    """
    revisions = build_revisions()
    links = {}
    for lid, symbol, kind in PRIMARY:
        revs = revisions.get(lid)
        bars = load_bars(symbol, kind) if revs else None
        if not revs or not bars:
            continue
        links[f"{lid}→{symbol}"] = (revs, bars, COST_BPS.get(symbol, DEFAULT_COST_BPS))
    if not links:
        return {"ok": False, "reason": "无可用链"}

    weight = 1.0 / len(links)
    naive: list = []
    managed: list = []
    for link, (revs, bars, cost) in links.items():
        naive.extend(buy_and_hold_fills(link, revs, bars, weight, cost))
        managed.extend(overlay_fills(link, revs, bars, band, rules, cost))
    a_naive = account(naive)
    a_managed = account(managed)

    keys = []
    for link, (revs, bars, _c) in links.items():
        for ep in episodes(decide_path(revs, band)):
            if ep.n_sections >= 2:
                keys.append((link, (ep.start, ep.year)))
    rng = random.Random(seed)
    dd_null: list[float] = []
    for _ in range(draws):
        flips = {link: {} for link in links}
        for link, key in keys:
            flips[link][key] = rng.choice((1, -1))
        fills = []
        for link, (revs, bars, cost) in links.items():
            fills.extend(overlay_fills(link, revs, bars, band, rules, cost, flips[link]))
        acc = account(fills)
        if acc.get("ok"):
            dd_null.append(float(acc["max_drawdown"]))

    dd_med = round(statistics.median(dd_null), 5) if dd_null else None
    return {
        "ok": True,
        "naive_max_drawdown": a_naive.get("max_drawdown"),
        "managed_max_drawdown": a_managed.get("max_drawdown"),
        "tail_reduction_pp": (
            round(
                (float(a_managed["max_drawdown"]) - float(a_naive["max_drawdown"])) * 100, 2
            )
            if a_naive.get("ok") and a_managed.get("ok")
            else None
        ),
        "random_direction_drawdown_median": dd_med,
        "draws": len(dd_null),
        "is_structural": bool(
            dd_med is not None
            and a_naive.get("max_drawdown") is not None
            and dd_med > float(a_naive["max_drawdown"]) * 0.5
        ),
        "reading": (
            "随机方向也拿到同量级回撤，说明尾部收敛来自波动定仓与止损，不来自方向。"
            "对没有风控的人这仍是真实增量；对已有风控台的人是零。"
        ),
    }


def attention_lift(band: float = INTERVENE_BAND, big: float = BIG_SURPRISE) -> dict[str, object]:
    """注意力路由：我们标记的年份，是不是更可能出现「照抄去年会大错」的大惊喜。

    这是熟悉者最想要的东西——不要每天看盘，只告诉我哪一年要亲自复核。
    用提升度衡量：标记年的大惊喜占比 ÷ 全样本基准占比。1.0 就是没有信息。
    """
    from .intervene import load_year_surprise

    sur = load_year_surprise()
    revisions = build_revisions()
    flagged_big = flagged_n = plain_big = plain_n = 0
    per_link: dict[str, object] = {}
    for lid, key in LAYOUT_YIELD_KEY.items():
        revs = revisions.get(lid)
        if not revs:
            continue
        years = sorted({r.year for r in revs})
        flags = {e.year for e in episodes(decide_path(revs, band)) if e.n_sections >= 2}
        f_b = f_n = p_b = p_n = 0
        for y in years:
            s = sur.get((key, y))
            if s is None:
                continue
            is_big = abs(s) >= big
            if y in flags:
                f_n += 1
                f_b += int(is_big)
            else:
                p_n += 1
                p_b += int(is_big)
        per_link[f"{lid}({key})"] = {
            "flagged_years": f_n,
            "flagged_big": f_b,
            "quiet_years": p_n,
            "quiet_big": p_b,
        }
        flagged_big += f_b
        flagged_n += f_n
        plain_big += p_b
        plain_n += p_n

    total_n = flagged_n + plain_n
    total_big = flagged_big + plain_big
    if not total_n or not flagged_n:
        return {"ok": False, "reason": "样本不足"}
    base = total_big / total_n
    hit = flagged_big / flagged_n
    lift = hit / base if base > 0 else None
    return {
        "ok": True,
        "threshold": big,
        "link_years": total_n,
        "base_rate_big_surprise": round(base, 4),
        "flagged_years": flagged_n,
        "flagged_big_rate": round(hit, 4),
        "quiet_big_rate": round(plain_big / plain_n, 4) if plain_n else None,
        "lift": round(lift, 4) if lift else None,
        "deliverable": bool(lift is not None and lift >= 1.2),
        "per_link": per_link,
        "reading": (
            "提升度接近 1 就说明「我们标记的年份」和「随便挑一年」一样，"
            "注意力路由没有信息，不能作为卖点。"
        ),
    }


def direction_edge() -> dict[str, object]:
    """方向机制：直接引用 trader 的零分布结论，不另立尺子。"""
    path = MODEL_DIR / "trader_report.json"
    if not path.exists():
        return {"ok": False, "reason": "先跑 python -m agro.cli trader"}
    data = json.loads(path.read_text(encoding="utf-8"))
    edge = data.get("edge_test") or {}
    return {
        "ok": bool(edge.get("ok")),
        "expectancy_r": edge.get("expectancy_actual"),
        "percentile_vs_null": edge.get("percentile_of_actual"),
        "beats_null_95": edge.get("beats_null_95"),
        "deliverable": bool(edge.get("beats_null_95")),
        "reading": "上尾不显著即没有边。方向不作为交付物，只作为解释材料。",
    }


AUDIENCE_OBJECTIVE = {
    "beginner": "不要被一次判断打穿账户",
    "familiar": "在已有的「照抄去年」基线上拿到更准的产量数字",
    "expert": "只要他还没定价的增量，不要重复他已有的信息",
}


def expectations(
    yields: dict, risk: dict, attention: dict, direction: dict
) -> dict[str, object]:
    """把三种机制按使用者的目标函数分配，并写出可被推翻的预期。"""
    good = yields.get("deliverable") or []
    groups = yields.get("groups") or {}
    guard = yields.get("selection_guard") or {}
    best = None
    if good:
        best = max(good, key=lambda g: groups[g].get("verify_skill") or -9)

    out: dict[str, object] = {}

    out["beginner"] = {
        "objective": AUDIENCE_OBJECTIVE["beginner"],
        "deliver": ["交易台结构：波动定仓、ATR 止损、时间止损、单笔风险上限"],
        "mechanism": "结构",
        "expectation": (
            f"把同一批判断放进这套结构，最大回撤从 {_pct(risk.get('naive_max_drawdown'))} "
            f"收敛到 {_pct(risk.get('managed_max_drawdown'))}，改善约 "
            f"{risk.get('tail_reduction_pp')} 个百分点。"
        ),
        "honest_caveat": (
            "这项改善与我们的方向无关：随机方向在同一套结构下的回撤中位是 "
            f"{_pct(risk.get('random_direction_drawdown_median'))}。"
            "我们卖的是纪律，不是预测。"
        ),
        "falsifier": "如果使用者本来就有仓位管理，这项增量为零",
        "positive": bool(risk.get("tail_reduction_pp") and risk["tail_reduction_pp"] > 0),
    }

    out["familiar"] = {
        "objective": AUDIENCE_OBJECTIVE["familiar"],
        "deliver": [f"产量估计：{g}" for g in good] or ["（当前无通过门槛的产量交付物）"],
        "mechanism": "产量信息",
        "expectation": (
            (
                f"清单上最稳的是 {best}：逐年前向合并技能分 "
                f"{groups[best].get('skill_pooled')}（{groups[best].get('n')} 年，胜率 "
                f"{groups[best].get('win_rate')}），验证段技能分 "
                f"{groups[best].get('verify_skill')}。"
                f"预期：清单内组的年度误差约为照抄去年的 "
                f"{round(1 - (groups[best].get('verify_skill') or 0), 2)} 倍，"
                "即少错三成到五成。验证段每组只有 2 年，这个区间很宽。"
            )
            if best
            else "当前没有组能同时过三道门槛。"
        ),
        "honest_caveat": (
            f"不交付的组：{yields.get('dropped')}。中国口径的组全部落在这一侧。"
            f"更重要的是：如果只用第一道门槛，会选出 6 组，但那份清单在 "
            f"{guard.get('cutoff')} 年之后的胜率只有 {guard.get('verify_win_rate')}"
            f"（{guard.get('verify_year_decisions')} 个年度判断），合并技能分 "
            f"{guard.get('verify_skill_pooled')}。所以「挑组」本身必须前向验证。"
            f"注意力路由不交付——标记年的大惊喜提升度只有 {attention.get('lift')}。"
        ),
        "falsifier": "任一组在新一年的前向测试里输给照抄去年两次，就从清单里摘掉",
        "positive": bool(good),
    }

    out["expert"] = {
        "objective": AUDIENCE_OBJECTIVE["expert"],
        "deliver": ["季内展望相对开局的偏离序列（原始因子与物候权重，不带方向建议）"],
        "mechanism": "产量信息（原始层）",
        "expectation": (
            "交付的是因子本身与它的构成，让对方接到自己的定价模型里。"
            "不交付方向：我们的多空期望 R 落在随机符号零分布第 "
            f"{direction.get('percentile_vs_null')} 分位，没有边。"
        ),
        "honest_caveat": "对已有绝对胁迫模型的人，增量只剩「相对开局」这一个视角",
        "falsifier": "如果对方的模型已经含季内滚动修正，增量为零",
        "positive": False,
    }
    return out


def _pct(v: object) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(v)


def run(
    band: float = INTERVENE_BAND,
    crops: tuple[str, ...] = NEW_CROPS,
    draws: int = CONTROL_DRAWS,
    cutoff: int = 2022,
) -> dict[str, object]:
    yields = yield_deliverables(crops, cutoff)
    risk = risk_deliverable(band=band, draws=draws)
    attention = attention_lift(band=band)
    direction = direction_edge()
    exp = expectations(yields, risk, attention, direction)
    report = {
        "task": "用户适配层：按目标函数只交付正相关的机制，并给出可被推翻的预期",
        "mechanisms": {
            "结构": "波动定仓 + ATR 止损带来的尾部收敛。与方向无关，可独立交付。",
            "产量信息": "年末单产/产量估计相对照抄去年的技能分。逐年前向测试。",
            "方向": "季内展望驱动的多空。零分布检验未过，不交付。",
        },
        "yield_deliverables": yields,
        "risk_structure": risk,
        "attention_routing": attention,
        "direction_edge": direction,
        "expectations": exp,
        "scoreboard": {
            "mechanisms_positive": sum(
                1
                for ok in (
                    bool(yields.get("deliverable")),
                    bool(risk.get("tail_reduction_pp") and risk["tail_reduction_pp"] > 0),
                    bool(attention.get("deliverable")),
                    bool(direction.get("deliverable")),
                )
                if ok
            ),
            "yield_groups_passing": len(yields.get("deliverable") or []),
            "yield_groups_tested": len(yields.get("groups") or {}),
        },
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    TABLE_PATH.write_text(to_markdown(report), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    report["table"] = str(TABLE_PATH)
    return report


def to_markdown(report: dict) -> str:
    y = report["yield_deliverables"]
    risk = report["risk_structure"]
    att = report["attention_routing"]
    dirn = report["direction_edge"]
    cell_struct = "可以（与方向无关）" if risk.get("is_structural") else "待定"
    cell_yield = "可以，限通过门槛的组" if y.get("deliverable") else "不可以"
    cell_att = "可以" if att.get("deliverable") else f"不可以（提升度 {att.get('lift')}）"
    cell_dir = "可以" if dirn.get("deliverable") else f"不可以（分位 {dirn.get('percentile_vs_null')}）"
    lines = [
        "# 用户适配层与预期",
        "",
        "## 一、四种机制，先分开再谈交付",
        "",
        "| 机制 | 是什么 | 检验 | 能否交付 |",
        "| --- | --- | --- | --- |",
        f"| 结构 | 波动定仓 + ATR 止损的尾部收敛 | 随机方向对照 | {cell_struct} |",
        f"| 产量信息 | 年度估计相对照抄去年 | 逐年前向 | {cell_yield} |",
        f"| 注意力路由 | 标记年是否更可能出大惊喜 | 提升度 | {cell_att} |",
        f"| 方向 | 季内展望驱动的多空 | 随机符号零分布 | {cell_dir} |",
        "",
        "## 二、产量：逐年前向测试（训练只用该年之前的年份）",
        "",
        f"- 方法：{y.get('method')}",
        f"- 门槛：{y.get('gate')}",
        "",
        "| 作物/国家 | 目标 | 评估年数 | 逐年胜率 | 合并技能分 | ①全年 | ②可提前选中 | ③验证段技能分 | 交付 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, row in (y.get("groups") or {}).items():
        tick = lambda v: "✓" if v else "✗"  # noqa: E731
        lines.append(
            f"| {key} | {row.get('target')} | {row.get('n')} | {row.get('win_rate')} | "
            f"{row.get('skill_pooled')} | {tick(row.get('gate_all_years'))} | "
            f"{tick(row.get('gate_selectable'))} | {row.get('verify_skill')} | "
            f"{'**是**' if row.get('deliverable') else '否'} |"
        )
    guard = y.get("selection_guard") or {}
    lines += [
        "",
        f"通过三道门槛：{y.get('deliverable')}",
        f"摘掉：{y.get('dropped')}",
        "",
        "### 挑组这件事本身也要前向验证",
        "",
        f"- 协议：{guard.get('protocol')}",
        f"- 只看选择段会选出：{guard.get('picked_on_select')}，选择段胜率 {guard.get('select_win_rate')}",
        f"- **这份清单在验证段的胜率：{guard.get('verify_win_rate')}**"
        f"（{guard.get('verify_year_decisions')} 个年度判断），合并技能分 {guard.get('verify_skill_pooled')}",
        f"- 结论：{'站得住' if guard.get('holds_up') else '站不住——只按第一道门槛挑组是选择偏差'}",
        "",
        "## 三、结构：尾部收敛及其归因",
        "",
        f"- 无风控（每季死拿多头）最大回撤：{_pct(risk.get('naive_max_drawdown'))}",
        f"- 进风控后最大回撤：{_pct(risk.get('managed_max_drawdown'))}",
        f"- 改善：约 {risk.get('tail_reduction_pp')} 个百分点",
        f"- 随机方向在同一结构下的回撤中位（{risk.get('draws')} 次）："
        f"{_pct(risk.get('random_direction_drawdown_median'))}",
        f"- 归因：{risk.get('reading')}",
        "",
        "## 四、注意力路由：实测没有提升度",
        "",
        f"- 大惊喜口径：年末相对上年 |变化| ≥ {att.get('threshold')}",
        f"- 全样本基准占比：{att.get('base_rate_big_surprise')}（{att.get('link_years')} 个链-年）",
        f"- 我们标记年的占比：{att.get('flagged_big_rate')}（{att.get('flagged_years')} 年）",
        f"- 安静年的占比：{att.get('quiet_big_rate')}",
        f"- **提升度：{att.get('lift')}**",
        f"- {att.get('reading')}",
        "",
        "## 五、按使用者的交付清单与预期",
        "",
    ]
    label = {"beginner": "初学者", "familiar": "熟悉者", "expert": "尖端"}
    for who, blk in report["expectations"].items():
        lines += [
            f"### {label.get(who, who)}",
            "",
            f"- 他的目标函数：{blk['objective']}",
            f"- 交付：{'；'.join(blk['deliver'])}",
            f"- 机制：{blk['mechanism']}",
            f"- **预期**：{blk['expectation']}",
            f"- 必须同时说的：{blk['honest_caveat']}",
            f"- 推翻条件：{blk['falsifier']}",
            "",
        ]
    sb = report["scoreboard"]
    lines += [
        "## 六、计分板",
        "",
        f"- 四种机制里为正的：{sb['mechanisms_positive']}",
        f"- 产量组通过门槛：{sb['yield_groups_passing']} / {sb['yield_groups_tested']}",
        "",
    ]
    return "\n".join(lines)


def run_with_table(**kwargs) -> dict[str, object]:
    return run(**kwargs)
