# 农期图谱 AI 助手与 Harness

## 目标与边界

助手是“解释与检索编排层”，不是产量模型、行情源或交易系统。它只读取本地已建图数据，通过受限工具解释：数据覆盖、图谱关系、历史单产和期货映射。它不得下单、写库、抓取任意网页、修改模型参数，或把市场相关性表述为因果和交易建议。

## 架构

```text
Web Assistant Panel
  -> POST /api/assistant
  -> Harness: 输入校验 / 系统策略 / 最大工具轮次 / 审计轨迹
  -> DeepSeek Chat Completions
  <-> 只读白名单工具: 图谱摘要 / 单产历史 / 市场覆盖
  -> 带 citations、tool_trace 的回答
```

模型只能提出工具调用；Harness 才会验证工具名和参数、执行本地函数并回传结果。工具结果和用户文本均视为不可信数据，不能改变系统策略。

## DeepSeek 接口

默认使用 OpenAI 兼容的 `https://api.deepseek.com/chat/completions`，模型为 `deepseek-v4-flash`。密钥只能通过 `DEEPSEEK_API_KEY` 环境变量提供。工具调用结构兼容 DeepSeek 的 function tools；默认关闭思考模式，避免将内部推理作为业务证据。官方接口和工具调用规范见 [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 与 [Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。

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

## Harness 运行策略

| 控制项 | 当前规则 |
| --- | --- |
| 工具 | 仅 3 个只读本地工具，JSON Schema 限定参数 |
| 最大工具轮次 | 4，可用 `AGRI_AI_MAX_TOOL_ROUNDS` 调整 |
| 历史 | 最多 8 条，每条最大 4000 字符 |
| 用户问题 | 最大 4000 字符 |
| 投资边界 | 禁止买卖、仓位、收益保证、个性化建议 |
| 数据边界 | 观测、推断、演示数据必须区分；缺口必须说明 |
| 密钥 | 仅环境变量；前端、日志、仓库均不出现密钥 |

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
