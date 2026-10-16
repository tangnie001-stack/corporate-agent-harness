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
