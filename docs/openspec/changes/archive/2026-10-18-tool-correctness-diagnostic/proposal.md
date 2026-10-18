## Why

当前 v6 题集已经让每条样本带上 `expected_tools=["retrieve_kb"]`（22/22），但评测侧仍把 `tool_correctness` 硬编码为 `null`——计划中的诊断指标没有落地。`["retrieve_kb"]` 只是这一版题集的取值，不是契约；评测侧必须对任意工具名、任意数量的 `expected_tools` 成立。现实结果是：

- 只能知道**整轮**检索覆盖率（`retrieval_coverage`），不知道**哪一题**该调用的工具没调用；
- 无法区分"工具调了但结果没命中"与"压根没调工具"（前者是检索质量问题，后者是工具选择问题）；
- v6 基线 22 题全部调用了 `retrieve_kb`，这个指标同时可以当"Agent 不再走预期工具路径"的回归哨兵。

## What Changes

- **评测消费 `expected_tools`**：逐题产出 `tool_correctness`（0.0–1.0），实际工具取顶层消息轨迹的 `tool_calls[].name`。实现不得假设工具名是 `retrieve_kb`、不得假设标签只有一项。
- **语义**：`tool_correctness` = 命中的期望工具数 / 去重后的期望工具数；每个期望工具至少被调用一次即命中，重复调用与额外工具不扣分；不比较参数、不比较顺序。
- **空样本照常评分**：`sample_class=empty` 且未调用期望工具记 0——这正是本指标要抓的"没检索直接作答"；跑图失败才记 N/A。
- **失败样本可观测**：跑图失败（异常、`[ERROR]`、空回答/迭代触顶）不再让整轮评测 abort；行为轴对该题记 N/A，`tool_correctness` 记 `null`，报告仍保留 N 行。空回答 SHALL 视为失败，避免 `expected_tools=[]` 时把失败样本记成 1.0。
- **委派不设特例**：只看顶层调用名，`delegate_task` 与其它工具同等对待；期望工具没在顶层出现就是 0，reason 可注明存在委派。子代理内部轨迹的盲区写进文档，不转成 N/A。
- **标签三态与校验**：字段缺失/`None` → N/A；显式 `[]` → 期望不调用任何工具；非空列表 → 按比例评分。非空标签必须为 `list[str]` 且元素非空；重复项在评分前按首次出现去重并记 warning。
- **存储与报告**：`detail_json` 写 `tool_correctness` 与未完全命中题的原因；CSV/MD 增加均值、完全命中题数与违规题清单；原因只进 detail，不进 CSV；v5 无标签记 N/A。
- **`--deepeval-only` 例外**：该模式不产出 `tool_correctness`，报告 SHALL 明确标注"未跑"，SHALL NOT 无声缺失。
- **不进主分/门禁/Langfuse**：与现有诊断指标同级，只存只读。
- 指标为 DeepEval 原生 `ToolCorrectnessMetric`，**不调裁判、无额外 token 成本**；必须 `async_mode=False`、`eval_mode="llm"`（默认异步模式下 `measure()` 返回 `None`，上游缺陷；外部 `DEEPEVAL_EVAL_MODE` 可能把构造路由到 TypeSafe/Jev）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `evaluation-pipeline`：新增"有标签时逐题计算 tool correctness"的诊断要求，明确标签三态、空样本/失败/无标签的 N/A 语义、0.0–1.0 值域、存储与报告口径，并保证不进主分与门禁；同时改写旧条款：
  - `Diagnostic metrics do not affect the main score`（空样本掩码范围、0.0–1.0、reason）；
  - `Metric failures are explicit, not silently dropped`（empty 不再"跳过全部诊断"）；
  - `Evaluation consumes v6 testsets without changing the scoring contract`（`expected_tools` 只供工具正确性，主分与裁判诊断仍不读取）；
  - `Agent behaviour is evaluated with DeepEval native metrics`（失败轨迹放行、行为分 N/A，不再整轮 abort）；
  - `Agent trajectory captured as diagnostic metadata`（从行为指标示例中移除 `ToolCorrectness`，明确其为同步诊断）。
- `testset-generation-deepeval`：`expected_tools` 从"只产出、评测不消费"改为"由评测消费；无该字段的旧题集记 N/A"。

> `deepeval-native-metrics` 与 `deepeval-synthesizer-testset` 已于 2026-10-18 归档，主规格已就位；本 change 的 MODIFIED delta 以归档后的主规格为基线。

## Impact

- **代码**：`src/cli/testset_io.py`（加载与标签校验）、`src/cli/eval_deepeval.py`（失败判定、数据流、评分、detail、报告）、`src/cli/eval_scoring.py`（诊断指标构造、行为轴断言放行）、`src/config/const.py`（如需新增列名/豁免常量）。
- **测试**：`tests/cli/` 下加载、limit、评分、诊断、detail、报告、行为轴 fail-loud 相关用例；现有断言"`tool_correctness is None`"需按新口径更新。
- **数据**：`eval_report.detail_json` 新增 `tool_correctness`（float/null）与 `tool_correctness_reason`；CSV 只写 `tool_correctness`，不新增 reason 列；`kb_eval` API 不返回。
- **行为轴**：失败轨迹不再触发整轮 fail-loud，行为分记 N/A；这是本 change 为兑现"失败样本 = N/A"必须一并修改的边界。
- **文档**：`src/cli/README.md` 的 CSV 列说明与内部流程、`docs/agents/eval-ledger.md` 的口径行、`docs/agents/glossary.md` 的术语登记。
- **依赖/API**：无新增依赖，无 API 变更。
