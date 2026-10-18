## Why

评测侧已与 RAGAS 脱钩（`deepeval-only-eval` 关运行、`deepeval-native-metrics` 换原生指标并移除依赖），出题也必须前迁，否则项目会永久失去"出题"能力：

- 旧出题模块 `src/cli/eval_ragas_generate.py` 依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`，在 0.3.9 下必然失败；该模块已随 `deepeval-native-metrics` 删除。
- `POST /api/ragas/generate` 与 CLI `--generate` 目前都是"能点但必失败"的状态，必须给出真正的替代路径。
- DeepEval 自带 `Synthesizer`：`generate_goldens_from_contexts(...)` 产出 `Golden`（`input` / `expected_output` / `retrieval_context`），与评测输入同构，**同栈、同数据结构**，无需为出题单独维护 ragas 管道。

现有 v5 题集（`data/deepeval/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json`，21 条）的实测数据质量问题，正是新出题器必须规避的：

| 问题 | 实测 |
|---|---|
| 多跳题占 2/3 | `single_hop_specific` 7 / `multi_hop_abstract` 7 / `multi_hop_specific` 7 |
| 题面语言漂移 | 索引 `[7,8,9,13,14]` 出现英文/中英拼接题（如「what did the 经审核的财务报表 show…」），但其上下文是**中文**——KB 四份文档分块 CJK 占比 0.944–0.998，**不存在"中英混装"语料** |
| 纯数值型参考答案 | 索引 `[7,13,14]`，参考答案是一串数字、非完整句 |
| 语料覆盖窄 | v5 只用到 4 份文档中的 2 份（51/161 分块），是旧 RAGAS 白名单遗留，非设计选择 |

这些直接对应 `deepeval-bridge-eval` 的 R11（faithfulness 不可复现）与 CP 不可解释，换指标不会消除。

## What Changes

### 范围冻结（本轮只允许一个变量：出题器）

1. **语料范围 = v5 的两份文档**（`neusoft_2025_q1.pdf` + `tencent_2024_annual.pdf`）。不扩到全量 4 份：v5 → v6 只允许"出题器"一个自变量，否则分数变化无法归因。扩文档作为独立变更（v7）验证。`--docs` 的**默认值是全部文档**（命令对任意 KB 都成立）；v6 运行时显式传这两份，范围冻结是**本次运行参数**而非命令默认语义。
2. **语言策略 = 按题校验，不拆库**。KB 实际是纯中文语料，"中英混装就拆库"解决的是不存在的问题；真实故障是出题器从中文上下文生成了英文题。
3. **ToolCorrectness 不在本 change**，拆至 `tool-correctness-diagnostic`。本 change 只在 v6 题集里产出 `expected_tools` 数据；评测侧构造 `ToolCall` 与接入诊断指标由该 change 消费。

三条的共同逻辑：冻结语料、收紧语言、切开评测改造，把 v6 的验收范围压到"题集质量"一项；评测侧只加一个 `testset_version` 字段用于归属，不动指标与评分口径。

### 出题实现

- 新增出题实现，按职责拆为四个模块：`src/cli/synth_contexts.py`（分块读取、分组、锚点头）、`src/cli/synth_gates.py`（语言/完整句/自足/去重/多跳比例）、`src/cli/synth_writer.py`（样本转换、元数据、原子写）、`src/cli/testset_synth.py`（编排与 CLI）。
- 产出沿用现有 `testset_<kb>_v<N>.json` 结构（`samples[].user_input` / `reference` / `reference_contexts`），**不改变** `testset_io.load_testset_samples` 的读取面。
- 用 KB 分块而非 `generate_goldens_from_docs` 重解析 PDF：语料与评测检索同源，也避免再引入 PDF loader 依赖。
- CLI 新增**独立出题命令** `python -m src.cli.testset_synth`；`--generate` 与 `POST /api/ragas/generate` 维持失败/410，只把指引改到新命令（不新增同步出题 API）。
- **版本与语料绑定**：`--version` 与 `--docs` 都省略 → 下一个版本 + 全部文档；只给 `--docs` → 下一个版本 + 该子集；显式 `--version` → 必须同时给 `--docs`，否则报错。每个 `--docs` 项必须匹配到分块，拼错即失败。
- 裁判调用在适配器内做**有界重试**（仅 `openai` 的 429 / 5xx / 超时，优先 `Retry-After`），并**按上下文隔离失败**：框架的裸 `gather` 没有异常隔离，一次 429 或一个坏 JSON 会让整批作废。
- 语言整体丢弃率 > 50% 时**直接失败退出**，不做 2× 重跑——那是语言约束失效，不是题量不足。

### 出题硬约束（本 change 的验收项，逐条可检）

1. **题面/答案语言 = 其 `reference_contexts` 主语言**：纯英文硬规则在原始句上执行（拦整句英文），句级比例与整体比例先去括号；整体按「CJK 字母 / (CJK + 拉丁字母)」判定（阈值 ≥ 0.85）；实体剔除用 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])`（CJK 视为边界、大小写不敏感）；0 字母输入判失败。
2. **参考答案为自包含完整句**：不得是裸数值/残句；数值型事实须带主体与指标词（如「归属于上市公司股东的净利润」），并有确定性检查。
3. **多跳比例受控**：`Multi-context` 记为 `multi_hop`、其余为单跳，占比 **≤ 1/3**；样本同时写 `hop_basis="evolution"`（代理标签，不宣称已证明跨块）；超出时按题面哈希确定性截断，不按文档尾部丢弃。
4. **带 `expected_tools` 标注**：每题统一标 `["retrieve_kb"]`（多跳不重复标——一次检索返回 top-k，不代表多次工具调用）；本 change 只产出数据，消费方是 `tool-correctness-diagnostic`。
5. **数值/单位/限定词显式登记**：题集中记录是否含需跨语言或单位换算才能判定的事实（对应 R11 病因①②）。
6. **语料覆盖可追溯**：`metadata.doc_names` / `doc_ids` / `company_source` / `source_chunks` / `covered_chunks` 与每题的 `source_file` 保留（v6 首版 = 两份文档 50/51，KB 全量 50/161）。

### 与 v5 的关系

- **v5 不删**：它是唯一的跨栈锚点。`v5 + 原生指标` 基线已在 2026-10-17 跑完（`run_type=baseline`），v6 生成后直接对照。
- 因语料范围已冻结，v5 → v6 的分数差异只解释为**题集差异**，不掺入覆盖范围变化。
- ragas 出题残留已在 `deepeval-native-metrics` 清理，本 change 只补新实现。

## Capabilities

### New Capabilities

- `testset-generation-deepeval`：用 DeepEval `Synthesizer` 从 KB 分块出题，产出与评测输入同构的 testset JSON；含上述 6 条硬约束、逐题语言校验、多跳比例控制、`expected_tools` 标注、覆盖可追溯。

### Modified Capabilities

- `evaluation-pipeline`：出题路径由「ragas 0.4.x（已下线）」改为「DeepEval Synthesizer」。本 change 不改指标构成与评分逻辑。

## Impact

- **代码**：新建 `src/cli/synth_contexts.py` / `synth_gates.py` / `synth_writer.py` / `testset_synth.py`，以及 `src/infra/llm/provider/deepeval_synth_llm.py`（schema 适配 + 有界重试）；`src/cli/eval_deepeval.py`（`--generate` 文案、`build_report_meta` / `save_markdown_report` / `_build_eval_detail` / `_save_eval_report` 增加 `testset_version`）；`src/cli/testset_io.py`（版本解析 + 报错文案）；`src/config/response_codes.py`（`Code.GONE_MSG` 指向新命令）；`src/core/log_events.py`（登记 `synth` 前缀）。
- **依赖**：`deepeval` 已含 `synthesizer`，**不新增第三方依赖**。
- **数据**：新增 `data/deepeval/testset/testset_<kb>_v6.json`（v5 保留）；题集结构新增 `expected_tools` / `hop_type` / `hop_basis` / `evolutions` / `source_file` / `numeric_risk` / `synth_quality` 字段（loader 忽略未知字段即可）。
- **评测**：不改指标构成与评分口径。仅补 `testset_version`（报告头 + `report_meta` + DB `detail_json` 条目），使报告/落库能自证用的是哪一版题集；`tool_correctness` 在 v5 / v6 上均仍为 `null`，由 `tool-correctness-diagnostic` 接入。
- **文档**：`docs/agents/eval-ledger.md`（v6 行）、`docs/agents/api_contract.md`（出题接口）、`requirements_pool.md`（扩文档 v7 登记）。
- **不改**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端。
