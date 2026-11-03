## 0. 前置与排期

- [ ] 0.1 **等 `alert-triage` 合并进 `dev-wsl`**：本变更复用其 `src/rca/sources/base.py`（`LogSource` 协议、`SourceEmptyError` / `SourceFailedError`）、`collector` 的分波并行与超时、`judge` 的定档、`render` 的卡片。未合并前开工会造成 `src/rca/` 双写冲突
- [ ] 0.2 确认 `turn-provenance-observability` 组 1+4 的落地状态；未落地时按 D11 走"字段不可用"降级，不阻塞开工
- [ ] 0.3 建隔离 worktree（`git worktree add`），base 取合并后的 `dev-wsl`

## 1. 迁移与配置

- [ ] 1.1 `src/infra/db/models/chat.py`：`MessageModel` 新增 `trace_id: Mapped[str | None]`（可空，注释写明"存量行不回填"）
- [ ] 1.2 新增 alembic 迁移：加列 + `ix_messages_trace_id` 索引
- [ ] 1.3 `src/infra/db/repos/chat_repo.py:127` `save_message`：从请求上下文取 `trace_id` 写入；取不到时留空
- [ ] 1.4 `src/config/settings.py` 新增 `DIAG_SCORE_FLOOR`（默认 0.35）、`DIAG_SCORE_GAP`（默认 0.05）、`DIAG_TRACE_LOOKBACK_ROUNDS`（默认 2）、`DIAG_MAX_REPRESENTATIVE_TRACES`（默认 3），每项写明"首版未标定"
- [ ] 1.5 `src/core/log_events.py` + `src/core/log_event_specs.py` 双处登记新事件：`diagnose done`、`diagnose undetermined`、`trace locate done`

## 2. 证据源

- [ ] 2.1 `src/rca/sources/trace.py`：`TraceSource`——按 `trace_id` 取观测与 `metadata.retrieval_contexts`，复用 `src/cli/eval_trace_io.py` 的 `fetch_trace`；取不到抛 `SourceFailedError`，trace 存在但无检索快照抛 `SourceEmptyError`
- [ ] 2.2 `src/rca/sources/error_log.py`：`ErrorLogSource`——按时间窗扫 ERROR 行、按第 3 段取 `trace_id`；沿用 `grafana.py` 的 `ensure_query_ok` 经验（成功判定不看 status，看数据体）
- [ ] 2.3 `src/rca/sources/probe.py`：`RetrievalProbe`——对原问题做确定性变体（抽实体/去年份/同义）后重检；命中且相关度过线 → "表述问题"，全空 → "覆盖缺失"。**默认不用模型改写**（不可复现），模型改写作为可选第二档并标注
- [ ] 2.4 三个 Source 均实现 `LogSource` 协议方法名与三态约定，测试用 `FixtureLogSource` 同款替身

## 3. 判定

- [ ] 3.1 `src/rca/diagnose/judge.py`：`root_cause` 枚举判定——`not_retrieved`（无 `retrieve_kb` 观测）、`low_relevance`（D5 三条件全满足）、`context_truncated`（`context_count < result_count` 且有 `context budget high`）、`not_cited`（有高分上下文但回答无引用）、`kb_stale`、`undetermined`
- [ ] 3.2 量纲门控：`score_kind != "rerank"` 时禁止进入 `low_relevance` 分支
- [ ] 3.3 D3 追溯：`history budget exceeded` / `context budget high` 或 `result_count == 0` 且问题含指代时，拉前 2 轮事实；禁止链式追溯
- [ ] 3.4 `tier` 与 `root_cause` 分开计算、分开存储；`tier` 沿用 `alert-triage` 的三档口径
- [ ] 3.5 证据不足（三态含 `empty` 或 `failed`）→ `undetermined`，并记录缺失的证据项清单

## 4. 报告与校验

- [ ] 4.1 `src/rca/diagnose/report.py`：报告结构含 `tier`、`root_cause`、`evidence[]`、`assertions[]`、`next_actions[]`、`basis_rounds`（本次用到前几轮）
- [ ] 4.2 `src/rca/diagnose/validator.py`：校验每条断言至少引用一个存在的 `evidence_id`；失败 → 降级为"证据清单 + 未判定"
- [ ] 4.3 模板约束：禁止出现未引用的因果断言；数字必须来自证据原文，不由模型转写（沿用 `alert-triage` D6）
- [ ] 4.4 渲染：群卡片（归属建议 + 根因 + 同类计数 + 详情链接）与详情页两套；卡片沿用企微 `button_interaction` 能力

## 5. 人工入口

- [ ] 5.1 `src/agents/skills/` 新增 `diagnose_trace` 工具（只读；输入 `trace_id`）
- [ ] 5.2 `skills/rca-error/SKILL.md`：frontmatter 声明证据清单（`trace_fetch` required、`error_log`、`retrieval_probe`、`history_lookback` 的 `when` 条件）
- [ ] 5.3 入口校验：`trace_id` 格式不符/不存在/属于另一环境 → 分别给出三种明确文案，不落到"诊断失败"
- [ ] 5.4 按 bot 分环境（`support` / `finance` 绑生产，`dev` 绑开发）

## 6. 告警定位器

- [ ] 6.1 `src/rca/locator.py`：时间窗（`T0-15min`~当前）内 ERROR 行 → `trace_id` 去重 → 按 `(错误类型, 接口, 错误码)` 聚类 → 每类取最早代表
- [ ] 6.2 D9 分支：窗口内无 ERROR / 有 ERROR 但无 `trace_id` → 结论"未落到应用层"，不进诊断
- [ ] 6.3 上限：最多诊断 3 个代表；超出只输出类别计数
- [ ] 6.4 接入报警轮：error 类告警走"定位 + 诊断"，延迟类仍走 `rca-latency`；按告警名白名单灰度、默认关闭

## 7. 落库与投递

- [ ] 7.1 新增诊断报告表：`diag_id / trace_id / env / trigger(manual|alert) / tier / root_cause / evidence(jsonb) / next_actions / basis_rounds / cost_ms / confirmed`，保留 180 天
- [ ] 7.2 写入口径：证据存摘要与定位，不存原文；原文只在详情页按需展开并留痕
- [ ] 7.3 详情页：复用现有登录，禁止免登链接；展开原文记录"谁、何时、哪个 diag_id"

## 8. 测试与验证

- [ ] 8.1 `tests/rca/test_judge.py`：六种 `root_cause` 各一例，含 `score_kind=fallback` 不得判 `low_relevance`
- [ ] 8.2 `tests/rca/test_locator.py`：聚类取代表、上限 3、空窗口 → "未落到应用层"
- [ ] 8.3 `tests/rca/test_validator.py`：引用缺失/伪造 → 降级
- [ ] 8.4 `tests/rca/test_trace_source.py`：用 `FixtureLogSource` 替身覆盖 `ok/empty/failed` 三态
- [ ] 8.5 回归集：`feedback` 点踩 + `prod-incident-log` 取 15~20 条，人工标注根因；根因一致率 ≥ 80%
- [ ] 8.6 活体：真实 `trace_id` 端到端跑通，验证报告可点开原文、P50 < 30s
- [ ] 8.7 待验证三项：`score_kind=timeout` 真实落库形态、多轮 trace 的 metadata 体积上限、告警 `details` 可提取字段

## 9. 文档

- [ ] 9.1 `docs/agents/troubleshooting.md`：在"症状 → 该查哪个事件"表加一行"值班助手诊断"入口
- [ ] 9.2 `docs/agents/api_contract.md`：若新增对内工具接口，登记参数与返回
- [ ] 9.3 `docs/agents/logging-rules.md`：登记 3 个新事件
- [ ] 9.4 `docs/agents/glossary.md`：`root_cause` / `tier` / `evidence_id` / `score_kind` 四个术语
