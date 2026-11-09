## MODIFIED Requirements

### Requirement: 服务端版本与必需组件集

系统 SHALL 以 Langfuse **v4 线的最新稳定镜像**作为自托管可观测后端，并 SHALL 在部署档中记录**实际使用的版本 tag**（不得使用浮动主版本 tag）。部署 SHALL 由 Langfuse web 容器、Langfuse worker 容器、ClickHouse、既有 PostgreSQL、既有 Redis 与对象存储构成；web 与 worker SHALL 使用**同一版本 tag**（同一次构建），以避免 schema 版本不一致。

ClickHouse SHALL 取最新 LTS 且版本 ≥ 25.12（v4 的最低要求：轻量更新、JSON 类型与全文检索）；部署 SHALL 关闭集群模式（单节点），SHALL 保持时区为 UTC。

#### Scenario: compose 中存在 v4 必需组件

- **WHEN** 检查 `docker-compose.yml` 与 `docker-compose.prod.yml` 的服务集合
- **THEN** 存在 `clickhouse` 与 `langfuse-worker` 服务
- **AND** `langfuse-web` 与 `langfuse-worker` 的镜像 tag 相同且为具体版本号
- **AND** ClickHouse 版本 ≥ 25.12 且 `CLICKHOUSE_CLUSTER_ENABLED=false`

#### Scenario: v4 必需 env 齐备

- **WHEN** 检查 `langfuse-web` / `langfuse-worker` 的环境变量
- **THEN** 含 `DATABASE_URL`、`NEXTAUTH_URL`、`NEXTAUTH_SECRET`、`SALT`、`ENCRYPTION_KEY`
- **AND** 含 `CLICKHOUSE_URL`、`CLICKHOUSE_USER`、`CLICKHOUSE_PASSWORD`、`CLICKHOUSE_MIGRATION_URL`
- **AND** 含 `REDIS_CONNECTION_STRING`（或等价的分项形式）
- **AND** 含 `LANGFUSE_S3_EVENT_UPLOAD_BUCKET` 及其访问配置
- **AND** 保留 `LANGFUSE_INIT_*`（含 `LANGFUSE_INIT_PROJECT_ID`、`_PUBLIC_KEY`、`_SECRET_KEY`），且与应用侧 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 成对相等

#### Scenario: 健康检查先于依赖启动

- **WHEN** 整栈冷启动
- **THEN** ClickHouse 具备 healthcheck，`langfuse-web` / `langfuse-worker` 在其 healthy 之后启动
- **AND** 首启日志中不出现 ClickHouse 连接拒绝或 schema 迁移失败

### Requirement: 存储复用既有 PostgreSQL

Langfuse SHALL 复用应用所在的同一 PostgreSQL 实例的**独立 database** 承接事务型数据（组织/项目/用户/评分配置/模型定义/数据集元数据），观测型数据（traces / observations / scores）SHALL 落 ClickHouse，原始事件 SHALL 落对象存储桶。ClickHouse SHALL NOT 作为应用业务数据的存储。

#### Scenario: 三类存储各就各位

- **WHEN** 启动 `langfuse-web` 与 `langfuse-worker`
- **THEN** 其 `depends_on` 覆盖 `postgres`、`clickhouse`、`redis`、`minio`（均以 healthy 为条件）
- **AND** `DATABASE_URL` 指向既有 `postgres` 服务上的 Langfuse database
- **AND** `CLICKHOUSE_*` 指向新增 `clickhouse` 服务
- **AND** `LANGFUSE_S3_EVENT_UPLOAD_*` 指向既有 `minio` 上的事件桶

#### Scenario: 数据库 schema 与新库一致

- **WHEN** `langfuse-web` 首次启动并执行自动迁移
- **THEN** 迁移在空的 Langfuse database 上从零完成
- **AND** 日志中不出现迁移历史不匹配或 `permission denied` 类错误

### Requirement: 单元资源上限

Langfuse web SHALL 声明显式 `mem_limit`（不低于 `1g`），worker 不低于 `512m`，ClickHouse 不低于 `1.5g`；web 与 worker SHALL 配置 `NODE_OPTIONS=--max-old-space-size`，使其堆上限小于容器内存上限。取值须相对实测空载占用留有可测余量。

#### Scenario: 内存上限为确定值且可容纳空载

- **WHEN** 检查三份 compose 中 `langfuse-web` / `langfuse-worker` / `clickhouse` 的服务定义
- **THEN** 三者均声明 `mem_limit`
- **AND** web 与 worker 设置 `NODE_OPTIONS`
- **AND** 冷启动后三容器 RSS 均低于各自上限的 80%

#### Scenario: 整栈不触发 OOM

- **WHEN** 在 8 GiB 目标机上运行整栈完成一轮对话
- **THEN** 不发生 OOM kill
- **AND** 宿主机可用内存仍为正

### Requirement: 退役资源不遗留

组件退役后 SHALL NOT 留下其运行期资源（容器、镜像引用、database、命名卷）；同时 SHALL NOT 删除仍在使用的资源（含新增的 ClickHouse 数据卷与对象存储事件桶）。

#### Scenario: 旧 v2 资源已清而在用资源完好

- **WHEN** 验收完成后检查容器、database、命名卷与桶
- **THEN** 不存在 v2 的 `langfuse-web` 容器、旧 `langfuse` database、`langfuse:2.95.11` 镜像引用
- **AND** 仍存在 `corporate_rag_postgres_data`、`corporate_rag_redis_data`、`corporate_rag_minio_data`、`corporate_rag_app_logs`
- **AND** 存在 ClickHouse 的命名数据卷与对象存储的事件桶

### Requirement: 凭据连续性

Langfuse database 重建后，`.env` 中的 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` SHALL 仍然有效。系统 SHALL 经 `LANGFUSE_INIT_PROJECT_ID` 与 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY` 播种既有 key，SHALL NOT 要求重新签发。

播种 SHALL 依赖三者齐备：`LANGFUSE_INIT_PROJECT_ID` 是载体，缺它时 project 与 key 均不创建（且不报错），凭据连续性即不成立。

#### Scenario: 重建后 key 不变

- **WHEN** 新建 `langfuse_v4` 空库并首次启动 v4，且 `LANGFUSE_INIT_PROJECT_ID` 与 keys 对均已配置
- **THEN** UI 中的 project 持有与 `.env` 相同的 key（secret 在 UI 中为掩码显示，以前缀/后缀核对）
- **AND** 用该 key 可成功完成一次写入

#### Scenario: 缺 PROJECT_ID 则凭据失效（负例）

- **WHEN** 在空库上首次启动，仅配置 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` / `LANGFUSE_INIT_PROJECT_SECRET_KEY` 而缺 `LANGFUSE_INIT_PROJECT_ID`
- **THEN** project 与 key 均不被创建，`.env` 中的 key 失效

## REMOVED Requirements

### Requirement: EOL 版本的暴露面收敛

**Reason**: v4 是当前维护的主线版本，不再是 EOL 版本，该 requirement 的前提消失。

**Migration**: 由新增的「版本维护与暴露面收敛」承接：保留"端口不直接对外"的约束，去掉"因 EOL 而收敛"的理由。

## ADDED Requirements

### Requirement: 版本维护与暴露面收敛

自托管可观测后端的版本 SHALL 跟随上游维护中的主线（v4），并 SHALL 记录实际部署版本与升级时间；web 端口 SHALL NOT 直接对公网暴露，生产 SHALL 经既有 nginx 反代访问。

#### Scenario: 端口不经公网直连

- **WHEN** 检查 dev 与 prod 的端口映射
- **THEN** 生产不发布 web 端口，由 nginx 的 `langfuse.<domain>` 反代
- **AND** dev 仅绑定回环地址

#### Scenario: 版本可追溯

- **WHEN** 审阅部署档
- **THEN** 能读到当前 Langfuse 与 ClickHouse 的具体版本号

### Requirement: 运行数据持久化

ClickHouse 与对象存储 SHALL 使用命名卷或持久化路径：容器重建、`docker compose down/up`、宿主机重启后，已采集的 trace 与原始事件 SHALL 仍然存在。

#### Scenario: 重建容器后数据仍在

- **WHEN** 完成一轮对话并对 `langfuse-web` / `langfuse-worker` / `clickhouse` 执行 `docker compose restart`
- **THEN** 重启前产生的 trace 仍可按 id 查询到
- **AND** 对象存储中的事件文件仍存在

### Requirement: 全新部署与旧库处置

本次升级 SHALL 使用**新的 Langfuse database**，SHALL NOT 在既有 v2 schema 之上执行 v4 迁移；旧实例与其数据 SHALL 在验收通过后删除，SHALL NOT 作为运行依赖保留。

#### Scenario: 新库从零迁移

- **WHEN** v4 首次启动并执行自动迁移
- **THEN** 迁移在空的 `langfuse_v4` database 上从零完成
- **AND** 该库的 `_prisma_migrations` 只包含 v3/v4 线记录，不含 v2 历史

#### Scenario: 已有数据卷上的新库通过显式语句创建

- **WHEN** PostgreSQL 数据卷已存在（非首次初始化）
- **THEN** `langfuse_v4` 通过显式 `CREATE DATABASE` 建立并完成授权
- **AND** 不依赖 `POSTGRES_DB` 或 init 脚本（两者只在空卷首启生效）
- **AND** web 首次启动不出现 database 不存在的 crash-loop

#### Scenario: 旧库与旧容器被移除

- **WHEN** 验收通过后检查
- **THEN** 不存在 v2 的 `langfuse-web` 容器与旧 `langfuse` database
- **AND** 不存在引用 `langfuse:2.95.11` 的镜像引用
