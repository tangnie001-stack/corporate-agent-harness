# DeepEval 统一入口 + 内嵌 RAGAS（路线 C）— 1 题冒烟 PoC 结果

> 一次性去风险验证，非可用代码。隔离 venv：`/tmp/deepeval-poc/.venv`。
> 裁判：DashScope OpenAI 兼容端点 `https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen3.8-flash`，`temperature=0`、不发 `top_p`、`max_tokens=8192`、关思考。
> 仓库只读；`/opt/venvs/corporate_rag` 未碰。
> **脚本未入库**：`run_k1.py` … `run_k6.py` 是一次性草稿（含类型不洁代码，会被 pre-commit 的 pyright 拦），故只留本结论文档；完整可复算命令见文末，按需重建。

## 结论表

| 项 | 结论 | 关键证据 | 影响 |
|----|------|---------|------|
| K1 DeepEval 用 DashScope 裁判 | **WORKS** | `AnswerRelevancyMetric(model=OpenAIModel(qwen3.8-flash, base_url=dashscope, temperature=0, generation_kwargs={max_tokens:8192}))` → `score=1.0`。推理模型 `reasoning_content` 未破坏结构化解析 | 主路径可用 |
| K2 RAGAS 桥 | **PARTIAL**（机制清楚，仅一条路可走） | 见下 | 必须传 langchain `ChatOpenAI`，不能复用 `build_ragas_judge()` |
| K3 pytest 9 兼容 | **WORKS** | `deepeval test run test_poc.py`（pytest 9.0.2）→ `1 passed`；失败用例 `EXIT_CODE=1` | 可作 CI 门禁 |
| K4 posthog 遥测 | **WORKS（可关）** | `DEEPEVAL_TELEMETRY_OPT_OUT=1` → `NoopBackend`，`posthog` 甚至不被 import | 合规可控；但 posthog 仍是硬安装依赖 |
| K5 `.env` 串味 | **WORKS（可阻）** | 默认 `.env`/`.env.local` 会被注入 `os.environ`；`DEEPEVAL_DISABLE_DOTENV=1` → 不注入 | CI/本地必须设 |
| K6 LangGraph 挂件 | **WORKS** | `CallbackHandler` 经 `config={"callbacks":[...]}` 挂上，捕获含 `toolSpans` 的轨迹 → `TaskCompletionMetric score=1.0` | 轨迹指标形态成立 |

## K2 细节（重点）

桥**怎么拿裁判**（源码证据）：
- `deepeval/metrics/ragas.py:72`（及各指标同款）：`if isinstance(self.model, str): chat_model = OpenAIModel(model=self.model).load_model(); else: chat_model = self.model`。
- `deepeval/models/llms/openai_model.py:476` `load_model()` 返回 `self._build_client(OpenAI)` → **裸 `openai.OpenAI` 客户端**（实测 `LOAD_MODEL_TYPE=openai.OpenAI`，非 langchain `BaseChatModel`）。
- 随后 `ragas.evaluate(dataset, metrics=[...], llm=chat_model)`。

实测两条路：
- **A) 字符串模型名**（`RAGASFaithfulnessMetric(model="qwen3.8-flash")`，并设 `OPENAI_API_KEY/OPENAI_BASE_URL` 指向 DashScope）→ ragas 内部 `AttributeError('OpenAI' object has no attribute 'generate')`，异常被 Job 吞掉 → **静默 `score=nan, success=False`**。⇒ 该路不可用。
- **B) 传 langchain `ChatOpenAI`**（指向 DashScope）→ `score=1.0`。⇒ **唯一可用构造**。

**能否复用 `build_ragas_judge()`？不能。** 桥要的是 langchain `BaseChatModel`，而 `build_ragas_judge()` 产出的是 ragas `InstructorBaseRagasLLM`，类型不接。

可照抄构造代码：
```python
from langchain_openai import ChatOpenAI
from deepeval.metrics.ragas import RAGASFaithfulnessMetric  # 聚合类名 RagasMetric

judge_chat = ChatOpenAI(
    model="qwen3.8-flash",
    api_key=DASHSCOPE_API_KEY,                      # 从 .env 注入，勿打印
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0,
    max_tokens=8192,
    extra_body={"enable_thinking": False},          # 对齐项目 thinking_extra_body(dashscope, False)
)
metric = RAGASFaithfulnessMetric(model=judge_chat, threshold=0.3)
```
> 注：ragas `evaluate` 会自行包一层，`ChatOpenAI` 的 `max_tokens`/`extra_body` 是否透传到 ragas 内部调用需下一轮确认（见残余未知）。

## 依赖与版本

- deepeval **4.2.8**（最新）。
- 顶层新拖入：`posthog==7.70.0`（硬依赖，py≥3.10 锁 `>=7,<8`）、pytest 家族 `pytest-xdist==3.8.0`、`pytest-repeat==0.9.4`、`pytest-rerunfailures==16.7`（`pytest==9.0.2`、`pytest-asyncio==1.4.0` 与项目一致）。
- 与项目版本漂移：PoC 解析出 `langchain-core==1.6.10` / `langchain-openai==1.7.2` / `langgraph==1.2.15` / `datasets==5.1.0` / `openai==3.3.0`；项目为 `1.4.8 / 1.3.3 / 1.2.9 / 4.0.0 / 2.53.0`。
- **冲突（`pip check`）**：`deepeval 4.2.8 has requirement click<8.4.0,>=8.0.0, but you have click 8.5.0.` 项目现装 `click==8.4.2` 也 > `<8.4.0` ⇒ 加 deepeval 必与现有 click 冲突。
- `openai` 在 deepeval 的 metadata 里未 pin；需下一轮确认 deepeval 4.2.8 与项目 `openai==2.53.0` 是否兼容。

## 已知拦路虎 / 必须改

1. **ragas 0.4.3 硬导入 `langchain_community.chat_models.vertexai`**，而 langchain-community≥0.4 已移除该子模块 ⇒ 导入即 `ModuleNotFoundError`。**项目已有解**：`src/cli/eval_ragas_generate.py:32 _ensure_vertexai_stub()` 建 stub。PoC 复用后 ragas 正常导入。（注意：DeepEval 入口下同样需要先执行该 stub 逻辑。）
2. **click 版本冲突**（deepeval `<8.4.0` vs 项目 `8.4.2`）——需对齐或隔离依赖。
3. **K2 只能走 langchain `ChatOpenAI`**，无法复用现有 `build_ragas_judge()`，两套裁判构造并存。

## 残余未知（建议下一轮补测）

1. `ChatOpenAI` 的 `max_tokens` / `extra_body(enable_thinking=False)` 是否经 ragas `evaluate` 透传到真实请求（需抓请求体或 A/B 耗时）。
2. DeepEval 默认（非测试模式）轨迹不留在 `trace_manager`（invoke 结束后被 evict），需靠 `trace_testing_manager.test_name`（轨迹测试模式）或 Confident AI 投递才能取 `_trace_dict` —— 生产 CI 形态要确认能否不依赖 Confident AI。
3. deepeval 4.2.8 与项目 `openai==2.53.0` 的兼容性（PoC 用的是 openai 3.3.0）。
4. `RAGASAnswerRelevancyMetric` / `RagasMetric` 聚合（需 embeddings）在 DashScope embedding 下的表现未测。
5. DeepEval 自带 `RAGAS*` 桥版本旧（断言 ragas≥0.2.1，但用 `ragas.metrics` 老式函数 `context_precision`/`faithfulness`），与项目现代 `ragas.metrics.collections` 口径的**分数可比性**未验证。

## 可复算命令序列（不含密钥）

```bash
python3 -m venv /tmp/deepeval-poc/.venv
/tmp/deepeval-poc/.venv/bin/python -m pip install deepeval "pytest==9.0.2"
/tmp/deepeval-poc/.venv/bin/python -m pip install "ragas==0.4.3" langchain-openai langchain-core datasets
# 建 ragas 兼容 stub（同 src/cli/eval_ragas_generate.py::_ensure_vertexai_stub）
/tmp/deepeval-poc/.venv/bin/python /tmp/deepeval-poc/run_k2_stub.py

cd /tmp/deepeval-poc
.venv/bin/python run_k1.py          # K1 -> WORKS score=1.0
.venv/bin/python run_k2.py          # K2 -> A 静默 nan / B WORKS 1.0
.venv/bin/python run_k5.py          # K5 -> 默认注入 / DISABLE=1 不注入
.venv/bin/python run_k6.py          # K6 -> 轨迹捕获 + TaskCompletion 1.0

cd /tmp/deepeval-poc/k3
DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 /tmp/deepeval-poc/.venv/bin/deepeval test run test_poc.py   # 1 passed
DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 /tmp/deepeval-poc/.venv/bin/deepeval test run test_fail.py  # EXIT_CODE=1
```

## 源码证据行

- K4：`deepeval/telemetry/client.py:55 telemetry_opt_out()`、`:119 get_backend()`（opt-out→`NoopBackend`）、`:92 from posthog import Posthog`；`deepeval/config/settings.py:929 DEEPEVAL_TELEMETRY_OPT_OUT`。
- K5：`deepeval/__init__.py:12 autoload_dotenv()`；`deepeval/config/settings.py:268 if parse_bool(os.getenv("DEEPEVAL_DISABLE_DOTENV"), default=False): return`。
- K2：`deepeval/metrics/ragas.py:72`（字符串→`OpenAIModel(model=str).load_model()`）、`deepeval/models/llms/openai_model.py:476 load_model()` 返回裸 `openai.OpenAI`。

---

## Q1 探测（2026-10-16）：`evals_iterator` 能否与 `asyncio.run` + `graph.ainvoke` 共存？

**问题**：本 change 的行为轴要求「`EvaluationDataset.evals_iterator` 驱动 + 循环体内 `graph.ainvoke(...)`」，而项目把空库检查 + 跑图放在**一个** `asyncio.run(...)` 内（引擎连接池不可跨事件循环复用）⇒ 是否撞 nested-loop？

**做法**：`/tmp/deepeval-poc-scripts/q1_probe_evals_iterator_async.py`（**脚本不入库**，与既有 K1–K6 探针同例，仅留本记录），两种走法各 1 题（玩具 async agent + `TaskCompletionMetric`，真调 judge）：
- A：外层 `asyncio.run(main())`，`evals_iterator` **默认**（`AsyncConfig(run_async=True)`），循环体 `await graph.ainvoke(..., config={"callbacks":[CallbackHandler()]})`
- B：同上但 `AsyncConfig(run_async=False)`（纯同步驱动）

**结果（两路均 WORKS）**：

| 走法 | 异常 | 消费 golden | `test_run_*.json` | 逐题分 |
|---|---|---|---|---|
| A（run_async=True，嵌套在 asyncio.run 内） | 无 | 1 | 已落盘 `test_run_20261016_155435.json` | `testCases[0].metricsData[0].score = 1.0` |
| B（run_async=False） | 无 | 1 | 已落盘 `test_run_20261016_155533.json` | 同上 `1.0` |

**机制（源码）**：`deepeval/utils.py:209 get_or_create_event_loop()` —— 当 `loop.is_running()` 为真时 **调 `nest_asyncio.apply()`**（`nest_asyncio` 是 `deepeval` 的硬依赖，METADATA 已列）⇒ 内层 `loop.run_until_complete(...)` 在**同一个**运行中的 loop 上重入执行，**不新建第二个 loop** ⇒ 项目"单事件循环 + 引擎连接池"假设**不被破坏**。`run_async=False` 的分支（`evaluate/execute/loop.py:149-507`）**零 `asyncio` 引用**，是纯同步驱动。

**结论**：spec 里"行为轴 SHALL 由 `evals_iterator` 驱动"**写成 SHALL 是可验收的**（D1 回滚触发条件② 不成立）。

**附带注意（落地时要写进代码注释）**：`nest_asyncio.apply()` 会**全局 monkeypatch 当前 asyncio**（进程级副作用）；本次评测 CLI 是短进程、可接受，但不得在长驻服务里顺手调用。

---

## Hook 冒烟（2026-10-16）：hooks 挂 `http_async_client` 会触发、挂 `http_client` 零触发

**问题**：`deepeval-bridge-eval` 把"框架内部请求级日志"写成 SHALL，但挂载字段一度写成 `http_client`。源码结论是 langchain 的 `http_client` **只喂同步 client**（`chat_models/base.py:806`；异步用 `:1273` 的 `http_async_client`），而 ragas 的裁判调用走异步 ⇒ 挂错字段 hooks 一条都不响。本轮做实测确认（此前只有源码级结论）。

**做法**：`/tmp/deepeval-poc-scripts/q_hook_smoke.py`（**脚本不入库**，与 K1–K6 / Q1 探针同例），两臂各跑一次真调裁判的 `RAGASFaithfulnessMetric.measure()`（judge 经项目 `build_chat("judge", …)` 构造）：

- **A（正确接线）**：`http_async_client=httpx.AsyncClient(timeout=…, event_hooks=(async hooks))`
- **B（错误接线）**：只传 `http_client=httpx.Client(timeout=…, event_hooks=(sync hook))`

**结果**：

| 臂 | judge 上的 client 字段 | hooks 事件数 | 明细 | 指标分 |
|---|---|---|---|---|
| A `http_async_client` | `http_async_client=set` / `http_client=None` | **4** | 2×request + 2×response（faithfulness 内两次调用）；response 均 `status=200`、`json_ok=True`、`n_choices=1`、`finish_reason=['stop']`、body 471 / 754 字节 | 1.0 |
| B 仅 `http_client` | `http_client=set` / `http_async_client=None` | **0** | 无任何事件 | 1.0（分数照出，只是**零可观测性**） |

**结论**：① 挂 `http_async_client` 时 hook **确实触发**，且能 `await response.aread()` + `json.loads` 拿到 `finish_reason`（失败留证与"429 识别"的观测前提成立）；② 挂 `http_client` 时 **0 触发**——错误接线**不会报错、只是静默失去全部可观测性与细粒度超时**（这正是它一路没被发现的原因）。⇒ 已把"响应 hook 触发数 > 0"与"只挂同步字段时 == 0"两条**互为反向的冒烟断言**写进 tasks 3.3。

**顺带实测**：`build_chat("judge", thinking=…, max_tokens=…, max_retries=0, http_async_client=…)` 经 provider 层透传后，judge 实例上 `http_async_client` 已置、`http_client` 为 `None`（即字段确实按名路由）。

---

## Phase 1 收口冒烟重跑（2026-10-16）：hook 断言 PASS，但 `faithfulness` 被证实为**非确定**

**背景**：Phase 1 的 T12 曾跑过一次真实链路冒烟，记录 `hook_events=13`、四指标有值，但 **`faithfulness=0.0`**（toy 题：答案「约 6603 亿元」对 context「…营业收入 6603 亿元。」），当时被挂账为"待证伪"，终审的假设是"判官严格"。T12 的原始报告文件随 SDD 工作区清理一并删除，故本轮重跑以恢复可复验证据。

**做法**：`/tmp/deepeval-poc-scripts/phase1_smoke.py`（脚本不入库）。真调裁判（`build_chat("judge", …)` + `thinking=False`），两题各自 `HookEvidence` 与 judge 同源：

- **A 原 toy 题**：答案 `约 6603 亿元`，context `腾讯控股 2024 年报：营业收入 6603 亿元。`
- **B 逐字答案探针**：答案 `腾讯控股 2024 年报显示，营业收入 6603 亿元。`（逐字抄自 context），同一 context

**结果（1 次）**：

| 题 | faithfulness | answer_relevancy | context_precision | context_recall | hook_events | 耗时 |
|---|---|---|---|---|---|---|
| A 原 toy | **1.0** | 0.444 | 1.0 | 1.0 | 13 | 37.4s |
| B 逐字答案 | **0.0** | 0.893 | 1.0 | 1.0 | 13 | 35.7s |

**Hook 断言的 A 向成立**：两题各 13 事件、`status_codes` 全 200、`abnormal=False` ⇒ tasks 3.3a 的 `hook_events > 0` **PASS**（合计 26）。

**但 A 题的 `faithfulness` 与 T12 记录相反（0.0 → 1.0）**，而 B 题（**更该得高分**的逐字抄写）反而 0.0。这不是"判官严格"，是**同一输入不同结果**。故追加重复性探针：

**做法**：`/tmp/deepeval-poc-scripts/faithfulness_repeat.py` —— **同一进程、同一 judge、同一题**连跑 4 次（每题新建指标实例，与 `score_rows` 内一致）。

**结果**：

```
[0] faithfulness=0.0   answer_relevancy=0.4628  ... 41.0s
[1] faithfulness=1.0   answer_relevancy=0.4590  ... 38.5s   （[2]/[3] 见下）
faithfulness 序列: [0.0, 1.0, 0.0, 1.0]
```

**结论**：

1. **`faithfulness` 在本判官下是二值抖动**（`[0.0, 1.0, 0.0, 1.0]`），输入完全一致、judge 完全一致、串行执行 ⇒ **不是并发/状态串用，也不是"判官严格"，而是调用级非确定性**。（注意 `answer_relevancy` 反而稳定在 0.44–0.47，尽管它的温度档更高（0.3 vs 0.01）—— 这把"温度分档"排除为主因，更像 faithfulness 内部的两段式 LLM 流程（抽陈述 → NLI 判定）某一段偶发翻转或解析回退。）
2. 挂账假设（"判官严格 / 未排除 `actual_output` 接线"）**不成立**：`actual_output` 接线正常（B 题答案更长更逐字，仍 0.0；且 context 侧指标稳定 1.0）。
3. **对 change 的影响**：`faithfulness` 进入主分，而主分/门禁/跨轮台账比对都假定同一输入可复现。若该指标二值抖动，**跨轮比对与阈值重标定（组 10）会失真** —— 这必须在 Phase 2/4 处置，不能当噪声忽略。

**探针自身的坑（供后人）**：`faithfulness_repeat.py` 里 judge 建在一个**随手丢弃的** `HookEvidence()` 上，逐题又传另一个 `ev` ⇒ 该探针打印的 `hooks=0` 是**构造错误**，不是产品缺陷。真实路径 `src/cli/eval_ragas.py:840-841/894` 里 `judge_evidence` 是**同一个对象**同时喂给 judge 与 `run_evaluation`，故 hooks 正常。但这暴露一个**静默失效耦合**：`score_rows(..., evidence=None)` 缺省即跳过 429 判定（`:418` 的 `evidence is not None` 守卫），且传了非 judge 所用的桶也照样静默零观测 —— 建议后续加一条断言（judge 的桶 is 传入的桶）。

**脚本**：`/tmp/deepeval-poc-scripts/{phase1_smoke.py,faithfulness_repeat.py}`（按本仓先例**不入库**，否则 ruff/pyright 会拦）。

---

## faithfulness 非确定性根因（2026-10-16）：语言错配 ⇒ 被迫做亿↔billion 换算

**接上一节**：`faithfulness` 实测二值抖动（`[0.0,1.0,0.0,1.0]`）。本节定位到机制，并验证两种修法。

### 定位手段

`/tmp/deepeval-poc-scripts/faithfulness_rootcause.py`：monkeypatch `ragas.metrics._faithfulness.Faithfulness._create_statements` / `_create_verdicts`（类级），dump 两段中间产物。同一进程、同一 judge、同一题连跑 6 次。

**6 次抽出的陈述（全英文）**：
```
"Tencent's 2024 operating revenue was approximately 660.3 billion yuan."
```
**6 次 NLI 判定**（分数 `[1,1,1,1,1,0]`）—— 关键在判官给出的 `reason`：

| iter | verdict | reason 摘要 |
|---|---|---|
| 0 | 1 | "1 billion is equal to 10 hundred million (10亿), 6603 hundred million equals 660.3 billion" |
| 1 | 1 | "6603 billion yuan is equivalent to 660.3 * 10 = 6603 billion? **No, let's re-calculate carefully.**… 660.3 billion" |
| 2 | 1 | "1 billion is equal to 100 million, and 1 亿 is 100 million, 6603 亿 equals 660.3 billion"（前提写错、结论对） |
| 3 | 1 | "There is a discrepancy in the magnitude… **Wait, let me re-read carefully.**… This matches exactly." |
| 4 | 1 | "6603 * 100,000,000 = 660,300,000,000 … 660.3 billion" |
| 5 | **0** | "**a discrepancy by a factor of 10 (6603 vs 660.3)** … cannot be directly inferred" ← 把 `6603 亿元` 当成 `6603`（丢掉"亿"） |

**机制**：陈述生成把中文答案**译成英文**并做了单位改写（`6603 亿元` → `660.3 billion yuan`）；NLI 段因此**必须做亿↔billion 换算**才能判定。`enable_thinking=False` 下该换算不稳（理由里能看到明显的自我纠错与偶发算错）⇒ verdict 在 0/1 之间跳。**1 句答案 ⇒ faithfulness 粒度就是 1/1 ⇒ 直接二值抖动。**

排除项（均已核）：解析回退不是原因（解析失败会 `raise RagasOutputParserException` → 被 executor 吞成 `NaN`，而实测是 0.0/1.0 不是 NaN）；温度分档不是主因（`answer_relevancy` 温度档更高却稳定）；并发/状态串用不是（同一进程串行）。

### 修法对照（`/tmp/deepeval-poc-scripts/faithfulness_fix_arms.py`，各臂 N=6）

| 臂 | 设置 | scores | distinct | 抽出的陈述 |
|---|---|---|---|---|
| **A** 基线 | thinking 关 + ragas 英文默认提示词 | `[1,1,1,1,0,1]` | `{0,1}` 抖动 | 英文 `…660.3 billion yuan` |
| **B** 开思考 | `RAGAS_JUDGE_THINKING=True`（提示词不变） | `[1,1,1,1,1,1]` | `{1.0}` 稳定 | **中文** `腾讯2024年营业收入约为6603亿元人民币` |
| **C** 中文提示词 | thinking 关 + 换 `statement_generator_prompt`/`nli_statements_prompt` 为**强制中文、禁翻译禁单位换算** | `[1,1,1,1,1,1]` | `{1.0}` 稳定 | **中文** `腾讯2024营业收入约6603亿元` |

**两个反直觉点**：① **B 臂用原英文提示词却吐出中文陈述** ⇒ `thinking=False` 时判官被英文提示词带跑做翻译，开思考才跟随答案语言；② 两臂的稳定**共同机制都是"语言对齐"**（中文陈述 ⇒ 无需单位换算），而非"思考更强"。

**结论**：根因 = **判官把中文答案译成英文陈述，迫使 NLI 段做亿↔billion 换算，非思考模式下该换算偶发失败**。修法至少两条可行：语言对齐（C，需替换 ragas 提示词）或开思考（B，一行 env，且附带修掉推理能力）。

**影响面（须评估）**：① 真实 21 题是中文语料 ⇒ `faithfulness` 系统性偏低且不可复现，直接影响主分、门禁与**跨轮台账比对**（组 10 阈值重标定会失真）；② **疑似同族问题**：`answer_relevancy` 在"问题对答案明显相关"的 toy 题上只有 **0.44**——该指标也是先生成问题再比相似度，若生成的是英文问题而原问题是中文，相似度会被系统性压低。**未验证，建议同法探针确认。**

**N 的局限**：每臂 N=6，样本小；"稳定"是 6/6，不足以排除低频抖动。若据此改设计，建议先跑 N≥20。

**脚本**：`/tmp/deepeval-poc-scripts/{faithfulness_rootcause.py,faithfulness_fix_arms.py}`（不入库）。

---

## answer_relevancy 0.44 根因（2026-10-16）：答案不自包含 ⇒ 生成器猜错主体（**玩具题假象**）

**背景**：冒烟里 `answer_relevancy` 在"问题对答案明显相关"的 toy 题上只有 0.44，疑与 faithfulness 同为语言错配。

**链路（ragas 0.3.9 `ResponseRelevancy`）**：答案 →（LLM，`strictness=3`）生成 3 个问题 → 与**原问题**算余弦相似度 → 均值 ×(1−全 noncommittal)。**注意：问题生成器只看答案，看不到 context。**

### 探针 `answer_relevancy_rootcause.py`（三臂，各 N=6）

| 臂 | scores | 生成的问题样例 |
|---|---|---|
| **A** 基线（thinking 关 + 英文提示词） | `[0.4404,0.4188,0.4511,0.4313,0.4173,0.4490]`（**稳，非抖动**） | `中国的GDP是多少？` / `What is the approximate amount in billions of yuan?` / `某公司的市值是多少？` |
| **B** 开思考（`thinking=True`） | **空 —— 6 次全 HTTP 400** | — |
| **C** 中文问题生成提示词（thinking 关） | `[0.4526,0.4767,0.4573,0.4549,0.4496,0.4460]`（≈0.45，几乎没变） | `2023年中国国内生产总值（GDP）大约是多少？`（**主体仍猜错**） |

**B 臂的报错（关键约束）**：
```
BadRequestError(400): InternalError.Algo.InvalidParameter:
The n parameter must be 1 when enable_thinking is true
```
`answer_relevancy` 请求 `n=strictness=3` ⇒ **DashScope 在 `enable_thinking=true` 时拒绝 n>1** ⇒ 该指标在开思考下直接不可用（错误被 ragas 吞成 NaN）。

### 对照：残句 vs 自包含答案（`answer_relevancy_selfcontained.py`，同一问题/context，thinking 关）

| 答案 | scores | 生成的问题 |
|---|---|---|
| **F 残句**「约 6603 亿元」（**无主体**） | `0.42–0.44` | `中国的GDP是多少？` / `某公司的市值是多少？` |
| **S 自包含**「腾讯控股 2024 年营业收入约为 6603 亿元。」 | **`[0.9656,0.8769,0.9164,0.9640,0.9655,0.9190]`** | `腾讯控股 2024 年的营业收入是多少？` |

**结论**：0.44 **不是**语言问题（C 臂只升到 0.45；英文生成问题也能拿 0.82–0.96，说明该 embedding 跨语言相似度足够），**也不是**判官问题，而是**答案不自包含**：生成器只看答案，残句没有主体 ⇒ 凭空猜主体（猜成"中国 GDP"）⇒ 与原问题相似度低。**这是 ragas 按设计惩罚"不完整答案"**（其 docstring：*"Answers with incomplete, redundant or unnecessary information is penalized"*）。**⇒ 0.44 是玩具题假象，管线无需为此改动。**

**但两条对 change 有效的发现**：

1. **`enable_thinking=True` 与 `answer_relevancy` 不兼容**（DashScope 下 `n>1` 被拒）。故上一节 faithfulness 修法 **B（开思考）不能全局采用** —— 单一 judge 跨指标共享，一开思考 `answer_relevancy` 即失效。⇒ **faithfulness 应选 C（语言对齐，thinking 保持关）**；若确要用 B，须给 `answer_relevancy` 单独配 thinking 关的 judge，或把 `strictness` 降到 1（改口径），两者都更重。
2. **口径提示（供组 10 阈值重标定）**：`answer_relevancy` 实质在测"答案能否自洽地反推问题"。若 agent 真实答案偏残句/多跳拼接，该指标会系统性偏低——**这是指标语义，不是故障**；重标定阈值时须按此理解取值。

**局限**：仍基于单题（1 个 toy 题）；`answer_relevancy` 在真实 21 题上的分布未测。若要据此改设计，建议在真实集上抽样复核。

**脚本**：`/tmp/deepeval-poc-scripts/{answer_relevancy_rootcause.py,answer_relevancy_selfcontained.py}`（不入库）。

**影响范围（已核源码）**：四项里**只有 `answer_relevancy` 会请求 `n>1`**（`strictness=3`，`_answer_relevance.py:142`）。`faithfulness`、`context_precision`、`context_recall` 的 `generate_multiple()`/`generate()` **均不传 `n`（默认 1）**（`_faithfulness.py:158/174`、`_context_precision.py:151`、`_context_recall.py:137`）⇒ **`enable_thinking=True` 恰好只打断 `answer_relevancy` 一个指标**。若将来确要开思考，最小代价路径是给该指标单独配 thinking 关的 judge（或把 `strictness` 降到 1，属改口径）。

---

## faithfulness 修法 C 的 N≥20 复核（2026-10-16）：**不通过** —— 暴露出第二个病因

**做法**：`/tmp/deepeval-poc-scripts/faithfulness_n20.py` —— **2 案例 × N=10 × 2 臂**（A 基线英文提示词 / C 中文提示词），共 40 次调用。第二个案例用于检验 T 案例结论能否泛化。

| 案例 | A 基线（英文提示词） | C 中文提示词 |
|---|---|---|
| **T 腾讯营收**（答案 `约 6603 亿元`，context `…营业收入 6603 亿元。`） | 判 0 **1/10** | 判 0 **0/10** ✅ |
| **M 茅台净利润**（答案 `约 857 亿元`，context `…归属于上市公司股东的净利润约 857 亿元。`） | 判 0 **10/10**（不是抖动，**确定性判错**） | 判 0 **6/10** ❌ |

**结论**：**修法 C 只治了"单位换算"那一类，治不了 M 那一类** ⇒ 不通过复核。

### M 案例的中间产物（`faithfulness_moutai_dump.py`，A/C 各 N=6）

**A 臂（英文提示词）—— 6/6 稳定判 0，病因是"NLI 段读错 context 的数值"**：
```
抽出陈述: "The net profit of Kweichow Moutai in 2024 is approximately 85.7 billion yuan."   ← 这句本身是对的（857 亿 = 85.7 billion）
NLI 理由: "The context states the net profit ... is approximately 857 billion yuan (857 亿元).
          The statement claims it is 85.7 billion yuan, which is a numerical discrepancy
          (off by a factor of 10)."                                                        ← 判官把 context 的「857 亿元」读成「857 billion」
```
⇒ 抽陈述那步正确，**是 NLI 段把中文 context 的数值读错**（丢了"亿"），再拿它去比 ⇒ 稳定判 0。

**C 臂（中文提示词）—— 陈述已正确，但暴露第二个病因：语义严格性摇摆**：
```
抽出陈述: "贵州茅台2024年净利润约为857亿元。"   ← 单位问题已消失
NLI 理由: "上下文指出'归属于上市公司股东的净利润'，而陈述仅称'净利润'…
          前者是后者的子集…然而更常见的判断标准是看核心事实是否被支持…"
```
⇒ 判官在 **"归母净利润" vs "净利润" 的限定词省略**上反复自我辩解，3/6 判 1、3/6 判 0。

### 补充：D 臂（`thinking=True` + 英文默认提示词）实测**两类都治好**

`faithfulness_moutai_thinking.py`（M 案例，N=6）：scores = **[1.0]*6**；且抽出的陈述自动为**中文**（`贵州茅台2024年净利润约为857亿元。`），理由不再纠结单位或限定词。结合先前 T 案例 B 臂 `[1]*6` ⇒ **D 在两类案例上共 12/12 稳定**。

### 修正后的综合结论

1. **`faithfulness` 在本判官下有至少两个独立病因**，且**都与"答案是对 context 的缩写/改写"有关**：① 中文数值的单位换算（T）；② 限定词省略 + NLI 读错 context 数值（M）。**换成中文提示词只能治 ①**。
2. **`thinking=True` 两类都治**（判官推理能力足够时，单位换算与限定词判断都稳），但**被 `answer_relevancy` 的 `n>1` 硬约束阻挡**（见上一节）。
3. **另一个统一视角**：M 的失败与 `answer_relevancy` 的 0.44 同源——**答案不是 context 的忠实长句而是缩写**。在真实 21 题上，若 agent 答案偏长/完整，严重度可能低于这两个玩具案例；**但任何"数值 + 限定词"型事实都会命中**。⇒ 需在真实集上抽样确认严重度。

**脚本**：`/tmp/deepeval-poc-scripts/{faithfulness_n20.py,faithfulness_moutai_dump.py,faithfulness_moutai_thinking.py}`（不入库）。

---

## Phase 2 前置 spike（2026-10-17）：行为轴链路打通 + 抓到"同步/异步 client"第二次现形

**目的**：写 Phase 2 计划前，把「自建 `DeepEvalBaseLLM` 适配器 + 三个 DeepEval 原生行为指标 + `evals_iterator` + `graph.ainvoke` + `CallbackHandler`」这条链在**本项目判官**上跑通并钉死形态。

**关键源码事实（决定适配器形状）**：`deepeval.metrics.utils.models.initialize_model` **只接受 `str | DeepEvalBaseLLM`**（`metrics/utils/models.py:170`）⇒ **langchain 模型不被接受**；且 `DeepEvalBaseLLM.generate_with_schema` 在非 native 模型上会**退回 `generate(prompt)`**、由 DeepEval 自己 `trimAndLoadJson`（`models/base_model.py:151`）⇒ **适配器只需实现 `load_model` / `generate` / `a_generate` / `get_model_name`，返回 JSON 文本**。

**做法**：`/tmp/deepeval-poc-scripts/phase2_behaviour_spike.py`（另见 `_async.py` 变体）。适配器 `BridgeEvalLLM(DeepEvalBaseLLM)` 包住 `build_bridge_judge()` 的裁判；toy 图（1 个工具）跑 1 题真链路；三指标 = `TaskCompletionMetric`（**`task=None`，逐题取**）/ `StepEfficiencyMetric` / `AgentLoopDetectionMetric`（**无 model 参数、纯 trace**）。

**结果（同步变体：`async_mode=False` + `AsyncConfig(run_async=False)`）**：

| 项 | 结果 |
|---|---|
| generator 耗尽 / JSON 落盘 | ✅ `consumed_goldens=1`、`test_run_*.json` 已写 |
| 三指标出分 | ✅ Task Completion 1.0 / Step Efficiency 1.0 / Agent Loop Detection 1.0（11s） |
| 逐题取任务（`task=None`） | ✅ 可用（`task_completion.py:121-122` 从 test case 取） |
| **`hook_events`** | ❌ **0**（`status_codes=[]`） |

**异步变体（指标默认 `async_mode=True` + `AsyncConfig(run_async=True)`）**：

| 项 | 结果 |
|---|---|
| 三指标出分 | ✅ 全 1.0（10s） |
| **`hook_events`** | ✅ **8**，`status_codes=[200,200,200,200]` |

**结论（三条，写进 Phase 2 计划）**：

1. **适配器形状已定**：`DeepEvalBaseLLM` 子类，`generate`/`a_generate` 调 provider 层裁判的 `invoke`/`ainvoke` 并返回**内容文本（JSON）**；`agent_loop_detection` 不需要 model。
2. **⚠️ 行为轴必须走异步路径**：同步模式下 DeepEval 调 `generate()` → `invoke()` → langchain **同步 client**，而 `build_bridge_judge` **只挂了 `http_async_client`** ⇒ **hooks 零触发、细粒度超时也失效**（与 Phase 1 那个 Blocker 同型，**第二次现形**）。修法 = 用默认的异步配置（`run_async=True` + `async_mode=True`），**不需要再加同步 client**。**防护**：行为轴跑完 SHALL 断言 `hook_events > 0`（与 tasks 3.3a 同理的反向冒烟）。
3. **`task=None` 可逐题取任务** ⇒ 三个指标**共用一个实例**即可，无需每题新建（`CallbackHandler` 仍需每题新建）。

**附带**：会打印 `⚠ WARNING: No hyperparameters logged`（无害；传 `identifier`/`hyperparameters` 可消）。另 `AsyncConfig(run_async=True)` 在 `asyncio.run` 内嵌套可用 —— 与 Q1 探测一致（`nest_asyncio.apply()`）。

**脚本**：`/tmp/deepeval-poc-scripts/phase2_behaviour_spike{,_async}.py`（不入库）。
