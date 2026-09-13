"""内置中国主粮布局与月气候态。数值是公开农气常识的可运行种子，不是业务产量。

接入 ERA5 / 国家气象站 / 统计年鉴后，只替换本文件的数据源，图与预测公式不动。
"""

from __future__ import annotations

from ..climate import Climatology, MonthClimate
from ..crops import CropLayout, PhenologyStage, YieldComponent

# month -> (tmean, tmax, tmin, precip)
_CLIM: dict[str, dict[int, tuple[float, float, float, float]]] = {
    "dongbei": {  # 哈尔滨一带
        1: (-18.0, -12.0, -24.0, 5),
        2: (-13.0, -6.0, -20.0, 6),
        3: (-3.0, 4.0, -10.0, 12),
        4: (8.0, 16.0, 0.0, 22),
        5: (16.0, 23.0, 8.0, 45),
        6: (21.0, 27.0, 15.0, 90),
        7: (23.0, 28.0, 19.0, 160),
        8: (21.5, 26.5, 17.0, 130),
        9: (15.0, 21.0, 8.0, 55),
        10: (6.0, 13.0, -1.0, 25),
        11: (-6.0, 0.0, -12.0, 10),
        12: (-15.0, -9.0, -21.0, 6),
    },
    "huabei": {  # 石家庄 / 豫北
        1: (-1.0, 5.0, -7.0, 4),
        2: (2.0, 8.0, -4.0, 7),
        3: (9.0, 15.0, 2.0, 12),
        4: (16.0, 23.0, 9.0, 25),
        5: (22.0, 29.0, 15.0, 40),
        6: (26.5, 33.0, 20.0, 70),
        7: (27.5, 32.5, 22.5, 160),
        8: (26.0, 31.0, 21.5, 140),
        9: (21.0, 27.0, 15.0, 55),
        10: (14.0, 21.0, 8.0, 25),
        11: (6.0, 13.0, 0.0, 12),
        12: (0.5, 6.5, -5.0, 5),
    },
    "changjiang": {  # 武汉 / 洞庭湖平原
        1: (4.5, 8.5, 1.0, 45),
        2: (6.5, 11.0, 3.0, 60),
        3: (11.0, 16.0, 7.0, 90),
        4: (17.0, 22.5, 12.5, 130),
        5: (22.0, 27.0, 17.5, 160),
        6: (25.5, 30.0, 21.5, 200),
        7: (29.0, 34.0, 25.0, 180),
        8: (28.5, 33.5, 24.5, 130),
        9: (24.0, 29.0, 20.0, 80),
        10: (18.0, 23.0, 14.0, 70),
        11: (12.0, 17.0, 8.0, 55),
        12: (6.5, 11.0, 3.0, 35),
    },
    "huanan": {  # 珠江三角洲
        1: (14.0, 19.0, 10.0, 40),
        2: (15.5, 20.0, 12.0, 60),
        3: (18.5, 23.0, 15.5, 85),
        4: (22.5, 27.0, 19.5, 180),
        5: (26.0, 30.5, 23.0, 260),
        6: (27.5, 32.0, 25.0, 280),
        7: (28.5, 33.0, 25.5, 230),
        8: (28.5, 32.5, 25.5, 230),
        9: (27.5, 31.5, 24.5, 180),
        10: (24.5, 29.0, 21.0, 80),
        11: (20.0, 25.0, 16.0, 40),
        12: (15.5, 20.5, 11.5, 30),
    },
}

from ..regions import REGIONS

_REGION_NAME = {rid: str(meta["name"]) for rid, meta in REGIONS.items()}


def climatology_from_era5() -> dict[tuple[str, int], MonthClimate]:
    """新增区域不手写气候态，直接由已下载的 ERA5 月汇总求多年平均。"""
    from ..collect import RAW, weather_monthly

    weather_dir = RAW / "weather"
    if not weather_dir.exists():
        return {}
    buckets: dict[tuple[str, int], list[dict]] = {}
    for path in weather_dir.glob("*.json"):
        try:
            rows = weather_monthly(path)
        except (KeyError, ValueError):
            continue
        for row in rows:
            buckets.setdefault((str(row["region_id"]), int(row["month"])), []).append(row)
    out: dict[tuple[str, int], MonthClimate] = {}
    for (rid, month), rows in buckets.items():
        n = len(rows)
        out[(rid, month)] = MonthClimate(
            rid,
            2000,
            month,
            round(sum(r["tmean"] for r in rows) / n, 2),
            round(sum(r["tmax"] for r in rows) / n, 2),
            round(sum(r["tmin"] for r in rows) / n, 2),
            round(sum(r["precip_mm"] for r in rows) / n, 1),
            "era5-normal",
        )
    return out


def climatology() -> Climatology:
    """原有四区沿用手写气候态（保持既有预测行为），新增区域用 ERA5 实测均值。"""
    normals: dict[tuple[str, int], MonthClimate] = {}
    for key, mc in climatology_from_era5().items():
        normals[key] = mc
    for rid, months in _CLIM.items():
        for m, (tmean, tmax, tmin, pr) in months.items():
            normals[(rid, m)] = MonthClimate(rid, 2000, m, tmean, tmax, tmin, pr, "climatology")
    return Climatology(normals)


def layouts() -> list[CropLayout]:
    return [
        CropLayout(
            id="maize.dongbei.spring",
            crop="玉米",
            system="东北春玉米",
            region_id="dongbei",
            region_name=_REGION_NAME["dongbei"],
            baseline_t_ha=7.4,
            area_kha=6500,
            irrigated_share=0.25,
            stages=[
                PhenologyStage("emerge", "出苗", 5, 5, 10, 22, 30, 6, 40, 0.15),
                PhenologyStage("vegetative", "营养生长", 6, 7, 18, 28, 33, 12, 180, 0.25),
                PhenologyStage("flower", "抽雄吐丝", 7, 8, 22, 30, 34, 16, 140, 0.35),
                PhenologyStage("fill", "灌浆成熟", 8, 9, 16, 26, 32, 8, 90, 0.25),
            ],
            components=[
                YieldComponent("ears", "穗数", 0.35),
                YieldComponent("kernels", "穗粒数", 0.40),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="开花期高温和伏旱是主风险。",
        ),
        CropLayout(
            id="wheat.huabei.winter",
            crop="小麦",
            system="华北冬小麦",
            region_id="huabei",
            region_name=_REGION_NAME["huabei"],
            baseline_t_ha=6.3,
            area_kha=7200,
            irrigated_share=0.70,
            stages=[
                PhenologyStage("sow", "播种出苗", 10, 11, 8, 18, 26, 0, 40, 0.15),
                PhenologyStage("winter", "越冬", 12, 2, -8, 6, 15, -18, 20, 0.10),
                PhenologyStage("joint", "返青拔节", 3, 4, 8, 18, 26, 0, 70, 0.25),
                PhenologyStage("flower", "抽穗扬花", 4, 5, 14, 24, 30, 6, 60, 0.30),
                PhenologyStage("fill", "灌浆成熟", 5, 6, 16, 26, 32, 8, 50, 0.20),
            ],
            components=[
                YieldComponent("spikes", "穗数", 0.40),
                YieldComponent("grains", "穗粒数", 0.35),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="灌浆期干热风是华北主风险；灌溉比例高，干旱胁迫部分被缓解。",
            prev_year_months=(10, 11, 12),
        ),
        CropLayout(
            id="rice.changjiang.single",
            crop="水稻",
            system="长江中下游一季稻",
            region_id="changjiang",
            region_name=_REGION_NAME["changjiang"],
            baseline_t_ha=7.1,
            area_kha=5400,
            irrigated_share=0.90,
            stages=[
                PhenologyStage("transplant", "移栽返青", 5, 6, 18, 28, 34, 12, 120, 0.15),
                PhenologyStage("tillering", "分蘖", 6, 7, 22, 30, 35, 16, 140, 0.20),
                PhenologyStage("heading", "孕穗抽穗", 7, 8, 24, 32, 35, 18, 160, 0.40),
                PhenologyStage("fill", "灌浆成熟", 8, 9, 20, 30, 35, 14, 100, 0.25),
            ],
            components=[
                YieldComponent("panicles", "有效穗", 0.35),
                YieldComponent("spikelets", "实粒数", 0.40),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="抽穗扬花遇 35°C 以上高温热害显著；灌溉高，干旱权重低于玉米。",
        ),
        CropLayout(
            id="rice.huanan.early",
            crop="水稻",
            system="华南早稻",
            region_id="huanan",
            region_name=_REGION_NAME["huanan"],
            baseline_t_ha=6.0,
            area_kha=2100,
            irrigated_share=0.85,
            stages=[
                PhenologyStage("sow", "播种育秧", 3, 3, 16, 26, 32, 10, 80, 0.15),
                PhenologyStage("tillering", "分蘖", 4, 5, 20, 30, 34, 14, 160, 0.25),
                PhenologyStage("heading", "抽穗扬花", 5, 6, 24, 32, 35, 18, 180, 0.35),
                PhenologyStage("fill", "灌浆成熟", 6, 7, 22, 32, 36, 16, 140, 0.25),
            ],
            components=[
                YieldComponent("panicles", "有效穗", 0.35),
                YieldComponent("spikelets", "实粒数", 0.40),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="龙舟水与抽穗期暴雨是主风险，当前模型用降水短缺刻画干旱，雨涝尚未单独建模。",
        ),
        CropLayout(
            id="maize.huabei.summer",
            crop="玉米",
            system="黄淮海夏玉米",
            region_id="huabei",
            region_name=_REGION_NAME["huabei"],
            baseline_t_ha=6.6,
            area_kha=5800,
            irrigated_share=0.45,
            stages=[
                PhenologyStage("sow", "麦收后播种", 6, 6, 18, 28, 34, 12, 60, 0.15),
                PhenologyStage("vegetative", "营养生长", 6, 7, 20, 30, 35, 14, 140, 0.25),
                PhenologyStage("flower", "抽雄吐丝", 7, 8, 22, 32, 35, 16, 150, 0.35),
                PhenologyStage("fill", "灌浆成熟", 8, 9, 18, 28, 33, 10, 80, 0.25),
            ],
            components=[
                YieldComponent("ears", "穗数", 0.35),
                YieldComponent("kernels", "穗粒数", 0.40),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="与冬小麦套作，开花期与华北盛夏高温重叠。",
        ),
        # ---- 大豆：三个主产国，同一气候框架、不同影响图谱 ----
        CropLayout(
            id="soy.dongbei.spring",
            crop="大豆",
            system="东北春大豆",
            region_id="dongbei",
            region_name=_REGION_NAME["dongbei"],
            baseline_t_ha=1.99,
            area_kha=7000,
            irrigated_share=0.10,
            stages=[
                PhenologyStage("emerge", "播种出苗", 5, 5, 12, 24, 32, 6, 50, 0.10),
                PhenologyStage("branch", "分枝", 6, 6, 18, 28, 33, 12, 90, 0.20),
                PhenologyStage("flower", "开花结荚", 7, 8, 20, 28, 33, 14, 160, 0.40),
                PhenologyStage("fill", "鼓粒成熟", 8, 9, 16, 26, 32, 8, 110, 0.30),
            ],
            components=[
                YieldComponent("plants", "株数", 0.30),
                YieldComponent("pods", "单株粒数", 0.45),
                YieldComponent("weight", "百粒重", 0.25),
            ],
            notes="旱作为主，开花结荚期缺水直接掉荚；不像玉米那样怕高温，怕的是花期旱。",
            country="CHN",
        ),
        CropLayout(
            id="soy.us.belt",
            crop="大豆",
            system="美国大豆带",
            region_id="us_belt",
            region_name=_REGION_NAME["us_belt"],
            baseline_t_ha=3.40,
            area_kha=33294,
            irrigated_share=0.08,
            stages=[
                PhenologyStage("plant", "播种", 5, 5, 12, 24, 32, 6, 90, 0.10),
                PhenologyStage("vegetative", "营养生长", 6, 6, 18, 28, 33, 12, 100, 0.15),
                PhenologyStage("flower", "开花结荚 R1-R4", 7, 7, 20, 29, 34, 14, 110, 0.35),
                PhenologyStage("fill", "鼓粒 R5-R6", 8, 9, 18, 27, 33, 10, 120, 0.40),
            ],
            components=[
                YieldComponent("plants", "株数", 0.25),
                YieldComponent("pods", "单株粒数", 0.45),
                YieldComponent("weight", "百粒重", 0.30),
            ],
            notes="美豆产量由 8 月鼓粒期降水主导（August rain makes beans），权重比花期更高。",
            country="USA",
        ),
        CropLayout(
            id="soy.br.matogrosso",
            crop="大豆",
            system="巴西马托格罗索大豆",
            region_id="br_soy",
            region_name=_REGION_NAME["br_soy"],
            baseline_t_ha=3.35,
            area_kha=46150,
            irrigated_share=0.03,
            stages=[
                PhenologyStage("plant", "播种", 10, 10, 20, 30, 36, 14, 150, 0.15),
                PhenologyStage("vegetative", "营养生长", 11, 11, 21, 31, 36, 15, 200, 0.15),
                PhenologyStage("flower", "开花结荚", 12, 1, 21, 30, 35, 16, 260, 0.35),
                PhenologyStage("fill", "鼓粒成熟", 2, 3, 20, 30, 35, 15, 220, 0.35),
            ],
            components=[
                YieldComponent("plants", "株数", 0.25),
                YieldComponent("pods", "单株粒数", 0.45),
                YieldComponent("weight", "百粒重", 0.30),
            ],
            notes="南半球：市场年 Y 对应 Y-1 年 10 月播种到 Y 年 3 月收获，雨季开始早晚决定播期与二季玉米。",
            country="BRA",
            prev_year_months=(10, 11, 12),
        ),
        # ---- 玉米：补美国带，价格侧对上 CBOT ZC ----
        CropLayout(
            id="maize.us.belt",
            crop="玉米",
            system="美国玉米带",
            region_id="us_belt",
            region_name=_REGION_NAME["us_belt"],
            baseline_t_ha=11.0,
            area_kha=35000,
            irrigated_share=0.15,
            stages=[
                PhenologyStage("plant", "播种", 4, 5, 10, 22, 30, 4, 90, 0.15),
                PhenologyStage("vegetative", "营养生长", 6, 6, 18, 28, 33, 12, 110, 0.20),
                PhenologyStage("silk", "抽雄吐丝", 7, 7, 20, 29, 32, 15, 110, 0.40),
                PhenologyStage("fill", "灌浆成熟", 8, 9, 17, 27, 32, 8, 100, 0.25),
            ],
            components=[
                YieldComponent("ears", "穗数", 0.30),
                YieldComponent("kernels", "穗粒数", 0.45),
                YieldComponent("weight", "千粒重", 0.25),
            ],
            notes="7 月授粉期高温干旱是美玉米单产的单一最大变量。",
            country="USA",
        ),
        # ---- 棉花：新疆与美棉带，风险结构完全不同 ----
        CropLayout(
            id="cotton.xinjiang",
            crop="棉花",
            system="新疆棉",
            region_id="xinjiang",
            region_name=_REGION_NAME["xinjiang"],
            baseline_t_ha=2.09,
            area_kha=2850,
            irrigated_share=0.98,
            stages=[
                PhenologyStage("sow", "播种", 4, 4, 12, 25, 33, 5, 20, 0.10),
                PhenologyStage("seedling", "苗期", 5, 5, 18, 28, 35, 10, 25, 0.15),
                PhenologyStage("square", "蕾期", 6, 6, 22, 32, 37, 14, 30, 0.20),
                PhenologyStage("boll", "花铃期", 7, 8, 24, 32, 38, 16, 45, 0.35),
                PhenologyStage("open", "吐絮", 9, 10, 16, 28, 34, 6, 20, 0.20),
            ],
            components=[
                YieldComponent("bolls", "铃数", 0.45),
                YieldComponent("weight", "单铃重", 0.30),
                YieldComponent("lint", "衣分", 0.25),
            ],
            notes="滴灌覆盖近全域，降水不是主约束；风险是 4 月倒春寒烂种与 9 月吐絮期连阴雨。",
            country="CHN",
        ),
        CropLayout(
            id="cotton.us.belt",
            crop="棉花",
            system="美国棉花带",
            region_id="us_cotton",
            region_name=_REGION_NAME["us_cotton"],
            baseline_t_ha=1.008,
            area_kha=2606,
            irrigated_share=0.35,
            stages=[
                PhenologyStage("plant", "播种", 5, 5, 16, 30, 36, 10, 50, 0.15),
                PhenologyStage("seedling", "苗期", 6, 6, 20, 32, 38, 14, 60, 0.15),
                PhenologyStage("square", "蕾期", 7, 7, 22, 33, 39, 16, 70, 0.25),
                PhenologyStage("boll", "花铃期", 8, 8, 22, 33, 39, 16, 70, 0.30),
                PhenologyStage("open", "吐絮收获", 9, 10, 16, 30, 36, 6, 40, 0.15),
            ],
            components=[
                YieldComponent("bolls", "铃数", 0.45),
                YieldComponent("weight", "单铃重", 0.30),
                YieldComponent("lint", "衣分", 0.25),
            ],
            notes="西德州以旱作为主，单产年际波动远大于新疆；干旱会直接触发弃收，是 ICE 棉花的供给侧变量。",
            country="USA",
        ),
        # ---- 咖啡：USDA PSD 无面积无单产，只能报产量 ----
        CropLayout(
            id="coffee.br.arabica",
            crop="咖啡",
            system="巴西阿拉比卡",
            region_id="br_coffee",
            region_name=_REGION_NAME["br_coffee"],
            baseline_t_ha=25.9,
            area_kha=1700,
            irrigated_share=0.25,
            stages=[
                PhenologyStage("flower", "开花", 9, 10, 18, 26, 32, 8, 140, 0.35),
                PhenologyStage("expand", "果实膨大", 11, 1, 18, 27, 32, 10, 200, 0.30),
                PhenologyStage("ripen", "成熟", 2, 4, 17, 26, 31, 8, 150, 0.20),
                PhenologyStage("harvest", "采收", 5, 8, 14, 24, 30, 2, 40, 0.15),
            ],
            components=[
                YieldComponent("branches", "结果枝数", 0.35),
                YieldComponent("cherries", "每节果数", 0.40),
                YieldComponent("weight", "单果重", 0.25),
            ],
            notes=(
                "南半球，市场年 Y 对应 Y-1 年 9 月开花到 Y 年 8 月采收。"
                "采收期霜冻（2021 年 7 月）与开花期干旱是 ICE 阿拉比卡的两大供给冲击。"
                "PSD 无面积统计，baseline 单位是千袋/千公顷，目标口径是产量而非单产。"
                "阿拉比卡有大小年（biennial bearing），当前模型未显式建模。"
            ),
            country="BRA",
            prev_year_months=(9, 10, 11, 12),
            target="production",
            unit="kt",
        ),
        CropLayout(
            id="coffee.yunnan",
            crop="咖啡",
            system="云南小粒咖啡",
            region_id="yunnan",
            region_name=_REGION_NAME["yunnan"],
            baseline_t_ha=13.8,
            area_kha=130,
            irrigated_share=0.30,
            stages=[
                PhenologyStage("flower", "开花", 3, 4, 18, 26, 31, 8, 90, 0.30),
                PhenologyStage("expand", "果实膨大", 5, 8, 19, 27, 32, 12, 320, 0.35),
                PhenologyStage("ripen", "成熟", 9, 11, 16, 25, 30, 6, 130, 0.25),
                PhenologyStage("harvest", "采收", 12, 12, 12, 22, 28, 2, 20, 0.10),
            ],
            components=[
                YieldComponent("branches", "结果枝数", 0.35),
                YieldComponent("cherries", "每节果数", 0.40),
                YieldComponent("weight", "单果重", 0.25),
            ],
            notes=(
                "北半球，3-4 月开花赶上干季末，旱情直接减产；采收跨到次年 1-2 月，"
                "当前按当年 12 月截断。中国产量占全球约 1%，不是 ICE 价格驱动方。"
            ),
            country="CHN",
            target="production",
            unit="kt",
        ),
    ]


def layout_map() -> dict[str, CropLayout]:
    return {x.id: x for x in layouts()}


def layouts_for(country: str = "CHN", crop: str | None = None) -> list[CropLayout]:
    """按国别取布局。加入美/巴布局后，凡是「按作物汇总面积」的地方都必须先过滤国别，
    否则中国玉米面积会被算上美国玉米带。"""
    return [
        lay
        for lay in layouts()
        if lay.country == country and (crop is None or lay.crop == crop)
    ]
