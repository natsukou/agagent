"""区县是需求图谱的最小空间单元。当前是主产区下的种子县，不是全国 2800 县全表。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class County:
    id: str
    name: str
    region_id: str
    lat: float
    lon: float
    crops: tuple[str, ...]
    area_kha: float  # 该县该熟制的示意面积，待统计年鉴替换


COUNTIES: tuple[County, ...] = (
    County("230184", "五常", "dongbei", 44.93, 127.15, ("水稻",), 180),
    County("220182", "榆树", "dongbei", 44.83, 126.55, ("玉米",), 220),
    County("220184", "公主岭", "dongbei", 43.50, 124.82, ("玉米",), 200),
    County("130109", "藁城", "huabei", 38.03, 114.85, ("小麦", "玉米"), 90),
    County("410526", "滑县", "huabei", 35.58, 114.68, ("小麦", "玉米"), 140),
    County("411626", "淮阳", "huabei", 33.73, 114.88, ("小麦", "玉米"), 110),
    County("421023", "监利", "changjiang", 29.82, 112.90, ("水稻",), 160),
    County("430921", "南县", "changjiang", 29.36, 112.40, ("水稻",), 85),
    County("360121", "新建", "changjiang", 28.69, 115.81, ("水稻",), 95),
    County("441283", "高要", "huanan", 23.03, 112.46, ("水稻",), 55),
    County("450881", "桂平", "huanan", 23.39, 110.08, ("水稻",), 70),
    County("440825", "徐闻", "huanan", 20.33, 110.17, ("水稻",), 40),
)


def by_region(region_id: str) -> list[County]:
    return [c for c in COUNTIES if c.region_id == region_id]


def by_crop(crop: str) -> list[County]:
    return [c for c in COUNTIES if crop in c.crops]
