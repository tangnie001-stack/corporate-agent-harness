# testset-generation-deepeval Specification

## Purpose
TBD - created by archiving change deepeval-synthesizer-testset. Update Purpose after archive.
## Requirements
### Requirement: Testset generation runs on KB chunks with a frozen corpus scope

出题 SHALL 从目标知识库的现有分块出题，SHALL NOT 重新解析原始文档。

- 分块 SHALL 通过 `VectorStore.get_all_chunks(kb_id)` 读取，按 `(doc_id, chunk_index)` 排序与分组（`source` 仅用于 `--docs` 过滤，同名文件不会串文档）。
- `--docs` 省略时 SHALL 使用该 KB 的全部文档（命令对任意 KB 成立）；v6 的冻结范围 SHALL 由本次运行显式传入 `neusoft_2025_q1.pdf`、`tencent_2024_annual.pdf` 实现，SHALL NOT 写进命令默认值。
- 分组 SHALL 为「同一文档内连续 2 块一组、组间不重叠」；文档块数为奇数时，末尾单块 SHALL NOT 单独成组。
- 每个上下文 SHALL 在首行携带 `【{公司} · {报告期}】` 头。

#### Scenario: Corpora default to all documents

- **WHEN** 用户执行 `python -m src.cli.testset_synth --kb-id <uuid>` 且未传 `--docs`
- **THEN** 该 KB 的全部文档分块 SHALL 参与分组，SHALL NOT 因缺少某个文件名而产出 0 个上下文

#### Scenario: v6 freezes the corpus explicitly

- **WHEN** 用户执行 v6 出题并传入 `--docs neusoft_2025_q1.pdf tencent_2024_annual.pdf`
- **THEN** `metadata.doc_names` SHALL 与 v5 相同，且 SHALL 与命令的默认行为无关

#### Scenario: Grouping is deterministic and drops trailing singletons

- **WHEN** 某文档有 13 个分块
- **THEN** 该文档 SHALL 产生 6 个上下文，第 13 块 SHALL NOT 出现在任何上下文中，且两份文档合计 `metadata.covered_chunks` SHALL 为 50

#### Scenario: Context carries the entity anchor

- **WHEN** 构造任一出题上下文
- **THEN** 其首行 SHALL 为 `【公司名 · 报告期】`，公司名 SHALL 为中文，且同一文档的所有组 SHALL 使用同一个公司名

#### Scenario: Company name is derived per document, not per chunk

- **WHEN** `metadata["company"]` 为 `tencent`，但该文档只有部分分块的 `parent_content` 含「腾讯控股有限公司」
- **THEN** 扫描该文档全部分块后 SHALL 得到中文全称并复用到所有组，SHALL NOT 出现 `【tencent · ...】`

#### Scenario: Narrative prefixes are stripped from the company name

- **WHEN** 扫描到「本公司的控股股东东软集团股份有限公司」
- **THEN** 推导结果 SHALL 为 `东软集团股份有限公司`，SHALL NOT 带「本公司的控股股东」

#### Scenario: Period keeps the year

- **WHEN** 某文档 `report_period` 为空、source 为 `tencent_2024_annual.pdf`
- **THEN** header 的 period SHALL 为 `2024年年度`（含年份），SHALL NOT 只有「年度」

#### Scenario: Missing Chinese company name fails loudly

- **WHEN** 某文档的所有分块都推导不出中文公司名
- **THEN** 命令 SHALL 非零退出，SHALL NOT 用 ASCII 名写 header 或写文件

### Requirement: Testset language matches the language of its contexts

出题 SHALL 同时使用 prompt 约束与确定性校验，使题面与参考答案的语言一致性成为**受检保证**。

- `StylingConfig` 的 `scenario` / `task` / `input_format` / `expected_output_format` SHALL 为中文，且 `input_format` SHALL 明确要求简体中文问句、包含公司名称与报告期、禁止英文句子与中英拼接；`expected_output_format` SHALL 要求以句号结尾。
- 校验 SHALL 在**自足改写之后**执行，且题面 SHALL 对**改写前原文**再判一次（防止中文前缀稀释极短英文题）。
- **纯英文硬规则 SHALL 在原始句上执行**：按 `。？！.!?` 切句，任一句「拉丁字母 ≥5 且 CJK=0」SHALL 直接失败（即使该句所有词都出现在上下文，也不能靠剔除绕过）。
- **句级比例 SHALL 先去掉括号内容再算**：按 `。？！.!?` 切句，凡字母数 `>= 5` 的句子 CJK 占比 SHALL `>= 0.5`（否则「腾讯控股有限公司（Tencent Holdings Limited）2024年…」会被误杀）。
- 整体比例 SHALL 去括号后，按 `(?<![A-Za-z0-9])实体(?![A-Za-z0-9])`（CJK 视为边界、大小写不敏感）剔除「上下文中的拉丁实体名 + 公司别名（含 ASCII 形）」。
- 占比分母 SHALL 为「CJK 字母数 + 拉丁字母数」；数字、标点、空白 SHALL NOT 计入；分母为 0 SHALL 判失败。
- 题面与参考答案的整体 CJK 占比 SHALL `>= 0.85`。
- 不满足者 SHALL 被丢弃，SHALL NOT 通过翻译改写；丢弃计数 SHALL 记入 `metadata.gate_stats.dropped.language`。

#### Scenario: English question is rejected

- **WHEN** Synthesizer 产出一条纯英文问句
- **THEN** 该条 SHALL 被丢弃，不计入 `testset_size`

#### Scenario: Mixed sentence is rejected

- **WHEN** 题面为「what did the 经审核的财务报表 show」
- **THEN** 该句的 CJK 占比低于 0.5，该条 SHALL 被丢弃

#### Scenario: Parenthetical English name is tolerated

- **WHEN** 题面含「腾讯控股有限公司（Tencent Holdings Limited）」且其余为中文
- **THEN** 剔除括号内容后 CJK 占比 SHALL 达标，该条 SHALL 被保留

#### Scenario: Latin entity from the context is tolerated

- **WHEN** 题面为「东软集团2025年第一季度报告中SAP SE的持股比例是多少？」且 `SAP SE` 作为整词出现在该样本上下文
- **THEN** 剔除该实体后 CJK 占比 SHALL 达标，该条 SHALL 被保留

#### Scenario: Case-insensitive company alias is tolerated

- **WHEN** 上下文写 `tencent`、题面写 `Tencent 2024年营业收入...`
- **THEN** 大小写不敏感的整词匹配 SHALL 剔除 `Tencent`，该条 SHALL 保留

#### Scenario: Entity adjacent to CJK is stripped

- **WHEN** 题面为「报告中SAP SE的持股比例是多少？」（CJK 与 `SAP` 之间没有空格）
- **THEN** `(?<![A-Za-z0-9])SAP SE(?![A-Za-z0-9])` SHALL 命中并剔除，该条 SHALL 保留

#### Scenario: Entity stripping cannot rescue an English sentence

- **WHEN** 一句英文的所有词都出现在上下文里
- **THEN** 句级检查 SHALL 在剔除前执行，该句 SHALL 因「拉丁 ≥5 且 CJK=0」被丢弃

#### Scenario: Zero-letter input is rejected

- **WHEN** 题面或参考答案不含任何字母（只有数字与标点）
- **THEN** 分母为 0，该条 SHALL 被丢弃

#### Scenario: Language is re-checked after self-contained rewrite

- **WHEN** 自足门给题面加上了中文公司名前缀
- **THEN** 改写后的题面 SHALL 重新过语言门，且改写前原文 SHALL 也过语言门，SHALL NOT 因为改写而绕过检查

### Requirement: Questions are grounded in their context

每道题 SHALL 锚定在其 `reference_contexts` 上，SHALL NOT 出现上下文里不存在的公司或错误报告期。

- 题面 SHALL 包含该上下文报告期的年份（从 `【公司 · 报告期】` 头解析）。
- 题面中「实体 + 年份」的实体部分 SHALL 出现在 `reference_contexts` 或公司别名中。
- 不满足者 SHALL 被丢弃，计数记入 `metadata.gate_stats.dropped.grounding`。

#### Scenario: Wrong year is rejected

- **WHEN** 上下文报告期为 `2025年第一季度`，题面写「东软集团2024年第一季度…」
- **THEN** 该条 SHALL 因缺少 `2025` 被丢弃

#### Scenario: Foreign company is rejected

- **WHEN** 上下文只含腾讯，题面写「…贵州茅台2024年年报…」
- **THEN** `贵州茅台` SHALL 不在上下文/别名中，该条 SHALL 被丢弃

#### Scenario: Grounded question is kept

- **WHEN** 题面为「腾讯控股2024年营业收入与毛利润的同比增长率分别是多少？」且上下文含腾讯 2024 年报
- **THEN** 该条 SHALL 通过锚定门

### Requirement: Reference answers are self-contained complete sentences

参考答案 SHALL 是可直接比对的自包含完整句，SHALL NOT 为裸数值或残句。

- 长度 SHALL `>= 8` 个字符。
- 结尾 SHALL 为 `。`、`！`、`？`、`.`、`!` 或 `?`（接受 ASCII 标点，使英文答案落到语言门而不是被误记为残句）。
- 内容 SHALL NOT 为纯数字/标点串。
- `numeric_risk=true` 时，「数值事实」SHALL 定义为第一个**后接单位/百分号/金额词**（元/万元/亿元/%/个百分点/倍）的数字；定位时 SHALL 跳过日期与期间成分（`19xx`/`20xx` 年、`x月`、`x日`、`Q1-Q4`、`H1/H2`、`1-9月` 区间）。
- 该数字之前 SHALL 出现主体词（公司 / 集团 / 本集团 / 母公司 / 归属于…股东 / 题干公司别名），且整句 SHALL 含 `EVAL_NUMERIC_METRIC_WORDS` 中至少一个指标词（营业收入 / 收入 / 营收 / 营业额 / 净利润 / 毛利 / 经营盈利 / 资产 / 负债 / 现金流 / 资本开支 / 持股比例 / 每股收益 等）。
- 只有指标词、没有主体（如「营业收入18.5亿元。」）SHALL NOT 通过。
- 数值型事实 SHALL 携带主体与指标名（如「归属于上市公司股东的净利润」），SHALL NOT 只写数字。
- 完整句门 SHALL 排在语言门之前，使裸数值答案被归因为 `not_complete_sentence` 而非 `language`。
- 不满足者 SHALL 被丢弃，丢弃计数 SHALL 记入 `metadata.gate_stats.dropped.not_complete_sentence`。

#### Scenario: Bare number is rejected

- **WHEN** 参考答案为 `1,234,567.89`
- **THEN** 该条 SHALL 被丢弃，并计入 `dropped.not_complete_sentence`

#### Scenario: Numeric answer without subject is rejected

- **WHEN** 参考答案为「为18.5亿元。」（长度 8，但没有主体与指标名）
- **THEN** 该条 SHALL 被 `numeric_risk` 主体检查丢弃

#### Scenario: Year does not count as the numeric fact

- **WHEN** 参考答案为「腾讯2024年营业收入为人民币18.5亿元。」
- **THEN** 年份 `2024` SHALL 被跳过，主体检查针对 `18.5`，该条 SHALL 通过

#### Scenario: Dates and quarters do not count as the numeric fact

- **WHEN** 参考答案为「2024年12月31日，公司实现营业收入18.5亿元。」或「2024年Q1营业收入为18.5亿元。」
- **THEN** 日期 / 季度 SHALL 被跳过，主体检查针对 `18.5`，两条 SHALL 通过

#### Scenario: Metric word alone is not a subject

- **WHEN** 参考答案为「营业收入18.5亿元。」
- **THEN** 该条 SHALL 因缺少主体词被丢弃

#### Scenario: Revenue synonym is accepted

- **WHEN** 参考答案为「腾讯2024年营收为18.5亿元。」
- **THEN** `营收` SHALL 命中指标词表，该条 SHALL 通过

#### Scenario: Complete sentence is kept

- **WHEN** 参考答案为「东软集团2025年第一季度营业收入为人民币18.5亿元。」
- **THEN** 该条 SHALL 通过完整句校验

### Requirement: Multi-hop share is bounded and labeled

每条样本 SHALL 带 `hop_type`，取值 `single_hop` 或 `multi_hop`；判定 SHALL 由 Synthesizer 写入的 `additional_metadata["evolutions"]` 推导，SHALL NOT 依赖 LLM 自述。

- `evolutions` 含 `Multi-context` → `multi_hop`；否则 → `single_hop`。
- 每条样本 SHALL 同时写 `hop_basis: "evolution"`；该标签 SHALL 被视为**筛选代理**，SHALL NOT 被描述为"已证明跨块推理"。
- 生成权重 SHALL 为 `Multi-context = 0.28`，其余六种各 `0.12`，`num_evolutions = 1`。
- 最终 `multi_hop / total` SHALL `<= 1/3`；超出时 SHALL 按 `sha1(user_input)` 升序丢弃多跳样本，SHALL NOT 按生成顺序/尾部丢弃，SHALL NOT 追加生成。
- `metadata.hop_stats` SHALL 记录两类实际数量。

#### Scenario: Hop type comes from evolutions

- **WHEN** 某样本的 `evolutions` 为 `["Multi-context"]`
- **THEN** 其 `hop_type` SHALL 为 `multi_hop`、`hop_basis` SHALL 为 `evolution`，且无需调用 LLM 判定

#### Scenario: Ratio is enforced deterministically

- **WHEN** 通过其他门的样本中多跳占比为 0.42
- **THEN** SHALL 按 `sha1(user_input)` 升序丢弃多跳样本至占比 `<= 1/3`，且 `metadata.hop_stats` SHALL 反映最终值

#### Scenario: Truncation does not favor one document

- **WHEN** 多跳样本分布在两份文档上且需要截断
- **THEN** 丢弃顺序 SHALL 由题面哈希决定，SHALL NOT 由 task 完成顺序或文档顺序决定

### Requirement: Every sample carries expected_tools

每条样本 SHALL 带 `expected_tools`，值为字符串列表。

- v6 题集中的所有样本 SHALL 标注 `["retrieve_kb"]`，SHALL NOT 因 `hop_type=multi_hop` 标注重复工具。该取值是 v6 的题集事实，SHALL NOT 被评测侧当作通用默认值。
- 存储形态 SHALL 为 `list[str]`，SHALL NOT 为 DeepEval `ToolCall` 对象。
- 评测侧 SHALL 消费该字段；无该字段的旧题集 SHALL 记 `null`（N/A）。

#### Scenario: Multi-hop still expects one retrieval

- **WHEN** v6 题集中某样本 `hop_type=multi_hop`
- **THEN** 其 `expected_tools` SHALL 仍为 `["retrieve_kb"]`

#### Scenario: Evaluation consumes expected_tools

- **WHEN** 用带 `expected_tools` 的题集（如 v6）运行 `eval_deepeval`
- **THEN** `tool_correctness` SHALL 按 `expected_tools` 逐题评分；四个主指标 SHALL NOT 读取 `expected_tools`

### Requirement: Numeric-risk facts are registered

每条样本 SHALL 带布尔字段 `numeric_risk`，标识参考答案是否含需要单位/口径判定的数值事实。

- 判定 SHALL 为：参考答案含数字，且含 `元` / `万元` / `亿元` / `%` / `个百分点` 之一。
- `numeric_risk=true` SHALL NOT 导致丢弃，仅登记，供后续分析 faithfulness 与 CP。

#### Scenario: Monetary figure is flagged

- **WHEN** 参考答案含「人民币18.5亿元」
- **THEN** `numeric_risk` SHALL 为 `true`

#### Scenario: Non-numeric answer is not flagged

- **WHEN** 参考答案不含任何数字
- **THEN** `numeric_risk` SHALL 为 `false`

### Requirement: Generation uses the provider judge through a schema-aware adapter

出题 SHALL 复用 provider 层的裁判 client，SHALL NOT 回落到 DeepEval 默认模型。

- 适配器 SHALL 继承 `BridgeDeepEvalLLM`，并支持 `generate(prompt, schema=...)`：
  - 未传 `schema` → 返回文本；
  - `schema is Response` → 返回 `Response(response=text)`；
  - 其它 schema → 返回 `schema(**trimAndLoadJson(text))`。
- JSON 解析失败 SHALL 抛 `ValueError`（SHALL NOT 抛 `TypeError`，该异常会被 Synthesizer 当作"不支持 schema"的信号）。
- 适配器 SHALL 对单次调用做 3 次重试（共 4 次尝试），且只重试限流/服务端/传输失败：
  - 捕获 `openai.APIStatusError`（按 `status_code` 判 429 / 5xx）、`openai.APITimeoutError`、`openai.APIConnectionError`；`httpx.TimeoutException` / `httpx.TransportError` 作兜底。SHALL NOT 只捕获 httpx 异常（`ChatOpenAI` 抛的是 openai 异常）。
  - `generate` 与 `a_generate` SHALL 共用同一策略；async 路径的等待 SHALL 用 `await asyncio.sleep`，SHALL NOT 用阻塞 `time.sleep`。
  - 优先采用 `exc.response.headers["Retry-After"]`（有限且 `>= 0`，上限 30s），否则指数退避 `0.5 × 2^(n-1)` + jitter，上限 8s；
  - 4xx（除 429）与 JSON 解析失败 SHALL NOT 重试。
- `Synthesizer` SHALL 以 `max_concurrent=4` 构造，SHALL NOT 使用默认 `100`。
- `a_generate_goldens_from_contexts` SHALL NOT 传 `_send_data`（该参数只存在于同步入口，async 签名没有它，传入会 `TypeError`；async 路径本身不上传）。

#### Scenario: Adapter returns a Response object

- **WHEN** Synthesizer 调用 `model.generate(prompt, schema=Response)`
- **THEN** 返回值 SHALL 具备 `.response` 属性，SHALL NOT 为 `str`

#### Scenario: OpenAI rate limit is retried

- **WHEN** 裁判抛出 `openai.RateLimitError`
- **THEN** 适配器 SHALL 重试，SHALL NOT 直接冒泡到编排层

#### Scenario: Retry-After is honored

- **WHEN** 429 响应的 `response.headers["Retry-After"]` 为 `12`
- **THEN** 下一次尝试 SHALL 至少等待 12 秒（上限 30 秒）

#### Scenario: Parse failure is not mistaken for missing schema support

- **WHEN** 裁判返回非法 JSON
- **THEN** 适配器 SHALL 抛 `ValueError`，SHALL NOT 抛 `TypeError`

#### Scenario: No default OpenAI model is constructed

- **WHEN** 构造 `Synthesizer`
- **THEN** 其 `model` SHALL 为 provider 裁判的适配器，SHALL NOT 出现 `gpt-3.5-turbo` 等默认模型调用

### Requirement: A single context failure does not abort the batch

出题 SHALL 按上下文隔离失败，SHALL NOT 依赖框架的裸 `asyncio.gather`。

- 编排层 SHALL 对每个上下文 `await a_generate_goldens_from_contexts([ctx], _reset_cost=False, _progress=..., _pbar_id=...)`；
- 外层 SHALL 使用 `asyncio.gather(..., return_exceptions=True)`，并发上限 SHALL 为 4；
- 进度 SHALL 共用一个 `Progress(disable=True)`，每个上下文分配独立 `pbar_id`；SHALL NOT 让 4 个调用各自创建 `Progress`（会争用模块级 console）；
- 单个上下文抛异常（含 JSON 解析失败、重试耗尽）SHALL 只跳过该上下文并记 warning，其余上下文 SHALL 继续产出；
- `metadata.gate_stats` SHALL 记录失败上下文数（`failed_contexts`）。

#### Scenario: One bad JSON does not kill the batch

- **WHEN** 某个上下文在 `SyntheticDataList` 解析时抛 `ValueError`
- **THEN** 其余上下文 SHALL 仍产出样本，最终文件 SHALL 写出（若总数达标）

#### Scenario: Exhausted retries stay local

- **WHEN** 某个上下文重试 3 次后仍 429
- **THEN** 只有该上下文失败，`failed_contexts` SHALL +1，命令 SHALL NOT 因它整体失败

#### Scenario: Zero surviving contexts fails loudly

- **WHEN** 所有上下文都失败
- **THEN** 命令 SHALL 非零退出且不写文件，并输出失败上下文数与最后一条错误

### Requirement: Generation is auditable and never writes a partial testset

出题 SHALL 产出可审计的元数据，并保证落盘原子性。

- `metadata` SHALL 含 `kb_id` / `kb_name` / `version` / `generated_at` / `llm_model` / `testset_size` / `doc_names` / `doc_ids` / `source_chunks` / `covered_chunks` / `company_source` / `synth_params` / `hop_stats` / `gate_stats`。
- `kb_name` SHALL 来自可选参数 `--kb-name`，缺省为空串；SHALL NOT 依赖服务层私有查询。
- **版本与语料绑定**：
  - `--version` 与 `--docs` 都省略 → 下一个版本（现有最大 + 1）+ 全部文档；
  - `--version` 省略但给了 `--docs` → 下一个版本 + 该子集（SHALL NOT 忽略用户选择）；
  - 显式 `--version` → SHALL 要求同时传 `--docs`，否则报错退出。
- `--docs` 的每一项 SHALL 至少匹配 1 个 chunk，否则非零退出；`--dry-run` SHALL 打印实际匹配到的 `doc_names` 与 `source_chunks`。
- 写入 SHALL 先写临时文件再 `rename` 到 `data/deepeval/testset/testset_<kb>_v<N>.json`；异常时 SHALL NOT 留下目标文件。
- 目标文件已存在时 SHALL 拒绝写入并退出非零，除非传入 `--force`。
- 语言门丢弃率 SHALL 按 `language_dropped / language_checked` 计算（`language_checked` = 进入语言门的样本数）；`> 50%` 时 SHALL 直接失败退出，SHALL NOT 再以 `goldens_per_context=2` 重跑。
- 通过所有门后样本数 `< 15` 时，SHALL 以 `goldens_per_context=2` 重跑一次；仍 `< 15` SHALL 失败退出，SHALL NOT 写出题集；`< 15` 的判定 SHALL 在多跳截断之后执行。
- `gate_stats` SHALL 只记**最终一轮**：`generated` / `kept` / `dropped` / `language_checked` / `self_contained_rewritten` / `failed_contexts` 与 `synth_params.goldens_per_context` 必须属于同一轮；`generated - sum(dropped) == testset_size` SHALL 成立。
- `gate_stats.dropped` SHALL 按门分项计数，且每条样本只记**第一个**未通过的门；`failed_contexts` SHALL 单独记录（不计入 `dropped`）。

#### Scenario: Partial file is never observable

- **WHEN** 出题在写入过程中抛异常
- **THEN** 目标版本文件 SHALL NOT 存在，已有旧版本 SHALL 不受影响

#### Scenario: Explicit version requires explicit docs

- **WHEN** 用户传 `--version 6` 但未传 `--docs`
- **THEN** 命令 SHALL 以非零码退出，SHALL NOT 生成或覆盖 v6

#### Scenario: Docs subset is honored without a version

- **WHEN** 用户传 `--docs a.pdf` 但不传 `--version`
- **THEN** 命令 SHALL 使用下一个版本 + 该子集，SHALL NOT 忽略 `--docs` 改用全部文档

#### Scenario: Misspelled doc name fails instead of silently shrinking the corpus

- **WHEN** `--docs` 中某一项没有匹配到任何 chunk
- **THEN** 命令 SHALL 非零退出，SHALL NOT 用剩下的文档继续出题

#### Scenario: Retry round does not double count

- **WHEN** 首轮 12 条触发 `goldens_per_context=2` 重跑并产出 18 条
- **THEN** `gate_stats` SHALL 只反映第二轮，且 `synth_params.goldens_per_context` SHALL 为 2

#### Scenario: Existing version is protected

- **WHEN** `testset_<kb>_v6.json` 已存在且未传 `--force`
- **THEN** 命令 SHALL 以非零码退出，且不修改该文件

#### Scenario: Low yield escalates once then fails

- **WHEN** 首轮通过门后只剩 12 条
- **THEN** SHALL 以 `goldens_per_context=2` 重跑一次；若仍 `< 15`，SHALL 失败退出且不写文件

#### Scenario: Language collapse fails fast without a second batch

- **WHEN** `language_dropped / language_checked` 超过 50%
- **THEN** 命令 SHALL 直接失败退出，SHALL NOT 触发 `goldens_per_context=2` 的重跑

#### Scenario: Zero language-checked samples does not divide by zero

- **WHEN** 所有样本都在自足门或完整句门被丢弃，`language_checked == 0`
- **THEN** 命令 SHALL 按语言失效非零退出，SHALL NOT 抛 `ZeroDivisionError`

#### Scenario: Each drop is attributed to one gate

- **WHEN** 某样本同时不满足语言与完整句
- **THEN** 它 SHALL 只计入**第一个未通过的门** `dropped.not_complete_sentence`（完整句门在语言门之前），SHALL NOT 同时计入 `dropped.language`

#### Scenario: Metadata reconciles generation and filtering

- **WHEN** 审阅 v6 文件
- **THEN** `gate_stats.generated - sum(gate_stats.dropped) SHALL == testset_size`
