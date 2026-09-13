# 农期图谱 AI 助手与 Harness

## 目标与边界

助手是“解释与检索编排层”，不是产量模型、行情源或交易系统。其固定领域结构是 **气候证据 → 产量信息 → 期货信息**；它只读取本地已建图数据，解释数据覆盖、图谱关系、历史单产、产量模型输出和期货映射。它不得下单、写库、抓取任意网页、修改模型参数，或把市场相关性表述为因果和交易建议。

## 架构

```text
Web Assistant Panel
  -> POST /api/assistant
  -> Harness: 输入校验 / 系统策略 / 最大工具轮次 / 审计轨迹
  -> DeepSeek Chat Completions
  <-> 只读白名单工具: 气候证据 / 产量模型与历史 / 期货信息 / 图谱摘要 / 研究验收
  -> 带 citations、tool_trace 的回答
```

模型只能提出工具调用；Harness 才会验证工具名和参数、执行本地函数并回传结果。工具结果和用户文本均视为不可信数据，不能改变系统策略。

## DeepSeek 接口

默认使用 OpenAI 兼容的 `https://api.deepseek.com/chat/completions`，模型为 `deepseek-v4-flash`。密钥优先从系统环境变量 `DEEPSEEK_API_KEY` 读取；本地开发也可使用仓库根目录下、已被 `.gitignore` 排除的 `.env`，系统环境变量始终覆盖本地文件。工具调用结构兼容 DeepSeek 的 function tools；默认关闭思考模式，避免将内部推理作为业务证据。官方接口和工具调用规范见 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。

## API 合约

`POST /api/assistant`

```json
{"question":"华北玉米的数据覆盖如何？","history":[]}
```

返回：

```json
{"answer":"...","citations":["data/agri_graph.db"],"tool_trace":[{"tool":"get_graph_summary","ok":true}],"model":"deepseek-v4-flash"}
```

`citations` 是实际调用工具返回的本地来源；`tool_trace` 便于审计，但不返回隐藏推理。

## 三层领域 Harness

| 层 | 助手可解释的内容 | 工具 | 明确不能做的事 |
| --- | --- | --- | --- |
| 气候证据 | 主产区月均温、极值温度、降水与数据覆盖 | `get_climate_evidence` | 把历史观测说成实时预报 |
| 产量信息 | 历史单产、图谱物候胁迫、基线气候态下的产量区间 | `get_yield_history`、`get_yield_outlook` | 将基线情景当实测；替代农艺师决策 |
| 期货信息 | 品种映射、角色、流动性、缓存可用性与覆盖缺口 | `get_futures_context`、`get_market_coverage` | 下单、交易建议、因果/收益承诺 |
| 研究验收 | 历史干预事件、方向命中、固定经验场景的模拟差异 | `get_intervention_research`、`get_persona_comparison` | 把历史 long/short 转成当前建议；推断提问者画像 |

每次回答使用固定输出顺序：**气候证据 → 产量含义 → 农情与数据建议 → 期货信息 → 覆盖缺口**。其中“建议”只指田间巡查、灌溉/病虫害核验和数据补采优先级；它不形成具体农业作业指令，也绝不形成交易建议。

## Harness 运行策略

| 控制项 | 当前规则 |
| --- | --- |
| 工具 | 8 个只读本地工具，JSON Schema 与服务端字段白名单双重限定参数 |
| 最大工具轮次 | 4，可用 `AGRI_AI_MAX_TOOL_ROUNDS` 调整 |
| 历史 | 最多 8 条，每条最大 4000 字符 |
| 用户问题 | 最大 4000 字符 |
| 投资边界 | 禁止买卖、仓位、收益保证、个性化建议 |
| 数据边界 | 观测、推断、演示数据必须区分；缺口必须说明 |
| 密钥 | 系统环境变量或被忽略的本地 `.env`；前端、日志、版本库均不出现密钥 |

## 上线前 Harness 计划

1. **数据契约**：将 `graph_export.json`、预测输出、行情文件统一为只读版本化快照，记录 `as_of`、来源、许可与覆盖范围。
2. **身份与限流**：在反向代理前添加登录、会话隔离、每用户/每 IP 速率限制与请求大小限制；当前开发服务器只绑定 `127.0.0.1`。
3. **工具扩展**：先加 `get_forecast(layout_id, as_of)`、`get_weather_window(region, range)`；每个工具须有字段白名单、范围校验、来源清单和单元测试。
4. **回答约束**：前端将“观测 / 模型推断 / 演示”做成不同标签；没有 citation 的关键陈述自动标记为待核验。
5. **评测集**：建立 `tests/fixtures/ai_eval.jsonl`，覆盖数据定位、覆盖缺口、拒绝交易建议、提示注入、工具越权、来源完整性六类用例；每次 prompt/工具变更都离线回归。
6. **可观测性**：记录匿名 request id、模型版本、工具名、耗时、token 用量、错误类型和 citations；不记录密钥、完整隐私数据或内部推理。
7. **发布门槛**：工具越权率 0、注入绕过率 0、来源遗漏率 < 2%、拒绝交易指令正确率 100%，并人工审阅所有高风险答案样本。

## 本地运行

在 PowerShell 设置环境变量后启动：

```powershell
$env:DEEPSEEK_API_KEY = "..."
python -m agro.cli ai-serve
```

服务启动在 `http://127.0.0.1:8787`。这不是生产部署配置；生产环境必须使用 HTTPS、身份验证、限流和审计存储。
