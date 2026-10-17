# ADR-0026：DeepEval 桥下的 RAGAS 裁判客户端策略（细粒度超时承载点换位 / 关 SDK 重试 / NaN 判据重试 / 两处桥不可控旋钮）

- **Status**：Superseded by 0027
- **Date**：2026-10-17
- **Deciders**：用户（议题提出与拍板）；Claude（换入口后的逐条复核、源码取证与实现）
- **Supersedes**：ADR-0024
- **修订记录**：2026-10-17 创建后、尚无代码依赖时补「决策 6 的落地现状」——初稿以完成态陈述了尚未实现的 hooks 字段，与 `src/infra/llm/provider/http_observability.py` 及 `docs/agents/logging-rules.md` 冲突。
- **关系**：落实 change `deepeval-bridge-eval` 的 design D3 / D4 / D9 / D11 / D13；观测事件字段口径以 `src/core/log_event_specs.py` 的 `EVENT_SPECS` 为唯一归属。

## 背景与问题

评测打分入口由 RAGAS 0.4.x 现代栈改为 **DeepEval 桥**（`deepeval/metrics/ragas.py`
驱动 ragas 0.3.9 的经典四指标）。ADR-0024 的六条裁判 client 决策建立在**现代栈**
（`llm_factory` + `InstructorBaseRagasLLM` + instructor 异常）之上，换入口后须**逐条复核**：
哪些仍成立、哪些承载点换位、哪些失效。

复核结论（逐条对 ADR-0024 的六条决策）：

| ADR-0024 决策 | 桥下状态 |
|---|---|
| 决策 1 细粒度 HTTP 超时 | **仍成立，承载点换位**：从 `build_native_client` 的 `AsyncOpenAI(timeout=…)` 换到 **`build_chat("judge", http_async_client=httpx.AsyncClient(timeout=…))`** |
| 决策 2 关 SDK 重试（`max_retries=0`） | **仍成立**，但**只关 SDK 重试**——ragas 框架层自带重试（见下「桥不可控旋钮②」） |
| 决策 3 本层有界重试 | **判据换位**：桥把失败吞成 `NaN` 不抛异常 ⇒ 判据从 `except Exception` 改为**非掩码 `NaN`** |
| 决策 4 `max_tokens=8192` | **仍成立**：经 `build_chat("judge", max_tokens=…)` 透传 |
| 决策 5 关思考 | **仍成立**：经 `build_chat("judge", thinking=False)`（`thinking_extra_body` 归一化） |
| 决策 6 失败必留原始响应 | **失效并改**：桥不给 instructor 异常、无 `last_completion` ⇒ 证据改由 `http_async_client` 的**请求级 event hooks** 承载 |

## 决策

### 决策 1：细粒度 HTTP 超时落在 `http_async_client`（非 `http_client`）

`build_chat("judge", http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=150, write=30, pool=10), event_hooks=…), max_retries=0)`。

**理由**：ragas 的裁判调用走**异步**（`evaluate` 提交 `metric.single_turn_ascore`），而
langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；
异步用 `:1273` 的 `http_async_client`）⇒ 只传同步字段会让超时与 hooks **双双失效**。
细粒度四维超时的理由同 ADR-0024 决策 1（httpx 无"整请求总预算"，平铺标量会四维同值、看不出卡在哪段）。

### 决策 2：`max_retries=0` 关 SDK 重试；但**不得声称"重试维度完整保住"**

`max_retries=0` 只关掉 **OpenAI SDK** 的重试；**ragas 框架层自带重试**——`ragas/llms/base.py:113`
用 `add_async_retry` 包裹每次生成，`ragas/run_config.py:52` 默认 `max_retries=10`，其重试
**只针对 `openai.RateLimitError`（HTTP 429）**。**桥不暴露 `RunConfig`** ⇒ 该项**登记为接受的偏离**。
防倍增效的口径：**外层 `RAGAS_METRIC_MAX_ATTEMPTS` SHALL NOT 重试限流类**（429 已由 ragas 重试 ≤10 次，外层再叠会 10×3 放大）；其余失败（超时 / 畸形响应 / 403 额度）仍由外层重试。

### 决策 3：本层有界重试的**判据是「非掩码 NaN」**，不是异常

桥调 `ragas.evaluate(...)` 未传 `raise_exceptions`（默认 `False`），`ragas/executor.py` 对
**任何** `Exception` 都 `return np.nan` ⇒ 桥不抛异常，原 `except Exception` 判据**永不触发**。
故判据改为**非掩码 `NaN`**（`RAGAS_METRIC_MAX_ATTEMPTS` 保留）。**不做**"合法缺值 / 裁判失败"
子分类——桥只回一个 `float`，HTTP 200 的 envelope 恒为合法 JSON，该分类不可实现。

### 决策 4 / 决策 5：`max_tokens=8192`、关思考 —— 保留

经 provider 层 `build_chat("judge", max_tokens=…, thinking=…)` 透传（`thinking` 经
`thinking_extra_body` 归一化，**SHALL NOT** 硬写 `enable_thinking`）。理由同 ADR-0024 决策 4/5。

### 决策 6：失败留证改由**请求级 hooks** 承载

换桥后无 instructor 异常、无 `last_completion` ⇒ 原 `_judge_failure_note` 不适用。
证据改由裁判 `http_async_client` 的 httpx event hooks 记录：**模型 / 耗时 / 状态码 /
请求体摘要 / 响应体摘要 + `finish_reason`**。**响应体必须记**——主因失败是 HTTP 200 +
畸形响应体，只记状态码看不到它。**hook SHALL 为 `async def` 且 `await response.aread()`**
（异步 client 是 `await hook(response)`，同步 hook 抛 `TypeError`）。**该日志只做留证**（限流例外见决策 2），
SHALL NOT 被当作 NaN 子分类依据。

**落地现状（2026-10-17，与决策意图的差距明写）**：`http_async_client` 的 hooks 当前实记
`url` / `status` / `body_bytes` / `json_ok` / `finish_reason`（响应体不可解析时另有 `body_exc`），
且**仅在失败时产日志行**（非 2xx 或响应体不可解析）；`model` 由每轮一次的 `eval model init`
事件承载，耗时由每次尝试的 `judge call` 事件承载。**请求体摘要与响应体摘要尚未落地**，
`json_ok` 仅能判"响应体不可解析"。该项已列入复查触发条件。

## 两处「桥不可控的旋钮」（明写，避免后人误以为仍全可控）

1. **温度档由 ragas 按 `n` 决定**：`n=1` 的三指标为 0.01，`answer_relevancy`（`n=3`）为 0.3（`ragas/llms/base.py:71-73`；`LangchainLLMWrapper` 改写见 `:229-231`）；
   `LangchainLLMWrapper` 会改写底层 `chat_model.temperature`，**`bypass_temperature` 默认 False
   且桥不暴露** ⇒ 拿不回温度 0。
2. **ragas 框架层 `RunConfig.max_retries=10`（限流类）不可配置**：见决策 2。

## 代价与复查触发条件

**代价**：① 温度不可控（评测侧接受，见 change design D3）；② ragas 框架层重试不可配置，
靠"外层不重试 429"避免乘法放大；③ 失败证据从"异常对象自带的原始响应"退化为"`HookEvidence` 桶 + 失败时的
HTTP 层告警"（响应体摘要尚未落地），粒度略降（但仍是当前可得的一手证据）。

**复查触发条件**：① 桥升级 ragas 版本（0.3 → 0.4 动过 API）⇒ 复核本 ADR 全条；
② 换裁判模型（尤其换 provider 或换回思考档）⇒ 重跑超时/预算对照；
③ 若上游 ragas 暴露 `RunConfig` 或改为可配置重试 ⇒ 可移除"外层不重试 429"的补偿口径。
④ 若确需决策 6 设计的完整请求级日志（模型 / 耗时 / 请求体摘要 / 响应体摘要、且成功调用也留痕），
   须另立项补 hooks 字段与证据落盘——当前只完成"证据桶 + 失败告警"。
