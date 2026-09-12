# 农业产量图谱

独立于数学知识组件图的农业域。复用「图 + 覆盖缺口 + 受限 Agent 输出」，边语义换成气候胁迫与产量构成，不用掌握前置。

数据流：气候动态预测 → 针对性作物完整布局 → 产量图谱加权 → 预期产量（带区间与理由码）→ 接入位。

## 跑

```text
cd agri
python -m unittest discover -s tests
python -m agro.cli graph-stats
python -m agro.cli layouts
python -m agro.cli climate --region changjiang --warm-demo
python -m agro.cli yield --layout rice.changjiang.single --warm-demo
python -m agro.cli yield-all
python -m agro.cli connectors
```

`--strict` 只用预报月份、不填气候态，用来暴露覆盖缺口。

大商所日行情（对齐 [DCEData](https://github.com/yuany3721/DCEData) 的品种与字段，不内嵌其爬虫）：

```text
python -m agro.cli dce-map
python -m agro.cli dce-load
python -m agro.cli dce-load --path your_dce.csv
python -m agro.cli collect
python -m agro.cli graph-build
python -m agro.cli graph-summary
```

## 当前五套布局

东北春玉米、华北冬小麦、黄淮海夏玉米、长江中下游一季稻、华南早稻。每套含主产区、熟制、物候、气候窗口、灌溉比例、产量构成与基线单产。

气候预报基线是「月气候态 + 距平持续性衰减」。ERA5 / 气象站 / 统计年鉴只替换连接器，不改图。

内置数字是可运行种子，不是业务产量。
# agagent
