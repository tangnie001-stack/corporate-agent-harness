## 1. Spike（先做，结论回填 design）

- [x] 1.1 spike①：验证 `LANGFUSE_TRACING_ENABLED=false` 时 SDK 5 的 `get_client().start_observation()` 是否零上报、`get_current_trace_id()` 是否为空；用指向必然失败的本地端口验证零 HTTP 请求
- [x] 1.2 spike②：验证 `asyncio.to_thread` 内创建 observation 的 trace 归属（是否仍属外层请求）；结论固化为测试
- [x] 1.3 spike③：v4 实例上取一条 trace，逐项对照 Observations API v2 字段与三个读取函数的消费字段（latency / usage / metadata / 子 observation）；并**回读轮末写入**的 `retrieval_contexts` / `tools_used`（验证根 observation metadata 合并方案可行）
- [x] 1.4 记录实施当日实际版本：Langfuse v4 tag、Python SDK 5.x 版本、ClickHouse LTS 版本，写回 design 的 Open Questions

## 2. 部署基座

- [x] 2.1 在既有 PG 实例上**显式** `CREATE DATABASE langfuse_v4` 并授权（不能依赖 `POSTGRES_DB` / init 脚本，它们只在空卷首启生效）；加存在性检查，旧 `langfuse` 库保持不动
- [x] 2.2 三份 compose 增加 `clickhouse`（≥25.12、UTC、`CLUSTER_ENABLED=false`、命名卷、healthcheck、1.5g）与 `langfuse-worker`（同 web 镜像 tag、无端口）
- [x] 2.3 三份 compose 为 `langfuse-web` / `langfuse-worker` 补 `CLICKHOUSE_*`、`REDIS_CONNECTION_STRING`、`LANGFUSE_S3_EVENT_UPLOAD_*`，并同步 `.env.template` 与 `.env.example`
- [x] 2.4 为 ClickHouse 用户补齐 v4 所需 grant（`ALTER ... ON default.*`、读 `system.parts/mutations/tables/processes/query_log`、`SYSTEM SYNC REPLICA`），并记录到 runbook
- [x] 2.5 在既有 MinIO 新建事件桶，S3 配置开启 `FORCE_PATH_STYLE`；验证事件文件可写可读
- [x] 2.6 调大 `langfuse-web` 的 `mem_limit`（≥1g）、worker（≥512m），两容器设 `NODE_OPTIONS=--max-old-space-size`
- [x] 2.7 拉取三个镜像并推入 ACR：`langfuse/langfuse:<tag>` 与 `langfuse/langfuse-worker:<tag>` 走 Docker Hub + 本机国内加速器（两者**必须同 tag**，实施时取 latest，如 4.76.0）；`docker.io/clickhouse/clickhouse-server` 取官方 compose 对应版本（如 26.3.62.3）；三者推入公开库 `deploy_base`，确认 ECS 同地域可拉取
- [x] 2.8 在 `docker-compose.image.yml` 中写死三个镜像的**具体 tag**（不用浮动 `:4`），并记录三者 digest 作为「生产跑的就是验过的那个」的证据
  - 2026-11-09 实测（ACR `deploy_base`，逐层推送后 registry 侧回读，均为单平台 amd64 manifest）：
    - `langfuse-4.76.0` → `sha256:ba55f5337897e4230458ca7af2d060d5f9dd2e9826b6f811669c58d1d649b1e8`（docker manifest v2）
    - `langfuse-worker-4.76.0` → `sha256:1405bb683bd916247bf48b1aba7954139f8abc459cf64e3850fdbf4ff4fd9cc9`（docker manifest v2）
    - `clickhouse-26.3.62.3` → `sha256:6eb24df16477b65c87b772a56eb341a9624529491856c103e74c537db59669f1`（oci manifest）
- [x] 2.9 dev 用 3001 端口 + `langfuse_v4` 起测试床，确认空库迁移成功、四个容器 healthy

## 3. 身份与开关（必须先于第 4 组）

- [x] 3.1 `src/infra/llm/tracing.py`：`new_trace_id()` 改为 `uuid4().hex`；`TRACE_ID_PATTERN` 改为 `^[0-9a-f]{32}$`；更新模块 docstring 的契约说明
- [x] 3.2 `src/middleware/trace_id.py`：保持"头 → 查询参数 → 生成"的优先级，非法值静默丢弃重生成（行为不变，仅规则收紧）
- [x] 3.3 `deploy/nginx/html/chat.html` 与 `login.html`：`generateTraceId()` 均改为 32 位 hex（`crypto.randomUUID().replace(/-/g,'')`，含旧浏览器回退实现）
- [x] 3.4 `deploy/nginx/nginx.conf`：对 app 的 vhost 增加 `map` 兜底（空值用 `$request_id`）并 `proxy_set_header X-Trace-ID`；不加响应头（避免与后端重复）
- [x] 3.5 `src/infra/llm/tracing.py`：新增开关判定，供各 span 创建点在函数体首行使用（根 observation 由装饰器产生、无法短路）；`LANGFUSE_TRACING_ENABLED` 在 tracing 模块 **import 期**同步给进程环境，确保早于首次 `get_client()`
- [x] 3.6 `src/cli/eval_deepeval.py`：`run_trace_id` 与**两处逐题** `eval_<12hex>`（`begin_eval_run` 与单题循环）统一改为 32hex；同步 `src/cli/check_abstain.py` 里的同类前缀
- [x] 3.9 `src/agents/tools/diagnose_tools.py` 与 `src/rca/diagnose/logs.py`：删除两处本地 `^trace_[0-9a-fA-F-]{36}$` 正则，复用 `tracing.TRACE_ID_PATTERN`；补 32hex 正例与旧格式负例
- [x] 3.10 `src/services/turn_runner.py` 与 `src/cli/eval_deepeval.py`：移除 `langfuse_observation_id=` kwarg，改在后台任务入口用 `start_as_current_observation(trace_context=TraceContext(trace_id=...))` 建根 observation；补回归测试「根 observation 的 trace_id == 传入 id，且按该 id 可查」
- [x] 3.7 文档同步：`CLAUDE.md`（TraceID 段）、`docs/agents/glossary.md`、`docs/agents/api_contract.md` 的格式与示例
- [x] 3.8 重写 `tests/infra/llm/test_tracing.py`：32hex 生成、非法入站被拒、合法入站沿用；全仓检索并更新断言 `trace_` 前缀的测试

## 4. SDK 迁移（2.60.10 → 5.x）

- [x] 4.1 `src/infra/llm/tool_trace.py`：`client.span()` → `start_observation(trace_context=TraceContext(...), as_type="tool")`；保持三条不变量（不按工具名分支、不用 client.trace、close 必调）
- [x] 4.2 `src/agents/graph/middleware.py`：`client.generation()` → `start_observation(as_type="generation", usage_details=...)`；重测"当前 observation 指向 chat_turn"的既有 workaround
- [x] 4.3 `src/services/agent_service.py` 三处与 `src/cli/eval_deepeval.py:240`：`update_current_trace` 改为 `propagate_attributes`（user/session/tags/可预知 metadata）+ 根 observation `update()`；迟到 metadata 写根 observation（D12）
- [x] 4.4 `src/rca/sources/mcp.py`：`client_instance.span()` → `start_observation`（不改 TraceSource 接口）
- [x] 4.5 `src/cli/eval_scoring.py`：`langfuse_context.client_instance` → `get_client()`；`client.score()` → `create_score()`（本 change 不加 `dataset_run_id` / `observation_id`）
- [x] 4.6 `src/cli/seed_langfuse_models.py` 与 `src/infra/llm/provider/pricing_seed.py`：复核 v4 的模型定义写入路径，确保一次对话的 calculated cost > 0
- [x] 4.7 `pyproject.toml`：`langfuse` 依赖改为 5.x 区间；全仓清掉 `langfuse.decorators` 引用，含 `src/main.py`（定价 seed 取 client）与 `src/cli/seed_langfuse_models.py`
- [x] 4.11 确认 `@observe` 的导入改为 `from langfuse import observe`，且装饰器不再依赖已删除模块
- [x] 4.8 `tests/conftest.py`：删除 `langfuse_context.configure` 与 `client.enabled` 逻辑，改为 env + 守卫双保险；保证逐例零 SDK 开销
- [x] 4.9 重写 Langfuse 桩：`tests/test_tracing_order.py`、`tests/config/test_langfuse_disabled.py`、`tests/services/test_run_generation_tracing.py`、`tests/agents/graph/test_loop_middleware.py`、`tests/cli/test_eval_trace_judge_call.py`、`tests/cli/test_eval_scoring.py`、`tests/cli/test_langfuse_score_names.py`、`tests/cli/test_eval_trace.py`、`tests/rca/test_mcp_source.py`
- [x] 4.10 新增 `tests/infra/llm/test_trace_context_otel.py`：固化 spike② 的线程池结论 + 断言关闭时零 span

## 5. 读路径（排在 trace-diagnosis 的 `src/rca/` 实现之后）

- [x] 5.1 `src/cli/eval_trace_io.py`：`fetch_trace`、`fetch_recent`、`fetch_session_traces` 三个读取函数改用 Observations API v2，向上层返回属性式对象；`.metadata` 合并根 observation metadata（D12）；不写版本分支
- [x] 5.2 沿用 spike③ 的字段对照表补齐映射；`metadata` / `observations` 取不到即抛可诊断错误（不得静默返回空）；仅 latency、usage 等非必需字段允许降级并记 warning
- [x] 5.3 校验四个消费方：`src/cli/eval_trace.py`、`src/cli/diagnose.py`、`src/rca/diagnose/sources.py`、`src/rca/diagnose/history.py`（只做字段校验，不改接口）
- [x] 5.4 用一条真实 trace 跑 `diagnose_trace`，确认证据链与报告渲染正常

## 6. 清理与决策

- [x] 6.1 `src/infra/llm/langfuse_purge.py`：**列举**改 `GET /api/public/traces` 分页 + 时间过滤（当前 SQL 读 PG `traces`，v4 下恒为 0 条）；**删除**改 `DELETE /api/public/traces/{id}`；审计行打印 host 而非 PG 库名
- [x] 6.2 `src/cli/purge_langfuse_traces.py`：dry-run 必须**命中 > 0 条**（用一条人为超期的 trace 验证），真删后 UI 查不到该 trace
- [x] 6.3 新增 ADR 0028（v4 全新部署，取代 ADR-0011 的 v2 决策）与 ADR 0029（清理改官方删除 API，取代 ADR-0013）；旧 ADR 的 Status 加反向指针
- [x] 6.4 更新 `docs/agents/deploy-runbook.md`（新组件、新变量、停机切换、无回滚、新增冒烟项）与 `infra-inventory.md`、`code-map.md`、`eval-ledger.md`
- [x] 6.5 需求池 H-07（Langfuse 去留）与 H-12（无指标出口）登记进展或结项（2026-11-09 各追加 v4 进展：H-07 正式入口换成域名、:3000 转诊断直连；H-12 复测 `/metrics` 与 `metrics/daily` 均 404）

## 7. 验收

- [x] 7.1 dev 上一轮普通对话：trace 树完整（`chat_turn` → `round` → `tool`）、token 与成本非 NULL、session/user/tag/metadata 齐全
- [x] 7.2 dev 上一轮委派对话：`delegate` span 父子关系正确
  - 修复前：`tools` / `delegate` 的 `parent_span_id` 是 SDK 合成的随机 id（库里不存在，树上是孤儿）
  - 修复后实测（trace `0220f935…`）：`chat_turn` → `tools` → `delegate_task` → `delegate`，`agent_turn` 挂 `chat_turn`，无合成父节点
- [x] 7.3 `LANGFUSE_ENABLE=false` 并把 host 指向必然失败的端口，跑一轮对话与一次评测：**零出网请求**、功能正常
  - 实测：`LANGFUSE_HOST=http://127.0.0.1:9` 跑 `--limit 1`，退出码 0，报告正常产出；`events_core` 15→15、`scores` 0→0，日志无连接拒绝/异常
- [x] 7.4 `docker compose restart` 后旧 trace 仍可查（验证命名卷）
- [ ] 7.5 控制台可登录；能对一条 trace 手工打分并在 Scores 中看到
  - 数据面已验：对 trace 0220f935… 写入 manual_check=1，CH scores 表可见；v4 的 events_only 模式下 GET /api/public/scores、/v2/scores 均 404（只能从控制台看）
  - 待办：在 http://localhost:3000 登录后在 UI 里对一条 trace 打分并确认 Scores 列表出现
- [x] 7.6 跑一次评测 CLI：分数写入成功，日志中无 "Ignoring trace ID"
  - 实测（2026-11-09，22 题，deep-eval-native / judge=qwen3.8-flash）：退出码 0；CH scores 154 条（7 个指标名）；Ignoring trace ID 0 次；报告 deepeval_eval_20261109_091944
  - 主分：faithfulness 0.9096 / answer_relevancy 0.9511 / context_precision 0.7883（低于 0.80 门禁）/ context_recall 0.8182；NaN 1/88
  - 台账已追加第 9 行（docs/agents/eval-ledger.md）
- [ ] 7.7 prod 停机窗口执行切换（关观测 → 停应用 → 换栈 → 起 → 冒烟 → 开观测），跑通 runbook 冒烟清单
  - 已完成：切换完成，站点/接口/Langfuse 控制台均 200
  - 第 5/6 项 ✅：生产知识库上传 `neusoft_2024_half.pdf` → `status=ready`，427 chunks（`POST /api/kbs/documents/list` 核实）
  - 第 7/8 项 ✅：KB 轮次 trace `079e0140…`：5 个观测（`chat_turn` → 2×`agent_turn` / `tools` / `retrieve_kb`）；检索 result=50 ctx=4 kind=rerank，分数 0.968/0.914/0.744/0.735；回答 1056 字含引用 `[1] [2]`；generation 成本 0.0014 / 0.0032 非空
  - 诊断活体：对 `079e0140…` 跑诊断 2.29s，`E1 [trace] retrieve_kb#1 — result=50 ctx=4 kind=rerank`，根因 `undetermined`（健康轮次）
  - ⚠️ Observations v2 投影：`cost` 需 `fields=…usage,model,metrics`、`input/output` 需 `fields=…io`，否则全为 null
  - 待办：第 9 项（控制台手工打分，见 7.5）、第 10 项（生产跑一次评测 CLI）
- [x] 7.8 在 v4 上完成 `trace-diagnosis` 的 8.5（15~20 条样本 + 根因一致率）与 8.6（活体端到端），随后归档该 change
  - 8.5 改用故障注入 22 条，一致率 22/22=100%；8.6 真实 trace 1.3~2.8s；8.7 三项同批完成
  - change 已归档为 `openspec/changes/archive/2026-11-09-trace-diagnosis`，6 条需求并入 `docs/openspec/specs/trace-diagnosis/spec.md`
- [ ] 7.9 删除 v2 容器、旧 `langfuse` database 与 `langfuse:2.95.11` 镜像引用；确认在用卷与桶完好
- [x] 7.10 用一条真实对话 trace 跑诊断：经读接口得到的轮末 `retrieval_contexts` 与 `tools_used` 均非空，`retrieval_contexts` 条数 > 0（证明读路径与迟到字段承载都没有静默退化）
- [x] 7.11 用 OTLP 反填 `startTimeUnixNano` 制造一条超期 trace，跑 purge：dry-run **命中 > 0**（命中 0 即判失败），真删后按 id 查不到
