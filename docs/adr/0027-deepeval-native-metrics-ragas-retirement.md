# ADR-0027：评测主分改用 DeepEval 原生四指标，RAGAS 全链路退役

- **Status**：Accepted
- **Date**：2026-10-17
- **Deciders**：用户（拍板脱钩、改名与诊断指标范围）；Claude（spike、源码核对、实现）
- **Supersedes**：ADR-0026（完全）、ADR-0022（完全）、ADR-0023（完全）
- **关系**：落实 change `deepeval-native-metrics`；旧 ADR 中仍生效的约束在「继续生效的约束」重述。

## 背景与问题

评测主分原先由 `deepeval.metrics.ragas` 桥驱动 ragas 0.3.9 的经典四指标。
桥带来三处不可控：温度按 `n` 被改写、框架层 429 重试不可配、失败被吞成 NaN。
DeepEval 4.2.9 已原生提供输入同构的四指标，且不需要 embeddings；继续保留桥会
让裁判策略、依赖与命名长期分叉。

## 决策

### 决策 1：四指标改用 DeepEval 原生实现

`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` /
`ContextualRecallMetric`，全部显式 `model=BridgeDeepEvalLLM(judge)`；`build_metrics`
删除 `embeddings` 参数。评分编排仍为题级串行。

**理由**：原生指标输入契约与桥一致；删掉 ragas 后少一层不可控包装。
**代价**：分数与旧口径不可比，必须重跑 baseline。

### 决策 2：裁判温度显式 0，SDK 重试固定 0

`build_bridge_judge` 显式 `temperature=0`、`max_retries=0`，并剥离 `LLM_KWARGS`
中的 `top_p`。

**理由**：删 ragas 后不再有按 `n` 分档；不设温度会落到 provider 默认值，
裁判 JSON 判定不可复现。SDK 重试可配会与外层有界重试叠乘。
**代价**：旧桥的 0.01/0.3 分数不可复用。

### 决策 3：诊断指标不参与主分

新增 `ContextualRelevancy`（仅判前 8 条 context）与 `NonAdvice(["financial"])`；
`ToolCorrectness` 等 v6 标注 `expected_tools` 后再启用。诊断值写入
`detail_json`，NaN 单独统计，不进入 `EVAL_METRIC_COLUMNS` 与门禁。

**理由**：诊断指标回答"检索是否贴题 / 是否越界给建议"，与主分的"答案质量"
不是同一问题；混入主分会改变口径。
**代价**：Relevancy 截断后不是全量语义，报告必须标注。

### 决策 4：彻底退役 RAGAS

删除 `ragas==0.3.9`、桥 import、出题模块与 `/api/ragas/generate` 的实现；
路由保留并返回 410。测试集加载器迁入 `src/cli/testset_io.py`。

**理由**：项目不再在任何运行路径使用 ragas；保留依赖与出题代码会误导维护者。
**代价**：出题能力暂时空缺，由后续 change `deepeval-synthesizer-testset` 承接。

### 决策 5：命名与路径全量切换

`eval_ragas.py` → `eval_deepeval.py`；`data/ragas/` → `data/deepeval/`；
`RAGAS_*` → `JUDGE_*` / `EVAL_*`；台账改 `eval-ledger.md`；Langfuse session
默认 `deepeval_eval_session`；score 后缀 `@deep-eval-native`。

**理由**：用户要求"项目里不再有 RAGAS"；改名与语义切换分开提交，便于回滚。
**代价**：部署侧 `.env` 必须一次性切换；旧 env 启动期 fail-fast。

### 决策 6：baseline 不判门禁

新口径首轮以 `--run-type baseline` 落库，`kb_eval` 返回 `passed=None`，
前端显示"未标定"；v6 阈值标定前所有全量运行都用 baseline。

**理由**：换尺子后 0.70 阈值未标定，判红/绿会误导。
**代价**：`eval_report.passed` 仍写 False，靠 `run_type` 约定区分。

## 继续生效的约束（来自被取代 ADR）

- 细粒度 HTTP 超时落在 `http_async_client`；hooks 必须 `async def` 且
  `await response.aread()`；`max_tokens` / `thinking` 经 provider 层透传；
  `max_retries=0`（ADR-0026）。
- 评估检索镜像生产、搜整库、不按白名单收窄（ADR-0023 的核心决策）；
  出题白名单随出题路删除，但评估侧不设白名单继续有效。

## 证据

- spike：2026-10-17，2 题真裁判 4m56s；Q0 全 1.0，Q14 0.33/0.54/0.20。
- 源码：`deepeval/metrics/utils/__init__.py::score_qag_verdicts` 的
  `empty_score=1`；`ContextualRelevancyMetric.a_measure` 的 per-context gather。
- 代码：`src/cli/eval_scoring.py`、`src/cli/eval_deepeval.py`、
  `src/infra/llm/provider/factory.py`。

## 代价与复查条件

- 空 verdict 返回 1.0/0 而非 NaN，主分可能偏乐观；v6 全量分布产出后复核。
- Relevancy 仅判前 8 条，不是全量语义。
- 若原生指标在大规模运行中出现不可接受的 NaN 率，按 spec 的逃生门流程评估替代实现。
