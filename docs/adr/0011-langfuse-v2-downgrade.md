# ADR-0011：自托管 Langfuse 由 v3 降至 v2，去掉 ClickHouse 与 worker

- **Status**：Accepted（**「暴露面收敛到仅回环」一条已被 ADR-0019 取代**；**整体已被 ADR-0028 取代**——2026-11-09 起升级到 v4 并全新部署）
- **Date**：2026-09-22
- **Deciders**：用户（决策）；Claude（调研、实测与评审）
- **Supersedes**：ADR-0004 的**一条附带陈述**（局部）。ADR-0004 写「trace 在 ClickHouse」；本决策后 trace 落在 PostgreSQL。ADR-0004 的主决策（**一个 PG 实例、两个 database**）不但仍成立，且被本决策强化 —— 降级后 Langfuse 完全依赖它。

## 背景与问题

自托管 Langfuse 自 v3 起把 **ClickHouse、独立 worker、S3 事件存储**变成**启动硬依赖**（官方 FAQ：所有自托管部署都必须包含一个 ClickHouse 实例）。对初期量级的部署，这是在为用不到的能力维护一整套有状态服务：

- **prod compose 的 ClickHouse 常驻 `mem_limit: 8g`**（`docker-compose.prod.yml`，移除前）
- **本机实测：三个 v3 容器从未创建** —— `corporate-rag-langfuse-web / -worker / corporate-rag-clickhouse` 全部 `No such container`，该 profile 长期未启用
- **tracing 链路构造性失效**：`@traced`（`src/infra/llm/langfuse_tracing.py:34`）全仓**零应用点**；`current_tracer` 的唯一读取点在 `src/rag/stream.py` 的 `stream_answer`，而该函数**全仓零调用方**（死代码）。即使打开 `LANGFUSE_ENABLE` 也不产出 trace
- **prompt 已按 ADR-0010 出列**，且 `.env` 中 `LANGFUSE_ENABLE=false` → 实际走本地兜底
- 规模定位：小公司初期，trace 维持在**十万级**

因此 v3 的收益（OLAP trace 存储、异步处理）在当前是**空的**，成本（三套有状态服务 + 运维）是**实的**。

## 候选方案

| 方案 | 做法 | 代价 | 收益 |
|---|---|---|---|
| A 保留 v3 | 维持现状 | 三套有状态服务常驻；prod 8g 内存上限；仍需运维 ClickHouse | trace/OLAP 能力完整；与官方主线一致 |
| **B 降至 v2.95.11** | 只留 `langfuse-web` + 复用既有 PG | **EOL，无安全补丁承诺**；升级需经 v3.29.0 中转；v2 期间 trace 不能自动上行 | 净减两个容器与 8g 上限；**不新增任何存储**；本机 `src/` 零改动 |
| C 整体去掉 Langfuse | 不再自托管可观测后端 | 失去 UI；**升级通道被堵死**（无处验证 prompt 远端与 tracing） | 归零运维 |

**选项 C 不是空想**：先前调研（`docs/tmp/deep-research-langfuse-postgres-only.md` §9.4）正主张「不建议为了只用 PG 而新上 EOL 的 v2」，优先「去掉 Langfuse / Phoenix / MLflow」。

## 决策

**选 B：降至 `langfuse/langfuse:2.95.11`，删除 `clickhouse` 与 `langfuse-worker`，复用既有 PostgreSQL。**

## 理由

关键一行：**Langfuse 不在请求路径上，所以"A 或 C"两边的核心论据都不成立。**

- 选项 C 与先前调研反对 v2 的理由是「把 EOL 服务放进关键路径」。但本仓实测：**langfuse-web 停掉后，应用仍能完整跑完一轮问答**（SSE 事件 `status → 33×token → model_info → agent_used → done`，**error 数 0**）。prompt 走本地兜底、tracing 未接线 → 它当前是**旁路**，不是关键路径。
- 选项 A 的收益（trace 的 OLAP 存储）当前无人消费。
- 选 B 还**保住了升级通道**：留一个可运行的后端，将来验证 prompt 远端与 tracing 接线时有落点；整体去掉则这条通道断掉。
- 代价可被结构性对冲：暴露面收敛到**仅回环**（实测 `172.31.144.17:3000` 连接失败），且承载的数据是空的。

## 后果

**正面**：

- 净减两个服务；prod 少一处 8g 内存上限与一套 ClickHouse 运维
- **不新增任何存储**：仍是一个 PG 实例、两个 database（强化 ADR-0004 的主决策）
- **`src/` 零改动**：SDK 早已 pin 在 Python SDK v2，v2 服务端为官方 Full 支持组合
- **凭据连续**：经 `LANGFUSE_INIT_PROJECT_ID` / `_PUBLIC_KEY` / `_SECRET_KEY` 播种，库重建后 `.env` 那对 key **实调可用**（非仅字符串相同）
- 跨容器可达性可控：显式 `HOSTNAME=0.0.0.0`（实测 `http://langfuse-web:3000` 从 app 容器返回 `{"status":"OK","version":"2.95.11"}`）

**负面 / 接受的代价**：

- **v2.95.11 属 EOL 线，不再有安全补丁承诺**。缓解：端口仅绑回环，不对外暴露，不承载公网流量
- **升级锁**：未来升 v3/v4 须经 `v3.29.0` 中转、跨两个大版本；且**v2 期间累积的 trace 不会自动上行**（官方 v2→v3 是后台 PG→ClickHouse 搬运）→ 明确接受"升级时丢弃历史 trace"
- **回滚成本高**：切回 v3 需重新引入 ClickHouse/worker/S3 并重建库，实际是"重新部署 v3"。且 `clickhouse_data` 卷已按要求删除，**回退的数据面依据一并消失**（用户明确接受）→ 风险控制靠"独立分支 + 验证通过前不合入"
- **minio 镜像按 index 摘要锁定，代价是不再自动获得 CVE 重建**：`cgr.dev/chainguard/minio` 只发布 `latest` / `latest-dev`，**无版本 tag**（实测 1000 个 tag 中非签名 tag 仅此两个），因此"锁版本"只能锁 digest。**dev 与 prod 同锁 index 摘要** `sha256:a3c85091…`（index 含 amd64/arm64；**不锁平台摘要**，否则另一架构拉取会失败）。收益：可复现，且"dev 验过的 = prod 跑的"成立。代价：`latest` 原本会自动流入 Chainguard 的持续 CVE 重建，锁死后**须按 CVE 公告人工 bump**（步骤见复查条件⑥）
- **prod 侧未运行验证**：prod 从未部署过 v3，本变更对 `docker-compose.prod.yml` 只做静态校验（`config` + 与 dev 同构对照）

**不解决的问题**（明确列出，避免后人误以为本 ADR 管了它）：

- **不接线 tracing** —— 另案（`@traced` 无应用点、`stream_answer` 是死代码，接线是一次独立改造）
- **trace 保留/清理机制未落地** —— Langfuse 的 Data Retention 在自托管下属企业版功能，OSS v2 无此开关。**残留（显式接受）**：一旦接线 tracing 而清理尚未落地，Langfuse 库中的 trace 将**无界增长**
- **不换 S3 实现** —— `cgr.dev/chainguard/minio` 只是权宜，见复查条件⑤
- **不处理 prod 的凭证变量名分裂** —— prod compose 读 `MINIO_ROOT_*`，app 读 `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`，两组值须一致

## 复查触发条件

1. **trace 量级超过既定阈值** → 评估 v3/v4 迁移
2. **Langfuse 首次被真正接入请求路径**（prompt 远端读取开启，或 tracing 接线）→ 重新评估本降级决策（这是选项 C 与先前调研的核心争点所在，届时它可能成立）
3. **接线 tracing 的那次变更必须一并落地 trace 保留/清理机制**（承接上文"不解决的问题"）
4. `src/config/settings.py:305` 的 `LANGFUSE_HOST` 陈旧默认值（`http://langfuse:3000`，仓库内无此服务名）另行清理 —— 本次未碰以维持 `src/` 零改动边界
5. **`cgr.dev/chainguard/minio` 不可长期依赖** → MinIO 上游 2026.09 已删 Docker 镜像且仓库存档，该镜像为第三方加固重建。需另立议题替换 S3 实现（Garage / RustFS / VersityGW / SeaweedFS 等）
6. **minio 的 digest 需要人工 bump（这是锁定带来的例行义务）** → 当前锁 `sha256:a3c85091…`；Chainguard 的 CVE 重建**不会自动流入**。须定期、或见其 CVE 公告时更新，步骤：① 取 `latest` 的新 index 摘要 → ② `docker pull cgr.dev/chainguard/minio@<新摘要>` 验证可拉（**含目标架构**）→ ③ 改两份 compose → ④ 更新本条 ADR 与本文件记录的摘要 → ⑤ 重启后确认 `documents` 桶与原始上传文件可读
   另：`cgr.dev` 不被国内加速器代理，是直连，**首次 prod 部署前必须先试一次 pull**
