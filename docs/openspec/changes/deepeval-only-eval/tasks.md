# Tasks

> 执行顺序即编号顺序；每组内为 TDD 小步（先写失败测试 → 实现 → 跑通 → 提交）。
> 规格：`specs/evaluation-pipeline/spec.md`；决策：`design.md`（D1–D5）；风险：R1–R4。

## 1. CLI 开关与跳过（D1 / D4）

- [ ] 1.1 写失败测试：`parse_args` 接受 `--deepeval-only`；与 `--gate` 同时出现时 `parser.error`（`SystemExit` 非零）。
- [ ] 1.2 实现：加参数 + 互斥校验（在 `parse_args` 内，早于任何 I/O）。
- [ ] 1.3 写失败测试：`main()` 在 `--deepeval-only` 下不调用 `build_metrics` / `run_evaluation`（monkeypatch 计数）。
- [ ] 1.4 实现：`main()` 分支——跳过度量构造与四指标评分。
- [ ] 1.5 跑通并提交。

## 2. 报告与落库语义（D2 / D3 / D5）

- [ ] 2.1 写失败测试：`save_markdown_report(result=None, ...)` 不输出逐题指标表与主分行，但输出头部元数据 + 行为轴段 + `RAGAS: 未跑`。
- [ ] 2.2 实现：`save_markdown_report` 的 `result=None` 路径。
- [ ] 2.3 写失败测试：`--deepeval-only` 下 `_save_eval_report` 不被调用、`save_results_csv` 不被调用。
- [ ] 2.4 实现：`main()` 跳过落库与 CSV。
- [ ] 2.5 跑通并提交。

## 3. 文档与台账口径

- [ ] 3.1 `src/cli/README.md`：补 `--deepeval-only` 用法与"无主分、不落库、勿用于 A/B 对比"。
- [ ] 3.2 `docs/agents/ragas-eval-ledger.md`：在「公共配置」或说明段落注明——该模式的运行以「口径版本 = `deepeval-only`」单列，不参与 RAGAS 主分对照。
- [ ] 3.3 跑通并提交。

## 4. 验收

- [ ] 4.1 静态闸门：`pytest tests/ -q` 全绿；`ruff format --check src/ tests/`；`ruff check src/ tests/` 与 `pyright src/` 不新增 error；`openspec validate deepeval-only-eval --strict`；`check_docs`；`check_adr`。
- [ ] 4.2 活体：`--deepeval-only --limit 1` → 行为轴有值、报告标注正确、无 CSV、无落库。
- [ ] 4.3 活体：`--deepeval-only --limit 5` → 同上。
- [ ] 4.4 活体：`--deepeval-only`（21 题）→ 记录耗时（预期 10–15 分钟），与 RAGAS 全量约 56 分钟对照。
- [ ] 4.5 验收记录写回计划文件并提交。
