# ADR-0029：trace 保留期清理改用 Langfuse 官方删除 API

- **Status**：Accepted
- **Date**：2026-11-09
- **Deciders**：用户（拍板：可清历史、可停机）；Claude（调研与实施）
- **关系**：**完全取代 ADR-0013**（直连 Langfuse PostgreSQL 跑级联 SQL）。ADR-0012 的「记原文 + 30 天保留期」仍有效，本 ADR 只更换其删除机制。

## 背景与问题

ADR-0013 的实现前提是「trace / observation / score 都在 Langfuse 的 PostgreSQL 里」，因此用直连 SQL 按 `CASCADE_DELETE_ORDER` 手动级联，并清理空的 `trace_sessions` 行。

升级到 v4（ADR-0028）后该前提消失：观测数据落 **ClickHouse**，PG 只保留事务数据。原 SQL 会命中不存在的表或恒为 0 条——**dry-run 显示「命中 0 条」会被误读为「没有超期数据」，保留期静默失效**。

v4 的 OSS 计划自带 `trace-deletion` 能力，公开 API 提供单条与批量删除；LANGFUSE 官方在删除时会同步清理关联的 observations 与 scores。

## 决策

1. **列举**：用 SDK 5 的 Observations API v2（`api.observations.get_many`，`is_root_observation=True` + `to_start_time=cutoff`，cursor 分页）取超期 trace 的 id 与 session_id。
2. **删除**：用 `api.trace.delete_multiple(trace_ids=[...])` 批量删除；单条路径保留 `api.trace.delete(id)`。
3. **保留 CLI 护栏不变**：dry-run、保留期下界、单次上限、双重确认、审计输出均保留。
4. **删除后置清理**：不再清理空 session（v4 无本地 `trace_sessions` 表，会话视图由后端按数据聚合）；`CascadeCounts` 中的子表计数置 0，仅 `traces` 记实删条数。
5. **去掉 project 作用域守卫**：API key 本身即 project 作用域，v4 不再需要「库内 project 数必须为 1」的本地假设。

## 理由

1. **唯数据源已变**：v4 的观测数据只在 ClickHouse，直连 PG 的 SQL 在语义上已失效。
2. **不自建级联**：官方删除 API 保证关联数据一致，避免复制一份与上游 schema 耦合的删除顺序契约。
3. **审计口径保留**：即使子表计数由后端完成，CLI 仍输出「请求删除 N 条、实际成功 M 条」，可核对。

## 被否的替代方案

- **(A) 直连 ClickHouse 执行 DELETE / mutation**：需要自己维护表名、分区与 mutation 语义，且与上游 schema 强耦合；一旦 schema 变更即失效。
- **(B) 设置自托管 Data Retention**：该能力属企业版，开源版无此配置项。
- **(C) 不做清理**：trace 会无限增长，且含用户原文与知识库片段，合规上不可接受。

## 后果

- 子表级联计数不再逐表可见，审计粒度从「逐表行数」降为「成功 trace 数」；
- 删除是服务端行为，UI 可见性可能存在短暂延迟，验收以「按 id 查询返回不存在」为准；
- 定时任务仍未接入（沿用原欠账，另立变更）。

## 复查条件

- 若官方删除 API 在自托管 v4 上不可用（授权或权限问题），回退到 ClickHouse 直连方案并重写本 ADR；
- 若后续需要「按 session / filter 批量删」，扩展为官方支持的批量删除入口，不回到自建 SQL。
