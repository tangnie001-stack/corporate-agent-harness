## Why

排障时拿到一个 `trace_id`，有四件事答不上来：**这次采样的温度是多少、哪个智能体真正生效、prompt 由什么组成、注入了哪些 skill**。它们只在数据库的派生记录里留有痕迹（如 `[[skill-injection]]` 行），日志与 trace 都读不到。

这四件事是"答错归因"的生成侧证据：没有温度就无法判断输出稳定性，没有 `prompt messages` 就区分不了"上下文被裁"与"模型忽略"，没有 `agent resolved` 就判断不了人设是否真的生效。

`turn-provenance-observability` 整包含 36 项，其中 29 项是**用户可见的来源声明与历史回放**，与本问题无关。本变更只摘出其中的日志部分（原组 1 的事件登记 + 原组 4 的 7 项），先让"从日志能判定"成立。

## What Changes

- 登记 5 个新日志事件：`agent resolved`、`prompt assembled`、`prompt messages`、`skill injected`、`skill dispatch`（`Event` 与 `EVENT_SPECS` 双处同名登记）。
- `model turn` 增加三个字段：`temperature`、`temp_source`（explicit / default）、`kb_bound`。
- 温度从分档处提取为**单一真源**：同一个变量既传给 LLM 调用也进日志，避免"日志记的和实际传的不是一个值"。
- `_resolve_session_agent` 返回结构化结果（生效值 + 来源），供 `agent resolved` 消费。
- `_preload_skills_text` / `_preload_if_first_round` 的返回类型改为 `(正文, 技能名列表)`，使 `skill injected` 能记录名单。

## Capabilities

### Modified Capabilities

- `agent-loop-observability`：新增两条要求——"每轮来源事实可查"（温度、生效智能体、人设是否生效）与"技能加载与分派可查"（命中/未注册、inline/fork、注入体量）。均只涉及日志，不涉及对外事件。

## Impact

**代码**

- `src/core/log_events.py` / `src/core/log_event_specs.py`：登记 5 个事件 + `model turn` 字段
- `src/agents/graph/agent_node.py`：温度单一真源（`:158-167`）+ `model turn`（`:194-203`）+ `prompt messages`（`_initial_messages`）
- `src/rag/prompt.py`：`build_system_prompt` 内记 `prompt assembled`；该模块**当前未 import logging**，需补模块级 logger
- `src/services/agent_service.py`：`agent resolved`（消费 `_resolve_session_agent` 结构化结果）、`skill injected`（两个注入点）、`skill dispatch`（`parse_prefix` 之后）

**契约**

- 不改 SSE、不改前端、不改 `process` 列口径；对外行为零变化，只有日志多字段。

**依赖**

- 无前置 change。`trace-diagnosis` 依赖本变更的产物。

**风险**

- `prompt.py` 补 logger 属主链路改动，需确认不引入循环 import。
- 事件级别由 `EventSpec` 固定，普通文本轮若也记 `agent resolved` / `skill dispatch` 会产生噪声 —— 两处均用调用点守卫"值为空时不记"。

## 与父 change 的关系

本变更是 `turn-provenance-observability` 的**先行子集**：

- 承接其原组 1 的 1.3 / 1.4 与原组 4 的 4.1 / 4.3 / 4.4 / 4.5 / 4.6（4.2 `agent resolved` 依赖原组 2 的 2.5 结构化改造，本变更一并带上）；
- 其**原组 4.2 之后的 SSE 声明（组 1 的 1.1/1.2、组 2、组 3）与前端回放（组 6）、文档同步（组 7）不在本变更内**；
- 父 change 落地时应删除已被本变更覆盖的任务，避免同一事件登记两次、同一函数改两遍。

## 非目标

- 不做用户可见的来源声明（SSE `status` 行）与历史回放
- 不做温度入 SSE（维持父 change 的决定：温度属运维信息）
- 不改 `iteration done`（迭代点拿不到消息构成，意图由 `prompt messages` 承载）

## 验收判据

拿一条真实 `trace_id`，**只读日志**即可回答四个问题，不需要查库：

1. 这次调用的温度是多少、是显式传的还是沿用默认；
2. 实际生效的是哪个智能体、来源是哪一种（绑定 / 新绑定 / 不一致忽略 / 未注册降级 / 未绑定）；
3. 本次 prompt 由什么组成（人设来源、kb 绑定、纪律与委派段是否注入、system / 注入 / 历史各几条）；
4. 本轮注入了哪些 skill、来自命令还是预设预加载、各多少字符；命令轮的分派结果与执行形态。
