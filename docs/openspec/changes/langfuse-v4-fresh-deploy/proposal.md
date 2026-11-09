## Why

当前自托管的是 Langfuse **v2.95.11**（v2 线最后一版，已 EOL，无安全补丁承诺）。它的开源版把 **LLM-as-a-Judge** 与 **人工标注队列** 做成 Cloud-only 的二进制授权项，自托管拿不到——这意味着「数据中心 + 测评中心」的两块地基（自动打分、人工复核回流）在 v2 上不存在。

同时，评测资产目前散在本地 JSON（`data/deepeval/testset/*.json`）里：不可筛选、不可标注、不可跨版本对比。v4 起这两项能力对自托管开源版开放（仅限量、不限功能），并自带 Dataset 版本化（每次增改归档产生时间戳版本）。

本次采用**全新部署、不迁移任何历史数据**：现有 Langfuse 库仅 28 MB / 336 条 trace / 668 条 score，且这些 score 是 DeepEval 结果的副本（真源在 `eval_report` 表与报告文件）。为这点历史走 v2→v3→v4 三跳迁移、并行期与后台迁移，工作量与风险都不成立。

## What Changes

- **BREAKING**：服务端由 `langfuse/langfuse:2.95.11` 升至 **v4 最新稳定版**；新增 ClickHouse（≥25.12）、`langfuse-worker`、S3/MinIO 事件桶；Langfuse 改用**新 database**（`langfuse_v4`），旧库不迁移、不保留
- **BREAKING**：Python SDK 由 `langfuse 2.60.10` 升至 **5.x**——`langfuse.decorators` 模块（`langfuse_context`、`configure`、`flush`）、低层 `client.span()` / `client.generation()` / `client.score()` 全部移除，改写为 `get_client()` / `start_observation(trace_context=...)` / `create_score()` / `propagate_attributes()`
- **BREAKING**：trace_id（含评测的 `run_trace_id` 与逐题 `trace_id`）由 `trace_<uuid>` / `eval_run_<ts>_<8hex>` / `eval_<12hex>` 统一改为 **32 位小写十六进制**（W3C Trace Context / OTLP 硬要求；v3+ SDK 对非法 id 静默忽略并自行生成，会导致日志与 trace 失联）；生成链改为 **前端 → nginx（`$request_id`）→ 后端** 三级，入站非 32hex 一律丢弃重生成
- 诊断与评测的**读路径**由 `client.fetch_trace()` 改为 v4 的 **Observations API v2**，只保留一套实现，不做 v2/v4 双兼容
- trace 保留期清理由「直连 Langfuse PostgreSQL 跑级联 SQL」改为 v4 官方 **`DELETE /api/public/traces/{id}`**（开源版自带 trace-deletion）
- 新增两条 ADR，分别**取代 ADR-0011**（v2 降级决策）与 **ADR-0013**（直连 PG 删除）；旧 ADR 只加反向指针，不改正文
- 三份 compose、两个 env 模板、`deploy-runbook`、`infra-inventory` 同步更新；发布镜像与流水线配置按新组件补齐

### Non-goals（明确不做）

- 不迁移任何历史 trace / observation / score / dataset；v2 实例与其数据库在验收后直接删除
- 不做 v2→v3→v4 的版本跳转，不启用 `dual` / `legacy` 写模式，不做后台迁移
- 不启用任何 evaluator / 标注队列的**业务配置**（那是后续 `eval-online-loop` 的事），本次只保证能力可用
- 不接入 purge 定时任务（本次只保证手动 CLI 可用）
- 不做 CI 门禁、不改题库来源、不加 `dataset_run_id` 回填（属于后续 `eval-dataset-plane`）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `observability-backend`：服务端版本与必需组件集、存储形态、资源档位、EOL 暴露面收敛、退役资源——由「v2 + 仅 PG」整体改为「v4 + ClickHouse + worker + 对象存储 + 新库」
- `llm-tracing`：trace_id 格式与入站校验、开关语义（运行时 `configure` → 应用层守卫 + 启动期 env）、工具/委派 span 与 generation 的实现方式（行为契约不变）
- `eval-trace`：trace 读取实现改 Observations API v2；分数写入由 `client.score` 改 `create_score`
- `deployment-runbook`：运行形态与资源档位、发布/冒烟清单、回滚与备份口径（改为「无迁移、停机切换、验收即完成」）

## Impact

**部署**：`docker-compose.yml` / `docker-compose.prod.yml` / `docker-compose.image.yml`；`.env.template` / `.env.example`；ACR 镜像（langfuse / worker / clickhouse）；`deploy/nginx/nginx.conf`（trace id 兜底 + 已具备的 `X-Forwarded-*`）；`deploy/postgres/init/`（新库需显式 `CREATE DATABASE`，不能依赖 `POSTGRES_DB` 首启）

**代码**：`src/infra/llm/tracing.py`、`tool_trace.py`、`src/agents/graph/middleware.py`、`src/services/agent_service.py`、`src/services/turn_runner.py`（`langfuse_observation_id` kwarg 的替代）、`src/rca/sources/mcp.py`、`src/agents/tools/diagnose_tools.py`（trace id 正则）、`src/rca/diagnose/logs.py`（trace id 正则）、`src/rca/diagnose/history.py`（读消费方）、`src/cli/eval_scoring.py`、`eval_trace_io.py`、`eval_deepeval.py`（run 与逐题 trace id、`langfuse_observation_id` kwarg）、`src/cli/seed_langfuse_models.py`、`src/main.py`（定价 seed 的 client 获取）、`src/infra/llm/provider/pricing_seed.py`、`src/infra/llm/langfuse_purge.py`、`src/middleware/trace_id.py`、`deploy/nginx/html/chat.html`、`deploy/nginx/html/login.html`

**测试**：`tests/conftest.py`（关停机制重写）、`tests/infra/llm/test_tracing.py`、`tests/test_tracing_order.py`、`tests/config/test_langfuse_disabled.py`、`tests/services/test_run_generation_tracing.py`、`tests/agents/graph/test_loop_middleware.py`、`tests/cli/test_eval_trace_judge_call.py`

**文档**：`CLAUDE.md`（TraceID 格式）、`docs/agents/glossary.md`、`api_contract.md`、`deploy-runbook.md`、`infra-inventory.md`、`code-map.md`、`eval-ledger.md`、需求池 H-07 / H-12

**外部依赖**：v4 服务端与 SDK 版本；ClickHouse 版本；ACR 与云效流水线；生产机内存与磁盘
