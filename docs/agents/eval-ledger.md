# 评测台账

> **唯一归属**：本文件记录**每次全量评测的运行配置、结果与改动**，供跨轮比对与事后回溯。
> 与 `docs/agents/troubleshooting.md` 的分工：那份记「踩坑案例与根因」；本份只记「每次跑了什么、结果如何、改了什么」。
> 与 `docs/adr/` 的分工：ADR 记**决策与代价**（不可变）；本份记**每次运行的事实**（追加式，不改旧值）。

## 为什么要有它

`data/deepeval/reports/*.md` 每轮重新生成，但**不记录裁判配置**（`src/cli/eval_deepeval.py` 的报告头只有日期 / TOP_K / 题数）。于是要回答「两次全量为什么分数不同」，只能去 `git log` 反推配置——本台账就是为了消除这个成本。

## 两条硬规则

1. **只追加，不改旧值。** 历史行的分数与配置是当时的记录；重跑另起一行，不原地改数字。
2. **配置如实填。** 报告未落盘配置时，写「报告未记录（推定：…）」并注明推定依据，**不写没有证据的配置**。

## 公共配置（所有行共用）

- 检索：`TOP_K_RETRIEVAL=30` · `TOP_K_RERANK=5`
- 评测并发：**口径分代** —— 当前 `deep-eval-native` 四指标评分**串行（并发=1）**（四项与诊断指标共用同一组指标实例与裁判，并发会互踩，见 design D7）；历史 #1–#4（现代栈）为**行级并发=5**，#5（DeepEval 桥）串行
- 测试集：`data/deepeval/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json`（21 条；四轮全量的题面集合已核对一致）
- **题集版本归属**：v6 题集对应 `testset_version=6`；报告头与 DB `detail_json` 均记录该值。历史报告（`report_path`）对应 v5 题集，二者不混用。
- 主分口径：仅 `pure_kb` 样本的四项均值（`src/cli/eval_scoring.py`）
- 门禁阈值：`faithfulness≥0.85` · `context_precision≥0.80` · `context_recall≥0.70` · `answer_relevancy≥0.85`
- 报告文件：`data/deepeval/reports/deepeval_eval_<timestamp>.csv` / 同名 `.md`（`--deepeval-only` 只产 `.md`）
- 历史 `report_path`：早期行指向 `data/ragas/`，该目录已不自动迁移且无消费方（仅作留档）；历史行不改写
- 当前口径版本：`deep-eval-native`（DeepEval 原生四指标；历史行的口径以「口径版本」列为准）
- 工具选择诊断：`tool_correctness` **已启用**（v6 题集带 `expected_tools`），按顶层轨迹逐题评分；仅诊断观测，不进主分/门禁/Langfuse

## 口径说明：`deepeval-only` 运行

以 `--deepeval-only` 跑的评测**不产出主分四指标**，故不进入运行台账的分数对照；如需登记，口径版本列记 `deepeval-only`，并在「主要异常」注明"仅行为轴"。它只用于 DeepEval 功能的迭代验收，**不参与发布门禁与跨轮分数比较**。

## 运行台账（全量；v5 及以前 21 题，v6 起 22 题）

| # | 开始（CST） | 报告文件 | 裁判配置 | 有效纯 KB | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | 门禁 | 主要异常 | 口径版本 | 逃生门 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-15 11:04 | `ragas_eval_20261015_030403` | 报告未记录（推定：思考 ON · 平铺 300s 超时） | 19 | 0.8618 | 0.8910 | 0.7571 | 0.8066 | 0.7108 | ❌ cp | 多行裁判超时，faith/answer_correctness 各缺 4 行 | N/A | 未启用 |
| 2 | 2026-10-15 14:52 | `ragas_eval_20261015_065238` | 报告未记录（推定：思考 OFF · 细粒度超时 · cap 8192） | 19 | 0.9089 | 0.8584 | 0.5303 | 0.8619 | 0.5809 | ❌ cp | 3 行 context_recall `RuntimeError` | N/A | 未启用 |
| 3 | 2026-10-15 16:03 | `ragas_eval_20261015_080359` | 报告未记录（推定：思考 OFF · cap 4096 · attempts=3） | 19 | 0.8530 | 0.8644 | 0.5227 | 0.8500 | 0.6071 | ❌ cp | 5 行 `IncompleteOutputException`（cap 顶格） | N/A | 未启用 |
| 4 | 2026-10-15 16:47 | `ragas_eval_20261015_084734` | 报告未记录（推定：思考 OFF · cap 8192 · attempts=3 · 细粒度超时 · temp=0） | 19 | 0.7813 | 0.8537 | 0.5004 | 0.8412 | 0.6164 | ❌ faith + cp | 1 行 `IncompleteOutputException`（Q16）+ Q4 空检索 | N/A | 未启用 |
| 5 | 2026-10-17 13:55 | `ragas_eval_20261017_055527` | 报告未记录（推定：桥裁判 · 思考 OFF · cap 8192 · 细粒度 `http_async_client`/`http_client` 超时 · SDK retries=0 · 串行评分；依据 `settings.py` 默认值与工厂透传）；`judge=qwen3.8-flash`（报告头已记录） | 19 | 0.8263 | 0.8848 | 0.5073 | 0.8195 | N/A | 未跑（首轮 --no-gate） | Q8（`web`）context_precision 三次 attempt 均约 182s 超时后 NaN，不入纯 KB 主分（主分 NaN 0/76） | bridge-0.3.9 / v5 | 未启用 |
| 6 | 2026-10-18 00:22 | `deepeval_eval_20261017_162242` | `caliber=deep-eval-native · judge=qwen3.8-flash · deepeval=4.2.9 · metrics=native`（报告头已记录） | 21 | 0.9726 | 0.9304 | 0.7395 | 0.8769 | N/A（原生四项不含） | 未启用（baseline） | 首轮 baseline，`--run-type baseline`，不判门禁；无失败样本（error 列全空）；主分 NaN 0/84，诊断 NaN 各 0/21；诊断 contextual_relevancy=0.3282 / non_advice=0.9683 | `deep-eval-native / v5` | 未启用 |
| 7 | 2026-10-18 12:14 | `deepeval_eval_20261018_041404` | `caliber=deep-eval-native · judge=qwen3.8-flash · deepeval=4.2.9 · metrics=native`（报告头已记录） | 22 | 0.9653 | 0.9515 | 0.7913 | 0.8413 | N/A（原生四项不含） | 未启用（baseline） | v6 全中文题集（22 题，33m16s）；主分 NaN 2/88（Q16 context_recall 裁判 `JSONDecodeError`、Q22 faithfulness 裁判输出截断），均不入均值；诊断 contextual_relevancy=0.3128 / non_advice=1.0000；CR 有 3 题 0 分：Q19 参考答 42% 与语料 53% 矛盾（出题器臆造数值）、Q15 题干写 2024 年但参考答用 Q4 口径、Q21 参考答 80,348 在语料中确有（检索未命中）。剔除此 3 题后 CR=0.9815，与 v5 中文子集 0.976 同档 | `deep-eval-native / v6` | 未启用 |

> **#1–#5 都卡在 context_precision**（0.7571 → 0.5303 → 0.5227 → 0.5004 → 0.5073），且它在两次配置间逐题剧烈翻转——见下方「已知口径与坑」。

## 工具选择（`tool_correctness`）基线

`tool_correctness` 自本 change 起**启用**（此前未启用，历史行无此值），口径为「命中的期望工具数 / 去重后的期望工具数」（0.0–1.0），只比较工具名与类型，不进主分/门禁/Langfuse。

| 基线 | 报告文件 | scored | Fully matched | mean | no_label | failed | metric_error |
|---|---|---|---|---|---|---|---|
| #7（v6，22 题） | `deepeval_eval_20261018_041404` | 22 | 22/22 | 1.0000 | 0 | 0 | 0 |

- **数字来源**：用 `deepeval_eval_20261018_041404.csv` 中既有的 **22 条轨迹** + v6 题集 `expected_tools` 标签做**离线重放**（`ToolCorrectnessMetric(model=Fake(), async_mode=False, eval_mode="llm")`），**不是一次新的 agent 运行**；重放输出 `{'n': 22, 'fully_matched': 22, 'mean': 1.0}`。
- 该轮 CSV 的 `tool_correctness` 列在运行时尚为空（实现晚于本轮运行）；重放证明按当前口径 22 题均完全命中，因此 `scored=22`、`no_label/failed/metric_error=0`。
- **均值口径**：`Tool correctness mean (scored)` 覆盖**全部样本**（含 `empty` 样本，其工具选择照常评分），与只统计 `pure_kb` 的主分不可横向比较（报告段已标 `scored`）。

## 最近一轮逐题异常（#4，`ragas_eval_20261015_084734`）

| 题 | 类别 | 现象 | 根因 |
|---|---|---|---|
| Q4 | `empty` | Agent **0 次工具调用**，contexts 为空；三项 context 指标被掩码跳过 | 被测 Agent 偶发不检索（参数化作答） |
| Q16 | `pure_kb` | context_recall 三次 attempt 全 `IncompleteOutputException`（cap 顶格） | 裁判输出退化（cap 是缓解非修复，见 `docs/adr/0024`） |
| Q13 / Q18 / Q21 | `pure_kb` | `context_precision=0.0` 但 `context_recall≥0.83`（逻辑矛盾） | 裁判判错：Q13 已用原始 context 证实（第 1 条 context 含 reference 的负债合计数字，仍被判 0） |
| Q17 | `pure_kb` | 10 次迭代、后半段重复同一查询；answer 仅 81 字（非答案） | 被测 Agent 打转、不收敛 |

## 改动记录（what changed）

| 时间（CST） | 提交 | 改动 | 效果 |
|---|---|---|---|
| 10-14 19:18 | `a13ae09d` | 现代裁判/embedding 工厂：`temperature=0`、不发 `top_p`、显式超时 | 现代栈落地 |
| 10-14 19:28 | `3fabcda9` | 执行内核迁 `@experiment`：失败记 `error` 列、行级并发、行序回排 | — |
| 10-15 11:03 | `a7fd9f02` | 裁判 cap 上调 8192 + 保留思考 | 截断类失败减少 |
| 10-15 14:19 | `05e889f4` | 细粒度 HTTP 超时 + 关 SDK 重试（`RAGAS_HTTP_MAX_RETRIES=0`）；`RAGAS_JUDGE_THINKING` 默认关 | 消掉「平铺 300s × SDK 重试」的 900s 空转 |
| 10-15 14:33–14:48 | `08070644` / `b973c246` | 单指标有界重试 `RAGAS_METRIC_MAX_ATTEMPTS=3`（超时也重试） | 失败行可被救回 |
| 10-15 15:49 | `059a244c` | cap 调 4096 + 裁判日志挂 `trace_id` + `trace_logs` 查日志 | **净亏**：失败反增（cap 变小） |
| 10-15 16:46 | `14e1d52e` | cap **回退 8192**；订正 ADR-0024 | 失败行由 5/21 降到 1/21（对比 #3 → #4） |

> **#4 相对 #3 的唯一代码改动** = `14e1d52e`（cap 4096 → 8192）。这是本次「跑了什么、改了什么」的最小可比对单元。

## 出题路状态（2026-10-17）

- **出题链路已删除**：原 RAGAS 出题模块（含 `run_generate` / `_ensure_vertexai_stub`）
  整文件移除；CLI `--generate` 保留参数、命中即报错并指向 DeepEval `Synthesizer`
  （见 change `deepeval-native-metrics`）。
- **测试集加载器已迁出**：`_load_latest_testset` 移到 `src/cli/testset_io.py`
  （不依赖 ragas），评测主流程从该模块加载。
- 评测输入 = **固定版本文件**（当前 `testset_<kb>_v6.json`，由 `src/cli/testset_synth.py` 用 DeepEval `Synthesizer` 生成），与评测链路解耦；不显式传 `--testset-version` 时取最新版本。

## 已知口径与坑（读表前先看）

1. **各指标的有效样本数不一致**：主分只统计 `pure_kb` 行；但 `answer_correctness` / `answer_relevancy` 不需 context，连空检索题也算；`context_recall` 又会因裁判失败再少一行。读均值前先确认分母。
2. **报告不落盘裁判配置**（缺口 c）：表中「裁判配置」列对历史行都是推定值，不可当证据。修法：报告头 + 旁挂 `*.meta.json` 记录 judge model / 思考档 / cap / 超时 / attempts / 测试集 / git commit。
3. **context_precision 不可复现**（缺口 b）：同题面、同 TOP_K，两次运行出现 1.0 → 0.0 的翻转，且有「recall 高但 precision=0」的判错行。跨轮比较该指标时，差异小于 ~0.1 不足为凭。
4. **喂给裁判的 context 未去重/截断**（缺口 a）：`src/cli/eval_deepeval.py` 把 Agent 全部工具产出原样传入，多轮检索的题 context 数可达 40+，而 context_precision 是「条数越多越吃亏」的 average precision。待调研定论后修。
5. **跨 testset 版本不可直接比分数**（#6 起）：v6 是重新出题的 22 题，与 v5 的 21 题只有部分主题重叠。题面难度、参考答案口径、题量都变了，#6 ↔ #7 的差值同时包含「被测系统变化」和「题集变化」，只能用于方向判断，不可当成回归信号。
6. **参考答案自身会污染 context_recall / precision**：v6 的 Q19 参考答写「经审核毛利率 42%」，语料表格是 53%，CR=0 判得对，是出题器臆造数值；Q15 题干写 2024 年、参考答用 Q4 口径。读到「CR 掉点」先抽查参考答是否站得住，再怀疑检索链。反过来 Q21 参考答的 80,348 在语料里确实存在，CR=0 是检索未命中，不能一律归因于题集。

## 怎么用

- **每跑完一轮全量**：在「运行台账」追加一行；若有代码改动，在「改动记录」补一行；逐题异常按需补。
- **对比**：先看「裁判配置」两列是否可比（缺口 c 修好前，配置不同的行不可直接比分数）；再看主分与门禁。
- **新发现的口径问题**：追加到「已知口径与坑」，不删旧条目。
