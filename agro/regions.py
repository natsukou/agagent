"""主产区地理锚点。天气按此坐标拉取，不是行政边界均值。"""

REGIONS: dict[str, dict[str, object]] = {
    "dongbei": {
        "name": "东北春玉米带",
        "anchor": "哈尔滨",
        "lat": 45.75,
        "lon": 126.65,
        "crops": ("玉米",),
    },
    "huabei": {
        "name": "华北小麦玉米带",
        "anchor": "石家庄",
        "lat": 38.04,
        "lon": 114.51,
        "crops": ("小麦", "玉米"),
    },
    "changjiang": {
        "name": "长江中下游稻区",
        "anchor": "武汉",
        "lat": 30.59,
        "lon": 114.31,
        "crops": ("水稻",),
    },
    "huanan": {
        "name": "华南双季稻区",
        "anchor": "广州",
        "lat": 23.13,
        "lon": 113.26,
        "crops": ("水稻",),
    },
}
