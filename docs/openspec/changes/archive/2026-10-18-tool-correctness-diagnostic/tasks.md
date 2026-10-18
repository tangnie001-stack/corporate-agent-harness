## 1. 数据流与失败标记

- [ ] 1.1 `src/cli/testset_io.py`：`_load_latest_testset` 返回 `(questions, ground_truth, expected_tools)`；labels 保留三态（字段缺失/`None` → `None`，显式 `[]` → `[]`，非空 → 列表），SHALL NOT 用 `or []` 抹平；校验非空标签为 `list[str]` 且元素为非空字符串，不合法时抛带 `q_index` 的 `ValueError`。
- [ ] 1.2 `src/cli/eval_deepeval.py`：`_limit_testset` 接收并同步截取 `expected_tools`，返回三元组；`limit` 校验语义不变；更新 `tests/cli/test_eval_deepeval_limit.py`。
- [ ] 1.3 `src/cli/eval_deepeval.py`：新增 `_is_failed_run(answer, trajectory)`，覆盖 `trajectory.failed is True`、`answer` 以 `[ERROR]` 开头、`answer` 为空/纯空白；`_collect_one_question` 异常分支的轨迹增加 `"failed": True`，成功分支不加键；更新受影响的轨迹相等断言。
- [ ] 1.4 `src/cli/eval_deepeval.py`：`run_evaluation` 的 `expected_tools` 与 `trajectories` 改为**必填** keyword-only 参数；任一缺失或长度不等直接抛错，SHALL NOT 静默全 N/A；rows 每题携带两者。
- [ ] 1.5 更新 `_load_latest_testset` 的测试替身（`tests/cli/test_deepeval_only.py`、`tests/test_tracing_order.py` 等）为三元组；更新 `run_evaluation` 的测试调用；跑 `tests/cli/test_eval_deepeval*.py` 确认数据流不破。

## 2. 失败样本可观测与行为轴放行

- [ ] 2.1 `src/cli/eval_scoring.py`：`assert_behaviour_scores_complete` 对 `trajectory["failed"] is True` 的题跳过"轨迹非空"与"至少一个非 None 行为分"断言；若 failed 题在 `test_run_*.json` 中没有对应行，题数对齐断言按 `q_index` 放行该题；其余题保持 fail-loud。
- [ ] 2.2 行为轴报告（`_build_behaviour_section`）对 failed 题显示 `N/A`，SHALL NOT 写 0；确认 `_write_langfuse_scores` 跳过 None。
- [ ] 2.3 测试：failed 轨迹不再 abort，报告保留 N 行且行为分 N/A；非 failed 的空轨迹仍抛错。

## 3. 指标构造与评分

- [ ] 3.1 `src/cli/eval_scoring.py`：`build_diagnostic_metrics` 增加 `tool_correctness`，构造 `ToolCorrectnessMetric(model=judge, async_mode=False, eval_mode="llm")`；明确不传 `available_tools`、`evaluation_params` 为空；更新 docstring。
- [ ] 3.2 新增转换辅助：`expected_tools: list[str]` → 去重保序后的 `[ToolCall(name=..., type=FUNCTION)]`；轨迹 `tool_calls` → `[ToolCall(name=..., type=FUNCTION, input_parameters=args or {})]`；空调用必须返回 `[]`，SHALL NOT 返回 `None`；SHALL NOT import `EVAL_SYNTH_EXPECTED_TOOLS`、SHALL NOT 对 `retrieve_kb` 做分支。
- [ ] 3.3 新增单题评分函数：标签为 `None` → `(null, "", no_label)`；`_is_failed_run` → `(null, "failed", failed)`；否则调用一次 `measure`（不重试），返回 `(0.0–1.0, reason, scored)`；reason 必须自行计算：完全命中为空串，未完全命中用 `expected_names - actual_names`，实际含 `delegate_task` 时追加说明；单行化并转义 `|`/换行；SHALL NOT 直接持久化 `metric.reason`。
- [ ] 3.4 `score_rows`：在 `sample_class == empty` 短路**之前**计算工具正确性；空样本分支只对 `contextual_relevancy` / `non_advice` 置 NaN，SHALL NOT 覆盖 `tool_correctness`；删除结尾硬编码的 `scored["tool_correctness"] = None`；写入 `tool_correctness_reason`。
- [ ] 3.5 单测语义矩阵（至少一个非 `retrieve_kb` 的工具名）：单工具命中/未调用/重复/额外；多工具部分命中 = 0.5；显式 `[]` 无调用 = 1.0、有调用 = 0.0；字段缺失/失败 = null；重复标签去重并 warning；坏标签抛错。
- [ ] 3.6 构造参数单测：`model is judge`、`async_mode is False`、`eval_mode == "llm"`、`available_tools is None`、`evaluation_params == []`；并断言 `measure()` 返回非 None（锁住上游 async 缺陷）。
- [ ] 3.7 更新 `tests/cli/test_eval_native_diagnostics.py`：诊断指标列表变为三项；空样本按新口径断言（未调用期望工具为 0；失败/无标签才是 None）；确认 `tools_called`/`expected_tools` 传 `[]` 而非 None。

## 4. 存储与报告

- [ ] 4.1 `_build_eval_detail`：读取 `tool_correctness` 写 `0.0–1.0` 或 `None`（SHALL NOT 写 NaN），读取 `tool_correctness_reason` 写字符串；删除硬编码 None；缺列时用 `.get`/空串保护；更新 `tests/cli/test_eval_deepeval_detail.py`。
- [ ] 4.2 新增报告小节「工具选择（期望工具调用命中）」：逐题分数与未完全命中原因；`Tool correctness mean (scored)` 与 `Fully matched: n/m`；计数行 `qa_count / no_label / failed / metric_error / scored`（四类互斥，优先级 `failed` > `no_label` > `metric_error` > `scored`）；固定限定"只比较工具名与类型（类型固定 FUNCTION）；不比较参数、顺序、调用次数"；v5 显示 N/A。
- [ ] 4.3 CSV 只保留 `tool_correctness` 列：`save_results_csv` 写盘前丢弃 `tool_correctness_reason`；确认 `apply_sample_masks` 不掩码 `tool_correctness`（加单测锁住）。
- [ ] 4.4 `--deepeval-only` 报告显式写「工具选择：未跑（--deepeval-only）」；确认该模式不构造/不评分该指标。
- [ ] 4.5 确认 `kb_eval` API 不返回 `tool_correctness` / `tool_correctness_reason`；更新报告相关测试（逐题表、诊断段、均值行、计数行）。

## 5. 规格、文档与校验

- [ ] 5.1 `src/cli/README.md`：内部流程补"消费 `expected_tools`"、CSV 列说明（只加 `tool_correctness`，不加 reason）、诊断说明补工具选择口径/三态/N/A、`--deepeval-only` 例外。
- [ ] 5.2 `docs/agents/eval-ledger.md`：把"tool_correctness 未启用"改为启用口径；记录首轮运行的 `scored` 分母、均值、完全命中数与未完全命中题。
- [ ] 5.3 `docs/agents/glossary.md`：登记 `tool_correctness`（命中数 / 去重后期望数；只比较工具名与类型；`[]`=期望不调工具；缺失=N/A）。
- [ ] 5.4 `openspec validate tool-correctness-diagnostic --strict` 通过；确认 `evaluation-pipeline` 的五条 MODIFIED 均完整复制主规格（特别是 `Evaluation consumes v6 testsets` 的 4 个 scenario 与 `Agent behaviour` 的全部场景），并确认 `testset-generation-deepeval` 的 MODIFIED 只改"消费"契约。

## 6. 验收

- [ ] 6.1 `POSTGRES_HOST=localhost pytest tests/cli/test_eval_*.py tests/cli/test_eval_deepeval*.py tests/cli/test_deepeval_only.py tests/test_tracing_order.py -v` 全绿；必要时扩展为全量 `pytest tests/`。
- [ ] 6.2 集成测试：用 stub graph/固定 trajectory 造一条**非 `retrieve_kb`** 的用例（期望工具与实际调用不匹配），跑通 `run_evaluation → detail_json → 报告`，断言 `0.0`、reason 与违规清单；再补一条多工具 0.5 用例。
- [ ] 6.3 失败路径集成测试：造一条空回答/`[ERROR]` 的用例，确认整轮不 abort、行为分 N/A、`tool_correctness=null`、报告保留该行。
- [ ] 6.4 活体冒烟：`python -m src.cli.eval_deepeval --kb-id <kb> --testset-version 6 --limit 5`，确认报告显示 `5/5` 完全命中、`detail_json` 有 `tool_correctness` 与 reason。
- [ ] 6.5 基线：用现有 `deepeval_eval_20261018_041404.csv` 的 22 条轨迹 + v6 标签离线重放，记录 `22/22` 与分母 22 到台账；全量 33 分钟重跑为可选确认，不伪造新运行数字。
- [ ] 6.6 `ruff format . && ruff check .` 无新增错误；`pyright src/` 不引入新 error。
