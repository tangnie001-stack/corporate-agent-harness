# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`（D1–D13）；风险：`design.md` 的 R1–R10。
>
> **提交点判据（明写，避免误读）**：仓库纪律"每组跑通再提交"在本 change 下**有已知例外**——第 1 组把 `ragas` 降到 0.3.9 后，`src/cli/eval_ragas.py` 里现代栈的导入与其测试（`tests/cli/test_eval_modern_stack.py`、`tests/cli/test_eval_ragas.py`）会**红到第 9 组**（删除动作与调用点改写同批）。故**第 1–8 组的"跑通并提交"= 本组新增/改动的测试通过**，SHALL NOT 被读成"全量绿"；全量绿以**第 9 组收口**为准，判据是**最终态无悬空引用**（spec「0.4.x-era artifacts」条款）。

> **Phase 1 分组调整（两处）**：
> 1. 「删除/重写现代栈测试」（原 9.6）在 Phase 1 的收尾任务（T11）内提前执行——否则阶段无法以「全量绿」收口；其复选框仍留第 9 组（未勾选），归组不变。
> 2. 「评测路径无现代栈符号」自查（原 1.3）作为**阶段级断言**在阶段末（T12）执行，而非在第 1 组内。

## 1. 依赖与环境（D1）

- [x] 1.1 `pyproject.toml`：`ragas` 由 `0.4.3` 改为 **`ragas==0.3.9`**（精确 pin，与仓库既有 pin 纪律一致）；新增 `deepeval`；显式记录 `click` 与 `huggingface_hub` 的下限，防回落。
- [x] 1.2 重建环境并留证：`pip install -e .` 后 `pip check` 无**新增**冲突；记录实装版本（`ragas` / `deepeval` / `click` / `huggingface_hub`）。
- [x] 1.3 自查：评测路径 grep 确认不再引用 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment`（scope 限评测路径；`factory.py` 仍含 `llm_factory` / `embedding_factory`，归 Phase 3）。
- [x] 1.4 **环境变量（官方明文，防污染）**：评测运行环境显式设 `DEEPEVAL_DISABLE_DOTENV=1`（否则 import 即把 `.env`/`.env.local` 注入 process env）与 `DEEPEVAL_TELEMETRY_OPT_OUT=1`（否则 posthog 遥测开启）；加测试断言两者生效。

## 2. 评测入口与指标面（D2）

- [x] 2.1 **先建裁判与 Embeddings（前移自第 9 组的"改嫁"；本组必须先做，否则本组提交点必红——桥要 langchain `BaseChatModel`，旧 `build_ragas_judge()` 产出 `InstructorBaseRagasLLM` 类型不接）**：裁判 SHALL 经**项目 provider 层**构造——`build_chat("judge", thinking=RAGAS_JUDGE_THINKING, max_tokens=RAGAS_JUDGE_MAX_TOKENS, http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(connect/read/write/pool=RAGAS_HTTP_TIMEOUT_*), event_hooks=...), max_retries=RAGAS_HTTP_MAX_RETRIES)`。**⚠️ 必须传 `http_async_client`，不是 `http_client`**——ragas 的裁判调用走**异步**，而 langchain 的 `http_client` **只喂同步 client**（`langchain_openai/chat_models/base.py:806`；异步用 `:1273`）⇒ 只传同步字段则细粒度超时与 hooks **双双失效**（务必落一个断言用例钉住该实参）。`thinking` 经 `thinking_extra_body` 按 provider 归一化；返回 `ReasoningPreservingChat`，是 `ChatOpenAI` 子类，桥可接收。**SHALL NOT** 在 `src/` 里直写 `ChatOpenAI` + 硬编码 DashScope 端点/密钥；**SHALL NOT 硬写 `extra_body={"enable_thinking": …}`**；**SHALL NOT 传 `temperature`**（温度由 ragas 决定）。`Embeddings` 复用现有 `build_embeddings()`。
- [x] 2.2 写失败测试：指标集为**经典四项**，且**不含** `RagasMetric` / `RAGASContextualEntitiesRecall`。
- [x] 2.3 写失败测试：每个桥度量构造时**显式传入 `model=`**（防回落到 `gpt-3.5-turbo`；该默认值在源码里是 **7 处**）。
- [x] 2.4 实现：`src/cli/eval_scoring.py` 的指标构造改为桥的四个单项；`RAGASAnswerRelevancyMetric` 传入显式 langchain `Embeddings`。
- [x] 2.5 实现：`src/cli/eval_ragas.py` 打分阶段由 modern `@experiment` **就地改写**为桥度量驱动（逐样本构造 `LLMTestCase`：`input` / `actual_output` / `expected_output` / `retrieval_context`）。**边界**：本组改写**编排本身**；第 9 组只清理因此**失引用**的辅助函数与残留，**不重复改写编排**。
- [x] 2.6 实现：**四指标评分串行（并发=1）** —— 停用对桥度量的并发调用。**依据**：桥复用 `ragas.metrics` 的模块级指标实例，`ragas.evaluate` 会复位 `metrics[i].llm = None`、`LangchainLLMWrapper` 会改写 `chat_model.temperature` ⇒ 并发会互踩、间歇性错分（design D7）。
- [x] 2.7 跑通并提交。

## 3. 裁判与元数据（D3 / D9）

- [x] 3.1 写失败测试：报告元数据含 `ragas` 版本、`deepeval` 版本、judge 模型、**温度档位说明（0.01 / 0.3，本项目不可控）**、`model=` 已显式传入的标记。
- [x] 3.2 实现：报告头与台账条目补齐上述字段；断言报告中 SHALL NOT 出现"裁判 temperature=0"表述。
- [x] 3.3 **框架内部调用的可观测性（D13）**：给裁判的 **`http_async_client`**（`httpx.AsyncClient`）挂 httpx **event hooks**，记录请求级日志（模型 / 耗时 / 状态码 / 请求体摘要 / **响应体摘要 + `finish_reason`**——主因失败是 HTTP 200 + 畸形响应体，只记请求与状态码会看不到）。**挂错字段 = 零触发**：`http_client` 只喂同步 client（langchain `chat_models/base.py:806`），ragas 走异步。**hook SHALL 为 `async def` 且用 `await response.aread()`**（异步 client 调 `await hook(response)`，`httpx/_client.py:1697`；同步 hook 会 `TypeError`；同步 `read()` 在异步流上抛 `RuntimeError`，`_models.py:944`）。**`trace_id` 以显式闭包绑定进 hook**（async def 内可闭包），SHALL NOT 依赖 contextvar（ragas `evaluate` 内部用自己的 `Executor` 线程池，不保证携带）；client 跨样本复用 ⇒ 绑到**可变证据桶**而非固定 `trace_id`（评分串行，安全）。**该日志只做留证**，SHALL NOT 用于 NaN 子分类。验收范围限「**经我们 `http_async_client` 发出的请求**」。
- [x] 3.3a **hook 冒烟断言（必做；方向相反的两条）**：跑 1 题桥度量（如 `RAGASFaithfulnessMetric`，真调裁判）断言 ① 响应 hook 触发 **> 0** 次、② hook 内 `await response.aread()` + `json.loads(body)` 成功且能取到 `finish_reason`、③ 指标有分；**并加负向用例**：只传 `http_client`（同步字段）时响应 hook 触发 **== 0**——把"挂错字段就零触发"钉成回归防线（正向已由 T12 冒烟实测 `hook_events=13` 证明；负向在仓内为结构断言 `http_client is None`，行为级 A=4 事件 / B=0 事件的实测证据见 `docs/tmp/deepeval-poc/RESULTS.md`「Hook 冒烟」）。
- [x] 3.4 跑通并提交。

## 4. 失败语义与主分口径（D4 / D7）

- [x] 4.1 写失败测试：桥返回 `NaN` 时该行**仍存在**、携带失败信息、被计入 `NaN` 计数，且**不参与**主分均值。
- [x] 4.2 写失败测试：`NaN`（裁判故障）与样本类别 `empty`（未检索）在报告中**标记不同**。
- [x] 4.3 **重试判据改造（D4；**不做 NaN 子分类**）**：桥把失败吞成 `NaN`、**不抛异常** ⇒ 把 `_score_metric_with_retry` 的判据从 `except Exception` 改为 **非掩码 `NaN`**（有界，`RAGAS_METRIC_MAX_ATTEMPTS` 保留）。**SHALL NOT 实现"按请求级日志把 NaN 分为合法缺值与裁判失败"的分类**——该分类不可实现（桥只回一个 float；HTTP 200 的 envelope 恒为合法 JSON，主因失败与"生成问题全空"在 HTTP 层同形）。**唯一例外：限流类（= HTTP 429 / `openai.RateLimitError`）不触发外层重试**（ragas 框架层已对该类重试 ≤10 次，`run_config.py:52` + `base.py:325-335`；429 由 hooks 状态码判定 ⇒ **hook 需按格记下状态码**，用于该例外）；**403 额度类不属该列，仍由外层重试**。写测试：非掩码 NaN → 重试 → 可恢复；**429 → 不触发外层重试**。
- [x] 4.4 **失败留证换承载点（D4 / D13）**：删 `_judge_failure_note`（桥无异常、无 `last_completion`），失败证据由裁判 **`http_async_client`** 上的请求级日志承载（与 3.3 复用同一 client）；**该日志只做留证，不用于 NaN 子分类**。
- [x] 4.5 实现：`apply_sample_masks` / `main_score_means` 改为「纯 KB **且非 NaN**」的四指标均值；报告新增 `NaN` 计数。
- [x] 4.6 实现：`eval_report` 落库与 `kb_eval` API 去掉 `answer_correctness`；同步 `RAGAS_METRIC_COLUMNS`、**`const.py:496` 的注释**（仍写"answer_relevancy/answer_correctness 不在此列"）、报告/stdout 断言、**`docs/agents/api_contract.md` 逐处**（`:733` / `:744` / `:909` / `:912` / `:1353` / `:1379` / `:1400`）、**`docs/agents/ragas-eval-ledger.md` 表头**（**新增「口径版本」列**；`answer_correctness` 列**保留**、历史行不动、新行留空/`N/A`——**不得删列**，否则表头列数与历史行不等、Markdown 表破损）。**同时处置"失义配置"**：① `RAGAS_EVAL_CONCURRENCY`（`const.py:515`，注释明写"行级并发上限"，`eval_ragas.py:533` 用）在四指标改**串行**后失义 ⇒ 删除或改为串行语义并重述注释；② 台账「公共配置」段（`ragas-eval-ledger.md:19` 的并发项、`:21` 的"**五项**均值"）SHALL 同步更新（历史行不动）。（`RAGAS_EVAL_CONCURRENCY` 处置与台账「公共配置」并发项归 Phase 3，本阶段未完成）
- [x] 4.7 **NaN 率门禁（D10，spec「NaN rate gates the evaluation」）**：`check_gate` 增加 NaN 率判据（阈值 = 经验初值 **10%**，`>阈值` 则 fail）；**分子/分母按 spec 口径**——**分母 = 纯 KB 样本 × 四指标**（实测 19×4 = **76 格**；`>10%` 即 **>7.6 格 ⇒ ≥8 格**），**分子 = 纯 KB 格里的全部 NaN**（掩码类由分母天然排除；合法缺值型与裁判失败型**一并计入**，**不做子分类**）；报告打印「**建议启用逃生门**」提示。写测试覆盖：`≤阈值` 不判失败、`>阈值` 判失败且出提示、**掩码 NaN 不计入分子/分母**。**注意单位**：SHALL NOT 用"题数"口径实现（与分母定义差约 4 倍）。
- [x] 4.8 跑通并提交。

## 5. Agent 行为轴（D6，**必须 `evals_iterator` 驱动、分数从 `test_run` JSON 回读**）

> 已由 1 题 spike 实测（2026-10-16）：只挂 `CallbackHandler` **不评分**（`metric.score=None`、`reason=''`）；分数落在 eval loop 内部（`copy_metrics()` 副本），**只能从 `test_run_*.json` 回读**：逐题分在 **`testCases[j].metricsData[k].score`**（`metricsScores` 是**按指标聚合**，只能作粗守卫）；且 `evals_iterator` 是 generator，**只有耗尽才落盘 JSON**。

> **组 5 实施差异（Phase 2 收口记，2026-10-17）**：5.1 未按原样落地（nest_asyncio 代码注释与专门回归测试未写），改为 main() 的 `hook_events > 0` 运行时断言 + D6/R9 写回 + 真链路冒烟守卫异步形态；5.6 并发负向验证未落地（仅有"每题独立 handler"前置断言，非并发跑 2 题）。

- [ ] 5.1 **异步可行性已在 change 起草期探测（2026-10-16，见 `docs/tmp/deepeval-poc/RESULTS.md`「Q1 探测」）**：外层 `asyncio.run` 内驱动 `evals_iterator`（默认 `run_async=True`）与 `AsyncConfig(run_async=False)` **两路均 WORKS**（1 题真调 judge、逐题分 1.0）；机制是 `get_or_create_event_loop()` 调 `nest_asyncio.apply()`，内层 `run_until_complete` 在**同一 loop** 重入。本项实施时**落一个回归测试**钉住该形态（勿新建第二个事件循环），并在代码注释写明 `nest_asyncio` 的进程级 monkeypatch 副作用。**（未按原样落地：nest_asyncio 注释与专门回归测试未写；异步形态改由 `main()` 的 `hook_events > 0` 运行时断言守卫——见 `eval_ragas.py:1058`，机制见 D6/R9）**
- [x] 5.2 写失败测试：**只挂 handler、不驱动 `evals_iterator` 时取不到分**（防止回归到被否定的形态）。
- [x] 5.3 实现（两半缺一不可）：`EvaluationDataset(goldens=[Golden(input=<题面>, expected_output=<参考答案>)])` + `evals_iterator(metrics=[…], display_config=DisplayConfig(results_folder=<OUT>))` 驱动循环；循环体内执行 `graph.ainvoke(..., config={"callbacks": [CallbackHandler()]})`（**handler 不挂 metrics**）。
- [x] 5.4 实现：从 `<OUT>/test_run_*.json` 的 **`testCases[].metricsData`**（含 `name` / `score` / `success`）**回读逐题**行为分；`golden ↔ testCase ↔ trace_id` 以 `order`/`input` 映射；SHALL NOT 读调用方持有的 `metric.score`；SHALL NOT 把 `metricsScores`（按指标聚合）当逐题分。**每题新建独立 `CallbackHandler` 实例**（不得跨题复用，否则串 trace）。
- [x] 5.5 写失败测试 + 断言：**轨迹非空 且 已从 `test_run` 取到该指标的分**，否则 fail-loud（`agent_loop_detection` 无 trace 时源码直接置 0.0）；并覆盖**生成器未耗尽**路径——循环体抛异常/提前 `break` ⇒ 无 JSON ⇒ fail-loud（循环 SHALL 跑到耗尽）。
- [ ] 5.6 **并发负向验证**：并发跑 2 题，断言两者的 trace 与分**不串**。**（未落地：仅有 `test_behaviour_driver.py` 的"每题独立 handler"断言作前置保障，非并发跑 2 题的负向验证）**
- [x] 5.7 选型并接入 ≤5 项 DeepEval 原生行为指标（候选：`TaskCompletion` / `StepEfficiency` / `AgentLoopDetection`）；**清单在本组内定稿并写回 design D6**（spec 已声明"实施期定稿"，不得与 design 互相推诿）。
- [x] 5.8 `summarize_trajectory` 保留，仅作报告诊断摘要；**不得**作为行为指标数据源。
- [x] 5.9 跑通并提交。

## 6. 出题路下线（D8）

- [ ] 6.1 实现：评测入口不再触发**测试集生成**（输入为既有版本文件）；**注意区分**——`_load_latest_testset` / `_ensure_vertexai_stub` 仍被评测主流程使用（`src/cli/eval_ragas.py:803/806/818-820`），SHALL NOT 一并移除。
- [ ] 6.2 实现：`src/cli/eval_ragas_generate.py` 文件头标注"**出题生成**保留未调用（依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`，且该依赖是**函数级导入**，故仅导入模块不会炸）"，并**指向前迁路径**（DeepEval `Synthesizer`，见 design D8）；台账同步登记；**不得**写成"整个模块不再被调用"。
- [ ] 6.3 实现：**硬护栏** —— `--generate` 路径显式失败并给出指引（说明原因 + **前迁路径 DeepEval `Synthesizer`** + "若确需暂用旧路须临时环境 + ragas 0.4.x"），SHALL NOT 让用户撞到裸 `ImportError`；加测试断言该路径的报错文案。
- [ ] 6.4 确认评测输入为**固定版本文件**（v5 / 21 条），评测流程与**出题生成**解耦（但测试集**加载**仍在评测路径内）。
- [ ] 6.5 跑通并提交。

## 7. 逃生门（D10，结构预留）

- [ ] 7.1 实现：把"采样/用例构造"与"评分"解耦到**可替换边界**（桥为默认实现）。**本组不实现替代实现** —— spec 已同步降级为"首次启用逃生门时再实现并冒烟，且登记台账/需求池"，避免"规格要求实现、任务只做边界"的错配。
- [ ] 7.2 实现：台账增列"口径版本"与"是否启用逃生门 + 原因 + 轮次"；并把"替代实现待做"登记进需求池。
- [ ] 7.3 跑通并提交。

## 8. Langfuse 回写与 score 口径（D9）

> 前提事实：**DeepEval 无 Langfuse 集成**，Langfuse 侧配方是 v3/v4 API（与我们自托管 v2 不匹配）⇒ 回写全由我们自建。

- [ ] 8.1 实现：沿用 `src/cli/eval_scoring.py` 的 `write_scores` 通道（`langfuse_context.client_instance` + `client.score`；**不得** `new Langfuse()`）；**行为轴指标也一并回写**，与四项同级、共用同一 `trace_id`。
- [ ] 8.2 实现：**新口径 score 名带后缀**（如 `faithfulness@bridge-0.3.x`），**不覆盖**历史同名 score；加测试断言命名规则。
- [ ] 8.3 实现：**NaN 不写**（维持"缺失"语义，SHALL NOT 写 `0`）；在 design/代码注释写明该语义，避免后人当 bug 修。
- [ ] 8.4 跑通并提交。

## 9. 0.4.x 遗留处置（D11）

> 原则：**不是"删光"，而是三分——复用 / 删除 / 改写**。每一项都要有落点。
> **提交粒度**：删除动作 SHALL 与「调用点改写 + 受影响测试」**同一提交**；判据是**最终态**无悬空引用，不要求每个中间提交都干净（否则 TDD 小步的提交点必红）。
> **注**：9.1/9.2 的"改嫁"已在**第 2 组前移**（裁判必须在第 2 组就可用）；本组只做剩余复用与删除/改写。

- [ ] 9.1 **复用（改嫁到 `build_chat("judge", …)`）**：`RAGAS_JUDGE_MAX_TOKENS` → `max_tokens=…`；`RAGAS_JUDGE_THINKING` → **`thinking=…`**（**不得**硬写 `extra_body={"enable_thinking": …}`，见 2.1）；`RAGAS_HTTP_TIMEOUT_{CONNECT,READ,WRITE,POOL}` → **`http_async_client=httpx.AsyncClient(timeout=httpx.Timeout(...))`**（**不是 `http_client`**——ragas 走异步，见 2.1）；`RAGAS_HTTP_MAX_RETRIES` → `max_retries=0`。**依据**：已抓包证实 `max_tokens` / 思考参数经桥透传；已核 `build_chat(**overrides)` 会把 client 参数透传给 `ChatOpenAI` ⇒ **细粒度超时（经 `http_async_client`）+ 关 SDK 重试保住**，承载点从 `build_native_client()` 换到 **provider 层的 `build_chat`**（不得直写裸 `ChatOpenAI`）。**⚠️ 不得声称"重试维度完整保住"**：`max_retries=0` 只关 SDK 重试，**ragas 框架层自带 `RunConfig.max_retries=10`**（仅限流类，`ragas/run_config.py:52`/`llms/base.py:113`）且桥不暴露 ⇒ 登记为**接受的偏离**，并在 4.3 落实"**外层重试不重试 429/限流类**"以避免乘法放大。
- [ ] 9.2 **复用（本层仍需要，判据改非掩码 NaN）**：`RAGAS_METRIC_MAX_ATTEMPTS` 保留，但**判据从 `except Exception` 改为非掩码 `NaN`**（桥把失败吞成 NaN、不抛异常，见 D4/4.3；**唯一例外：429/限流类不重试**）；`RAGAS_JUDGE_TIMEOUT` 语义并入 9.1 的 `http_async_client` 超时。
- [ ] 9.3 **删除**：`src/infra/llm/provider/factory.py` 的 `build_ragas_judge()` / `build_ragas_embeddings()`；`build_native_client()` 若确认无其他调用方则一并删除（先全仓核引用）。
- [ ] 9.4 **清理残留**（第 2 组已就地改写编排，本项**只删因此失引用的部分**）：`src/cli/eval_ragas.py` 的 `_EvalResult`、`_score_metrics`、`_exc_chain_summary`、`@experiment` 装饰器/import 残留。**所有权边界（避免同一动作两个 owner）**：`build_metrics()` 的改写归 **2.4**；`_score_metric_with_retry` 判据归 **4.3**；`_judge_failure_note` 删除归 **4.4**；本项 SHALL NOT 重复这三件事。
- [ ] 9.5 **改写**：`judge call` / `metric retry` 日志事件（埋点位置从"我们调裁判"变为"桥内部调 ragas"）；`src/cli/trace_logs.py` 的适用范围随之重述。
- [ ] 9.6 **测试**：删除/重写 `tests/cli/test_eval_modern_stack.py`、`tests/cli/test_metric_retry.py`、`tests/infra/llm/provider/test_ragas_modern_factories.py`、`test_native_client.py`；核对 `test_registry_models.py`（judge 角色绑定是否仍有其他消费方）。
- [ ] 9.7 **ADR**：新增一条 ADR 取代 `docs/adr/0024-ragas-judge-client-policy.md` 的裁判 client 政策（含 `temperature=0` 那条），并按仓库约定在旧 ADR 的 Status 加**反向指针**、更新 ADR 索引表。**新 ADR SHALL 记录两处"桥不可控的旋钮"**（与旧 ADR 的"显式可控"承诺形成对照，避免后人误以为仍全可控）：① 温度档由 ragas 按 `n` 决定（0.01 / 0.3），`bypass_temperature` 不暴露；② **ragas 框架层自带 `RunConfig.max_retries=10`（限流类）**、桥不暴露 ⇒ `max_retries=0` 只关 SDK 重试，**不得声称重试维度完整保住**。
- [ ] 9.8 **死引用自查**：`grep -rn` 确认全仓不再出现 `ragas.metrics.collections` / `llm_factory` / `embedding_factory` / `@experiment` / `build_ragas_judge` / `build_ragas_embeddings` 的**悬空引用**（出题路的 0.4.x 依赖例外，需在第 6 组标注）。
- [ ] 9.9 跑通并提交。

## 10. 验收

- [ ] 10.1 静态闸门：`ruff check` / `pyright`（不新增 error）/ `openspec validate deepeval-bridge-eval --strict` / `python -m src.cli.check_docs` 全过。
- [ ] 10.2 **首轮用 `--no-gate`**：先跑出全量分布（**不用旧阈值判新尺子**），确认四项指标与行为指标均有值、`NaN` 计数为 0 或已解释、行为分确系从 `test_run_*.json` 的 **`testCases[].metricsData`** 回读（非 `metricsScores`）、**NaN 重试**（4.3）确被触发过或明确未触发。
- [ ] 10.3 活体：1 题 → 5 题 → 全量 21 题，逐级确认。
- [ ] 10.4 **阈值重标定**：按 10.2 的分布取保守下界（≥均值×0.9 且 ≥历史同名×0.9，具体在分布出来后定稿），写入单一常量，并更新 `kb_eval` 下发。
- [ ] 10.5 **NaN 阈值复核（对应 4.7 的门禁实现）**：以 10.2 的**实测 NaN 率**复核"经验初值 10%"，必要时改值并同步 spec 与 `check_gate` 常量。
- [ ] 10.6 **行为指标清单已定稿**（第 5 组 5.7 的产出已写回 design D6），且行为轴**不进主分/门禁**已在实现中体现。
- [ ] 10.7 台账新增一行（标注口径版本 = `bridge-0.3.9 / v5`），历史行不改。
- [ ] 10.8 验收记录写回计划文件并提交。
