## 1. 配置与适配器

- [ ] 1.1 `src/config/settings.py` 新增 `EVAL_SYNTH_CHUNKS_PER_CONTEXT`（默认 2）、`EVAL_SYNTH_GOLDENS_PER_CONTEXT`（默认 1）、`EVAL_SYNTH_MAX_CONCURRENT`（默认 4），并在 `.env.example` / `.env.template` 写入同名项。
- [ ] 1.2 `src/config/const.py` 新增 `EVAL_LANG_CJK_MIN_RATIO = 0.85`、`EVAL_LANG_SENTENCE_CJK_MIN_RATIO = 0.5`、`EVAL_HOP_MULTI_MAX_RATIO = 1 / 3`、`EVAL_SYNTH_MIN_SAMPLES = 15`、`EVAL_SYNTH_EXPECTED_TOOLS = ["retrieve_kb"]`、`EVAL_SYNTH_EVOLUTION_WEIGHTS`（**字符串键**：`"Multi-context": 0.28`，其余六种各 `0.12`；不 import deepeval）、`EVAL_NUMERIC_METRIC_WORDS`（营业收入 / 净利润 / 毛利 / 经营盈利 / 资产 / 负债 / 现金流 / 资本开支 / 持股比例 / 每股收益等指标词表）。
- [ ] 1.3 新建 `src/infra/llm/provider/deepeval_synth_llm.py`：`SynthDeepEvalLLM(BridgeDeepEvalLLM)`，`generate` / `a_generate` 支持 `schema=`；`Response` 分支返回 `Response(response=text)`，其它 schema 返回 `schema(**trimAndLoadJson(text))`；解析失败抛 `ValueError`。模块顶部先 `import src.config.settings` 再 import deepeval。
- [ ] 1.4 适配器内 3 次重试（共 4 次尝试）：`generate` 与 `a_generate` 共用同一策略；捕获 `openai.APIStatusError`（按 `status_code` 判 429/5xx）、`openai.APITimeoutError`、`openai.APIConnectionError`，httpx 兜底；优先 `exc.response.headers["Retry-After"]`（有限、`>=0`、上限 30s），否则指数退避 + jitter（上限 8s）；async 用 `await asyncio.sleep`；4xx（除 429）与 JSON 解析失败不重试。
- [ ] 1.5 在 `tests/infra/llm/provider/test_deepeval_synth_llm.py` 覆盖：无 schema 返回文本、`schema=Response` 有 `.response`、非法 JSON 抛 `ValueError` 且不是 `TypeError`、```json 包裹可解析、`openai.RateLimitError` 同步/异步都重试成功、异步 `Retry-After: 12` 被遵守、`openai.NotFoundError` 不重试、async 不使用阻塞 `time.sleep`。
- [ ] 1.6 断言适配器不会构造默认模型：`SynthDeepEvalLLM(chat).model` 与 `get_model_name()` 均来自传入的 chat，不出现 `gpt-3.5-turbo`。
- [ ] 1.7 `testset_synth.py` 把字符串权重转换为 `Evolution` 枚举，并单测七个键全部映射、权重和为 1.0。

## 2. 分块与上下文构造

- [ ] 2.1 新建 `src/cli/synth_contexts.py`：`load_chunks(kb_id, docs)` 调 `VectorStore.get_all_chunks`，按 `(doc_id, chunk_index)` 排序；`docs` 为空时不过滤（默认全部文档）。
- [ ] 2.2 `group_chunks(chunks, size)` 实现「同文档内连续 N 块一组、不重叠、丢弃末尾单块」，按 `doc_id` 分组（不是 `source`），返回 `list[ContextGroup]`（含 `chunks` / `source` / `doc_id`）。
- [ ] 2.3 `derive_company(chunks_of_doc)`：优先 `metadata["company"]` 含 CJK；否则先扫该文档全部 `parent_content`，仍无匹配再扫 `content`；按 `chunk_index` 取第一个候选，剥掉叙述前缀（`本公司`/`公司`/`子公司`/`控股股东`/`股东`/`持有`/`的`），剥后长度须 ≥4；仍找不到则抛错终止，不返回 ASCII 名。
- [ ] 2.4 `derive_period(chunks_of_doc)`：`report_period` 优先；缺失时用 source 年份 + 后缀（`annual→年年度`、`q1..q4→年第X季度`、`h1→年上半年`、`h2→年下半年`）；有年份无后缀 → `{year}年`；无年份且无 `report_period` → 用 `metadata["report_type"]` / `["quarter"]` 兜底，仍为空则报错。
- [ ] 2.5 `company_aliases(name, raw_company)`：后缀表**最长优先**（`股份有限公司 / 有限责任公司 / 控股有限公司 / 有限公司 / 集团`），递归剥离，长度 ≥2 才加入；并把 `raw_company` 的 ASCII 形（如 `tencent`）并入别名。单测 `腾讯控股有限公司 → 腾讯`。
- [ ] 2.6 `render_context(group)` 在组内首块前置 `【{company} · {period}】`，其余块原样，返回 `list[str]` 与 `header_company`。
- [ ] 2.7 测试 `tests/cli/test_synth_contexts.py`：13 块文档产出 6 组、丢弃第 13 块；用**真实 38 块分布**（仅 5 块含全称）断言腾讯得到 `腾讯控股有限公司` 且 19 组 header 全为中文并含 `2024`；「本公司的控股股东东软集团股份有限公司」剥前缀后得到 `东软集团股份有限公司`；同名不同 `doc_id` 不串组；找不到全称/period 时抛错。
- [ ] 2.8 `--dry-run` 输出上下文数、覆盖块数、实际 `doc_names`、每组首行头、目标路径（已存在时提示需 `--force`）与调用量区间（每 context 5–8 次，单轮 125–200），不调用任何 LLM。

## 3. Synthesizer 调用与编排

- [ ] 3.1 `src/cli/testset_synth.py` 构造 `Synthesizer(model=SynthDeepEvalLLM(judge), async_mode=True, max_concurrent=EVAL_SYNTH_MAX_CONCURRENT, styling_config=..., evolution_config=..., filtration_config=...)`；`main()` 保持同步，内部只有一个 `asyncio.run(run_and_dispose(_run()))`，读 DB、出题、写盘在同一 loop 内完成并 dispose engine。
- [ ] 3.2 `StylingConfig` 四字段按 design D4 写中文硬约束（scenario / task / input_format / expected_output_format），`expected_output_format` 明确要求句号结尾。
- [ ] 3.3 `EvolutionConfig(num_evolutions=1, evolutions=EVAL_SYNTH_EVOLUTION_WEIGHTS)`；`FiltrationConfig(synthetic_input_quality_threshold=0.5, max_quality_retries=2, critic_model=SynthDeepEvalLLM(judge))`。
- [ ] 3.4 **按上下文拆调用**：建一个 `Progress(disable=True)`，每个 group 分配 `pbar_id`，调 `await a_generate_goldens_from_contexts([ctx], include_expected_output=True, max_goldens_per_context=..., _reset_cost=False, _progress=progress, _pbar_id=pbar_id)`；**不传 `_send_data`**（async 签名没有该参数）；外层 `asyncio.gather(..., return_exceptions=True)` + 信号量 4；异常上下文记 warning 跳过。
- [ ] 3.5 `golden_to_sample(golden, group)` 写全 D8 字段：`input→user_input`、`expected_output→reference`、**`context→reference_contexts`（`golden.retrieval_context` 恒为 None，不得读取）**、`expected_tools=EVAL_SYNTH_EXPECTED_TOOLS`、`hop_type` 由 `evolutions` 推导、`hop_basis="evolution"`、`numeric_risk` 由 4.3 同函数计算、`synth_quality=additional_metadata["synthetic_input_quality"]`、`source_file` 取自 group。
- [ ] 3.6 测试 `tests/cli/test_testset_synth.py`：伪模型固定 JSON 时断言每 context 只发起允许的调用；**一个 context 抛 `ValueError` 时其余仍产出、最终仍写文件**；全部失败时非零退出且不留文件；`golden.retrieval_context is None` 时 `reference_contexts` 仍非空；用 `inspect.signature` 断言传给 async 方法的 kwargs 合法（无 `_send_data`）；2 context 并发出题不产生 Rich 进度表刷屏。

## 4. 质量门

- [ ] 4.1 新建 `src/cli/synth_gates.py`：`strip_parentheticals` + `strip_context_entities`（`(?<![A-Za-z0-9])实体(?![A-Za-z0-9])`、大小写不敏感，实体集 = 上下文拉丁 token + 公司别名）+ `cjk_ratio`（分母 = CJK + 拉丁字母；0 分母判失败）。
- [ ] 4.2 `check_language(sample)`：**纯英文硬规则在原始句上执行**（任一句拉丁 ≥5 且 CJK=0 失败）；**句级比例先去括号**再判（字母 ≥5 的句子 CJK ≥0.5）；整体去括号 + 剔实体后 ≥0.85；题面与参考答案都查；在自足改写之后执行，且对改写前原文再判一次。
- [ ] 4.3 `check_complete_sentence(reference, numeric_risk)`：长度 ≥8、以 `。！？.!?` 结尾、非纯数字标点串；`numeric_risk=true` 时定位第一个**后接单位/百分号/金额词**的数字，跳过日期与期间（`19xx/20xx年`、`x月`、`x日`、`Q1-Q4`、`H1/H2`、`1-9月`）；要求数字前有主体词、整句含指标词。
- [ ] 4.4 `check_self_contained(samples, aliases_by_company)`：调用前 stash 每条 `original_user_input`，由各文档 `(company, aliases)` 组装 `{company_norm: set}`，再复用 `eval_self_contained.enforce_self_contained`；返回 `kept`、`dropped` 与 `rewritten` 条数；改写后由 4.2 复查原文与改写文。
- [ ] 4.5 `dedupe(samples)`：题面去标点/空白后小写，精确去重，保留首次出现。
- [ ] 4.6 `enforce_hop_ratio(samples)`：按 `hop_type` 统计，超过 `1/3` 时按 `sha1(user_input)` 升序丢弃多跳样本至达标；不按生成顺序/文档顺序。
- [ ] 4.7 `run_gates(goldens)` 按「自足 → 完整句 → 语言 → 去重 → 多跳」执行，返回 `(kept, stats)`；`stats` 含 `generated`、`failed_contexts`、`self_contained_rewritten`、`language_checked` 与 `dropped` 分项；**每条样本只记第一个未通过的门**，保证 `generated - sum(dropped) == kept`。
- [ ] 4.8 `compute_numeric_risk(reference) -> bool`：含数字且含单位/百分号/金额词即为 true；由 `golden_to_sample` 与 `check_complete_sentence` 共用，避免两处各算一套。
- [ ] 4.9 测试 `tests/cli/test_synth_gates.py`：纯英文题被丢；中英拼接句被丢；括号英文名与 `SAP SE` 被容忍；`tencent`/`Tencent` 大小写不敏感；CJK 紧邻 `SAP SE` 能剔除；英文句所有词都在上下文时仍被丢；`为18.5亿元。` 与 `营业收入18.5亿元。` 被丢；`腾讯2024年营业收入为人民币18.5亿元。`、`2024年12月31日，公司实现营业收入18.5亿元。`、`2024年Q1营业收入为18.5亿元。` 通过；裸数字记为 `not_complete_sentence`；重复题只留一条；多跳 0.42 按哈希截到 ≤1/3；0 字母输入被丢。
- [ ] 4.10 测试 `numeric_risk`：含「人民币18.5亿元」为 true，不含数字为 false；`compute_numeric_risk` 与 writer 的字段一致。
- [ ] 4.11 **锚定门**：`check_grounding(question, period, contexts, aliases)`——题面必须含上下文报告期年份；题面「实体 + 年份」的实体必须在上下文/别名中；在自足门之后、完整句之前执行，失败记 `dropped.grounding`。补三条测试：错误年份、外来公司（贵州茅台）、正常题通过。

## 5. 落盘与元数据

- [ ] 5.1 新建 `src/cli/synth_writer.py`：`build_metadata(...)` 写入 design D8 全部字段（含 `company_source`、`hop_stats`、`gate_stats.language_checked` / `self_contained_rewritten` / `failed_contexts`、实际 `goldens_per_context`）。
- [ ] 5.2 `write_testset(path, payload)` 先写 `<path>.tmp` 再 `os.replace`；异常时清理临时文件。
- [ ] 5.3 目标文件存在且无 `--force` 时抛 `FileExistsError`，CLI 转成非零退出并给出提示。
- [ ] 5.4 断言 `gate_stats.generated - sum(dropped) == testset_size` 且只统计最终一轮；不成立时拒绝写入。
- [ ] 5.5 `load_testset_samples` 回归：v6 新字段可被忽略，`_load_latest_testset` 仍返回 `(questions, ground_truth)`。
- [ ] 5.6 测试 `tests/cli/test_synth_writer.py`：原子写、`--force` 保护、对账失败拒绝写、v5/v6 双读、重跑轮次不双计。

## 6. CLI 与旧入口

- [ ] 6.1 `main()` 支持 `--kb-id`（必填）、`--version`（省略=现有最大版本+1）、`--docs`（省略=全部文档）、`--kb-name`（可选）、`--force`、`--dry-run`；**显式 `--version` 且未传 `--docs` 时直接报错退出**；`--docs` 给了但没给 `--version` 时用「下一个版本 + 该子集」；每个 `--docs` 项必须匹配 ≥1 chunk，否则报错。
- [ ] 6.2 低产出与早停：先判 `generated == 0` / 所有上下文失败 → 非零退出；再判 `language_checked == 0` → 按语言失效退出（不做除法）；否则 `language_dropped / language_checked > 50%` 直接退出、不重跑；最后 `kept < EVAL_SYNTH_MIN_SAMPLES`（在多跳截断之后判断）时以 `goldens_per_context=2` 重跑一次，仍不足则非零退出且不写文件。
- [ ] 6.3 日志事件：先在 `src/core/log_events.py` 的 `LOG_PREFIXES` 加 `"synth"`、在 `Event` 枚举加事件，再登记 `EVENT_SPECS` 与 `docs/agents/logging-rules.md`：`testset synth start` / `gate` / `done`（info）+ `testset synth context failed`（warning，仅失败时记）；**不得登记 debug 级事件**（`_ALLOWED_LEVELS` 只有 info/warning/error）。
- [ ] 6.4 `src/cli/eval_deepeval.py` 的 `--generate` 报错文案指向 `python -m src.cli.testset_synth`，同时保留 `Synthesizer` 与 `deepeval-synthesizer-testset` 两个 token（现有 `test_deepeval_generate_guard.py` 断言它们）。
- [ ] 6.5 `src/config/response_codes.py` 的 `Code.GONE_MSG` 改为指向 `python -m src.cli.testset_synth`（410 的 message 来自这里，改 `api/ragas_generate.py` 无效）；同时更新 `testset_io` 的 FileNotFoundError 文案。
- [ ] 6.6 测试：`--generate` 退出码 1 且含新命令（保留旧 token）；`POST /api/ragas/generate` 返回 410 且 `message` 含新命令。
- [ ] 6.7 版本解析单一来源：`testset_io._scan_versions(kb_id)`，`load_testset_samples` 也改用它；`resolve_testset_version(kb_id, version)`（评测：显式版本不校验存在、`None` 取 max、无文件抛 FileNotFoundError、放在 `main()` 现有 try 内）；`next_testset_version(kb_id)`（出题：max+1，不复用 resolve）。
- [ ] 6.8 `build_report_meta(..., testset_version=None)` 两个分支（正常 / `--deepeval-only`）都写入；`save_markdown_report` 在 `report_meta is not None` 内渲染，`None` 写 N/A 或不写；`_build_eval_detail` / `_save_eval_report` 新参数带默认值；`testset_version=None` 时**仍然 insert**，条目写 `null` 并记 warning；补断言覆盖报告头、`detail_json`、`--deepeval-only` 分支，并兼容 `test_deepeval_only.py` / `test_tracing_order.py` / `test_eval_overall_score.py` / `test_eval_deepeval.py` 的现有调用。

## 7. 文档同步

- [ ] 7.1 `src/cli/README.md` 新增出题章节：命令、参数、输出路径、`--dry-run`、`--force`、显式版本必须配 `--docs`。
- [ ] 7.2 `docs/agents/code-map.md` 登记 `synth_contexts.py` / `synth_gates.py` / `synth_writer.py` / `testset_synth.py` 的职责。
- [ ] 7.3 `docs/agents/api_contract.md` 更新出题 CLI 契约（参数、退出码、题集结构、410 文案、`testset_version`）。
- [ ] 7.4 `docs/agents/glossary.md` 登记 `hop_type` / `hop_basis` / `multi_hop` / `expected_tools` / `numeric_risk`。
- [ ] 7.5 `docs/agents/requirements_pool.md` 登记「v7 扩到全量 4 份文档」为后续需求，注明 v6 冻结在 v5 范围。
- [ ] 7.6 更新 `proposal.md` 的 Impact：`src/api/ragas_generate.py` 明确为「维持 410，仅改 `Code.GONE_MSG`」。

## 8. 验收（活体）

- [ ] 8.1 `python -m src.cli.testset_synth --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --version 6 --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf --kb-name test --dry-run`：确认实际匹配的 `doc_names`、25 个上下文、50/51 块、调用量区间 125–200。
- [ ] 8.2 正式出题：同命令去掉 `--dry-run`；记录耗时、`gate_stats`（含 `failed_contexts`、`language_checked`、`self_contained_rewritten`）、`hop_stats`。
- [ ] 8.3 逐项核对：`dropped.language / language_checked`（记录实际值，不写"100%"）、`self_contained_rewritten` 条数、多跳 ≤1/3、`expected_tools` 全覆盖、`numeric_risk` 有值、`source_chunks=51` / `covered_chunks=50`、header 全中文且含年份、`gate_stats` 对账成立、抽检 3 条 kept 样本的原生语言与自足性。
- [ ] 8.4 抽读 3 条样本：题面含公司+报告期、答案是完整句、`reference_contexts` 首行有 `【...】` 头、`hop_basis="evolution"`。
- [ ] 8.5 跑 5 题冒烟：`python -m src.cli.eval_deepeval --kb-id ... --testset-version 6 --run-type baseline --limit 5`，确认无 400/超时，`tool_correctness` 仍为 `null`。
- [ ] 8.6 跑 v6 全量 baseline（`--run-type baseline`，不加 `--gate`），记录四项均值、诊断均值、NaN 格数与耗时；确认报告头与 DB `detail_json` 的 `testset_version=6`。
- [ ] 8.7 在 `docs/agents/eval-ledger.md` 追加 v6 行（口径 `deep-eval-native / v6`），注明与 v5 的差异只归因于题集；若 `kept` 在 15–18，标注"题量偏低"。
- [ ] 8.8 静态闸门：`POSTGRES_HOST=localhost pytest tests/ -q`、`ruff check src/ tests/`、`pyright src/` 无新增错误。

## 9. 收尾

- [ ] 9.1 全仓 grep 确认无 `eval_ragas_generate` 残留引用（历史 research/tmp/archive 除外）。
- [ ] 9.2 `openspec validate deepeval-synthesizer-testset --strict`、`check_docs`、`check_adr` 全过；**不要**把 `openspec validate --all` 写成全绿——`context-budget-calibration-and-hardening` 是既有的无 delta 失败项，与本 change 无关。
- [ ] 9.3 归档前置确认：`deepeval-native-metrics` 先归档（本 change 的 delta 依赖其已改写的同名 requirement）。
- [ ] 9.4 `tool-correctness-diagnostic` 目前是空目录（只有 `.gitkeep`），不被 `openspec validate` 识别；本 change 不把它纳入校验，等后续填工件后再启用。
