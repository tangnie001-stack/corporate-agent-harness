# 阿里云 ARMS Grafana 版 API 实测：告警规则、告警历史与查询代理

> 调研日期：2026-11-04 ｜ 性质：外部事实冻结记录（实测）
> 对象：`grafana-cn-2r42scs2308.grafana.aliyuncs.com`（阿里云 ARMS 可观测可视化 Grafana 版，版本 10.0.9）
> 方法：用 Viewer 角色的 Service Account Token 直接调用官方 HTTP API，逐接口记录状态码与返回形态

## 结论摘要

1. **工作区只开放 80 端口**。`https://<host>:80` 正常，`https://<host>:443` 连接超时；浏览器地址栏里的 `:80` 不是笔误。
2. **鉴权用 Service Account Token + `Authorization: Bearer`**。10.x 起 API Key 已被取代，11.x 完全移除。
3. **规则定义用 `/api/prometheus/grafana/api/v1/rules` 可读**（Viewer 即可），返回 2 分组 15 条规则，含查询语句、阈值、for 时长。
4. **`/api/v1/provisioning/alert-rules` 对 Viewer 返回 403**，但**不需要它**——它是写接口 + UID，归因只读场景用不上。
5. **告警历史用 `/api/annotations?type=alert`**，形态是**每分钟一条快照**而非"触发/恢复事件"，需按 alertname + 时间窗聚合。
6. **`POST /api/ds/query` 可以代理执行 SLS 查询**，一个 token 覆盖所有数据源（SLS 与 Prometheus 皆可）。
7. **`/api/ds/query` 的 `status` 字段恒为 500，但 `frames[].data.values` 里有真实数据** —— 不能用 status 判成败。

## 详述

### 连通性

| 目标 | 结果 |
|---|---|
| DNS 解析 | 121.43.141.93 |
| `https://<host>/health`（443） | 超时（12s） |
| `http://<host>/`（80，明文） | 400 |
| `https://<host>:80/` | 302 |
| `https://<host>:80/api/health` | **200** `{"commit":"Aliyun-ARMS","database":"ok","version":"10.0.9"}` |

### 接口清单

| 接口 | 状态 | 返回 |
|---|---|---|
| `GET /api/health` | 200 | 版本与数据库状态 |
| `GET /api/prometheus/grafana/api/v1/rules` | **200** | 2 分组 / 15 规则 / 约 31 KB |
| `GET /api/v1/provisioning/alert-rules` | **403** | `accessErrorId/title/message` |
| `GET /api/annotations?type=alert&limit=100` | **200** | 100 条（上限），约 55 KB |
| `POST /api/ds/query` | **200** | `results.<refId>.frames[]` |

### 规则定义的实际形态（以 createOrder 为例）

`/api/prometheus/grafana/api/v1/rules` 返回的每条 rule 结构：

- `name`：告警名（中文）
- `query`：**字符串形式的 JSON 数组**，含 A/B/C 三个 refId
  - `A`：SLS 查询（`datasource.uid = dej5bbsvyrksgf`，`logstore = welife-c53n`）
    `* | select avg(upstream_response_time)*1000 as "支付单createOrder", __time__-__time__%60 as t where request_uri = '/welife-pay/pay/createOrder' group by t`
  - `B`：`__expr__` reduce，`expression: "A"`，`reducer: "last"`
  - `C`：`__expr__` threshold，`expression: "B"`，`evaluator: {type:"gt", params:[1000]}`
- `duration`：**300**（秒，即 for 5 分钟）
- `relativeTimeRange.from`：**21600**（查询回看 6 小时）
- `annotations.__dashboardUid__` / `__panelId__`：可回链到面板
- `labels`：仅 `alertname / grafana_folder / noticeA / noticeB / severity`，**无 uri、无 service**

**含义**：告警条件是「连续 5 次评估，每次取最近 1 分钟的 avg，均 > 1000ms」。uri 与阈值都能从规则定义解析得到，不必额外加 label。

### 告警历史（annotations）的实际形态

`GET /api/annotations?type=alert&limit=100&from=&to=` 返回数组，元素形如：

- `time` / `timeEnd`：多为同一时刻（**每分钟一条，不是触发→恢复区间**）
- `tags`：为空
- `text`：`接口成功率低于95% {alertname=..., grafana_folder=..., noticeA=..., severity=...}`

**影响**：无法直接得到"某次告警持续了多久"，需要按 alertname 聚合相邻分钟点、或用状态变化推断；`limit=100` 是上限，跨度大时需分页。

### 查询代理（ds/query）

请求：

```json
{
  "from": "<epoch_ms>", "to": "<epoch_ms>",
  "queries": [{
    "refId": "A",
    "datasource": {"type": "aliyun-log-service-datasource", "uid": "dej5bbsvyrksgf"},
    "logstore": "welife-c53n",
    "query": "* | select avg(upstream_response_time)*1000 as avg_ms, count(*) as cnt, __time__-__time__%60 as t where request_uri = '/welife-pay/pay/createOrder' group by t order by t",
    "type": "all", "xcol": "t", "ycol": ""
  }]
}
```

返回：

```json
{"results":{"A":{"status":500,"frames":[{
  "schema":{"fields":[{"name":"time"},{"name":"avg_ms"},{"name":"cnt"}]},
  "data":{"values":[[<时间序列>],[<avg_ms>],[<cnt>]]}
}]}}}
```

**注意两点**：

1. `status` 恒为 **500**，即使查到了数据；必须看 `frames[].data.values` 是否为空。
2. 返回是**列式**（多字段 → 多个数组），不是行式。

### 用 ds/query 复核 createOrder 的流量（近 6 小时）

- 90 个分钟点
- `avg_ms`：647 ~ 695
- `cnt`：**2 单/分钟**

对照告警阈值 1000ms：触发意味着某几分钟内「2 单的平均」都超过 1 秒，是持续数分钟的轻度劣化，而非单条请求抖动。

## 反面与风险

- **端口依赖**：本机测试环境 443 不通、80 通。生产环境是否同样如此未验证；若某侧只开 443，需分别确认。
- **`status: 500` 的误导性**：任何按 HTTP 惯例写"非 2xx 即失败"的客户端都会把所有查询判为失败。
- **Viewer 的权限边界**：provisioning 接口 403 属预期；若未来要做自动静默（silence）或改规则，需要另行评估更高权限，**不建议直接给归因服务提权**。
- **annotations 语义**：它是"每分钟快照"，不是事件流；直接统计条数会高估告警次数。
- **规则 query 是字符串套 JSON**：需要二次 `json.loads`，且不同规则结构不一致（有的直接是 PromQL 字符串，如网关类规则）。

## 待确认项

1. 生产环境访问该 Grafana 的端口与网络路径（是否同样只开 80）。
2. annotations 在时间跨度大时的分页行为与总量级。
3. 网关类规则（Prometheus / PromQL）能否同样通过 `/api/ds/query` 查询，返回形态是否一致。
4. Token 有效期策略与轮换方式（本次验证用的 token 已暴露在对话中，应重建）。

## 来源

| 来源 | 类型 | 用途 |
|---|---|---|
| https://grafana-cn-2r42scs2308.grafana.aliyuncs.com:80/api/health | 实测 | 版本 10.0.9，Aliyun-ARMS |
| https://grafana-cn-2r42scs2308.grafana.aliyuncs.com:80/api/prometheus/grafana/api/v1/rules | 实测 | 规则定义（200 / 2 组 15 条） |
| https://grafana-cn-2r42scs2308.grafana.aliyuncs.com:80/api/v1/provisioning/alert-rules | 实测 | Viewer 403 |
| https://grafana-cn-2r42scs2308.grafana.aliyuncs.com:80/api/annotations?type=alert | 实测 | 告警历史形态 |
| https://grafana-cn-2r42scs2308.grafana.aliyuncs.com:80/api/ds/query | 实测 | 查询代理与 frames 形态 |
| https://help.aliyun.com/zh/arms/observable-visualization-grafana-edition/api-description | 官方文档 | 「完全兼容开源 Grafana HTTP API」 |
| https://help.aliyun.com/zh/arms/observable-visualization-grafana-edition/use-api-key-to-share-grafana-dashboards | 官方文档 | 10.x 起 API Key 由 Service Account Token 取代；11.x 移除 |
