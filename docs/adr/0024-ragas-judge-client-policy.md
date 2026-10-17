# ADR-0024：RAGAS 裁判客户端策略（细粒度超时 / 关 SDK 重试 / 本层有界重试 / 失败必留原始响应）

- **Status**：Superseded by 0026
- **Date**：2026-10-15
- **Deciders**：用户（议题提出与拍板：超时维度、重试次数、预算与思考档位）；Claude（系统排查、实测取证与实现）
- **关系**：落实 change `ragas-modern-stack` 的 design D6（裁判构造与失败语义）；沿用 ADR-0022/0023 的「评估检索镜像生产」口径（本 ADR 不涉及白名单）；观测事件的字段口径以 `src/core/log_event_specs.py` 的 `EVENT_SPECS` 为唯一归属。

## 背景与问题

RAGAS 评测（KB `4a1dcb8b…`，21 题全量）出现三类症状，排查耗数日：

1. **总耗时异常**：21 题曾在 **1h31m20s** 内跑完，其中只有约 8 分钟在真正计算，其余为等待；
2. **指标失败**：一次全量 **5/21 行**带 `error`，`context_recall` 覆盖率仅 18/21；
3. **成因不明**：失败在不同配置下呈现不同异常类名（`IncompleteOutputException` / `InstructorRetryException` / `RuntimeError`），一度只能凭结果反推。

**⚠ 与 design 早期结论方向相反，必须记明**：本 change 的 design 早期把成因写成「输入过大撑爆」「模型生成失控、撞满 max_tokens」。**这两条均被实测推翻**（见「证据」E4/E5），本 ADR 以实测为准。

## 决策

### 决策 1：HTTP 超时按**维度**分开，不用平铺标量

`httpx.Timeout(connect=10, read=150, write=30, pool=10)`，经自定义 `http_client` 交给 SDK。

**理由**：`AsyncOpenAI(timeout=300)` 这类**标量会被 httpx 展开成四个维度全部 300**（实测：`connect=read=write=pool=300`），且 httpx **没有「整请求总预算」概念**——四维是**分阶段**计时（pool→connect→write→read）。平铺值的代价：一次挂死请求白等 300s，且看不出卡在哪一段。`read=150s` 取自实测分位：正常调用 p50=6.2s / p95=55.3s / p99=110.9s（E2），150s 高于 p99 约 35%。

### 决策 2：openai SDK `max_retries=0`（关闭 SDK 自动重试）

**理由**：SDK 默认 `max_retries=2`，且 **`APITimeoutError` ⊂ `APIConnectionError` 属于会被重试的异常** ⇒ 一个逻辑调用实际发出 **3 次** HTTP（实测 mock transport，E3）。这是**静默的乘法放大**：日志只见墙钟变长，看不出发生了什么。

**勘误**：曾判断「instructor 自身重试会再叠一层」——**不成立**：`instructor.from_openai` 无 `max_retries` 参数，其日志为 `Total attempts: 1`。**唯一的重试放大层是 SDK**。

### 决策 3：重试收敛到**我们这层**，有界、逐指标、含超时；`error` 只在**最终失败**时非空

`RAGAS_METRIC_MAX_ATTEMPTS=3`（最多两次重试），逐指标独立重试，**超时也重试**；每次重试记 `metric retry` 事件；成功则不写 `error`（值即真值）。

**理由**：
- 失败主因是模型**偶发**「吐空结构」（E4）——**快（7–16s）且随机**，重试成功率高；
- 不重试会把失败行踢出该指标均值 ⇒ **乐观偏差 + 各指标分母不齐**（`main_score_means` 逐列 `.mean()` 各自跳过 NaN）；
- **超时也重试**：曾按「超时重试大概率再挂」的**推测**排除超时，随后实测抓到反例（E6，一次 150s 超时重试后成功恢复）⇒ 不排除；
- 放在本层而非 SDK 层，是因为本层**可见（事件留痕）、有界、逐指标独立**。

### 决策 4：`RAGAS_JUDGE_MAX_TOKENS=8192`

**理由**：RAGAS 内建默认仅 **1024**，会截断**正常**输出 ⇒ 必须上调。取 **8192 而非 4096**：
- 两处**独立**观测都显示 8192 的失败**不多于** 4096：A/B 探针（同输入 16,988 字符，15 次/档）**4096 失败 1 / 8192 失败 0**（E10）；真实 21 题全量 **4096 → 5/21** vs **8192 → 3/21**（E9）。
- 且**均耗时无差异**（探针 35s vs 33s）——跑飞的调用本就稀有，大 cap **不付时间代价**。

**⚠ 证据强度（如实标注）**：n 小（探针 15/档、全量 1 轮/档），**方向一致但不构成硬证据**；决策的主要依据是「**8192 不比 4096 慢**」，在此前提下没有理由选更紧的 cap。

**⚠⚠ 不得把 cap 当「时间旋钮」**：`max_tokens` 是**上限而非目标**——
- 正常调用 `finish_reason=stop`（输出 200–1,500 token），**cap 与耗时无关**；
- 只有「撞 cap」的调用，其耗时才随 cap 近似线性变化。

⇒ cap↑ **不一定**变慢、cap↓ **不一定**变快。更要紧的是：**cap↓ 会让更多调用撞 cap → 更多失败 → 触发本层重试（每次重试都是一次完整调用）**，总时间**可能反而上升**。
**判据应为「成功率」而非「耗时」**：4096 → 真实全量 5/21 行失败、探针 1/15；8192 → 3/21、0/15；而耗时 29m07s vs 29m48s（全量）、35s vs 33s（探针）**均在噪声内**，不构成取舍依据。

**⚠ 必须记明的边界**：cap **不是**失败根因。失败根因是**模型偶发退化成吐空对象/垃圾对象**（E4/E8/E11），cap 只决定它**在哪一步被抓**（撞满 → `IncompleteOutputException`；自己停 → `ValidationError`）。⇒ **调 cap 不解决根因**，只是让失败落进"可被 instructor 内部重试"的类别（见 E11）。后续若换裁判模型或简化 schema，需重新取证。

### 决策 5：裁判**关闭思考**（`RAGAS_JUDGE_THINKING=false`）

**理由**：`qwen3.8-flash` 默认开思考（实测一句「只回复 OK」亦产生 `reasoning_tokens=51`）。开思考在 21 题全量下出现 **5/21 行 `Request timed out`、耗时 1h31m**；关思考后同类运行显著更快。结构化判定在 `temperature=0` 下不依赖 CoT。（注：早期曾把「关思考」误判为引发 schema 形状错误——当时的失败实为**模型退化吐空对象**，与思考档位无关，见 E11。）

### 决策 6：裁判失败**必须留下原始响应**，并挂 `trace_id`；提供 `trace_logs` 按链查询

- 每次尝试记 `judge call` 事件（`metric` / `q_index` / `trace_id` / `attempt` / `seconds` / `ok` / `note`）；
- 失败时 `note` 由 `_judge_failure_note` 从 instructor 异常的 `last_completion` 带出 **`finish_reason` + 输出 token 数 + 原始输出片段**；
- `run_evaluation` 增加 `trace_ids` 参数并写入行，使报告里的 `trace_id` 可直达裁判调用；
- 新增 `python -m src.cli.trace_logs <trace_id> [--grep …] [--count]` 按 trace 拉 `logs/app_*.log` + `logs/error_*.log`。

**理由（本条是本次排查的最大教训）**：只记异常**类名**会把根因丢光——`RuntimeError` / `InstructorRetryException` 都是**包装层**，真正的因（模型吐空结构 / 截断 / 超时）只在**原始响应**里。此前多日只能凭结果反推，先后误判两次；补上「原始响应 + trace 串联」后，根因当轮即定（E4/E8）。

## 证据

| 编号 | 实测（2026-10-14 ~ 10-15，KB `4a1dcb8b…`） |
|---|---|
| E1 | 平铺标量展开：`AsyncOpenAI(timeout=300)` → `connect=read=write=pool=300`（httpx 展开）；SDK 默认 `DEFAULT_TIMEOUT=Timeout(connect=5, read=600, write=600, pool=600)`、`DEFAULT_MAX_RETRIES=2` |
| E2 | 21 题全量（关思考、细粒度超时）：**29m48s**（旧配置 1h31m20s）｜400 次裁判调用｜单次 p50=6.2s / p90=25.8s / p95=55.3s / **max=152.3s**｜无 300s 空转 |
| E3 | SDK 重试层实测（mock transport，始终连接失败）：`max_retries=2` → 实际发 **3** 次 HTTP；`=0` → **1** 次。`instructor.from_openai` 无 `max_retries` 参数 |
| E4 | 失败真因：同输入（16,037 字符）**并发 12 → 9/12 成功**（7–21s、1–2 条分类）；3 次失败均为 `finish_reason=**stop**`、`completion_tokens=**148–215**`、原始输出 `{ "classifications": [ { }, { }, { } ] }` ⇒ **模型偶发吐「空结构」**（pydantic 校验失败） |
| E5 | prompt 规模分解（实测渲染）：RAGAS 官方模板**固定开销 5,767 字符**；每条 context ≈2,000 字符；5 条 ⇒ **16,037 字符**（与实测 16,674 吻合）。**chunk 不大**：DB 分块长度均值 **652**、最大 **1,998**（161 块）⇒ 推翻「chunk 太大 / 条数太多」 |
| E6 | 超时可重试的反例：一次 `context_recall` 恰好 **150.0s**（read 上限）超时，重试后成功恢复 |
| E7 | `max_tokens` 对失败**耗时**的影响：撞 cap 的调用 **141s（cap=8192）→ 72.7s（cap=4096）**。⚠ 早期据此推断"4096 截断了合法长输出"，**已被 E11 推翻**（当时的失败实为退化空对象循环，不是合法输出被切） |
| E8 | 单题完整因果链（`trace_logs eval_808341338592 --grep "judge call"`）：`attempt=1 72.7s ok=false IncompleteOutputException` → `attempt=2 5.5s ok=false InstructorRetryException<-ValidationError: 6 validation errors` → `attempt=3 4.2s ok=true`。⇒ 同一指标上**两种失败模式并存**（截断 / 空结构），且 `attempts=3` 当场救回该行（用 attempts=2 会丢该指标） |

补充观测（未纳入决策，仅记录）：`prompt_tokens ≈ 38–40k` 且 `cached_tokens ≈ 39k`（E4 的 usage）——**字符数不能直接推 token 数**，与 E5 的字符分解不可混用。

| E10 | `max_tokens` A/B 探针（同输入 16,988 字符、含 25 条陈述长参考，**各 15 次**）：**4096 → 成功 14/失败 1**（`IncompleteOutputException`，`finish_reason=length`、`out=4096`）；**8192 → 成功 15/失败 0**。**均耗时几乎相同**（35s vs 33s，max 59s vs 40s）⇒ 大 cap 不付时间代价 |
| E11 | **失败根因的原始响应取证**（`trace_logs <trace_id> --grep "ok=false"`，2026-10-15 全量）：`q7 context_recall` attempt 1/2/3 全部 `finish_reason=length out=4096`，raw 均为**一长串空对象**（`{ "classifications": [ { }, { }, { }, …`）；`q8 faithfulness` attempt 1 `finish_reason=stop` + 69 个 `ValidationError`（`statements.0 = {}`），attempt 2 开头合法（`{ "statements": [ { "statement": "腾讯2024年度…"`）随后劣化为垃圾（`{ },{"},}{" : 1.0, ": " :"}`）。⇒ **两类异常名是同一根因的两种表现**（模型退化吐空/垃圾对象），**不是"合法长输出被截断"** |
| E12 | **重试类的可重试性（源码级）**：`instructor/v2/core/retry.py:47` `_RETRYABLE_PARSE_ERRORS = (ValidationError, JSONDecodeError, AsyncValidationError, ResponseParsingError)` 会被 instructor **内部重试**；而 `IncompleteOutputException` 在同文件 184/227/259/429/472/504 处均为**显式 `raise`、不参与内部重试** ⇒ 失败落在哪一类，直接决定它还能否拿到额外重试机会 |

### E9 配置 → 耗时 / 失败行 对照（2026-10-14 ~ 10-15，同 KB，同一批测试集）

| 配置（思考 / `max_tokens` / 超时 / SDK 重试 / 本层重试） | 题量 | 墙钟 | 带 `error` 的行 |
|---|---|---|---|
| ON / 1024 / 平铺 300s / 2 / 无 | 1 | 3m35s | 0/1 |
| ON / 1024 / 平铺 300s / 2 / 无 | 5 | **13m33s** | 2/5 |
| OFF / 4096 / 平铺 300s / 2 / 无 | 5 | **3m37s** | 2/5 |
| OFF / 4096 / 平铺 300s / 2 / 无（2 道硬题） | 2 | 2m24s | 0/2 |
| ON / 8192 / 平铺 300s / 2 / 无（2 道硬题） | 2 | **22m16s** | 0/2 |
| ON / 8192 / 平铺 300s / 2 / 无 | 5 | **12m01s** | 0/5 |
| ON / 8192 / **细粒度 read150** / **0** / 无 | 5 | **4m59s** | 0/5 |
| OFF / 4096 / 细粒度 / 0 / 1 次 | 5 | **5m46s** | 0/5 |
| **ON** / 8192 / 平铺 300s / 2 / 无 | **21** | **1h41m**（实验内部 1h31m20s，尾部卡在进程退出） | **5/21** |
| OFF / 8192 / 细粒度 / 0 / 无 | **21** | **29m48s** | **3/21** |
| OFF / 4096 / 细粒度 / 0 / 2 次 | 5 | **4m28s** | 0/5 |

**读法**：① 平铺 300s + SDK 重试是主因（`1h41m` → `29m48s` 主要靠细粒度超时 + 关 SDK 重试）；② 关思考对**全量**收益明显（同 21 题 `1h41m` → `29m48s`）；③ `attempts=3` 当场救回过行（E8）。
**口径说明**：墙钟取自 CLI 进程的墙钟（任务通知里的 `Duration`），非指标计算时长；早期几行改配置时**尚未提交**，故不能靠 git 归属、以当时的会话记录为准。


## 代价与复查触发条件

**代价**：
- 关思考牺牲潜在的推理质量（本仓评测未观测到收益）；
- `max_tokens=4096` 对「合法超长输出」更紧（当前测试集参考答案最长约 400 字符，余量充足）；
- 本层重试最坏 `attempts × read` = 3×150s（仅在真失败时发生）。

**复查触发条件**：
1. 若评测语料出现**合法**长输出（如参考答案含数十条陈述）导致 4096 截断 ⇒ 重新标定 `RAGAS_JUDGE_MAX_ATTEMPTS` / `MAX_TOKENS`；
2. 若裁判换模型（尤其换回开思考档或换 provider）⇒ 重跑「思考 × 预算」对照，本 ADR 的决策 4/5 需重新取证；
3. 若上游 RAGAS/instructor 修好「空结构」或改为 `Mode.TOOLS`（百炼在思考模式下以 400 拒绝 `tool_choice`）⇒ 可下调 `attempts`；
4. 若 SLO 要求单次评测时长上限，需同时复核 `read=150s` 与 `RAGAS_EVAL_CONCURRENCY`。
