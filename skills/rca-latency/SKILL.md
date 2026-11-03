---
name: rca-latency
description: 响应时间类告警的三档判定
user-invocable: false
disable-model-invocation: true
match: ".*响应时间.*"
evidence:
  - id: stats
    tool: latency_stats
    required: true
    timeout: 8
    params:
      uri: "${alert.uri}"
  - id: detail
    tool: request_detail
    required: false
    timeout: 8
    when: "stats.total < 20"
    params:
      uri: "${alert.uri}"
  - id: impact
    tool: business_impact
    required: false
    timeout: 8
---

## 判定顺序

1. 先看样本量：0 条判"日志断流或服务不可用"；1–19 条逐条比对明细；≥20 条才谈趋势。
2. 看节点分布：单节点显著高于其他节点 → 单容器问题；全节点一致 → 共享依赖或流量。
3. 看上游耗时占比：接近总耗时 → 瓶颈在后端；远小于总耗时 → 网关排队。
4. 查近 7 天同告警是否反复出现且自愈。

## 禁止

- 不得以"未找到原因"推断无需处理。
- 不得引用"语义未知"的数值。
- 不得在文本中复述证据里的数字（数字由卡片渲染层填充）。
