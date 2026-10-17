## Why

评测侧已与 RAGAS 脱钩（`deepeval-only-eval` 关运行、`deepeval-native-metrics` 换原生指标并移除依赖），出题也必须前迁，否则项目会永久失去"出题"能力：

- `src/cli/eval_ragas_generate.py` 依赖 ragas 0.4.x 的 `default_transforms_for_prechunked`，**在 0.3.9 下必然失败**；`deepeval-native-metrics` 会连依赖一起删除。
- `POST /api/ragas/generate` 与 CLI `--generate` 目前都是"能点但必失败"的状态，必须给出真正的替代路径。
- DeepEval 自带 `Synthesizer`：`generate_goldens_from_docs(...) -> List[Golden]`，产出字段（`input` / `expected_output` / `retrieval_context`）与评测输入同构，**同栈、同数据结构**，无需为出题单独维护 ragas 管道。

现有 v5 题集（`testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json`，21 条）的实测数据质量问题，正是新出题器必须规避的：

| 问题 | 实测 |
|---|---|
| 多跳题占 2/3 | `single_hop_specific` 7 / `multi_hop_abstract` 7 / `multi_hop_specific` 7 |
| 英文/中英混杂 | 索引 `[7,8,9,13,14]`，如「what did the **经审核的财务报表** show…」 |
| 纯数值型参考答案 | 索引 `[7,13,14]`，参考答案是一串数字、非完整句 |
| 同一 KB 中英混装 | 东软（中文）与腾讯（英文）两份文档在同一知识库 |

这些直接对应 `deepeval-bridge-eval` 的 R11（faithfulness 不可复现）与 CP 不可解释，换指标不会消除。

## What Changes

- 新增出题模块（建议 `src/cli/testset_synth.py`）：`Synthesizer.generate_goldens_from_docs(...)` → 转成现有 `testset_<kb>_v<N>.json` 结构（`samples[].user_input` / `reference` / `reference_contexts`），**不改变** `_load_latest_testset` 的读取面。
- CLI 新增**出题入口**（如 `--synth`），与 `--deepeval-only` / `--generate`（保留为下线提示）区分；`POST /api/ragas/generate` 改为指向新出题器（或同步下线，二选一在 design 定）。
- **出题硬约束（本 change 的验收项，逐条可检）**：

  1. **语料语言统一**：一个 KB 只出同一种语言的题；中英混装的库要在出题前拒绝并提示先拆库。
  2. **题面语言与语料一致**：不得出现「what did the 经审核的财务报表 show」这类中英拼接题。
  3. **参考答案为自包含完整句**：不得是裸数值/残句；数值型事实须带主体与限定词（如「归属于上市公司股东的净利润」）。
  4. **多跳比例受控**：`multi_hop_*` 占比 **≤ 1/3**，其余为单跳；在题集元数据中记录每题的 `hop_type`。
  5. **带 `expected_tools` 标注**：每题标注完成所需工具（如 `["retrieve_kb"]`），供 `ToolCorrectnessMetric` 使用。
  6. **数值/单位/限定词显式登记**：题集中记录是否含需跨语言或单位换算才能判定的事实（对应 R11 病因①②）。

- **v6 与 v5 并存**：v5 不删，它是唯一的跨栈锚点；v6 生成后先跑 `v5 + 新指标` 基线，再跑 `v6 + 新指标` 对照（两步不可颠倒）。
- **启用 `ToolCorrectnessMetric`**：从 v6 起进入诊断指标（不进主分/门禁）；v5 上保持 `N/A`。
- 清理 ragas 出题残留：`eval_ragas_generate.py` 已在 `deepeval-native-metrics` 删除，本 change 只补新实现。

## Capabilities

### New Capabilities

- `testset-generation-deepeval`：用 DeepEval `Synthesizer` 从 KB 文档出题，产出与评测输入同构的 `Golden` / testset JSON；含上述 6 条硬约束、`expected_tools` 标注、语言一致性校验、多跳比例控制。

### Modified Capabilities

- `evaluation-pipeline`：出题路径由「ragas 0.4.x（已下线）」改为「DeepEval Synthesizer」；`ToolCorrectness` 从"不可用（无标注）"变为"v6 起可用"。

## Impact

- **代码**：新建 `src/cli/testset_synth.py`（或等价模块）；`src/cli/eval_ragas.py`（`--synth` 入口）；`src/api/ragas_generate.py`（改指向或下线）；`src/main.py`（路由）。
- **依赖**：`deepeval` 已含 `synthesizer`，**不新增第三方依赖**。
- **数据**：新增 `data/ragas/testset/testset_<kb>_v6.json`（v5 保留）；题集结构新增 `expected_tools` / `hop_type` / `numeric_risk` 元数据（`_load_latest_testset` 忽略未知字段即可）。
- **评测**：`ToolCorrectness` 在 v6 上启用；v5 的 `expected_tools` 缺失仍记 `N/A`（不 fail-loud）。
- **文档**：`docs/agents/ragas-eval-ledger.md`（v6 行）、`docs/agents/api_contract.md`（出题接口）、design D8 的 v6 接手点（本 change 即承接方）。
- **不改**：`src/agents/**`、生产链路、`kb_eval` API 形状、前端。
