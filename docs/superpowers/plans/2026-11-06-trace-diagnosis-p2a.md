# trace-diagnosis P2a（分型 · 档位 · 卡片 · 落库 · 入口）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P1 的证据内核接成一条可用链路——企微群里给一个 `trace_id`，收到一张可点开的诊断卡片，结论同时落库。追溯（D3）与 RetrievalProbe 放 P2b。

**Architecture:** 分型与档位是纯函数（`kind` → `judge_error` / `judge_answer`，`kind + root_cause` → `tier` + `owner`）；卡片由代码渲染，模型不产出数字；报告写 `diagnosis_reports`，群消息只放摘要与详情链接。

**Tech Stack:** Python 3.11+ / pytest / FastAPI / SQLAlchemy(async) / alembic / 企微 markdown 消息

**Spec:** `docs/openspec/changes/trace-diagnosis/design.md`（D1/D4/D6/D9/D10/D12/D13）、`specs/trace-diagnosis/spec.md`

## Global Constraints

- 测试命令一律 `POSTGRES_HOST=localhost pytest ...`（宿主侧约定）。
- 判定只用硬证据；证据不足一律 `undetermined`，不得推测。
- 模型不产出报告数字：卡片文本全部由代码拼装。
- 只读：诊断链路不得写业务数据（仅写诊断报告表）。
- 新配置/文案入 `src/config/`（`settings.py` / `diag.py`），阈值标注"未标定"。
- 顺序：3.4 tier → 3.6 分型 → 4.1/4.3/4.4 报告与渲染 → 7.x 落库 → 5.x 入口 → 9.x 文档。

---

### Task 1: 档位与归属（3.4）

**Files:**
- Create: `src/rca/diagnose/tier.py`
- Modify: `src/rca/diagnose/models.py`（`DiagnosisKind` / `Owner` / 报告补字段）
- Test: `tests/rca/test_diagnose_tier.py`

**Interfaces:**
- Produces: `class DiagnosisKind(str, Enum): ANSWER="answer" | ERROR_5XX="5xx" | ERROR_4XX="4xx"`
- Produces: `class Owner(str, Enum): OPS="ops" | DEV="dev" | KB="kb"`
- Produces: `def assess(kind: DiagnosisKind, cause: RootCause) -> tuple[Tier, Owner]`

**判定表（代码固化，不读模型）：**

| kind | root_cause | tier | owner |
|---|---|---|---|
| 5xx | 任意 | 需要处理 | dev |
| 4xx | 任意 | 建议观察 | ops |
| answer | 四类已定义根因 | 需要处理 | kb |
| answer | undetermined | 建议观察 | ops |

- [ ] Step 1 写失败测试：`assess` 四行 + `Tier` 复用 `src.rca.models.Tier`
- [ ] Step 2 确认失败
- [ ] Step 3 实现（`DiagnosisReport` 追加 `kind/tier/owner/diag_id`，均带默认值，保持 P1 调用方不变）
- [ ] Step 4 跑测试
- [ ] Step 5 提交：`feat(diag): 分型与档位分开计算（tier 复用 alert-triage 口径）`

### Task 2: 分型分派（3.6）

**Files:**
- Modify: `src/rca/diagnose/judge.py`
- Test: `tests/rca/test_diagnose_judge.py`（补 error 分支）

**Interfaces:**
- Produces: `def judge(calls, *, kind=ANSWER, cited, has_context_budget_high) -> tuple[RootCause, str]`（按 kind 分派）
- Produces: `def judge_answer(...)`（P1 逻辑原样迁移）
- Produces: `def judge_error(kind) -> tuple[RootCause, str]`（4xx/5xx 均不下检索结论）

**约束：**
- 5xx/4xx 不进入检索判定（检索侧事实与错误条目无因果关系）。
- 4xx 分类（参数 / 鉴权 / 限流 / 不存在）**依赖状态码证据**，本期日志行不含状态码 → 只输出归属与"分类待 access 日志采集"（2.5 上线后补）。

- [ ] Step 1-5 同上（测试：answer 走原逻辑、5xx/4xx 恒为 undetermined 且原因含归属）

### Task 3: 报告结构补全（4.1）

**Files:**
- Modify: `src/rca/diagnose/pipeline.py`
- Modify: `src/rca/diagnose/report.py`（校验断言非空引用不变）
- Test: `tests/rca/test_diagnose_pipeline.py`

**变更：**
- `diagnose(trace_id, *, kind=ANSWER, ...)`：日志行转为 `Evidence(kind="log", locator="log:<file>:<line>", summary="<event> <截断 40 字>")`，最多 5 条。
- 错误类断言："窗口内 ERROR {n} 条，首条 {event}"，引用日志证据 id；answer 类断言维持 P1 口径。
- `basis_rounds=0`（追溯在 P2b）。

- [ ] Step 1 写失败测试：5xx trace 产出 `tier=需要处理`、`owner=dev`、日志证据 ≥1
- [ ] Step 2-5 实现 + 跑测试 + 提交：`feat(diag): 报告补 tier/owner 与日志证据`

### Task 4: 脱敏同源（5.4）

**Files:**
- Create: `src/utils/mask.py`
- Modify: `src/agents/tools/log_backend.py`、`src/rca/render.py`
- Test: `tests/utils/test_mask.py`

**Interfaces:**
- Produces: `def mask_nodes(text: str) -> str`（IPv4[:port] → `node-***`）

- [ ] Step 1-5：抽公共函数，两处改为导入，行为不变（既有测试即回归）

### Task 5: 卡片渲染（4.4）

**Files:**
- Create: `src/rca/diagnose/render.py`
- Create: `src/config/diag.py`（`DiagnoseTexts`：卡片行模板、入口三种文案、归属中文名）
- Test: `tests/rca/test_diagnose_render.py`

**输出（企微 markdown）：**

```
**诊断 · {kind 中文}**
> 档位：{tier} ｜ 归属：{owner}
> 根因：{reason_text}

{basis}
[查看详情]({detail_url})
```

- 证据超过 3 条时只列前 3 条 + "另 N 条见详情"。
- `detail_url` 为空时省略链接行（CLI 场景）。
- 渲染前过 `mask_nodes`。

- [ ] Step 1-5 同上；提交：`feat(diag): 卡片渲染（档位/归属/根因/证据计数）`

### Task 6: 落库（7.1 / 7.2）

**Files:**
- Create: `src/infra/db/models/diag.py`
- Create: `alembic/versions/0004_diagnosis_reports.py`
- Create: `src/infra/db/repos/diag_repo.py`
- Test: `tests/infra/db/test_diag_model.py`、`tests/infra/db/test_diag_repo.py`

**表 `diagnosis_reports`：**

| 列 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | diag_id |
| trace_id | String(64) | 索引 |
| env | String(16) | 部署环境名（来自 settings.PROFILE/ENV） |
| trigger | String(16) | manual / alert |
| kind / tier / root_cause / owner | String | 判定结果 |
| evidence / assertions | JSONB | 摘要与定位，不存原文 |
| next_actions | Text | 换行拼接 |
| basis_rounds / cost_ms | Integer | |
| confirmed | Integer | 0/1，人工确认 |
| created_at | UTCDateTime | 索引（保留 180 天，清理属运维） |

- [ ] Step 1 模型/迁移；Step 2 repo `save()` / `get()`；Step 3 测试（模型列 + repo 假 session）；Step 4 提交：`feat(db): 诊断报告表与 repo（证据只存摘要）`

### Task 7: 入口工具（5.1 / 5.3）

**Files:**
- Create: `src/agents/tools/diagnose_tools.py`
- Modify: `src/services/agent_service.py`（log 档工具面并入 + 工具名集合）
- Test: `tests/agents/tools/test_diagnose_tool.py`

**Interfaces:**
- `@tool("diagnose_trace") async def diagnose_trace(trace_id: str, kind: str = "answer") -> str`
- 依赖注入：`configure(trace_source=None, log_source=None, repo=None)`（lifespan 注入；测试注入替身）
- 三种入口文案（`DiagnoseTexts`）：
  - 格式不符 → `trace_id 格式应为 trace_<uuid>`
  - trace 不存在 → `未找到该 trace（可能超过 30 天保留期）`
  - 其他取数失败 → `trace 不可用（可能属于另一环境）`
- 成功：落库（trigger=manual）→ 返回卡片（含详情链接 `/diagnose.html?diag_id=…`）

- [ ] Step 1-5 同上；提交：`feat(diag): diagnose_trace 工具挂 log 能力档`

### Task 8: skill（5.2）

**Files:**
- Create: `skills/rca-error/SKILL.md`
- Test: `tests/rca/test_rca_error_skill.py`（frontmatter 可解析、description 非空）

- 正文：只收 `trace_id`；必须调 `diagnose_trace`；不得改写卡片里的数字；未判定就说未判定。
- `user-invocable: false`、`disable-model-invocation: true`、`match` 覆盖"报错/500/打不开/查一下这个 trace"。

- [ ] Step 1-4 提交：`feat(skills): 新增 rca-error 入口技能`

### Task 9: 详情页与接口（7.3 / D10）

**Files:**
- Create: `src/api/rca.py`（`GET /rca/diag/{diag_id}`）
- Modify: `src/main.py`（注册 router）、`src/middleware/auth.py`（`/api/rca` 必须登录）
- Create: `deploy/nginx/html/diagnose.html`
- Test: `tests/api/test_rca_api.py`（登录缺失 → 401；存在 → 200）

- 页面：未登录跳 `login.html`；展示报告全文（证据摘要、断言、下一步）；`expanded` 留痕暂以日志事件记录（原文展开依赖后续 source 存原文，本期无原文可展）。

- [ ] Step 1-5 提交：`feat(api): 诊断详情接口与页面（需登录）`

### Task 10: 日志事件与文档（1.5 / 9.1-9.4）

**Files:**
- Modify: `src/core/log_events.py`、`src/core/log_event_specs.py`（`diagnose done` / `diagnose undetermined`）
- Modify: `docs/agents/logging-rules.md`、`docs/agents/troubleshooting.md`、`docs/agents/api_contract.md`、`docs/agents/glossary.md`
- Modify: `docs/openspec/changes/trace-diagnosis/tasks.md`（回填）

- [ ] Step 1-3 提交：`docs: 登记诊断事件与入口（含契约与术语）`

---

## 自检

- 规格覆盖：D4（tier/root_cause 分离）→ T1；D13（分型）→ T2；D6（引用校验）→ 沿用 P1 + T3；D10（摘要卡片 + 登录详情）→ T5/T9；7.2（只存摘要）→ T6。
- 不做的：追溯（3.3）、RetrievalProbe（2.3）、窄协议（2.4）、nginx（2.5）、定位器（6.x）、回归集（8.5）→ P2b / P3。
- 类型一致：`DiagnosisReport` 新字段全带默认值，P1 调用方与测试不需改动。
