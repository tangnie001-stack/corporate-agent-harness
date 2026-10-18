## Context

当前 v6 题集（`testset_<kb>_v6.json`，22 题）的每条样本带 `expected_tools=["retrieve_kb"]`；评测侧当前把 `tool_correctness` 硬编码为 `null`（`src/cli/eval_deepeval.py:1626`）。`["retrieve_kb"]` 只是这一版题集的取值，不是契约——评测实现必须对任意工具名、任意数量的标签成立。

现状与约束：

- **标签可用但未接通**：`testset_io.load_testset_samples` 返回完整 sample，评测实际走的 `_load_latest_testset` 只回 `(questions, ground_truth)`（`src/cli/testset_io.py:110`）。
- **实际轨迹已采集**：`summarize_trajectory(messages)` 产出 `{"tool_calls": [{"name", "args"}], "iterations"}`（`src/cli/eval_scoring.py:196`），但只进 CSV/MD，未进 `run_evaluation` 的 rows。
- **现有掩码会吃掉信号**：`sample_class=empty` 会把 `CONTEXT_METRIC_COLUMNS + DIAGNOSTIC_METRIC_COLUMNS` 全部置 NaN（`src/cli/eval_scoring.py:124`），而"没检索直接作答"正是本指标要抓的样本；主规格 `Metric failures are explicit, not silently dropped` 还写着 empty 样本"跳过全部指标（含诊断）"。
- **失败样本当前会整轮 abort**：`eval_deepeval.py:1405-1406` 在评分前调用 `assert_behaviour_scores_complete`；`eval_scoring.py:367-371` 对空轨迹直接抛错。任一题异常 → 无 detail/CSV/DB，规格里的"失败 = N/A"在 CLI 上不可达。
- **v6 基线是常数**：22/22 都调用了 `retrieve_kb`（21 题 1 次、Q20 2 次），全部 `pure_kb`。本指标在 v6 上会是 1.0，价值是回归哨兵与逐题归因，不是区分题间质量。
- **上游语义**（DeepEval 4.2.9 实测）：默认非精确匹配为"每个期望工具至少被调用一次"，重复/额外调用不扣分；多工具时返回匹配比例（期望两个、命中一个 → 0.5）；`async_mode=True`（默认）时 `measure()` 返回 `None`；`available_tools` 会额外触发 LLM 工具选择评分；`evaluation_params` 开启参数比对时，expected 的 `input_parameters=None` 会触发 `AttributeError`（不是 0）；`tools_called=None` / `expected_tools=None` 会抛 `MissingTestCaseParamsError`。
- **归档状态**：`deepeval-native-metrics` 与 `deepeval-synthesizer-testset` 已于 2026-10-18 归档，主规格已包含诊断指标与 `expected_tools` 契约。

## Goals / Non-Goals

**Goals:**

- 对带 `expected_tools` 的题集逐题产出 `tool_correctness` ∈ [0.0, 1.0]，定位"该调用的工具没调用"的题。
- 汇总层同时充当回归哨兵：任何未完全命中的题在报告与台账中可见。
- 空样本按实际轨迹评分（0 或 1），只有跑图失败才 N/A。
- **失败样本可观测**：异常、`[ERROR]`、空回答/迭代触顶都不再让整轮评测 abort；行为分记 N/A，`tool_correctness` 记 `null`，报告保留 N 行。
- 标签三态与工具名无关：缺失 → N/A；`[]` → 期望不调工具；非空 → 按比例评分。
- v5 及更早无标签题集记 N/A，不 fail-loud。
- 零裁判调用、无额外 token 成本；不进主分、门禁、Langfuse。

**Non-Goals:**

- 不判断工具入参、顺序或调用次数（重复/额外不扣分）。
- 不把子代理内部轨迹纳入观测（委派不设 N/A 特例，见 D4）。
- 不为 v5 推断或补写 `expected_tools`。
- 不做工具别名映射、大小写折叠或"未知工具"校验；工具名按精确字符串匹配。
- 不改题集生成契约、不改主分四项、不改门禁。
- 不新增 API 字段（`kb_eval` 仍不返回诊断键）。
- `--deepeval-only` 不产出工具正确性；报告显式标注"未跑"，不扩展现有行为轴模式。

## Decisions

### D1：用 DeepEval 原生 `ToolCorrectnessMetric`，语义按"名称 + 类型"理解

指标语义是"期望工具是否至少各调用一次"，DeepEval 默认非精确匹配正好覆盖，且与既有"用原生指标"的口径一致。手写判定虽只有几行，但会引入第二套工具正确性语义，后续换指标或加参数比对时口径分叉。

实测语义（默认参数，`evaluation_params=[]`）：

| 期望工具 | 实际调用 | 得分 |
|---|---|---|
| `retrieve_kb` | `retrieve_kb` ×1 | 1.0 |
| `retrieve_kb` | `retrieve_kb` ×2 | 1.0 |
| `retrieve_kb` | `retrieve_kb` + `search_web` | 1.0 |
| `retrieve_kb` | 只有 `search_web` | 0.0 |
| `retrieve_kb` | 无调用 | 0.0 |
| `retrieve_kb` | 只有 `delegate_task` | 0.0 |
| `retrieve_kb` + `search_web` | 只调用 `retrieve_kb` | 0.5 |
| `[]` | 无调用 | 1.0 |
| `[]` | 调用任一工具 | 0.0 |

表格里的工具名只是示例；实现不得 import `EVAL_SYNTH_EXPECTED_TOOLS`，也不得对 `retrieve_kb` 做任何分支。两侧 `ToolCall` 的 `type` 都使用默认 `FUNCTION`，报告口径写"只比较工具名与类型"。

### D2：构造参数固定为 `model=judge, async_mode=False, eval_mode="llm"`

- `async_mode=False`：实测默认 `async_mode=True` 时 `measure()` 返回 `None`（`self.score` 已赋值但不返回），若走现有 `_score_metric_with_retry` 会被当 NaN。**这是上游行为，不是本仓 bug。**
- `eval_mode="llm"`：外部 `DEEPEVAL_EVAL_MODE=hybrid/system_one` 会让构造走 TypeSafe/Jev 路径，可能在构造期失败或引入非预期模型；固定为 `llm` 才符合"不调裁判、零成本"。
- `model=judge`：指标不调裁判，但仍显式传 `BridgeDeepEvalLLM`，避免初始化 DeepEval 默认模型（`gpt-3.5-turbo` 已停服）。
- **不传 `available_tools`**：否则会额外调用裁判做工具选择评分，成本与语义都超出本 change。
- `evaluation_params` 保持空、`should_exact_match=False`、`should_consider_ordering=False`：标签只有工具名，没有参数；精确匹配会把合法的重复检索判 0。
- 评分失败不重试：指标是确定性的，失败即为环境/构造问题，重试无意义。

### D3：标签三态、形状校验与失败判定

`expected_tools` 有三种状态，不能用 `or []` 抹平：

- 字段缺失 / `None` → 无标签，记 N/A；
- 字段存在且为 `[]` → 期望"不调用任何工具"，按 D1 语义评分（无调用 1.0，有调用 0.0）；
- 非空列表 → 按比例评分。

加载时校验形状：`expected_tools` 必须是 `None` 或 `list[str]`，元素必须是非空字符串；否则抛带 `q_index` 的 `ValueError`（fail-loud，不静默错分）。去重放在评分转换函数里（幂等），按首次出现保留并记 warning；这样 `run_evaluation` 的直接调用方也不会绕过。

失败判定统一为 `_is_failed_run(answer, trajectory)`：

- `trajectory.get("failed") is True`；
- `answer` 以 `[ERROR]` 开头；
- `answer` 为空或纯空白（覆盖迭代触顶返回空内容的已知故障）。

失败样本不调用指标，`tool_correctness=null`，`tool_correctness_reason="failed"`。空回答若被产品视为合法输出，必须另行修改这条约定；本 change 明确按失败处理，避免 `expected_tools=[]` 时把失败样本记成 1.0。

### D4：委派不设特例，只比较顶层调用名

实际调用 = 该题顶层消息轨迹的 `tool_calls[].name`。`delegate_task` 与其它工具同等对待：

- 期望工具出现在顶层 → 命中；
- 期望工具没出现在顶层 → 0，即使实际调用了 `delegate_task`；
- reason 注明"存在 delegate_task，但期望工具未在顶层出现"，帮助人判断是否需要展开子代理轨迹。

不把"委派里可能检索了"转成 N/A：那会让指标依赖"委派一定检索"的假设，也会让"所有题都委派"时哨兵永久失明。子代理内部轨迹不可观测是已知盲区，写进文档；要真正观测需另立 change 扩展轨迹采集。

### D5：数据流沿现有链路扩展，参数必填

```
v6 JSON ──expected_tools──► _load_latest_testset ──► _limit_testset ──┐
                                                                       ├─► run_evaluation rows
messages ──summarize_trajectory──► trajectories ───────────────────────┘        │
                                                                                ▼
                                                                          score_rows
                                                                                │
                                  detail_json / CSV / MD ◄──────────────────────┘
```

- `_load_latest_testset` 返回 `(questions, ground_truth, expected_tools)`；labels 保留 `None`（缺失）与 `[]`（显式空）的区别；`_limit_testset` 同步截取三段，防错位。
- `run_evaluation` 的 `expected_tools` 与 `trajectories` 为**必填**，长度必须与 `questions` 一致；缺失或长度不齐直接抛错。静默 N/A 会让指标在调用方漏传时消失，与本 change 的目的相反。
- rows 每题携带 `expected_tools` 与 `trajectory`，评分器直接从行内取，不再二次读文件。

### D6：失败样本可观测：行为轴放行，不再整轮 abort

`assert_behaviour_scores_complete` 当前对空轨迹 fail-loud；这与 `_collect_one_question` 的异常分支直接冲突。本 change 选择**让失败样本可观测**：

- `assert_behaviour_scores_complete` 对 `trajectory["failed"] is True` 的题跳过"轨迹非空"和"至少一个非 None 行为分"断言；若 failed 题在 `test_run_*.json` 中没有对应 `testCase` 行，题数对齐断言也按 `q_index` 放行该题；其余题仍 fail-loud。
- 行为轴报告对该题显示 `N/A`，不把缺失行为分写成 0。
- `score_rows` 照常产出该题的行，`tool_correctness=null`；主分/诊断按既有掩码逻辑处理。
- 这样 `detail_json`、CSV、MD、DB 都保留 N 行，符合主规格"失败样本不丢行"。

代价是行为轴 fail-loud 的范围缩小；用"只对 `failed=True` 放行"锁住，不改变未失败但无 trace 的告警。

### D7：存储用裸键，报告单列一节

- `detail_json` 每题新增 `tool_correctness`（`0.0`–`1.0` 或 `null`）与 `tool_correctness_reason`（完全命中时为空串）。
- `tool_correctness` 的 N/A 必须写成 `None`，不能走现有诊断列的 `float("nan")` 路径（`json.dumps` 会输出非标准 `NaN`）。
- 报告新增独立小节「工具选择（期望工具调用命中）」：逐题分数、未完全命中题的原因，以及：
  - `Tool correctness mean (scored): x.xxxx`；
  - `Fully matched: n/m`；
  - 计数行：`qa_count` / `no_label` / `failed` / `metric_error` / `scored`；四类互斥，优先级 `failed` > `no_label` > `metric_error` > `scored`；
  - 固定限定："只比较工具名与类型（类型固定 FUNCTION）；不比较参数、顺序、调用次数。"
- `m` 与均值分母都取 `scored`（有标签、非失败、分数非 null）；`no_label` / `failed` / `metric_error` 分别计数，保证分母可解释。
- CSV 只写 `tool_correctness`，**不新增 reason 列**；`save_results_csv` 在写盘前丢弃 `tool_correctness_reason`，避免无意改 CSV schema。
- `--deepeval-only` 不产出该指标；报告显式写"工具选择：未跑（--deepeval-only）"。
- `kb_eval` API 不返回这两个键（只存不读）。
- 不回写 Langfuse score：与现有两项诊断一致，避免把诊断值混入观测口径。

### D8：评分入口与时序

- `build_diagnostic_metrics` 增加第三个键 `tool_correctness`（构造即 `async_mode=False, eval_mode="llm"`）。
- `score_rows` 在 `sample_class == empty` 短路**之前**计算工具正确性；空样本分支只掩码其余诊断（`contextual_relevancy` / `non_advice`），不覆盖该值。
- 失败/无标签分支直接返回 `None` + reason，不调用指标；委派不单独分支。
- 现有 `score_rows` 末尾还有一次 `scored["tool_correctness"] = None`，必须删除。

### D9：规格与归档顺序

- `evaluation-pipeline` 用 **ADDED** 新增两条工具正确性要求；用 **MODIFIED** 改写五条旧条款：
  - `Diagnostic metrics do not affect the main score`：空样本只掩码两项裁判诊断，`tool_correctness` 按自身规则评分；`detail_json` 由"恒 null"改为 `0.0–1.0/null` + reason。
  - `Metric failures are explicit, not silently dropped`：empty 不再"跳过全部指标（含诊断）"；区分"未检索到材料（可评分）"与"跑图失败（N/A）"。
  - `Evaluation consumes v6 testsets without changing the scoring contract`：`expected_tools` 只供 `tool_correctness`，四项主分与两项裁判诊断仍不读取；完整保留原 4 个 scenario。
  - `Agent behaviour is evaluated with DeepEval native metrics`：`failed=True` 的轨迹放行，行为分 N/A；其余空轨迹仍 fail-loud。
  - `Agent trajectory captured as diagnostic metadata`：从行为指标示例中移除 `ToolCorrectness`，明确它是同步诊断，不适用 `evals_iterator` / `test_run` 取分。
- `testset-generation-deepeval` 用 **MODIFIED** 把"评测侧不消费"改为"由评测消费"；不放开出题侧的工具名契约。
- 归档顺序是 `deepeval-native-metrics` → `deepeval-synthesizer-testset` → 本 change；前两者已于 2026-10-18 归档。

### D10：测试策略

单元测试覆盖语义矩阵，不依赖真实裁判；用例至少包含一个非 `retrieve_kb` 的工具名，防止实现隐式特化：

- 单工具：命中 1.0、未调用 0.0、重复/额外 1.0。
- 多工具：两个期望命中一个 0.5。
- 空标签：`[]` + 无调用 1.0；`[]` + 有调用 0.0。
- 缺失标签：null；失败（异常/`[ERROR]`/空回答）：null。
- 重复标签：去重后按集合评分，并产生 warning。
- 坏标签：`"retrieve_kb"` / `[1]` / `[""]` / `[None]` → 带 `q_index` 的 `ValueError`。
- `measure` 返回非 None（防 `async_mode` 回归）；构造参数断言含 `eval_mode == "llm"`。
- 数据流：loader/limit 三段对齐；`run_evaluation` 缺参数或长度不齐时抛错；`--limit` 后标签不错位。
- 集成：一条非 `retrieve_kb` 的期望 + 无调用轨迹，跑通 `run_evaluation → detail_json → 报告`，断言 0 与违规清单。
- 行为轴：`failed=True` 的轨迹不触发 abort，行为分显示 N/A；非 failed 的空轨迹仍抛错。
- 报告计数：`no_label` / `failed` / `metric_error` / `scored` 与实际行数对账；CSV 不含 `tool_correctness_reason` 列。

## Risks / Trade-offs

- **v6 上恒 1.0** → 信息量低。缓解：报告显示违规题清单与完全命中数，任何未完全命中都视为回归信号；台账记录分母。
- **只看工具名与类型，不看参数** → 无法发现"调了正确工具但查询写错"。这是刻意的范围冻结；参数质量由检索/回答指标覆盖。
- **委派盲区** → 委派且子代理检索时，顶层看不到期望工具，会记 0（假阴性）。缓解：reason 注明存在 `delegate_task`；是否展开子代理轨迹另立 change。选择 0 而不是 N/A，是为了让哨兵不可通过"全部委派"绕过。
- **空回答按失败处理** → 若未来产品把空回答视为合法输出，需修改 D3；当前取"失败"是为了避免 `expected_tools=[]` 的 1.0 假阳性。
- **行为轴 fail-loud 范围缩小** → 只对 `failed=True` 放行；非 failed 的空轨迹仍然抛错。用单测锁住。
- **未知/拼错的工具名** → 自然匹配不到，记 0；不做别名或拼写纠正。标签质量属于出题侧责任。
- **重复标签** → 评分前去重 + warning；若不去重，DeepEval 会要求重复调用。
- **N/A 序列化** → 必须显式 `None`；现有诊断列的 `float("nan")` 路径不能复用。
- **CSV schema** → reason 只进 detail；CSV 丢弃该列，避免无意变更消费方。
- **上游 `async_mode` / `eval_mode` 缺陷** → 固定构造参数，并用单测锁住返回值非 None。

## Migration Plan

1. `deepeval-native-metrics` 与 `deepeval-synthesizer-testset` 已归档，主规格已就位。
2. 实现数据流与评分；v5 行为保持 N/A，无需数据迁移。
3. `eval_report.detail_json` 只新增键，不改表结构；历史行保持原样（无这两个键），读取方按 `.get` 兼容。
4. 回滚：删除指标构造与 rows 字段即可，不影响主分；detail 新键可忽略；行为轴放行逻辑可单独回退。

## Open Questions

- 无阻塞项。后续若要观测子代理内部的工具调用，另立 change 扩展轨迹采集；本 change 明确把委派视为顶层工具选择的一部分。
