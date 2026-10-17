# RAGAS 评测台账

> **唯一归属**：本文件记录**每次 RAGAS 全量评测的运行配置、结果与改动**，供跨轮比对与事后回溯。
> 与 `docs/agents/troubleshooting.md` 的分工：那份记「踩坑案例与根因」；本份只记「每次跑了什么、结果如何、改了什么」。
> 与 `docs/adr/` 的分工：ADR 记**决策与代价**（不可变）；本份记**每次运行的事实**（追加式，不改旧值）。

## 为什么要有它

`data/ragas/reports/*.md` 每轮重新生成，但**不记录裁判配置**（`src/cli/eval_ragas.py` 的报告头只有日期 / TOP_K / 题数）。于是要回答「两次全量为什么分数不同」，只能去 `git log` 反推配置——本台账就是为了消除这个成本。

## 两条硬规则

1. **只追加，不改旧值。** 历史行的分数与配置是当时的记录；重跑另起一行，不原地改数字。
2. **配置如实填。** 报告未落盘配置时，写「报告未记录（推定：…）」并注明推定依据，**不写没有证据的配置**。

## 公共配置（所有行共用）

- 检索：`TOP_K_RETRIEVAL=30` · `TOP_K_RERANK=5`
- 评测并发：**口径分代** —— 桥路径（ragas 0.3.9 + DeepEval，本 change 后）四指标评分**串行（并发=1）**（桥复用 ragas 模块级指标实例，并发会互踩，见 design D7）；历史 #1–#4（现代栈）为**行级并发=5**
- 测试集：`data/ragas/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v5.json`（21 条；四轮全量的题面集合已核对一致）
- 主分口径：仅 `pure_kb` 样本的四项均值（`src/cli/eval_scoring.py`）
- 门禁阈值：`faithfulness≥0.85` · `context_precision≥0.80` · `context_recall≥0.70` · `answer_relevancy≥0.85`

## 运行台账（全量 21 题）

| # | 开始（CST） | 报告文件 | 裁判配置 | 有效纯 KB | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness | 门禁 | 主要异常 | 口径版本 | 逃生门 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2026-10-15 11:04 | `ragas_eval_20261015_030403` | 报告未记录（推定：思考 ON · 平铺 300s 超时） | 19 | 0.8618 | 0.8910 | 0.7571 | 0.8066 | 0.7108 | ❌ cp | 多行裁判超时，faith/answer_correctness 各缺 4 行 | N/A | 未启用 |
| 2 | 2026-10-15 14:52 | `ragas_eval_20261015_065238` | 报告未记录（推定：思考 OFF · 细粒度超时 · cap 8192） | 19 | 0.9089 | 0.8584 | 0.5303 | 0.8619 | 0.5809 | ❌ cp | 3 行 context_recall `RuntimeError` | N/A | 未启用 |
| 3 | 2026-10-15 16:03 | `ragas_eval_20261015_080359` | 报告未记录（推定：思考 OFF · cap 4096 · attempts=3） | 19 | 0.8530 | 0.8644 | 0.5227 | 0.8500 | 0.6071 | ❌ cp | 5 行 `IncompleteOutputException`（cap 顶格） | N/A | 未启用 |
| 4 | 2026-10-15 16:47 | `ragas_eval_20261015_084734` | 报告未记录（推定：思考 OFF · cap 8192 · attempts=3 · 细粒度超时 · temp=0） | 19 | 0.7813 | 0.8537 | 0.5004 | 0.8412 | 0.6164 | ❌ faith + cp | 1 行 `IncompleteOutputException`（Q16）+ Q4 空检索 | N/A | 未启用 |
| 5 | 2026-10-17 13:55 | `ragas_eval_20261017_055527` | 报告未记录（推定：桥裁判 · 思考 OFF · cap 8192 · 细粒度 `http_async_client`/`http_client` 超时 · SDK retries=0 · 串行评分；依据 `settings.py` 默认值与工厂透传）；`judge=qwen3.8-flash`（报告头已记录） | 19 | 0.8263 | 0.8848 | 0.5073 | 0.8195 | N/A | 未跑（首轮 --no-gate） | Q8（`web`）context_precision 三次 attempt 均约 182s 超时后 NaN，不入纯 KB 主分（主分 NaN 0/76） | bridge-0.3.9 / v5 | 未启用 |

> **#1–#5 都卡在 context_precision**（0.7571 → 0.5303 → 0.5227 → 0.5004 → 0.5073），且它在两次配置间逐题剧烈翻转——见下方「已知口径与坑」。

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

- **出题生成入口已下线**：CLI `--generate` 显式失败（依赖 ragas 0.4.x 的
  `default_transforms_for_prechunked`，而评测侧为 ragas 0.3.9）；出题**代码保留未调用**，
  前迁路径 = DeepEval `Synthesizer`（见 change `deepeval-bridge-eval` design D8）。
- **测试集加载器仍在用**：`_load_latest_testset` / `_ensure_vertexai_stub` 被评测主流程
  调用 ⇒ `src/cli/eval_ragas_generate.py` **不可删**。
- 评测输入 = **固定版本文件**（`testset_<kb>_v5.json`），与出题生成解耦。

## 已知口径与坑（读表前先看）

1. **各指标的有效样本数不一致**：主分只统计 `pure_kb` 行；但 `answer_correctness` / `answer_relevancy` 不需 context，连空检索题也算；`context_recall` 又会因裁判失败再少一行。读均值前先确认分母。
2. **报告不落盘裁判配置**（缺口 c）：表中「裁判配置」列对历史行都是推定值，不可当证据。修法：报告头 + 旁挂 `*.meta.json` 记录 judge model / 思考档 / cap / 超时 / attempts / 测试集 / git commit。
3. **context_precision 不可复现**（缺口 b）：同题面、同 TOP_K，两次运行出现 1.0 → 0.0 的翻转，且有「recall 高但 precision=0」的判错行。跨轮比较该指标时，差异小于 ~0.1 不足为凭。
4. **喂给 RAGAS 的 context 未去重/截断**（缺口 a）：`src/cli/eval_ragas.py` 把 Agent 全部工具产出原样传入，多轮检索的题 context 数可达 40+，而 context_precision 是「条数越多越吃亏」的 average precision。待调研定论后修。

## 怎么用

- **每跑完一轮全量**：在「运行台账」追加一行；若有代码改动，在「改动记录」补一行；逐题异常按需补。
- **对比**：先看「裁判配置」两列是否可比（缺口 c 修好前，配置不同的行不可直接比分数）；再看主分与门禁。
- **新发现的口径问题**：追加到「已知口径与坑」，不删旧条目。
