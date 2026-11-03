# 证据工具说明

判定只依赖以下三个取数工具，均实现于 `src/rca/sources/base.py` 的 `LogSource` 协议。
输出结构即证据 `data`，渲染层按同一结构取数，勿在判定文本里复述具体数字。

## latency_stats

按上游节点聚合窗口内请求。

- 入参：`uri`（接口路径）、`start_ts` / `end_ts`（秒级时间窗，由采集器注入）
- 返回：`{"total": int, "latency_ms": {"p95": float}, "slow": {"threshold": int, "count": int}, "by_node": [{"addr": str, "cnt": int}]}`
- 用途：样本量、p95、慢请求占比、节点分布

## request_detail

窗口内的请求明细，用于小样本逐条比对。

- 入参：`uri`、`start_ts` / `end_ts`、`limit`（默认 100）
- 返回：`[{"ts", "upstream_addr", "request_time_ms", "status"}]`
- 用途：逐条看耗时构成与状态码，区分网关排队与后端慢

## business_impact

核心接口当前窗口与昨日同时段的请求量对比。

- 入参：`start_ts` / `end_ts`（核心接口清单来自配置 `RCA_BUSINESS_URIS`）
- 返回：`{"endpoints": [{"uri", "pv", "base_pv"}]}`
- 用途：判断是否为全局流量变化，而非单接口劣化

## 三态

- `ok`：拿到数据
- `empty`：查询成功但无数据（如窗口内无请求）
- `failed`：未查到（超时 / Grafana 报错 / 字段缺失）

必需源（`required: true`）为 `failed` 时采集立即结束，不跑后续证据。
