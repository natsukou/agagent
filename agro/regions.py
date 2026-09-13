"""主产区地理锚点。天气按此坐标拉取，不是行政边界均值。

气候框架对全球统一：只要给出经纬度，Open-Meteo ERA5 就能取到同一套日要素。
作物的区域与影响图谱各自不同，所以区域必须带国别（产量标签按国别对齐）
与半球（南半球生长季跨年，月份不能照搬北半球）。
"""

REGIONS: dict[str, dict[str, object]] = {
    "dongbei": {
        "name": "东北春玉米大豆带",
        "anchor": "哈尔滨",
        "lat": 45.75,
        "lon": 126.65,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("玉米", "大豆"),
    },
    "huabei": {
        "name": "华北小麦玉米带",
        "anchor": "石家庄",
        "lat": 38.04,
        "lon": 114.51,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("小麦", "玉米"),
    },
    "changjiang": {
        "name": "长江中下游稻区",
        "anchor": "武汉",
        "lat": 30.59,
        "lon": 114.31,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("水稻",),
    },
    "huanan": {
        "name": "华南双季稻区",
        "anchor": "广州",
        "lat": 23.13,
        "lon": 113.26,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("水稻",),
    },
    "xinjiang": {
        "name": "新疆棉区",
        "anchor": "石河子",
        "lat": 44.30,
        "lon": 86.04,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("棉花",),
    },
    "yunnan": {
        "name": "云南小粒咖啡区",
        "anchor": "普洱",
        "lat": 22.82,
        "lon": 100.97,
        "country": "CHN",
        "hemisphere": "N",
        "crops": ("咖啡",),
    },
    "us_belt": {
        "name": "美国玉米大豆带",
        "anchor": "爱荷华 Ames",
        "lat": 42.03,
        "lon": -93.62,
        "country": "USA",
        "hemisphere": "N",
        "crops": ("玉米", "大豆"),
    },
    "us_cotton": {
        "name": "美国棉花带",
        "anchor": "德州 Lubbock",
        "lat": 33.58,
        "lon": -101.85,
        "country": "USA",
        "hemisphere": "N",
        "crops": ("棉花",),
    },
    "br_soy": {
        "name": "巴西大豆带",
        "anchor": "马托格罗索 Sorriso",
        "lat": -12.55,
        "lon": -55.72,
        "country": "BRA",
        "hemisphere": "S",
        "crops": ("大豆",),
    },
    "br_coffee": {
        "name": "巴西阿拉比卡咖啡带",
        "anchor": "米纳斯吉拉斯 Patrocínio",
        "lat": -18.94,
        "lon": -46.99,
        "country": "BRA",
        "hemisphere": "S",
        "crops": ("咖啡",),
    },
}


def country_of(region_id: str) -> str:
    return str(REGIONS[region_id]["country"])


def is_southern(region_id: str) -> bool:
    return REGIONS[region_id].get("hemisphere") == "S"


def by_country(country: str) -> list[str]:
    return [rid for rid, meta in REGIONS.items() if meta["country"] == country]
