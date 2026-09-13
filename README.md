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
python -m agro.cli train --test-years 2023,2024
python -m agro.cli demand-graph
python -m agro.cli nowcast --step 3 --test-years 2023,2024
python -m agro.cli gcn --test-years 2023,2024
python -m agro.cli industry-eval --refresh --test-years 2023,2024
python -m agro.cli futures --refresh
python -m agro.cli futures-align --symbol C0
python -m agro.cli psd --refresh
python -m agro.cli multicrop --table --test-years 2023,2024
python -m agro.cli event-study --table
python -m agro.cli accuracy --table
python -m agro.cli strategy --table
python -m agro.cli intervene --table
python -m agro.cli personas
python -m agro.cli ai-serve
```

期货只用公开接口：新浪连续主力日线、郑商所公开日报 txt、Yahoo 国际基准。大商所 `dayQuotesCh` 返回 412，不绕过。

已注册 26 个国内农产品品种（DCE / CZCE / SHFE / INE，广期所无农产品）与 13 个国际基准。流动性分三档：liquid / thin / dormant。政策收储的原粮（强麦、普麦、粳稻、籼稻、菜籽）判为 dormant，不可作价格信号；玉米、大豆压榨链、油脂、糖、棉、鲜果为 liquid。`price_without_layout` 列出有价格但缺产量布局的品种（补上大豆、棉花后剩 6 个）。

产业预测完整表写到 `data/models/industry_table.md`。精度口径、测试集拆分、期货链路缺口见 `docs/07-精度与市场链路.md`。

## 当前十三套布局

气候框架全球统一（同一套 ERA5 要素 + 同一套胁迫函数），但每个作物有自己的产区与影响图谱。

- 中国三主粮：东北春玉米、黄淮海夏玉米、华北冬小麦、长江中下游一季稻、华南早稻
- 大豆：东北春大豆、美国大豆带、巴西马托格罗索（南半球跨年）
- 玉米：美国玉米带
- 棉花：新疆棉区、美国德州高原（对 ICE CT）
- 咖啡：巴西米纳斯阿拉比卡（南半球跨年）、云南小粒咖啡

每套含主产区锚点、国别、熟制、物候、气候窗口、灌溉比例、产量构成与基线单产。
南半球布局用 `prev_year_months` 声明落在上一自然年的生长季月份。
咖啡在 USDA PSD 里没有面积与单产，`target` 标为 `production`（kt），不假装能报 t/ha。

新作物结构的标签来自 USDA FAS PSD 四个 zip（谷物/油籽/棉花/咖啡），
以 `scope='PSD:<ISO3>'` 入库，与 OWID/FAO 的中国序列分开存。
训练结果、单位换算陷阱与信息口径说明见 `docs/08-新作物结构训练.md`。

## 季内因子与期货收益（负结果）

`event-study` 用严格事前构造检验「季内胁迫因子修正 → 期货收益率」：
截至日的因子只用当日及之前的天气，未来月份用剔除当年的气候态补齐，
建仓在信息日之后第一个交易日。带跨品种安慰剂与时移安慰剂。

结论是没有信号：20 组检验（5 条链 × 4 个持有期）最大 |r| 仅 0.12，
置换 p 全部 > 0.17。所以棉花在季末估产上的优势**不能**推论成价格预测能力。
见 `docs/09-季内因子与期货收益.md`。

## 两项验收测试

`accuracy` 把仓库里每条产量路径放到同一把尺子下，用技能分
`1 - MAE_model/MAE_persist` 而不是 MAPE 判定——因为作物年单产自相关极强，
照抄去年就能到 1% 量级，MAPE 低说明的是标的容易。
结果：15 条路径只有 3 条真正跑赢持续基线，技能分中位数 −0.22。

`strategy` 把因子修正做成可下单规则并带成本回测，
判定「有没有边」看策略夏普是否超出随机符号零分布的上尾。
结果：5 条链 0 条通过，唯一显著的是跨品种安慰剂，且显著在坏的一侧。

两张表与解读见 `docs/10-两项验收测试.md`。

## 绿星 Agent 助手

悬浮助手使用受控 Harness 调用八个只读工具，覆盖气候、产量、期货、图谱摘要、历史干预研究和固定画像比较。干预结果只用于解释历史验收和失败证据，不生成当前交易建议；画像只代表固定实验条件，不用于识别或推断提问者。接口、密钥配置和安全边界见 `docs/11-AI助手与Harness.md`。

气候预报基线是「月气候态 + 距平持续性衰减」。ERA5 / 气象站 / 统计年鉴只替换连接器，不改图。

内置数字是可运行种子，不是业务产量。
# agagent
