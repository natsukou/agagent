"""产量预测准确率总测试：把仓库里每一条产量路径放到同一把尺子下。

为什么不能只看 MAPE：产业表报「单产 MAPE 1.39%」听起来很准，
但同一批测试年上「照抄去年」也是 1.39%。作物年单产本身就是高度自相关的
慢变量，绝对误差小说明的是标的容易，不是模型有本事。

所以这里每条路径都算技能分：

    skill = 1 - MAE_model / MAE_persist

技能分 > 0 才意味着模型带来了持续基线之外的信息。全表最后给一个计分板：
有几条路径真正跑赢了持续基线。

覆盖的路径：
- train.yield      中国三主粮全国年单产，OWID/FAO 标签
- train.sales      全国谷物产量（销量代理），世界银行标签
- industry.chosen  产业表最终选用方法（含防泄漏的训练内选择），对 USDA PSD 对照集
- multicrop.*      大豆/玉米/棉花/咖啡 × 国别，共 9 组，PSD 标签
- nowcast          区县三日窗 → 季末单产（弱监督）
- gcn              区县需求图卷积 → 季末单产（弱监督）
"""

from __future__ import annotations

import json
from pathlib import Path

from .collect import ROOT

MODEL_DIR = ROOT / "models"
REPORT_PATH = MODEL_DIR / "accuracy_report.json"

TEST_YEARS = (2023, 2024)


def skill(model_mae: float | None, persist_mae: float | None) -> float | None:
    """相对持续基线的技能分。持续基线误差为 0 时无法定义。"""
    if model_mae is None or persist_mae is None or persist_mae <= 0:
        return None
    return round(1.0 - model_mae / persist_mae, 4)


def _row(
    task: str,
    target: str,
    label: str,
    model: dict | None,
    persist: dict | None,
    *,
    note: str = "",
    mean: dict | None = None,
    deliverable: bool = True,
) -> dict[str, object]:
    m_mae = None if not model else float(model["mae"])
    p_mae = None if not persist else float(persist["mae"])
    return {
        "task": task,
        "deliverable": deliverable,
        "target": target,
        "label_source": label,
        "n": (model or {}).get("n"),
        "model_mae": m_mae,
        "model_mape": None if not model else float(model["mape"]),
        "persist_mae": p_mae,
        "persist_mape": None if not persist else float(persist["mape"]),
        "mean_mape": None if not mean else float(mean["mape"]),
        "skill_vs_persist": skill(m_mae, p_mae),
        "beats_persist": bool(m_mae is not None and p_mae is not None and m_mae < p_mae),
        "note": note,
    }


def collect_rows(test_years: tuple[int, ...] = TEST_YEARS) -> list[dict[str, object]]:
    from . import gcn as gcn_mod
    from . import industry as industry_mod
    from . import multicrop as mc_mod
    from . import nowcast as nc_mod
    from . import train as train_mod

    rows: list[dict[str, object]] = []

    # ---- 1. 中国三主粮全国年单产与销量代理 ----
    base = train_mod.run(test_years=test_years)
    y = base.get("yield") or {}
    if y.get("ok"):
        rows.append(
            _row(
                "train.yield",
                "全国年单产 t/ha（小麦/水稻/玉米）",
                "OWID/FAO",
                y.get("model"),
                y.get("baseline_persist"),
                mean=y.get("baseline_mean"),
                note="三主粮混在一条回归里，作物哑变量区分",
            )
        )
    s = base.get("sales") or {}
    if s.get("ok"):
        rows.append(
            _row(
                "train.sales",
                "全国谷物产量 t（销量代理）",
                "World Bank WDI",
                s.get("model"),
                s.get("baseline_persist"),
                mean=s.get("baseline_mean"),
                note="不是真实销量，是供给侧代理",
            )
        )

    # ---- 2. 产业表：对独立对照集 USDA PSD ----
    ind = industry_mod.build_table(test_years=test_years)
    ind_m = ind.get("metrics") or {}
    per_method = ind_m.get("per_method_yield") or {}
    sel = ind.get("method_selection") or {}
    rows.append(
        _row(
            "industry.chosen",
            "全国年单产 t/ha（选用方法）",
            "USDA FAS PSD（独立对照集）",
            ind_m.get("national_yield"),
            per_method.get("persist"),
            note=f"选用方法={sel.get('method', '?')}，由训练内留一年决定",
        )
    )
    # oracle 是「每行都挑对方法」的上界，只作参考，不是可交付精度
    if per_method.get("oracle_upper_bound"):
        rows.append(
            _row(
                "industry.oracle（仅上界）",
                "全国年单产 t/ha（逐行挑最优方法）",
                "USDA FAS PSD（独立对照集）",
                per_method["oracle_upper_bound"],
                per_method.get("persist"),
                note="用测试年实测挑方法，必然偏乐观，不可交付",
                deliverable=False,
            )
        )
    if ind_m.get("national_production"):
        rows.append(
            _row(
                "industry.production",
                "全国产量 kt = 单产 × 上年面积",
                "USDA FAS PSD（独立对照集）",
                ind_m.get("national_production"),
                None,
                note="面积用上一年实测，避免用当年实际收获面积",
            )
        )

    # ---- 3. 新作物结构 9 组 ----
    mc = mc_mod.run(test_years=test_years)
    for key, res in (mc.get("results") or {}).items():
        if not res.get("ok"):
            continue
        best = res["best"]
        # 模型列取该组最优的那条（岭回归或仅图谱），持续基线单独列
        cand = res["model_ridge"] if best != "graph_only" else res["graph_only"]
        rows.append(
            _row(
                f"multicrop.{key}",
                res["target"],
                "USDA FAS PSD",
                cand,
                res["baseline_persist"],
                note=f"该组最优={best}",
            )
        )

    # ---- 4. 区县三日窗 ----
    nc = nc_mod.run_nowcast(test_years=test_years)
    if nc.get("ok"):
        rows.append(
            _row(
                "nowcast.3d",
                "季末单产 t/ha（三日窗修正）",
                "全国年单产（弱监督，区县共享标签）",
                nc.get("model"),
                nc.get("baseline_persist"),
                mean=nc.get("baseline_mean"),
                note="预测的是本季期末单产，不是当天收割量",
            )
        )

    # ---- 5. 图卷积 ----
    g = gcn_mod.run_gcn(test_years=test_years)
    if g.get("ok"):
        rows.append(
            _row(
                "gcn.county",
                "季末单产 t/ha（区县图卷积）",
                "全国年单产（弱监督，无区县监督信号）",
                g.get("model"),
                g.get("baseline_persist"),
                note=g.get("warning", ""),
            )
        )

    return rows


def run(test_years: tuple[int, ...] = TEST_YEARS) -> dict[str, object]:
    rows = collect_rows(test_years)
    # oracle 那一行是「逐行用测试年实测挑方法」的上界，不可交付，不进计分
    scored = [
        r for r in rows if r["skill_vs_persist"] is not None and r["deliverable"]
    ]
    winners = [r for r in scored if r["beats_persist"]]
    best_mape = min(
        (r for r in rows if r["model_mape"] is not None and r["deliverable"]),
        key=lambda r: r["model_mape"],
    )
    best_skill = max(scored, key=lambda r: r["skill_vs_persist"]) if scored else None

    report = {
        "task": "产量预测准确率总测试",
        "test_years": list(test_years),
        "split_rule": "按年切分，训练用较早年份，测试用最近两年，禁止随机打乱",
        "metric_note": (
            "MAPE 单看会高估能力：作物年单产自相关极强，照抄去年就能到 1% 量级。"
            "判断有没有本事要看 skill = 1 - MAE_model/MAE_persist，大于 0 才算有增量信息。"
        ),
        "rows": rows,
        "scoreboard": {
            "tasks_total": len(rows),
            "tasks_non_deliverable": sum(1 for r in rows if not r["deliverable"]),
            "tasks_with_persist_baseline": len(scored),
            "tasks_beating_persist": len(winners),
            "beating_persist_names": [r["task"] for r in winners],
            "lowest_mape_task": {"task": best_mape["task"], "mape": best_mape["model_mape"]},
            "highest_skill_task": (
                None
                if best_skill is None
                else {"task": best_skill["task"], "skill": best_skill["skill_vs_persist"]}
            ),
            "median_skill": (
                None
                if not scored
                else round(
                    sorted(r["skill_vs_persist"] for r in scored)[len(scored) // 2], 4
                )
            ),
        },
        "guards": [
            "所有切分按年，训练集只含较早年份",
            "产业表的方法选择只在训练年上留一年交叉，不看测试年实测值",
            "产业表产量用上一年面积，不用当年实际收获面积",
            "多作物组的标签来自 USDA PSD，与 OWID/FAO 训练标签不同机构",
            "区县两条路径是弱监督：标签是全国年单产，区县没有独立监督信号",
        ],
        "known_limits": [
            "棉花标签目前只有 PSD 单一来源，没有独立对照集",
            "多作物每组只有 7 个训练年、5 个特征加截距，接近饱和",
            "每个产区用单点锚点代表，空间代表性有限",
            "岭回归与仅图谱用整季已发生天气，属季末估产，不是播种前预报",
        ],
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["artifact"] = str(REPORT_PATH)
    return report


def to_markdown(report: dict) -> str:
    lines = ["# 产量预测准确率总测试", ""]
    lines.append(f"- 测试年：{report['test_years']}")
    lines.append(f"- 切分：{report['split_rule']}")
    lines.append(f"- 读法：{report['metric_note']}")
    lines.append(
        "- 范围：这张表是**固定两年留出**的横向体检，每组只有 2 个测试点，"
        "不用来决定对外交付哪些组。交付资格由 `agro.cli audience` 的逐年前向"
        "加选择偏差守卫决定，见 `docs/14-用户适配与交付门槛.md`。"
    )
    lines.append("")
    lines.append("## 一、逐路径精度")
    lines.append("")
    lines.append("| 路径 | 可交付 | 标的 | 标签来源 | n | 模型 MAPE | 持续基线 MAPE | 技能分 | 胜持续 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in report["rows"]:
        mm = "-" if r["model_mape"] is None else f"{r['model_mape']:.4f}"
        pm = "-" if r["persist_mape"] is None else f"{r['persist_mape']:.4f}"
        sk = "-" if r["skill_vs_persist"] is None else f"{r['skill_vs_persist']:+.4f}"
        win = "-" if r["persist_mape"] is None else ("是" if r["beats_persist"] else "否")
        lines.append(
            f"| {r['task']} | {'是' if r['deliverable'] else '否'} | {r['target']} | "
            f"{r['label_source']} | {r['n']} | {mm} | {pm} | {sk} | {win} |"
        )
    sb = report["scoreboard"]
    lines.append("")
    lines.append("## 二、计分板（只计可交付路径）")
    lines.append("")
    lines.append(f"- 路径总数：{sb['tasks_total']}（其中不可交付 {sb['tasks_non_deliverable']}）")
    lines.append(f"- 有持续基线可比的：{sb['tasks_with_persist_baseline']}")
    lines.append(f"- **真正跑赢持续基线的：{sb['tasks_beating_persist']}**")
    if sb["beating_persist_names"]:
        lines.append(f"- 跑赢的是：{', '.join(sb['beating_persist_names'])}")
    lines.append(f"- MAPE 最低：{sb['lowest_mape_task']['task']}（{sb['lowest_mape_task']['mape']:.4f}）")
    if sb["highest_skill_task"]:
        lines.append(
            f"- 技能分最高：{sb['highest_skill_task']['task']}（{sb['highest_skill_task']['skill']:+.4f}）"
        )
    lines.append(f"- 技能分中位数：{sb['median_skill']:+.4f}")
    lines.append("")
    lines.append("## 三、防泄漏守卫")
    lines.append("")
    for g in report["guards"]:
        lines.append(f"- {g}")
    lines.append("")
    lines.append("## 四、已知限制")
    lines.append("")
    for c in report["known_limits"]:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


def run_with_table(**kwargs) -> dict[str, object]:
    report = run(**kwargs)
    md = MODEL_DIR / "accuracy_table.md"
    md.write_text(to_markdown(report), encoding="utf-8")
    report["artifact_markdown"] = str(md)
    return report
