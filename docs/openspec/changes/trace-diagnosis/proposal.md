## Why

告警侧已有 `alert-triage` 回答"**要不要处理**"，但"**为什么**"仍靠人肉：拿 trace_id → 翻 Langfuse → grep 日志 → 心算 → 结论只存在脑子里。同一条 trace 两个人查可能得出两个根因，不留档、不可回归、换人重来一遍。

三处缺口（均已核对代码或实测）：

1. **消息表没有 trace_id** —— `MessageModel` 只有 `session_id / role / content / sources / model_name / process / status / created_at`，从"哪一轮答错"到 trace 没有路，只能靠用户点踩（`feedback` 表有 `trace_id`）或 7 天内的日志反查。
2. **生成侧事实缺字段** —— 温度、生效智能体、prompt 组成、skill 注入在日志里都不存在，排障时只能反查数据库里的注入行。由 `turn-provenance-observability` 的组 1+4 补，本变更消费其产物。
3. **检索侧证据颗粒度不足** —— trace 里原本只有最终上下文，没有"召回多少条、精排后剩几条、分数是什么量纲"。本变更随附的字段补充已修复（见「已落地的前置改动」）。

2026-11-04 本地活体实测（`trace_afe90f74` 等）：

- **日志 trace_id 与 Langfuse trace id 一致**：同一次请求的 id 在容器日志出现 25 行、在 Langfuse 命中 1 条；此前"0/20 命中"是样本不重叠（库内 trace 止于 10-16），非 id 不一致。
- **trace metadata 携带 `retrieval_contexts`**：含 `chunk_id / doc_id / page / score / order_in_prompt`，30 天可用；而 app 日志只有 7 天 —— 检索侧证据应以 trace 为主源，日志只补过程性事实（`rerank timeout/skip`）。
- **"零召回"几乎不存在**：混合检索 + 精排恒返回 top_k（问"量子纠缠"仍召回 32 条、进上下文 5 条）。判别相关性的有效信号是精排分数：相关 0.60~0.76，无关 0.19~0.21。
- **三种形态在 trace 里可区分**：调了检索（有 `retrieve_kb` span）、没调检索（走 `search_web`）、完全没有 tools span（直接作答）。
- **ERROR 日志 92% 带 trace_id**（1428 行中 1314 行）：告警侧定位器可用，余下 8% 属于无请求上下文的路径。

## What Changes

- 新增**确定性诊断内核** `diagnose(trace_id)`：证据采集与判定为代码流水线，模型只做归纳与措辞，不决定下一步。
- **复用 `alert-triage` 已实现的骨架**（`src/rca/` 的 Source 协议、`empty/failed` 三态、分波采集、代码定档、卡片渲染），新增三个证据源：`TraceSource`（按 trace 取观测与 metadata）、`ErrorLogSource`（窗口内 ERROR 行 → trace_id）、`RetrievalProbe`（改写后重检，区分"表述问题"与"覆盖缺失"）。
- 新增 `skills/rca-error/SKILL.md`：以 frontmatter 声明证据清单（tool/params/required/timeout/when），由代码解析并分波执行，与 `rca-latency` 同构。
- **两个入口**：人工 `trace_id`（企微 bot）；告警定位器（告警时刻 + 症状 → 扫窗口 → 聚类取代表 trace）。定位器之后的一切与人工入口完全相同。
- **报告约束**：每条断言必须引用 `evidence_id`；`root_cause` 用枚举而非自由文本；校验不通过时降级为"证据清单 + 未判定"，禁止带错引用放行。
- **落库与投递**：新增诊断报告表（保留 180 天）；`messages` 表补 `trace_id` 列；群内投递摘要卡片、详情页需登录。
- **消息表迁移**：`messages` 新增 `trace_id`（可空，存量不回填——老会话不可诊断，日志亦已轮转）。

## 已落地的前置改动

`ctx.retrieval_trace` 快照补充三个字段（随本提案附件提交，`src/agents/tools/rag_tools.py`、`src/rag/context.py`、`src/rag/retrieval.py`）：

- `result_count`：融合后候选条数；
- `context_count`：精排后进入 prompt 的条数；
- `score_kind`：`rerank`（精排相关度）/ `fallback`（精排失败回退为 1-distance）/ `timeout`（外层超时回退）/ `none`。

意义：不区分 `score_kind` 会把"精排降级"误判为"知识库覆盖缺失"，两者修法相反。

## Capabilities

### New Capabilities

- `trace-diagnosis`：单 trace 的证据采集与根因判定——证据清单声明、三态降级、根因枚举、引用约束、无法判定时的显式降级、报告落库与投递。
- `error-trace-locator`：告警症状 + 时间窗 → 候选 trace 列表；窗口内无 trace 时输出"未落到应用层"作为结论而非缺证据。

### Modified Capabilities

- `alert-notify`：error 类报警轮的输入由告警文本自由分析改为"定位 + 诊断"，结论形态与 `alert-triage` 对延迟类的改造保持一致。
- `session-persistence`：消息行新增 `trace_id`，写入时取自请求上下文。

## Impact

**代码**

- `src/rca/` 扩展：`sources/trace.py`、`sources/error_log.py`、`diagnose/`（pipeline、probe、report、validator）
- `src/agents/skills/` 新增诊断工具；`skills/rca-error/SKILL.md`
- 迁移：`messages` 加列 + 索引
- 复用既有读取器：`src/cli/eval_trace_io.py` 的 `fetch_trace` / `parse_trace`

**接口**

- 对外：无新增 HTTP 接口，沿用 `/api/alerts/webhook/{token}`
- 对内：Langfuse 只读查询；日志窗口扫描（本地文件起步，SLS 之后替换数据源）

**依赖**

- `alert-triage` 合并（复用 `src/rca/` 与 `LogSource`）
- `turn-provenance-observability` 组 1+4（生成侧事实字段）

**风险**

- 根因误判被当真：只读 + 证据可点开 + "无法判定"必须真实出现
- 定位器把"告警未落到应用层"误报为应用缺陷
- 报告落库引入新的敏感数据存储面（与出域审计同一口径）
- 7 天日志窗口造成的"查不到"被误读为"没问题"

## 非目标

- 支付投诉归因（二期；且不在本系统数据域内）
- 自动执行修复（只出命令文本，由人执行）
- 自然语言入口（二期，先只收 trace_id）
- 同类问题聚类、跨 KB 聚合统计
- 私有化模型路由
- 独立 MCP 服务（出现第二个消费方时再评估）

## 验收判据

1. 回归集（15~20 条，来自 `feedback` 点踩 + `prod-incident-log`）根因一致率 ≥ 80%
2. 诊断 P50 < 30 秒
3. "无法判定"必须真实出现过，且能说清缺哪份数据
4. 报告中引用的 `evidence_id` 100% 可回溯
5. 上线两周内值班同学主动使用 ≥ 10 次

## 关键决策

已定，详见 `design.md`：

- **D3 粒度**：单 trace 起步 + 有条件向前追溯 2 轮（禁链式）。
- **D4 1→N**：按错误类型聚类取代表、上限 3；`tier` 与 `root_cause` 强制分离。
- **D5 分数判据**：相对为主 + 三条件联判 + `score_kind` 门控；阈值配置化，首版标注未标定。
