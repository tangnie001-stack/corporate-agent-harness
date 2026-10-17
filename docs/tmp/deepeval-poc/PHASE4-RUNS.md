# Phase 4 运行记录（v5 · DeepEval 桥 + ragas 0.3.9）

> 口径：`bridge-0.3.9 / v5`。本阶段**只记录分布，不标定阈值**（C 折中，见 Phase 4 计划）。
> 每级一行：命令 / 耗时 / 报告路径 / 关键校验。

## 运行环境

- 日期：2026-10-17
- 宿主：WSL，`.venv/bin/python`
- 容器：postgres / redis / minio healthy；langfuse / nginx / app running（三者未声明 healthcheck，`docker compose ps` 只报 `Up`）
- 命令前缀：`POSTGRES_HOST=localhost`

运行命令（1 题 / 5 题 / 21 题只差 `--limit`；**本次不加** `--gate`）：

```bash
POSTGRES_HOST=localhost .venv/bin/python -m src.cli.eval_ragas \
  --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 5 [--limit 1|5]
```

固定值：KB id `4a1dcb8bb340473c8149ead5b7f75873`，题集版本 `5`（21 条），裁判 `qwen3.8-flash`。

## 运行总览

| 级 | 开始（本地） | 结束 | 耗时（日志窗口） | 耗时（墙钟） | 报告 | QA | NaN |
|---|---|---|---|---|---|---|---|
| 1 题 | 12:11:12 | 12:12:41 | 1m29s | 1m35s | `ragas_eval_20261017_041112.md` | 1 | 0/4 |
| 5 题（失败） | 12:12:58 | 12:16:47 后僵住（末条日志 12:16:27.730） | — | 人工终止 | 无 | 5（题集条数） | — |
| 5 题 | 12:42:48 | 12:49:57 | 7m09s | 7m18s | `ragas_eval_20261017_044248.md` | 5 | 0/20 |
| 21 题（失败） | 12:50:23 | 12:57:53 后僵住 | — | `timeout 3600` 终止 | 无 | 21（题集条数） | — |
| 21 题 | 13:55:27 | 14:50:50 | 55m23s | 55m42s | `ragas_eval_20261017_055527.md` | 21 | 0/76 |

耗时的两个口径：日志窗口 = 该轮首条 `[cli] testset loaded` 到末条 `[cli] evaluation done`（或最后一条活动）的差；墙钟 = 控制器观测的 `time` 输出（含解释器启动与收尾）。两者差 6–19 秒，属正常。

失败轮无报告，"QA" 列填的是**题集条数**，不是报告里的 `QA Pairs`；两轮都以僵住告终，耗时口径不适用，故记 `—`。

四指标与行为轴均从**报告正文**读取/计算，不引第三方汇总。

---

## 1 题（Step 2 冒烟）

- 命令：`… --testset-version 5 --limit 1`
- 报告：`data/ragas/reports/ragas_eval_20261017_041112.md` / `.csv`
- 行为产物：`data/ragas/reports/behaviour_20261017_041112/test_run_20261017_121140.json`
- 日志窗口：12:11:12.443 → 12:12:41.975（1m29s）
- 退出码：未记录（由控制端驱动）
- `**QA Pairs:** 1`，`**NaN cells:** 0/4 (rate=0.0)`
- `**Main scores (pure-KB):**` faithfulness=1.0000, answer_relevancy=0.9855, context_precision=1.0000, context_recall=1.0000
- Caliber / Judge：`caliber=bridge-0.3.9, judge=qwen3.8-flash, model_arg_explicit=True`；`Runner versions: ragas=0.3.9, deepeval=4.2.9`

| 题 | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| Q1 | 1.0000 | 0.9855 | 1.0000 | 1.0000 |

Agent 行为轴（1 行）：Task Completion=1.0000，Step Efficiency=1.0000，Agent Loop Detection=1.0000。

其他：`metric retry` 1 次（12:11:43 faithfulness q_index=0 reason=nan），第 2 次尝试成功，故报告无 NaN。

---

## 失败 #1：5 题 · 行为轴写盘后卡在 RAGAS 评估开始前（12:12–12:38）

- 命令：`… --testset-version 5 --limit 5`
- 日志：12:12:58.524 `testset loaded count=5` → 12:16:26.213 `question answer done index=5`；末条日志为 12:16:27.730 的 Langfuse `Unexpected error`，此后到 12:42:48.635 下一轮启动**无任何日志**。
- 关键缺失：**从未出现 `[cli] evaluation start samples=5`**，即卡在"答案生成完成 → RAGAS 评估开始"之间，进程内无细粒度超时事件可观测。
- 另有 `question answer failed index=3 err=""`（12:16:16.928，约 3 分钟后失败，err 为空）。
- 产物：**无报告 / 无 CSV**；行为目录 `behaviour_20261017_041258/` 下留有 `test_run_20261017_121647.json`（1.6 MB，12:16:47 写入），其 `testCases=4`——与 `index=3` 无答案一致，即**行为轴产物为 4/5**。JSON 写入于 12:16:47，**之后**才卡住，位置在 RAGAS 评估开始前（无 `judge call` 日志）。
- 空窗：自行为 JSON 写入 **12:16:47** 至下一轮 12:42:48，26m01s。
- 处置：人工终止。根因与修复见 Task 1b（裁判路径细粒度超时）。

---

## 5 题（Step 3）

- 命令：`… --testset-version 5 --limit 5`
- 报告：`data/ragas/reports/ragas_eval_20261017_044248.md` / `.csv`
- 行为产物：`data/ragas/reports/behaviour_20261017_044248/test_run_20261017_124402.json`
- 日志窗口：12:42:48.635 → 12:49:57.547（7m09s）
- 退出码：未记录（由控制端驱动）
- `**QA Pairs:** 5`，`**NaN cells:** 0/20 (rate=0.0)`
- `**Main scores (pure-KB):**` faithfulness=0.8000, answer_relevancy=0.9301, context_precision=0.5400, context_recall=1.0000
- 行为轴段行数 = 5，无 `RuntimeError: 行为分题数不齐`

| 题 | faithfulness | answer_relevancy | context_precision | context_recall |
|---|---|---|---|---|
| Q1 | 1.0000 | 0.9914 | 1.0000 | 1.0000 |
| Q2 | 1.0000 | 0.8136 | 0.5000 | 1.0000 |
| Q3 | 1.0000 | 0.9775 | 0.5000 | 1.0000 |
| Q4 | 0.0000 | 0.8933 | 0.2000 | 1.0000 |
| Q5 | 1.0000 | 0.9749 | 0.5000 | 1.0000 |

Agent 行为轴（5 行均值）：Task Completion=0.9700，Step Efficiency=0.7000，Agent Loop Detection=1.0000。

其他：`metric retry` 1 次（12:44:05 faithfulness q_index=0 reason=nan），第 2 次尝试成功，报告无 NaN。

---

## 失败 #2：21 题 · judge 400 后进程僵住（12:50–13:50）

- 命令：`… --testset-version 5`（21 题，后台）
- 12:50:23.180 `testset loaded count=21`。
- 12:57:48.823 **第 21 题答案完成**（`[cli] question answer done index=21`，`logs/app_2026-10-17.log:17985`）。
- 12:57:53.069 **bridge judge 收到 `status=400`**（`[provider] bridge judge http non-2xx`，trace `trace_ccc326a1-…`，`:17986`）。应用日志只记状态码，响应体文本 `Range of input length …` 来自控制器侧进程 traceback。
- 同一秒内 **39 条 `caught asyncio.CancelledError`**（12:57:53.097–.113，`:17987-18025`）。
- 此后**无任何日志**；下一条是 13:55:27.475（下一轮 21 题开始，`:18026`）⇒ **400 后进程僵住（CancelledError 后未退出）**，`timeout 3600` 于 13:50 到期终止。
- 另：`question answer failed index=5 err=""`（12:54:01，约 3 分钟失败，流程继续）。
- 产物：**无报告 / 无 CSV / 无行为目录**（`data/ragas/reports/behaviour_*` 下无 12:50–13:55 之间的目录），该轮未产出任何报告。
- 处置：`timeout 3600` 终止。根因与修复见 Task 1c（超长 prompt 截断），复跑时共触发 36 次截断（见下）。

---

## 21 题（Step 4 全量，唯一一次成功全量）

- 命令：`… --testset-version 5`
- 报告：`data/ragas/reports/ragas_eval_20261017_055527.md` / `.csv`
- 行为产物：`data/ragas/reports/behaviour_20261017_055527/test_run_20261017_140219.json`（17 MB）
- 开始：13:55:27.475（首条 `[cli] testset loaded count=21`）
- 结束：14:50:50.301（末条 `[cli] evaluation done`；`report saved` 14:50:49.532）
- 耗时：**55m23s**（日志窗口）/ 55m42s（墙钟）
- 报告头：
  - `**QA Pairs:** 21`
  - `**NaN cells:** 0/76 (rate=0.0)`（分母 76 = 19 纯 KB × 4）
  - `**Caliber / Judge:** caliber=bridge-0.3.9, judge=qwen3.8-flash, model_arg_explicit=True`
  - `**Runner versions:** ragas=0.3.9, deepeval=4.2.9`
  - `**Configuration:** TOP_K_RETRIEVAL=30, TOP_K_RERANK=5`
  - `**Temperature regime:** n1_metrics=0.01, answer_relevancy=0.3 (ragas_decides_by_n)`
- 退出码：0（控制器观测）；日志侧佐证为尾部连续出现 `results saved` / `report saved` / `eval report saved` / `evaluation done`

### 四指标（纯 KB，19 题）

`**Main scores (pure-KB):**` faithfulness=0.8263, answer_relevancy=0.8848, context_precision=0.5073, context_recall=0.8195

复算核对（对报告表格 19 行 pure_kb 逐列求均值）：0.8263 / 0.8848 / 0.5073 / 0.8195，与报告头一致。

### NaN 计数

- 报告口径：`0/76 (0.0%)`，分母 = 19 纯 KB × 4 指标（Q8、Q11 为 `web` 样本，不计入主分分母）。
- 表内唯一 NaN：**Q8 的 context_precision**（`sample_class=web`，不在 76 的分母内），故主分 NaN = 0 与表内 1 个 `nan` 并不矛盾。

### Agent 行为轴（21 行，从报告 `## Agent 行为轴` 表算）

| 指标 | 均值 | min | max |
|---|---|---|---|
| Task Completion | 0.9024 | 0.4000 | 1.0000 |
| Step Efficiency | 0.5929 | 0.1000 | 1.0000 |
| Agent Loop Detection | 1.0000 | 1.0000 | 1.0000 |

算法：解析报告 `## Agent 行为轴` 段的 21 行数据行，逐列取算术平均（未加权、未剔除任何行）。校验：TC 求和 18.95/21=0.90238，SE 求和 12.45/21=0.592857，Loop 21 行全 1.0。

低分集中在 Step Efficiency：11 行 ≤0.5（Q8=0.10、Q15=0.10、Q11/Q16/Q17/Q19=0.25、Q3/Q6/Q9/Q10/Q18=0.50），与轨迹里"检索重试多次仍无结果后转 `search_web`"的行为一致（如 Q8 迭代 9 次、Q15 迭代 6 次）。

### 截断告警（Task 1c 修复的可见证据）

```bash
awk '/^2026-10-17 [0-9]{2}:[0-9]{2}:[0-9]{2}\./ && substr($0,1,19) > "2026-10-17 13:55:26"' logs/app_2026-10-17.log \
  | grep -c 'bridge prompt truncated'
# 36
```

- 36 次，全部落在 **14:01:34–14:02:04**，即行为轴（DeepEval 桥）阶段；RAGAS `evaluation start samples=21` 在 14:02:20 之后，RAGAS 裁判阶段 0 次。
- prompt 长度 min=503,705 / max=2,261,560（字符），limit=400,000；单次最大丢弃 1,861,560。
- 与失败 #2（400 `Range of input length`，`logs/app_2026-10-17.log:17986` 记 `status=400`）互为印证：不截断则超限，截断后跑通。

---

## 校验记录（Step 5 / Step 6）

### Step 5：行为分来自 `testCases[].metricsData`

```bash
python3 - <<'PY'
import glob, json
f = sorted(glob.glob("data/ragas/reports/behaviour_20261017_055527/test_run_*.json"))[-1]
print("file:", f)
d = json.load(open(f))
tc = d["testCases"]
assert len(tc) == 21, len(tc)
for t in tc:
    assert "metricsData" in t, t.get("input")
    names = [m["name"] for m in t["metricsData"]]
    assert names, t.get("input")
print("OK testCases=21; metricsData present; sample names:", names)
print("aggregate metricsScores keys:", [m.get("metric") for m in d.get("metricsScores", [])])
PY
```

输出：

```
file: data/ragas/reports/behaviour_20261017_055527/test_run_20261017_140219.json
OK testCases=21; metricsData present; sample names: ['Task Completion', 'Step Efficiency', 'Agent Loop Detection']
aggregate metricsScores keys: ['Task Completion', 'Step Efficiency', 'Agent Loop Detection']
```

结论：21 条 `testCases` 全部带 `metricsData`，逐题行为分来源成立；聚合 `metricsScores` 与逐题同名三指标一致，未出现"用 `metricsScores` 当逐题分"的替代路径。

### Step 6：NaN 重试取证（窗口 = 13:55:26 之后）

```bash
awk '/^2026-10-17 [0-9]{2}:[0-9]{2}:[0-9]{2}\./ && substr($0,1,19) > "2026-10-17 13:55:26"' \
  logs/app_2026-10-17.log > /tmp/phase4_21w2.log      # 700 行，13:55:27.475 → 14:51:05.773
grep -c 'metric retry' /tmp/phase4_21w2.log                              # 3
grep -cE 'judge call.*ok=(False|false)' /tmp/phase4_21w2.log             # 4
grep 'metric retry' /tmp/phase4_21w2.log
grep -E 'judge call.*ok=(False|false)' /tmp/phase4_21w2.log
```

（`awk` 加了时间戳锚点：裸 `$0 > "…"` 会把 traceback 续行也选进来，行数虚高到 856。）

结果：

| 序号 | 时间 | 事件 |
|---|---|---|
| 1 | 14:02:22.602 | `judge call metric=faithfulness q_index=0 trace_id=eval_39e243380208 attempt=1 seconds=2.3 ok=false`（伴随 `APIConnectionError`） |
| 2 | 14:02:22.603 | `metric retry metric=faithfulness q_index=0 reason=nan` |
| 3 | 14:02:38.658 | `judge call … metric=faithfulness q_index=0 attempt=2` → 成功 |
| 4 | 14:15:36.855 | `judge call metric=context_precision q_index=7 trace_id=eval_37bafce2e6b9 attempt=1 seconds=182.6 ok=false` |
| 5 | 14:15:36.856 | `metric retry metric=context_precision q_index=7 reason=nan` |
| 6 | 14:18:41.423 | `judge call … q_index=7 attempt=2 seconds=184.1 ok=false` + `metric retry` |
| 7 | 14:21:44.012 | `judge call … q_index=7 attempt=3 seconds=181.6 ok=false`（重试耗尽） |

- **retry 次数：3**（q0/faithfulness ×1、q7/context_precision ×2）
- **失败 judge 调用：4**（q0 ×1、q7 ×3）
- **与报告 NaN 的对应关系**：
  - q_index=0 → 报告 Q1，重试后成功，报告 faithfulness=1.0000（**非** NaN）。
  - q_index=7 → 报告 Q8，三次全失败，报告 Q8 context_precision=`nan`（**对应**）。
  - 报告头 `NaN cells: 0/76` 不含该格，因为 Q8 是 `web` 样本、不在纯 KB 分母内（见上「NaN 计数」）。
- 结论：**触发过重试，且 1 处 NaN 可精确对上**；纯 KB 主分 0 NaN。
- 备注：同日文件 `metric retry` 共 141 条，其余 138 条分布在 00–03、08–10 时的更早批次与本阶段前两级（12:11、12:44 各 1 条），不属本窗口。

---

## 附：Step 4 校验命令原始输出

```text
$ MD=data/ragas/reports/ragas_eval_20261017_055527.md
$ grep -E '^\*\*QA Pairs:\*\*|^\*\*NaN cells:\*\*|^\*\*Main scores|^## Agent 行为轴' "$MD"
**QA Pairs:** 21
**NaN cells:** 0/76 (rate=0.0)
**Main scores (pure-KB):** faithfulness=0.8263, answer_relevancy=0.8848, context_precision=0.5073, context_recall=0.8195
## Agent 行为轴

$ grep -c '^|' "$MD"
46
```

46 = 主表（21 数据行 + 表头 + 分隔行）+ 行为轴表（21 数据行 + 表头 + 分隔行），两张表各 21 行数据，无缺行。
