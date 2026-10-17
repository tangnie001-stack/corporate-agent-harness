# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`。
>
> **本 change 分两组、可分段验收**：1–2 组是「换指标」，3–5 组是「退役 RAGAS」。两组都在本 change 内，但 1–2 组跑通后即可先验证指标，再继续清理。

## 1. 原生四指标替换

- [ ] 1.1 写失败测试：`build_metrics` 返回 **DeepEval 原生**四项（`AnswerRelevancyMetric` / `FaithfulnessMetric` / `ContextualPrecisionMetric` / `ContextualRecallMetric`），键序 = `EVAL_METRIC_COLUMNS`，且**不再 import** `deepeval.metrics.ragas`。
- [ ] 1.2 实现：`src/cli/eval_scoring.py:build_metrics` 改为原生四项（`model=judge`；`AnswerRelevancy` 需 `embeddings`）。
- [ ] 1.3 更新受影响的既有测试（桥相关断言、指标类名）。
- [ ] 1.4 跑通并提交。

## 2. 诊断指标（不进主分 / 门禁）

- [ ] 2.1 写失败测试：新增 `ContextualRelevancyMetric`（**不需要 `expected_output`**）与 `NonAdviceMetric`，二者只进诊断列表，不出现于 `EVAL_METRIC_COLUMNS`。
- [ ] 2.2 实现：新增诊断指标构造与逐题评分（沿用串行；失败记 `NaN`，不阻断）。
- [ ] 2.3 实现：报告新增「诊断指标」段；`main_score_means` / `check_gate` **不读**诊断指标。
- [ ] 2.4 跑通并提交。

## 3. 出题链路下线

- [ ] 3.1 新建 `src/cli/testset_io.py`：迁入 `_load_latest_testset`（纯 JSON 读取），保持签名不变。
- [ ] 3.2 删除 `src/cli/eval_ragas_generate.py`（含 `_ensure_vertexai_stub`、`run_generate`、`_build_transforms` 等出题件）。
- [ ] 3.3 `POST /api/ragas/generate` 改为明确错误（HTTP 410 / `ResponseModel` 错误码），文案给前迁路径（DeepEval `Synthesizer`）；从 `src/main.py` 摘除路由注册。
- [ ] 3.4 删除/改写出题测试：`test_generate_transforms.py` / `test_generate_llm_context.py` / `test_generate_entity_anchor.py` / `test_eval_ragas_personas.py` / `test_generate_guard.py`（护栏改为断言新文案）/ `test_self_contained_gate.py`（保留纯函数测试，删除 ragas 依赖部分）。
- [ ] 3.5 `--generate` 护栏文案改为「出题已迁移到 DeepEval Synthesizer（见 `deepeval-synthesizer-testset`）」，不再提「装回 ragas==0.4.3」。
- [ ] 3.6 跑通并提交。

## 4. 配置与依赖清理

- [ ] 4.1 `RAGAS_*` 重命名：裁判类 → `JUDGE_*`（`RAGAS_LLM_MODEL` / `RAGAS_PROVIDER` / `RAGAS_JUDGE_*` / `RAGAS_HTTP_*` / `RAGAS_METRIC_MAX_ATTEMPTS`）；目录类 → `EVAL_*`（`RAGAS_DATA_DIR` / `RAGAS_TESTSET_DIR` / `RAGAS_REPORT_DIR` / `RAGAS_LLM_CACHE_DIR` / `RAGAS_USER_ID`）；`RAGAS_METRIC_COLUMNS` → `EVAL_METRIC_COLUMNS`；删除仅出题使用的 `RAGAS_TEST_SIZE` / `RAGAS_TEST_PERSONAS` / `RAGAS_SELF_CONTAINED_HINT` / `RAGAS_DOC_WHITELIST`。
- [ ] 4.2 删除 `pyproject.toml` 的 `ragas==0.3.9`（保留 `deepeval` / `click` / `huggingface_hub` 的 pin）。
- [ ] 4.3 死引用自查：`grep -rn "ragas\|RAGAS_" src/ tests/ --include='*.py'` 零命中（`docs/` 历史文档除外）。
- [ ] 4.4 跑通并提交。

## 5. 文档与 ADR

- [ ] 5.1 新增 ADR-0027，取代 ADR-0026 中「桥相关」的部分（细粒度超时 / 关 SDK 重试 / 失败留证仍适用；「温度分档」与「ragas 框架层 max_retries=10」两项随 ragas 移除而失效）。旧 ADR 的 `Status` 加反向指针，更新索引。
- [ ] 5.2 同步 `docs/agents/api_contract.md`（指标来源、诊断字段）、`src/cli/README.md`、`docs/agents/ragas-eval-ledger.md`（口径版本列改 `deep-eval-native`）、`docs/agents/selection-baseline.md`（RAGAS 条目标注退役）。
- [ ] 5.3 跑通并提交。

## 6. 验收

- [ ] 6.1 静态闸门：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/`；`ruff check src/ tests/` 与 `pyright src/` 不新增 error；`openspec validate deepeval-native-metrics --strict`；`check_docs`；`check_adr`。
- [ ] 6.2 **手工选样冒烟（5 题，优先于全量）**：造临时子集（v5 的 index `0` 简单中文 / `2` 多跳中文 / `7` 英文 / `13` 纯数值 / `14` 多轮），跑通后再进 6.3。

  ```bash
  .venv/bin/python - <<'PY'
  import json
  src = "data/ragas/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json"
  dst = "data/ragas/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v99.json"
  d = json.load(open(src))
  d["samples"] = [d["samples"][i] for i in (0, 2, 7, 13, 14)]
  json.dump(d, open(dst, "w"), ensure_ascii=False, indent=2)
  print("wrote", len(d["samples"]))
  PY
  ```

  ⚠️ **用完必须删除** `_v99.json`：`_load_latest_testset` 默认取「最新版本」，残留会让后续默认评测误用这个子集。

- [ ] 6.3 21 题全量：原生四指标 + 诊断指标 + 行为轴，产出**新基线**（`deep-eval-native`），记录 NaN、耗时、各指标均值；确认 `ToolCorrectness` 记 `N/A`（v5 无 `expected_tools`）。
- [ ] 6.4 验收记录写回计划文件并提交。
