## 0. 前置与排期

- [x] 0.1 `alert-triage` **已合并**（`558919ad`）。按其真实代码核对后，复用范围为 `models` / `collector` / `loader` / `sources/base` 三态 / `grafana` 解析辅助；`LogSource` 协议、`judge()`、`render_card` 均为延迟专用，不复用（见 design D2）
- [x] 0.2 `bot-agent-routing` **已归档**（`archive/2026-11-06-bot-agent-routing`）。按其真实实现修正：trace 名仍是 `chat_turn`（`alert_turn` spike 已证伪回退）；无 `rca_*` metadata；日志能力在 `LOG_PROFILES={bot-ops, bot-dev}` 档开放；日志工具为接口维度（`uri` + 白名单），**不复用**
- [x] 0.3 生成侧事实**已在 `dev-wsl` 实现**（`ce0f0cc1` 起：5 个事件 + `model turn` 温度字段 + SSE 声明 + 前端回放）；活体 trace 已验证 `prompt assembled` / `prompt messages` / `model turn` 落日志，`agent resolved` / `skill *` 按设计在空值轮不记。本项不阻塞
- [x] 0.4 建隔离 worktree（`/root/code/corporate_rag-trace-diag`，分支 `feat/trace-diagnosis`），base 取三者合并后的 `dev-wsl`；P1 已合回 `dev-wsl`（`0c3a3425`），后续阶段在该 worktree 继续

## 1. 迁移与配置

- [x] 1.1 `src/infra/db/models/chat.py`：`MessageModel` 新增 `trace_id: Mapped[str | None]`（可空，注释写明"存量行不回填"）
- [x] 1.2 新增 alembic 迁移：加列 + `ix_messages_trace_id` 索引
- [x] 1.3 `src/infra/db/repos/chat_repo.py:127` `save_message`：从请求上下文取 `trace_id` 写入；取不到时留空
- [x] 1.4 `src/config/settings.py` 新增 `DIAG_SCORE_FLOOR`（默认 0.35）、`DIAG_SCORE_GAP`（默认 0.05）、`DIAG_TRACE_LOOKBACK_ROUNDS`（默认 2）、`DIAG_MAX_REPRESENTATIVE_TRACES`（默认 3），每项写明"首版未标定"
- [x] 1.5 `src/core/log_events.py` + `src/core/log_event_specs.py` 双处登记新事件：`diagnose done`、`diagnose undetermined`（新增 `[diagnose]` 前缀）。`trace locate done` 随 6.x 定位器落 P3

## 2. 证据源

- [x] 2.1 `src/rca/diagnose/sources.py`：`TraceSource`——按 `trace_id` 取观测与 `metadata.retrieval_contexts`，复用 `src/cli/eval_trace_io.py` 的 `fetch_trace`；取不到抛 `SourceFailedError`，trace 存在但无检索快照抛 `SourceEmptyError`
- [x] 2.2 `LocalLogSource`（`src/rca/diagnose/logs.py`，**本地日志**，方案 A）：读容器 volume `/data/logs` 的 `app_*.log` / `error_*.log`，按窗口取 `ERROR` 行、第 3 段即 `trace_id`；解析复用 `src/cli/replay_trace.py` 的 `parse_trace_logs`；**不复用** `sls_query_latency` / `sls_query_requests`（`uri` 必填 + 白名单 + 明细无 trace_id）。`TraceLocator` 抽象保留，MCP 实现为后续替换点
- [ ] 2.5 **日志可采集化（只改应用机 nginx，入口机不动）**：
  - `deploy/nginx/nginx.conf` 的 `masked` 增加 `$upstream_addr` / `$upstream_status` / `$upstream_http_x_trace_id` / `$request_time`
  - `access_log` 双写：保留 `/dev/stdout`（`docker logs` 习惯不变）+ 新增 `/var/log/nginx/access.log`
  - `docker-compose.image.yml` 的 nginx 服务加挂载 `/data/nginx-logs:/var/log/nginx`
  - 验证三点：正常请求三个新字段有值；app 不可达（502）时 `upstream_status` 与 trace 为空；脱敏格式不变（query 仍被掩）
  - **采集器安装、机器组、SLS 采集配置不在本变更内**（运维动作，见 runbook）
- [ ] 2.3 `src/rca/sources/probe.py`：`RetrievalProbe`——对原问题做确定性变体（抽实体/去年份/同义）后重检；命中且相关度过线 → "表述问题"，全空 → "覆盖缺失"。**默认不用模型改写**（不可复现），模型改写作为可选第二档并标注
- [ ] 2.4 三个 Source 实现**新窄协议** `ErrorEvidenceSource`（`trace_fetch` / `error_logs` / `retrieval_probe`），与 `LogSource` 并列、不扩展后者；三态沿用 `SourceEmptyError` / `SourceFailedError`；测试替身照 `FixtureLogSource` 的写法

## 3. 判定

- [x] 3.1 `src/rca/diagnose/judge.py`：`root_cause` 枚举判定——`not_retrieved`（无 `retrieve_kb` 观测）、`low_relevance`（D5 三条件全满足）、`context_truncated`（`context_count < result_count` 且有 `context budget high`）、`not_cited`（有高分上下文但回答无引用）、`undetermined`
- [x] 3.2 量纲门控：`score_kind != "rerank"` 时禁止进入 `low_relevance` 分支
- [x] 3.3 D3 追溯（`src/rca/diagnose/history.py`）：`history budget exceeded` / `context budget high` 或零召回且问题含指代时拉前 2 轮事实；只取事实（条数/是否被裁/模型名），不重新判定，不再触发追溯。本轮未检索且上一轮被截 → 归 `context_truncated`
- [x] 3.4 `tier` 与 `root_cause` 分开计算、分开存储（`src/rca/diagnose/tier.py`，报文档位/归属/根因三个字段）；`tier` 沿用 `alert-triage` 的三档口径
- [x] 3.5 证据不足（三态含 `empty` 或 `failed`）→ `undetermined`，并记录缺失的证据项清单
- [x] 3.6 问题分型（D13）：`kind ∈ 5xx | 4xx | answer` 由入口判定，分派到 `judge_error` / `judge_answer`；4xx 只输出归属（ops）。**4xx 的细分（参数/鉴权/限流/不存在）依赖 access 日志状态码**，随 2.5 落 P3

## 4. 报告与校验

- [x] 4.1 报告结构含 `kind`、`tier`、`owner`、`root_cause`、`evidence[]`、`assertions[]`、`next_actions[]`、`basis_rounds`、`diag_id`（`src/rca/diagnose/models.py`）；`basis_rounds` 由 3.3 追溯填充
- [x] 4.2 `src/rca/diagnose/report.py` 的 `validate()`：校验每条断言至少引用一个存在的 `evidence_id`；失败 → 降级为"证据清单 + 未判定"
- [x] 4.3 模板约束：本期报告文本全部由代码拼装（模型不产出任何断言与数字），未引用断言由 `validate()` 拦截
- [x] 4.4 `src/rca/diagnose/render.py`（**不复用** `render_card`）：群卡片（档位 + 归属 + 根因 + 依据 + 详情链接）与详情页 `deploy/nginx/html/diagnose.html`；同类计数属告警级聚合，随 6.x 落 P3

## 5. 人工入口

- [x] 5.1 `src/agents/tools/diagnose_tools.py` 的 `diagnose_trace`（只读 + 落库），并入 `LOG_CAPABILITY` 工具面（`bot-ops` / `bot-dev` 同享），不新建独立机器人
- [x] 5.2 `skills/rca-error/SKILL.md`：agent 侧技能（模型可自动调用），正文写明分型选择、只调一次、原样转述、未判定不猜。**未声明 `evidence` 清单**——证据采集在工具内的确定性流水线里完成，agent 技能不参与采集编排
- [x] 5.3 入口校验三文案：格式不符 / 未找到（含超保留期）/ 不可用（另一环境或检索异常），见 `src/config/diag.py`
- [x] 5.4 脱敏同源：抽取 `src/utils/mask.py::mask_nodes`，日志工具与诊断卡片共用
- [x] 5.5 环境隔离**不按 bot 区分**（`DIAG_ENV` 一个部署一个值）：`WeComBotConfig` 只有 `key/bot_id/secret/name/profile`，没有环境字段；日志源配置是单例（`load_rca_config()`）。诊断随部署环境走（一套部署一套日志源），profile 只决定工具面

## 6. 告警定位器

- [ ] 6.1 `src/rca/locator.py`：时间窗（`T0-15min`~当前）内 ERROR 行 → `trace_id` 去重 → 按 `(错误类型, 接口, 错误码)` 聚类 → 每类取最早代表；取 trace 按 **`chat_turn`**（不存在 `alert_turn`），告警轮用 tag `pf:alert` 区分；**不读** `rca_*`（已被移出归档变更）
- [ ] 6.2 三态显式分支（**不得合并**）：
  - 查询成功且 0 行 / 有行但无 `trace_id` → "未落到应用层"（D9，这是结论）；
  - 日志源不可达 / 超时 / 参数错误 → "日志源不可用，未判定"（failed 态，**不得**落进 D9）；
  - 有行且有 `trace_id` → 正常进入诊断
- [ ] 6.3 上限：最多诊断 3 个代表；超出只输出类别计数
- [ ] 6.4 接入报警轮：error 类告警走"定位 + 诊断"，延迟类仍走 `rca-latency`；按告警名白名单灰度、默认关闭

## 7. 落库与投递

- [x] 7.1 `diagnosis_reports` 表（另有 `kind` / `owner` / `assertions`）+ alembic `0004`；`created_at` 建索引，180 天清理属运维
- [x] 7.2 证据存摘要与定位（`DiagRepo.save` 只写 id/kind/locator/summary），不存原文
- [x] 7.3 详情页 `deploy/nginx/html/diagnose.html` + `GET /api/rca/diag/{diag_id}`，`/api/rca` 走必须登录分支。**本期无原文可展开**（7.2 只存摘要），展开留痕随原文来源落地一并做

## 8. 测试与验证

- [x] 8.1 `tests/rca/test_diagnose_judge.py`：五类 `root_cause` 各一例，含 `score_kind=fallback` 不得判 `low_relevance`
- [ ] 8.2 `tests/rca/test_locator.py`：聚类取代表、上限 3、空窗口 → "未落到应用层"
- [x] 8.3 `tests/rca/test_diagnose_report.py`：引用缺失/伪造 → 降级
- [x] 8.4 `tests/rca/test_trace_source.py` / `test_local_log_source.py`：注入式替身覆盖 `ok/empty/failed` 三态
- [ ] 8.5 回归集：`feedback` 点踩 + `prod-incident-log` 取 15~20 条，人工标注根因；根因一致率 ≥ 80%
- [ ] 8.6 活体：真实 `trace_id` 端到端跑通，验证报告可点开原文、P50 < 30s
- [ ] 8.7 待验证三项：`score_kind=timeout` 真实落库形态、多轮 trace 的 metadata 体积上限、告警 `details` 可提取字段

## 9. 文档

- [x] 9.1 `docs/agents/troubleshooting.md` 症状表加"值班助手结论未判定"一行
- [x] 9.2 `docs/agents/api_contract.md` §2.10 登记 `GET /api/rca/diag/{diag_id}`
- [x] 9.3 `docs/agents/logging-rules.md` 登记 `[diagnose]` 前缀与两个事件（`trace locate done` 随 P3）
- [x] 9.4 `docs/agents/glossary.md` 新增「trace 诊断」小节：`root_cause` / `tier` / `kind` / `evidence_id` / `score_kind`
