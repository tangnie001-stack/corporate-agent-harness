# ADR-0013：trace 保留期清理的后端定为直连 Langfuse PostgreSQL 的 SQL

- **Status**：Accepted（**已被 ADR-0029 取代**——v4 后清理改走官方删除 API）
- **Date**：2026-09-23
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **关系**：为 ADR-0012 的「30 天保留期」提供**可工作的删除机制**，不取代 0012 —— 0012 定「记原文 + 留 30 天」并落了清理 CLI 的入口与护栏，本 ADR 提供其真正可工作的删除后端。

## 背景与问题

ADR-0012 决定 trace 记录 prompt / 回答原文并保留 30 天，但保留期只是「到期该删」的意图 —— **删除动作由谁、通过什么接口执行**尚未落地。原设计的前提是「调用 Langfuse 的公开删除 API」；本次实测推翻了该前提。

实测证据（2026-09-23，dev 容器 `corporate-rag-langfuse-web`，镜像 `langfuse/langfuse:2.95.11`，api key 基本认证）：

| 请求 | 结果 |
|---|---|
| `GET /api/public/traces` | 200 |
| `GET /api/public/traces/{traceId}` | 200 |
| `DELETE /api/public/traces`（body `{"traceIds":[...]}`） | **405 Method Not Allowed** |
| `DELETE /api/public/traces/{traceId}` | **405** |
| `POST /api/public/traces/delete` | 405 |
| `/api/openapi.yaml` / `/api/docs` / `/api/openapi.json` | 404（v3 才提供） |

- **不是客户端 URL 写错**：本项目 pin 的 SDK `langfuse==2.60.10` 里，两个删除方法分别打 `DELETE api/public/traces/{trace_id}` 与 `DELETE api/public/traces` + `{"traceIds": [...]}` —— **正好落在这两条 405 路上**。
- **根因是版本差，不是 license 门禁**：GitHub Discussion #5952（2025-03）中他人同样拿到 405，结论是「endpoint 是新的、容器太旧」，升级到最新 v3 后 self-hosted 删除正常。容器 bundle 内存在 `traces.deleteMany`（Prisma），说明 v2 服务端**内部**有删除能力（UI 用），只是**没有挂到公开 API**。
- **真正 enterprise-gated 的是 Data Retention**：官方可用性表 `Hobby ✗ / Core ✗ / Pro ✓ / Enterprise ✓ / Self Hosted = Enterprise Edition`，且「On self-hosted instances, data is stored indefinitely by default」。
- **官方社区对 v2 self-hosted 保留期给出的答案就是清 PostgreSQL**：Discussion #3949（2024-10）：「Currently, Langfuse does not provide built-in support for automated batch deletion of trace data based on a retention policy. You would need to manually manage data retention by running cleanup tasks on your PostgreSQL database. You can purge the trace and observation tables based on the `created_at` timestamp」，并建议「write a custom script」。

同时需先明确**删除面**（v2.95.11 真实 schema，实测）：

- `traces` 表含 `id` / `timestamp`（无时区）/ `input`(jsonb) / `output`(jsonb) 等列 —— **prompt 与回答原文就在 `input` / `output` 里，位于 PG 内，不在对象存储**。
- 含 `trace_id` 的表：`observations`、`scores`、`trace_media`、`observation_media`、`dataset_run_items`。
- `trace_sessions.id` 即 session_id，**无** `trace_id` → 某 session 的 trace 全删后会留下**空 session 行**。
- `comments` 用多态 `object_type` / `object_id`，**无** `trace_id`；`events` 是 v2 遗留摄取表，**无** trace_id 且当前 0 行。
- **没有任何外键指向 `traces`** → 级联删除必须由我们自己保证。
- 当前行数：`traces=8 / observations=0 / scores=0 / trace_sessions=5 / trace_media=0 / projects=1`；minio 里只有本项目的 `documents` 桶，**没有 langfuse 的媒体桶**。
- app 容器 env 已有 `LANGFUSE_POSTGRES_PASS`；langfuse 库是同一 PG 实例上的独立 database（user `langfuse`），保留期常量与清理 CLI 入口见 `src/config/const.py:319-324` 与 `src/cli/purge_langfuse_traces.py`。

要回答的问题：**在「不升级 v3、也不购买企业授权」的前提下，用哪条路径执行保留期删除。**

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 升到 v3/v4，用官方删除 API | 升级 Langfuse 后调用其公开删除接口 | 重新引入 ClickHouse / worker（ClickHouse 最小 2 CPU·8 GiB）等整套有状态服务，**推翻 ADR-0011**；v2→v3 须经 v3.29.0 中转，且历史 trace 不会自动上行 | 用官方接口，无自研删除逻辑 |
| B 只依赖 Enterprise 版 Data Retention | 启用企业版自带的保留期功能 | self-hosted 需企业授权，**闭源门禁**；为一条删除需求整体抬升授权面 | 官方维护，无需自研 |
| C 放弃自动清理，把「trace 无界增长」作为显式残留 | 不做删除，承认库会一直增长 | ADR-0012 的「记原文」取舍正是靠 30 天保留期对冲，放弃保留期会让那个取舍不成立；存储无界增长 | 零实现成本 |
| **D 直连 `langfuse` 库跑 SQL（选中）** | 复用既有清理 CLI 入口与护栏，仅把删除后端换成对 PG 的 SQL 删除 | 自研删除逻辑，且**我们的代码写入第三方（Langfuse）schema** | 不新增任何服务 / 组件；删除同步即时、便于验证 |

## 决策

**选 D：保留期删除改为直连 `langfuse` 库执行 SQL。** 具体决策：

1. **删除后端** = 直连 `langfuse` 库跑 SQL。不用 v3 才有的公开删除 API、不用 Enterprise 版 Data Retention、不升级 v3。
2. **复用既有 CLI 入口与全部护栏**（dry-run / 保留期下界 1 天 / 单次删除上限 1000 / 非 dry-run 需 `--yes` 且 `LANGFUSE_PURGE_ALLOW=1` / 审计输出），**只换删除后端**；默认保留期 30 天与 `--retention-days` 语义不变。
3. **删除面与顺序**：对每个超期 trace_id，在同一事务内依序删 `observations` → `scores` → `trace_media` → `observation_media` → `traces`；**并连带清理已无任何 trace 的 `trace_sessions` 行**（避免 UI 里留下空会话）。
4. **超期判据 = `traces.timestamp`**，与官方 Data Retention 对 Traces 的判据一致。
5. **作用域 = 本项目**：这是单 project 部署；**库内 project 数 ≠ 1 时拒绝执行**（防止误删其它项目的 trace）。
6. **接受「我们的代码写入第三方 schema」这一耦合**；缓解是 v2.95.11 已是 EOL 定版、schema 实际冻结，并以复查触发条件兜底。

## 理由

关键一行：**要删的东西本来就在我们自己复用的那个 PG 实例里，而唯一「官方」的删除通道要么不存在（v2 无公开 API）、要么要付门禁代价（v3 / Enterprise）—— 为一条删除需求去承担一整套有状态服务或一份企业授权，代价与收益不成比例。**

- **不选 A**：v3+ 的删除 API 是「新的」，用它就得先升级；而升级正是 ADR-0011 明确否掉的路（重新引入 ClickHouse / worker，推翻已决结论）。自研 SQL 删除比「升一个大版本 + 引入多套有状态服务」小得多。
- **不选 B**：Data Retention 在 self-hosted 属企业版功能，是闭源门禁。为一条按时间批量删除去买授权，等于用整体授权面换一个窄功能。
- **不选 C**：ADR-0012 之所以敢「记原文」，正是以 30 天保留期对冲留存面；放弃保留期会让 0012 的取舍前提不成立（原文无界留存）。这不是「省了实现」，而是把风险挪进未决状态。
- **选 D 的正面依据**：prompt / 回答原文就在 `traces.input` / `output`（jsonb），**在同一 PG 实例内的独立 database**，app 侧已有凭据；官方社区对 v2 self-hosted 保留期给出的建议正是「清 PostgreSQL / 自写脚本」。此路径**不新增任何服务或组件**，且删除是**同步即时**的（与官方 API 的「异步、通常 15 分钟内、无删除确认」不同，这是我们的实现差异；该口径出自 Langfuse 官方 Data Deletion 文档：「Usually, trace data is deleted from our system within 15 minutes of the delete call. There is no deletion confirmation or notification」，https://langfuse.com/docs/administration/data-deletion ）。

## 后果

**正面**：

- **不新增任何服务 / 组件**：对比升 v3 要加 ClickHouse / worker / Redis / S3，本方案只在既有 PG 上执行删除。
- **删除同步即时、便于验证**：不像官方 API 那样异步且「通常 15 分钟内」（Langfuse 官方 Data Deletion 文档：「within 15 minutes of the delete call」，https://langfuse.com/docs/administration/data-deletion ），执行完即可核对行数。
- 复用既有 CLI 与全部护栏，误删防线（保留期下界 / 单次上限 / 双重确认 / 审计输出）保持不变。

**负面 / 接受的代价**：

- **我们的代码写入第三方（Langfuse）的 schema**：删除语句依赖 `traces` / `observations` / `scores` 等的表名与列名，属跨系统耦合。缓解：v2.95.11 已是 EOL 定版、schema 实际冻结；并以复查触发条件① / ② 兜底（升级或 schema 变动即重估）。
- **单-project 作用域既是守卫也是限制**：实现以「库内 project 数 ≠ 1 即拒绝」防止误删其它项目；一旦部署变为多 project，该守卫会让清理直接失效，需重评。
- **无外键可依，级联删除由我们自己保证**：必须显式按依赖顺序删除，漏删会留下孤儿行。

**不解决的问题**（明确列出，避免后人误以为这条 ADR 管了它）：

- **blob / 媒体的物理删除**：当前 `trace_media=0` 且 minio 无 langfuse 媒体桶，故不处理对象存储中的 blob。
- **`dataset_run_items`**：沿用官方语义 —— 数据集项**不随**源 trace 消失，只让源链接失效。
- **`comments`**：多态引用，当前无 trace 级评论，故不处理。
- **不做脱敏**：承接 ADR-0012 留下的 `mask` 回调遗留，本 ADR 只定删除后端。

## 复查触发条件

1. **升级 Langfuse 到 v3+** → 应改回官方删除 API 并撤掉本 SQL 耦合。
2. **Langfuse 的 schema 发生任何变化**（打破「v2.95.11 冻结」这一前提）→ 重估删除面与删除语句。
3. **引入多模态 / 媒体 trace 使 `trace_media` 非空** → 需处理对象存储里的 blob。
4. **Langfuse 恢复 v2 维护并后向移植了删除 API** → 重估是否切回官方接口。
5. **变为多 project 部署** → 第 5 条的单-project 作用域需重评。
