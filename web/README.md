# 前端演示原型

这是一个零依赖 ES Modules 单页前端工程，无构建步骤、无外部依赖。

当前界面已升级为全国农业期货分布图。它用交易所与主产区锚点展示“交易所—合约目录—产区”的关系；底图是演示性空间轮廓，不应用作行政区边界或精确地理分析。

原型只对仓库内置的大商所日行情样例（2024-07-15 至 2024-07-16）显示价格走势。郑商所和上期所仅展示已规划的农业合约目录，并明确标为待接行情文件。

后续生产化时，建议新增一个只读 API 层，分别向前端提供：

- 图谱节点与边：来自 `data/graph_export.json`；
- 气候时序与预报状态：来自 `weather_month` / 预测模块；
- 市场日线：来自通过 `agro.datasets.dce` 校验后的行情文件；
- 预测与理由码：来自 `agro.predict` / `agro.industry` 输出。

当前页面不直接读取本地 JSON 或 SQLite，因此可用 `file://` 直接打开；接入真实数据时再由 API 或构建期导出替换页面内的演示数据。

## 前端结构

- `index.html`：应用挂载点和静态资源声明；
- `app/main.js`：应用启动；
- `app/state.js`：集中状态与订阅机制；
- `app/data/market-catalog.js`：交易所、合约与产区演示数据；
- `app/components/`：侧栏、地图、市场抽屉、状态面板；
- `styles/`：设计令牌和布局样式。

## 悬浮 AI 助手

前端助手由 `app/components/assistant-widget.js` 挂载在应用根节点之外，因此地图、策略页切换或重新渲染不会清空对话。浏览器只向同域 `/api/assistant` 发送问题、最近 8 条对话和当前页面筛选上下文；服务端实现位于 `api/assistant.js`。

Serverless Harness 开放六个只读解释工具：平台范围、作物覆盖、市场覆盖、模型边界、历史干预研究和固定画像比较；同时限制问题长度、历史长度和最多 4 轮工具调用。干预与画像结果只用于解释历史验收，不会转成当前交易建议，也不会用于推断提问者身份。GNN / GCN 不直接产出路由或交易决策，农业域也不调用 `mvp/kcg` 边界内核。

在 Vercel 项目中配置以下服务端环境变量后再发布：

```text
DEEPSEEK_API_KEY=服务端密钥
DEEPSEEK_BASE_URL=与密钥套餐及地域匹配的 OpenAI 兼容地址
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_MS=45000
```

密钥不得写入 `index.html`、浏览器 JavaScript 或 Git。`sk-sp-` 是阿里云百炼套餐专属密钥，必须使用订阅页给出的 Token Plan 或 Coding Plan 专属地址，不能与百炼通用地址或 DeepSeek 官方地址混用。
