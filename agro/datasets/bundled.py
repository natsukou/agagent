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

_REGION_NAME = {
    "dongbei": "东北春玉米带",
    "huabei": "华北小麦玉米带",
    "changjiang": "长江中下游稻区",
    "huanan": "华南双季稻区",
}


def climatology() -> Climatology:
    normals: dict[tuple[str, int], MonthClimate] = {}
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
    ]


def layout_map() -> dict[str, CropLayout]:
    return {x.id: x for x in layouts()}
