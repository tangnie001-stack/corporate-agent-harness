## Context

评测侧已与 RAGAS 完全脱钩：`deepeval-only-eval` 关闭 RAGAS 运行，`deepeval-native-metrics` 换成原生四指标并删除 ragas 依赖、出题模块与 `POST /api/ragas/generate` 的实现（现为 410 桩）。出题能力目前为空，CLI `--generate` 命中即报错。

现有 v5 题集（21 条）是唯一的跨栈锚点：它由旧 RAGAS 出题器生成，存在多跳占 2/3、题面语言漂移（中文上下文生成英文题）、纯数值答案。这些缺陷会让 CP/CR 不可解释，换指标消除不了，必须用新出题器重做一版同语料范围的 v6。

当前 KB（`4a1dcb8bb340473c8149ead5b7f75873`）有 4 份文档、161 个分块，全部为中文（分块 CJK 占比 0.944–0.998）；v5 只用到其中 2 份（东软 2025 Q1 + 腾讯 2024 年报，51 块）。

关键约束：

- 本轮只允许"出题器"一个自变量，语料范围必须与 v5 一致。
- 出题质量必须是**确定性可检**的，不能只靠 prompt 期望。
- 评测主链（`eval_deepeval`、四指标、诊断指标、`tool_correctness`）本 change 不改。

## Goals / Non-Goals

**Goals:**

- 用 DeepEval `Synthesizer` 从 KB 分块产出 v6 题集（同 v5 的两份文档），落 `data/deepeval/testset/testset_<kb>_v6.json`。
- 六条硬约束全部有确定性校验：语言、自包含完整句、多跳 ≤ 1/3、`expected_tools`、数值风险登记、覆盖可追溯。
- 提供 CLI 出题入口，可重复执行、可审计（失败原因计数写进题集元数据）。
- 让评测报告能自证题集版本（`testset_version`），支撑 v5/v6 对照。
- 不新增第三方依赖，不改变评测侧读取面。

**Non-Goals:**

- 不扩到全量 4 份文档（留 v7）。
- 不启用 `ToolCorrectnessMetric`，不改 `score_rows` / `_bridge_test_case`（留 `tool-correctness-diagnostic`）。
- 不改造 `POST /api/ragas/generate`（维持 410，只更新提示文案）。
- 不做题集人工评审界面、不做增量出题。
- 不改 v5 文件。

## Decisions

### D1 出题路径：`generate_goldens_from_contexts`，不用 `from_docs`

- **选择**：从 DB 读分块，自己分组后调用 `Synthesizer.generate_goldens_from_contexts(...)`。
- **字段来源**：Synthesizer 只填 `Golden.context`，**`Golden.retrieval_context` 恒为 `None`**（`_a_generate_from_context` 只传 `input` / `context` / `expected_output`）。因此 `reference_contexts` 必须取 `golden.context`（或回退到我们自己渲染的组），SHALL NOT 读 `retrieval_context`。
- **替代**：`generate_goldens_from_docs(document_paths, ContextConstructionConfig(...))`——需重新解析 PDF，引入 PDF loader 依赖，且 `ContextConstructionConfig` 会再切一次块、与评测检索的分块不同源。
- **理由**：出题上下文必须与评测检索看到的分块一致，否则 CP/CR 的语义再次错位；`from_contexts` 让我们完全控制分组，也避免新增依赖。
- **代价**：不能用 Synthesizer 的 context 质量评分/自动分块，分组质量由我们负责（D2 给出确定性规则）。

### D2 上下文构造：doc 内连续 2 块成组、不重叠、尾部单块丢弃

- **规则**：按 `(doc_id, chunk_index)` 排序与分组（`source` 仅用于 `--docs` 过滤，同名文件不会串文档）；每 2 块一组；某文档总数为奇数时，末尾单块不单独成组（因为 `Multi-context` evolution 要求"用到 Context 的全部元素"，单块组会产生退化的多跳题）。
- **范围**：`--docs` **默认不过滤**（用 KB 全部文档），保证命令对任意知识库成立；v6 运行时显式传 `--docs neusoft_2025_q1.pdf tencent_2024_annual.pdf`（= v5 范围）。实测 13 + 38 块 → 6 + 19 = **25 个上下文**，覆盖 **50/51** 块。
- **替代**：一题一块（51 个上下文，题量翻倍且大量单块退化多跳）；按 `report_type` 分组（跨表结构，语义不连贯）。
- **代价**：块数为奇数的文档丢 1 块（本次仅东软 Q1；腾讯 38 块全用），`metadata.source_chunks=51` / `covered_chunks=50` 记录，不隐藏。
- **理由**：确定性、可复现、与 v5 的 51 块范围对齐，且保证每个上下文都能承载多跳 evolution。

### D3 模型适配：新增 `SynthDeepEvalLLM(BridgeDeepEvalLLM)`，支持 `schema=`

- **事实**：`Synthesizer._generate` 对非原生模型执行 `res = model.generate(prompt, schema=Response); return res.response`；`_generate_schema` 期望返回 Pydantic 实例。当前 `BridgeDeepEvalLLM.generate(prompt, **kwargs)` 吞掉 `schema` 并返回 `str` ⇒ `AttributeError: 'str' object has no attribute 'response'`（已实测）。
- **选择**：新建子类，重写 `generate` / `a_generate`：
  - `schema is None` → 调用父类行为，返回文本；
  - `schema is Response` → `Response(response=text)`；
  - 其它 schema → `schema(**trimAndLoadJson(text))`（用 `deepeval.metrics.utils.generation.trimAndLoadJson`，兼容 ```json 包裹）；
  - 解析失败 → 抛 `ValueError`（**不能**抛 `TypeError`：Synthesizer 把 `TypeError` 当"模型不支持 schema"的信号，会走另一条路径）。
- **重试（必需，不是优化）**：裁判固定 `max_retries=0`，而本路径没有外层重试。适配器 SHALL 在单次调用内做 3 次有界重试：
  - **异常类型**：`ChatOpenAI.invoke` 抛的是 `openai.RateLimitError` / `openai.InternalServerError`（均属 `openai.APIStatusError`）、`openai.APITimeoutError`、`openai.APIConnectionError`，**都不是 httpx 异常**（MRO 已核对）。因此重试判定 SHALL 捕获 `openai.APIStatusError`（按 `status_code` 判 429/5xx）+ `openai.APITimeoutError` + `openai.APIConnectionError`，`httpx.TimeoutException` / `httpx.TransportError` 只作兜底；
  - 优先取 `exc.response.headers["Retry-After"]`（有限且 `>= 0`，上限 30s），否则指数退避 `0.5 × 2^(n-1)` + 0~0.25s jitter，上限 8s；
  - 4xx（除 429）与 JSON 解析失败**不重试**，直接抛出。
- **批量隔离（必需）**：`a_generate_goldens_from_contexts` 用裸 `asyncio.gather`（无 `return_exceptions`），`_a_generate_from_context` 与 `_a_generate_schema` 都不捕获 `ValueError`（`_a_generate_schema` 只捕获 `TypeError`）⇒ 一坏 JSON / 一次最终 429 会让整批作废。**不要依赖框架的批量入口**：由 `testset_synth` 对每个上下文单独调用 `a_generate_goldens_from_contexts([ctx], _reset_cost=False)`，外层用 `asyncio.gather(..., return_exceptions=True)` + 信号量 4；失败的上下文记日志并跳过，其余继续产出。
- **替代**：直接改 `BridgeDeepEvalLLM`（影响原生指标路径，回归面大）；返回 `str` 等 Synthesizer fallback（`**kwargs` 签名使其永远不触发 TypeError）；整批重试（一次 429 重跑全部上下文，代价最大）。
- **理由**：出题 schema（`SyntheticDataList` / `Response` / `SyntheticData`）与指标判定 schema 不同，隔离在子类可避免指标路径回归；按上下文拆调用把"全有或全无"变成"尽量多产出"。
- **代价**：多一个类、一层重试与一层编排；Synthesizer 的 JSON 解析失败不再有质量重试兜底，只能丢弃该上下文，日志需带原始响应片段。

### D4 语言：prompt 硬约束 + 确定性校验双保险

- **prompt 侧**（`StylingConfig`，字段会被拼成 `MUST strictly adhere to` 的硬指令）：
  - `scenario`：财务分析师阅读 A 股/港股上市公司定期报告
  - `task`：基于给定中文年报/季报上下文提出可用该上下文回答的问题
  - `input_format`：简体中文问句，一句话，必须包含公司名称与报告期，不得出现英文句子或中英拼接
  - `expected_output_format`：简体中文完整陈述句，包含主体、指标与数值/单位（如适用），不得为裸数字
- **校验侧**（`src/cli/synth_gates.py`，确定性；在自足改写**之后**执行，改写引入的前缀也会被复查）：
  1. **纯英文硬规则在原始句上执行**：按 `。？！.!?` 切句，任一句「拉丁字母 ≥5 且 CJK=0」直接失败——即使该句所有词都出现在上下文，也不能靠剔除绕过；
  2. **句级比例先去掉括号内容再算**：按 `。？！.!?` 切句，凡字母数 ≥5 的句子 CJK 占比须 ≥0.5（否则 `腾讯控股有限公司（Tencent Holdings Limited）2024年...` 会被误杀）；
  3. **整体比例**：去括号后，按 `(?<![A-Za-z0-9])实体(?![A-Za-z0-9])`（CJK 视为边界，大小写不敏感）剔除上下文中的拉丁实体名 + D5 公司别名；分母 = CJK + 拉丁字母，0 分母判失败；整体 `>= 0.85`；
  4. 题面与参考答案都要过；题面还要对**自足改写前**的原文再判一次（gate wrapper 在调用 `enforce_self_contained` 前 stash `original_user_input`，不写入最终 JSON）。
  5. 不过者**丢弃**，不翻译（翻译会引入新语义偏差）。
- **替代**：只靠 `input_format`（概率行为，v5 已证明会漂移）；生成后统一翻译（多一次 LLM、答案可能被改写）。
- **理由**：prompt 负责提高命中率，校验负责把语言一致性变成受检保证；括号先剔除避免英文别名误杀，`(?<![A-Za-z0-9])` 解决「报告中SAP SE的…」因 CJK 与拉丁都属 `\w` 而无词边界的问题。

### D5 自足门：复用 `enforce_self_contained`，并给每个上下文加【公司 · 报告期】头

- **头构造**：每个上下文首行拼 `【{company} · {period}】`，与 v5 的 `reference_contexts` 结构一致，`enforce_self_contained` 可直接复用。
- **company SHALL 按文档推导一次，不得逐 chunk 回退**：
  1. `metadata["company"]` 含 CJK → 直接用；
  2. 否则先扫描该 `source` 全部 chunk 的 `parent_content`；该文档仍无匹配时再扫描 `content`；取匹配 `[\u4e00-\u9fff]{2,12}(?:股份有限公司|有限责任公司|有限公司)` 的候选；
  3. 对候选**剥掉叙述性前缀**（`本公司` / `公司` / `子公司` / `控股股东` / `股东` / `持有` / `的`，可重复），得到公司名；
  4. 多个候选时 SHALL 按 `chunk_index` 升序取**第一个剥完前缀后长度 ≥4** 的候选（文档标题通常在 chunk 0），SHALL NOT 取股东/子公司名；
  5. 仍未找到 → **整个命令失败退出**，SHALL NOT 把 ASCII 名（如 `tencent`）写进 header。
  - 依据：实测腾讯 38 块中只有 5 块的 `parent_content` 前 500 字含中文全称，逐 chunk 推导会让 16/19 组退化成 `【tencent · 2024年】`；同时正则无左边界会把「本公司的控股股东东软集团股份有限公司」整段抓进来。
- **period**：`metadata["report_period"]` 优先；缺失时用 `source` 中的年份 + 后缀，后缀表 `annual→年年度`、`q1→年第一季度`、`q2→年第二季度`、`q3→年第三季度`、`q4→年第四季度`、`h1→年上半年`、`h2→年下半年`，例如 `tencent_2024_annual.pdf → 2024年年度`。有年份但后缀未命中 → `{year}年`；无年份且无 `report_period` → 用 `metadata["report_type"]` / `["quarter"]` 兜底，仍为空则报错退出。SHALL NOT 只写「年度」而丢掉年份。
- **别名**：`{全称, 逐级去后缀}`，后缀表**最长优先**：`股份有限公司 / 有限责任公司 / 控股有限公司 / 有限公司 / 集团`；递归剥离，长度 ≥2 才加入。例：`东软集团股份有限公司 → 东软集团 → 东软`，`腾讯控股有限公司 → 腾讯`（`控股有限公司` 必须先于 `有限公司` 匹配，否则停在 `腾讯控股`）。同时把 `metadata["company"]` 的 ASCII 形（`tencent`）并入别名，供英文题面命中。
- **失败语义**：沿用既有实现——单公司可保义改写（前缀中文公司名），多公司或无锚点直接丢弃。
- **代价**：公司名推导是启发式，推导结果写进 `metadata.company_source`（`metadata` / `parent_content`）以便审计；推导失败是硬失败，不做静默降级。

### D6 多跳定义：`Multi-context` = multi_hop，其余 = single_hop

- **依据**：`MultiContextEvolution` 的 prompt 明确要求"use information from all elements of `Context`"，即必须同时用到 ≥2 个分块；`Reasoning` 是在单块内多步推理，检索仍是一次。
- **标签是代理，不是证明**：`evolutions_used` 只记录被抽中的 evolution，不校验模型是否真的用了两块。因此每条样本 SHALL 同时写 `hop_basis: "evolution"`，`hop_type` 只作为**筛选代理**；报告/台账 SHALL NOT 把 `multi_hop` 当作"已证明跨块推理"。
- **evolution 配置**：`num_evolutions=1`（每条只施加一种），权重 `Multi-context=0.28`，其余六种各 `0.12`（和 = 1.0）。默认 1/7 只有 14%，多跳样本太少；0.28 留出筛选余量且低于 1/3 上限。
- **比例门**：最终 `multi_hop / total > 1/3` 时，按 `sha1(user_input)` 升序丢弃多跳样本到刚好 ≤ 1/3。**SHALL NOT 按生成顺序/尾部截断**——`goldens` 的顺序来自各 context task 的完成顺序，按尾部丢会系统性偏向某一份文档。
- **理由**：`hop_type` 必须由可观测字段（`additional_metadata["evolutions"]`）推导，不能靠 LLM 自述；确定性哈希顺序保证同输入重跑得到同一题集。

### D7 `expected_tools` 统一标注 `["retrieve_kb"]`

- **理由**：一次 `retrieve_kb` 返回 top-k 分块，多跳题不等于多次工具调用。若给多跳题标两个 `retrieve_kb`，`ToolCorrectnessMetric` 会要求实际调用两次，与 Agent 行为不符。
- **存储**：JSON 里是 `list[str]`，不是 DeepEval `ToolCall`；转换由 `tool-correctness-diagnostic` 负责。
- **多跳的可检性**由 `hop_type` + `evolutions` 承载，不塞进 `expected_tools`。

### D8 数据契约：v6 只在 v5 结构上追加字段

```json
{
  "metadata": {
    "kb_id": "...", "kb_name": "", "version": 6,
    "generated_at": "ISO8601", "llm_model": "...",
    "testset_size": 21,
    "doc_names": ["neusoft_2025_q1.pdf", "tencent_2024_annual.pdf"],
    "doc_ids": ["..."],
    "source_chunks": 51, "covered_chunks": 50,
    "company_source": {"neusoft_2025_q1.pdf": "metadata", "tencent_2024_annual.pdf": "parent_content"},
    "synth_params": {"chunks_per_context": 2, "goldens_per_context": 1,
                     "max_concurrent": 4, "evolution_weights": {"...": 0.28}},
    "hop_stats": {"single_hop": 16, "multi_hop": 5},
    "gate_stats": {"generated": 24, "kept": 21,
                   "language_checked": 23, "self_contained_rewritten": 2,
                   "failed_contexts": 1,
                   "dropped": {"language": 1, "self_contained": 0,
                               "grounding": 0,
                               "not_complete_sentence": 1, "duplicate": 1,
                               "hop_ratio": 0}}
  },
  "samples": [{
    "user_input": "...", "reference": "...",
    "reference_contexts": ["【东软集团股份有限公司 · 2025年第一季度】\n..."],
    "expected_tools": ["retrieve_kb"],
    "hop_type": "single_hop",
    "hop_basis": "evolution",
    "evolutions": ["Concretizing"],
    "source_file": "neusoft_2025_q1.pdf",
    "numeric_risk": true,
    "synth_quality": 0.83
  }]
}
```

- `testset_io.load_testset_samples` 只读 `user_input` / `reference`，新增字段不影响读取。
- 不写 `ragas_version`；`llm_model` 记录裁判/出题模型名。
- `kb_name` 由 `--kb-name` 可选参数提供，默认空串；**不**调 `AppService` 私有 repo 反查（`kb_name` 只是可读性字段，不值得引入跨层依赖）。

### D9 质量门顺序：自足 → 锚定 → 完整句 → 语言 → 去重 → 多跳比例

- 顺序：自足改写会改题面，必须排在语言门之前，改写结果才会被语言门复查；锚定门紧随其后，防止改写掩盖错误年份/外来公司；完整句排在语言前，使裸数字答案被正确归因为 `not_complete_sentence` 而不是 `language`。
- **锚定门（实测补充）**：题面 SHALL 包含该上下文报告期的年份；题面中「实体 + 年份」的实体部分 SHALL 出现在 `reference_contexts` 或公司别名中。否则记为 `dropped.grounding`。依据：v6 首轮出现「东软集团2024年…」（上下文是 2025 Q1）与「腾讯…贵州茅台2024年…」（外来公司）两类题，语言/自足门都放行。
- **计数语义：首次失败即归类**。一条样本只记一个 `dropped` 键（第一个未通过的门），保证 `generated - sum(dropped) == testset_size` 可对账；复合失败分布不追求。
- 去重：题面去标点/空白后小写，完全相同即丢弃（只做精确去重，不做语义相似度）。
- 完整句判定：`len >= 8`、以 `。！？.!?` 结尾（接受 ASCII 标点，使英文答案落到语言门而不是被误记为残句）、且非纯数字/标点串；`expected_output_format` 显式要求"以句号结尾"。
- **数值型答案的主体检查**：
  - 定义「数值事实」= 第一个**后接单位/百分号/金额词**（元/万元/亿元/%/个百分点/倍）的数字；定位时跳过日期与期间成分（`19xx`/`20xx` 年、`x月`、`x日`、`Q1-Q4`、`H1/H2`、`1-9月` 区间）；
  - `numeric_risk=true` 时，该数字之前 SHALL 出现**主体词**（公司 / 集团 / 本集团 / 母公司 / 归属于…股东 / 题干公司别名），且整句 SHALL 含 `EVAL_NUMERIC_METRIC_WORDS` 中至少一个指标词（营业收入 / 收入 / 营收 / 营业额 / 净利润 / 毛利 / 经营盈利 / 资产 / 负债 / 现金流 / 资本开支 / 持股比例 / 每股收益 等，清单入 `src/config/const.py`）；
  - 只有指标词、没有主体（如 `营业收入18.5亿元。`）→ 不通过；
  - 不满足 → 记为 `not_complete_sentence`。例：`腾讯2024年营业收入为人民币18.5亿元。`、`2024年12月31日，公司实现营业收入18.5亿元。` 必须通过；`为18.5亿元。`、`营业收入18.5亿元。` 必须丢弃。
- `numeric_risk`：参考答案含数字且含单位/百分号/金额词（元/万元/亿元/%/个百分点）→ `true`，仅登记，不淘汰（但会触发上一条主体检查）。

### D10 CLI 与 API：新增独立出题命令，API 维持 410

- **入口**：`python -m src.cli.testset_synth --kb-id <uuid> [--version 6] [--docs a.pdf b.pdf] [--kb-name 名称] [--force] [--dry-run]`。
- **版本与语料绑定**：
  - `--version` 省略且 `--docs` 省略 → 下一个版本（现有最大 + 1）+ 全部文档；
  - `--version` 省略但给了 `--docs` → 下一个版本 + **该子集**（不忽略用户选择）；
  - 显式 `--version` → 必须同时给 `--docs`，否则报错退出。这样版本化运行（尤其 v6）天然可复现。
- **文档名校验**：`--docs` 的每一项 SHALL 至少匹配到 1 个 chunk，否则非零退出；`--dry-run` SHALL 打印实际匹配到的 `doc_names` 与 `source_chunks`，拼写错误在 dry-run 就暴露。
- **替代**：在 `eval_deepeval` 加 `--synth`——会让评测 CLI 同时承担长任务出题，参数与退出语义混在一起。
- **API**：`POST /api/ragas/generate` 继续返回 410，文案改为指向上述命令。理由：出题是分钟级长任务，需要任务队列/进度接口，本 change 不做；给一个会超时的同步接口比 410 更糟。
- **覆盖保护**：目标文件已存在时拒绝写入，需 `--force`；`--dry-run` 只打印分组、实际 `doc_names`、目标路径与调用量（含 style rewrite），并在目标已存在时提示「正式运行需 `--force`」，不调 LLM。
- `--generate` 的报错文案同步指向新命令。

### D11 并发、编排与重试：按上下文拆调用

- `Synthesizer(max_concurrent=4)`：默认 100 会打出 429 风暴。
- **编排在 `testset_synth`，不在框架**：每个上下文单独 `await a_generate_goldens_from_contexts([ctx], _reset_cost=False, _progress=..., _pbar_id=...)`，外层 `asyncio.gather(return_exceptions=True)` + 信号量 4；失败上下文记日志继续。理由见 D3（框架的裸 `gather` 没有异常隔离）。
- **`_send_data` 不是 async 方法的参数**：`a_generate_goldens_from_contexts` 的签名没有它（只有同步入口有），传入会 `TypeError`；async 路径本身不上传，SHALL NOT 传。
- **进度条**：4 个 context 并发时，每次调用若各自建 `Progress` 会共用模块级 `custom_console`，多个 Rich `Live` 互相踩踏。编排层 SHALL 建**一个** `Progress(disable=True)`，为每个上下文 `add_task` 得到 `pbar_id`，通过 `_progress` / `_pbar_id` 传入；进度只由 `testset synth` 事件汇报。**接受的代价**：框架 `remove_pbars` 每个 context 同步 `sleep(0.1)`，25 个约 2.5s 的 loop 阻塞，不修正。
- 裁判本身 `max_retries=0`；由 `SynthDeepEvalLLM` 对 429 / 5xx / 超时做 **3 次重试（共 4 次尝试）**，`generate` 与 `a_generate` 共用同一策略，async 等待用 `await asyncio.sleep`（SHALL NOT 用阻塞 `time.sleep`）。
- `FiltrationConfig(max_quality_retries=2)` 只处理"质量不合格重写"，与传输层重试互不替代。
- **调用量**：每上下文 1 次 input + 最多 2 轮 critic/rewrite（4 次）+ 1 次 evolution + 1 次 style rewrite + 1 次 expected output = **5–8 次**；单轮 125–200 次，`goldens_per_context=2` 重跑约 250–400 次。
- **事件循环**：`main()` 保持同步，但内部只有**一个** `asyncio.run(run_and_dispose(_run()))`，在同一 loop 内完成"读 DB + 25 个 context 的 async gather + 写盘"，并在结束时 dispose engine（避免同进程第二次调用复用已关闭 loop 的连接池）；SHALL NOT 在读 DB 的 loop 关闭后再调框架的同步入口，也 SHALL NOT 在已运行的 loop 里调同步入口。
- **import 顺序**：`testset_synth` / `deepeval_synth_llm` SHALL 先 `import src.config.settings`（设置 `DEEPEVAL_DISABLE_DOTENV` / `DEEPEVAL_TELEMETRY_OPT_OUT`）再 import `deepeval`，避免遥测与 `.env` 加载时序被框架抢先。
- 单次调用超时沿用 `JUDGE_HTTP_TIMEOUT_*`（细粒度 read 超时），不新增超时配置。

### D12 观测定点：三个 info 事件 + 失败上下文 warning

- `testset synth start`（kb_id / docs / contexts / 预估调用量区间）、`testset synth gate`（generated / kept / dropped / failed_contexts / language_checked）、`testset synth done`（path / size / 耗时）。
- **不登记 debug 级事件**：`_ALLOWED_LEVELS` 只有 `info/warning/error`，登记 `debug` 会在 import 期断言失败。逐上下文成功不打日志；失败登记 `testset synth context failed`（warning，调用点守卫，仅失败时记）。
- **登记顺序**：先在 `src/core/log_events.py` 的 `LOG_PREFIXES` 加 `"synth"`、在 `Event` 枚举加事件，再登记 `EVENT_SPECS` 与 `docs/agents/logging-rules.md`；否则 `_validate_registry` 会在 import 期断言失败，CLI 直接起不来。

### D13 失败即不落盘

- 任一阶段抛异常 → 不写 v6 文件（写临时文件后 `rename`），避免留下半成品被 `_load_latest_testset` 当成最新版本。
- **检查顺序**：先判「所有上下文失败 / `generated == 0`」→ 非零退出；再判「`language_checked == 0`」→ 视为 100% 语言失效，非零退出（不得做除法）；之后才计算 `language_dropped / language_checked`。
- **语言整体失效早停**：分母 SHALL 为 `language_checked`（进入语言门的样本数，= `generated - 自足丢弃 - 完整句丢弃`），而非 `generated` 或 `kept`；`language_dropped / language_checked > 50%` 时直接失败退出，**不做** `goldens_per_context=2` 的 2× 重跑——这是 `StylingConfig` 约束失效，不是题量不足，重跑同一参数不会变好。
- 产出 `< 15` 题时：用 `goldens_per_context=2` 重跑一次（**同一组上下文**，数量随 `--docs` 而定），仍 `< 15` 则报错退出并保留日志，不降级写文件；`< 15` 的判定 SHALL 在多跳截断**之后**执行。
- `kept` 落在 `15–18` 时允许落盘，但台账必须标注"题量偏低"，避免与 v5 的 21 条直接比较时被误读。
- **`gate_stats` 只记最终一轮**：`generated` / `kept` / `dropped` / `language_checked` / `self_contained_rewritten` / `failed_contexts` 均来自实际写盘的那一轮；`synth_params.goldens_per_context` 记实际使用值（1 或 2），保证等式仍成立。

### D14 报告记录测试集版本

- **单一版本扫描源**：`testset_io._scan_versions(kb_id) -> list[int]` 供三处复用，SHALL NOT 在 `resolve` 与 `load` 各写一套正则。
- `resolve_testset_version(kb_id, version) -> int`（评测用）：显式版本**不做存在性校验**（交给 loader），`None` 时取最大版本；无文件时抛 `FileNotFoundError`。`main()` 把它放在现有 `try` 内（保证 `finally: flush_tracing()` 仍执行），resolved 值同时传给 loader 与报告。
- `next_testset_version(kb_id) -> int`（出题用）：最大版本 + 1；SHALL NOT 复用 resolve（出题允许目标文件不存在）。
- `build_report_meta(..., testset_version: int | None = None)`：**两个返回点**（`ragas_enabled=False` 早返回与正常返回）都写入该键。
- `save_markdown_report`：在 `report_meta is not None` 内渲染 `**Testset version:**`；`None` 时写 `N/A` 或不写，SHALL NOT 裸取键。
- `_build_eval_detail(..., testset_version: int | None = None)`、`_save_eval_report(..., testset_version: int | None = None)`：新参数一律**带默认值的关键字参数**。`testset_version=None` 时**仍然 insert**，逐题条目写 `testset_version: null` 并记 warning；SHALL NOT 因缺版本跳过落库（否则 `test_overall_score` / `test_eval_deepeval` 的现有断言会失败）。
- DB `detail_json` 逐题条目带 `testset_version`，不需要 schema 变更；CSV 加列会破坏现有消费方，**不加**。
- 理由：v6 落盘后 `--testset-version` 默认取最新，后续评测会静默换卷；报告/DB 不记版本就无法自证 v5/v6 的归属。这是本 change 唯一的评测侧改动，不触碰指标与评分口径。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| Synthesizer 内部 prompt 为英文，仍可能输出英文题 | `StylingConfig` 硬约束 + D4 确定性门；丢弃率写入 `gate_stats`，不静默 |
| 公司名推导错误（尤其中文名不在 metadata 的文档） | 按文档扫描 `parent_content or content`；剥叙述性前缀；记录 `company_source`；找不到中文全称直接失败，不写 ASCII header |
| 报告期丢年份（腾讯 `report_period` 为空） | 从 source 提取年份后拼后缀（`2024年年度`），验收断言 header 含 `2024` |
| 4 个 context 并发时 Rich `Progress` 争用同一 console | 编排层只建一个 `Progress(disable=True)`，每个 context 分配 `pbar_id`，通过 `_progress` 传入 |
| 尾部单块被丢弃，覆盖 50/51 | `source_chunks` / `covered_chunks` 显式记录，验收时可见 |
| 25 上下文 × 5–8 次调用 ≈ 125–200 次请求，429 或超时 | `max_concurrent=4` + 适配器 3 次有界重试 + **按上下文隔离**（单点失败不拖垮整批）；`--dry-run` 给区间 |
| Synthesizer 对 JSON 解析失败的处理路径依赖 `TypeError` 信号，且 `gather` 不隔离 | 适配器抛 `ValueError`；编排层逐上下文调用并捕获异常，失败上下文跳过 |
| 多跳比例在生成后才知道，可能超 1/3 | 权重预设 0.28 + 按 `sha1` 确定性截断；`hop_stats` 记录实际值 |
| `hop_type` 只证明抽中 `Multi-context`，不证明真跨块 | 样本写 `hop_basis: "evolution"`；台账注明是代理标签，不宣称"已证明多跳" |
| v6 题量与 v5 不同（21 vs 目标 ≥15），分数对比受题量影响 | 对比只看**均值**，不看总分；题量写入台账；两版都按 `run_type=baseline` 跑，不判门禁 |
| 出题调用会产生费用 | `--dry-run` 可先看上下文数与预估调用量（单轮 125–200 次；2× 重跑 250–400 次） |

## Migration Plan

1. 代码合并后无需迁移：v6 是新增文件，v5 保留。
2. 首次运行：`python -m src.cli.testset_synth --kb-id 4a1dcb8bb340473c8149ead5b7f75873 --version 6 --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf --kb-name test`。
3. 校验：`metadata.gate_stats` + `hop_stats`，再跑 `python -m src.cli.eval_deepeval --kb-id ... --testset-version 6 --run-type baseline`（评测侧已在 change A 之后可用）。
4. 报告头应出现 `testset_version: 6`；台账同时记录 v6 与 v5 的版本号，避免"最新版本"歧义。
5. 回滚：删除 v6 文件即可；评测默认取最新版本，删后自动回落到 v5。不涉及 DB schema、不涉及生产链路。

## Open Questions

- 无阻塞项。评审提出的五项已在正文收敛：
  1. 语料冻结改为机制保证（D10：显式 `--version` 必须同时给 `--docs`）；
  2. 事件循环与 import 顺序见 D11；
  3. 评分消费面已在 `evaluation-pipeline` delta 中改为「只消费 user_input / reference，检索上下文来自运行期」；
  4. `gate_stats` 口径见 D13（只记最终一轮）；
  5. 归档顺序见 tasks 9.3（`deepeval-native-metrics` 先归档）。
