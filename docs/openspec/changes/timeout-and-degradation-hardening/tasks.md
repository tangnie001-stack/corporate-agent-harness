# tasks — timeout-and-degradation-hardening

> **本文件是「范围 + 验收索引」，不承载步骤级实施内容**：改哪个文件的哪一行、先写什么后写什么，由实施计划（`docs/superpowers/plans/`）承载。两处不得含同一套可执行步骤（步骤单一真源）。
>
> 决策依据见同目录 `design.md`（D1–D17）；规格见 `specs/`（2 个新能力 + 3 个能力 delta）。

## 0. 前置（未满足前不得开工）

- [x] 0.1 **用户 2026-10-16 指示：本轮只更新文件，暂不提交、暂不建 worktree。** 开工前须先补齐：把本 change 的 ③④ 产物提交到 `dev-wsl`（提交时用 `docs/openspec/...` 路径，**勿用 `openspec/...`** —— 根目录软链会报 beyond a symbolic link），再 `git worktree add`。判据：主工作区非空 / 本 change 会产生多次提交 / 跨天推进（三条全中）；代价须知：worktree **只隔离 git**，不隔离 Docker。
- [x] 0.2 **验收边界**：worktree 内只跑**非 DB 单测 + `ruff` + `pyright`**；DB 用例与端到端回主工作区（worktree 缺 gitignored `data/`，`tests/parsers` 会环境性失败，不是回归）。
- [x] 0.3 **对照实测载体**：**本地真实发起调用**取分位（本机 `.env` 已走真实 provider）；**本机日志不可用**（stub 流量，`latency_ms=0~1`）；**不在生产做对照**。
- [x] 0.4 每阶段收尾走 ⑥ 两道闸门（`verification-before-completion` + `requesting-code-review`）；本 change 属**纯代码任务为主**，无破坏性操作。
- [ ] 0.5 **定序与开工前置断言（D17）**：`deepeval-bridge-eval` 的在途改动**已收口**（2026-10-16 拉入 `b8ca89ae`，脚本/机检可复跑）；仍与 `turn-provenance-observability` 共享 `agent_service.py` / `log_events.py` / `log_event_specs.py` / `const.py` ⇒ **动这四个文件前**先看它是否在途。**断言按阻塞面分开，不再一律卡住开工**：
  - **阻塞 P1/P2/P3 代码的（两条）**：① **生产自有键与 `RAGAS_*` 族无交集**（D2「键名归属」，本侧可自证）；② 对方仍计划删 `build_native_client`（"核引用后删"）⇒ **动 `factory.py` 前重读其当前形态**，且本变更不引用该函数。
  - **只阻塞「建本变更的 ADR」的（一条）**：③ 对方那条取代 ADR-0024 的新 ADR **编号已落**（截至 2026-10-16 `docs/adr` 最大仍是 `0024`）—— 本变更 ADR 序号要排在它之后 ⇒ **不阻塞任何代码任务**，只影响建档时机。
  **超时策略真源**：生产链路归本变更、评测链（`RAGAS_*` 族、含 `judge`）归它，两侧不互相覆盖。
- [x] 0.6 **提交 / 开工前跑一致性机检**：`python docs/tmp/check-change-consistency.py docs/openspec/changes/timeout-and-degradation-hardening`（应 **0 命中**）。它兜的是本 change 连过三轮评审反复出现的那一类缺陷 —— **修正只改了一半 / 只改了一处**（design 改了 spec 没改、spec 改了 tasks 没改）。检查项、四类误报的调参记录与灵敏度验证结果见脚本头部 docstring。

## 1. P1 底座：重试原语 + 显式超时 + 关 SDK 重试（可独立交付）

**交付项**
- [x] 1.1 新增无状态重试原语模块（`src/infra/retry_policy.py`）：从 `src/models.py` 迁出 `with_retry` 并改造 —— 异常分类、有界、含 jitter、**sync/async 两版**；唯一调用点一次改到位，**不保留别名转发**（D1/D5）。
- [x] 1.2 三个 `build_*` 显式传入超时与重试，SDK `max_retries=0`；取值按**角色**住 `settings`，**不进 `registry.yaml`**（D2/D11）。**生产侧用自有键名、不复用 `RAGAS_*`** —— 否则评测链改那些键会**静默改生产**（D2「键名归属」，第三轮 I6）。
- [x] 1.3 rerank 超时下沉到客户端（经 `client` 注入点传 `request_timeout`）；**内层 attempts=1 作唯一预算**，外层 `wait_for` 提到 **6s** 仅作双保险（D13 修订：三层超时必须**嵌套**，否则外层 5s 先放弃、底层线程仍跑到约 18s）。
- [x] 1.4 首版取值表按 `design.md` D11 落地（含 `main` 的**传输层单一 read=180s** 与**业务层两档阈值**、embedding 的查询/入库两档）；全部 env 可覆盖。**业务层两档还须各自按思考档分档**（首事件前 非思考 60s / 思考 150s；首事件后 30s / 60s），两档都须 **< 传输层 read**（第三轮 I1）；评测侧**已可由 env 覆写**（如 `MAIN_TIMEOUT_READ=…`）以适配离线批量；「覆写值写入报告」归评测链（`deepeval-bridge-eval`），**本变更不实现**。
- [x] 1.5 **主链路 LLM 的本层有界重试（D16）**：**模型对象级包装**（包 `build_chat` 产物、**转发 `bind_tools`**、守卫落在 **`_astream`** 内），**仅在首个 chunk 之前**重试、**已产出 chunk 即不重试**、每次重试留痕。**必须与 1.2 的"关 SDK 重试"成对**（不得只关不加）。⚠ `Runnable.with_retry` **明确不重试 stream**（`langchain_core/runnables/retry.py:378`）、循环 middleware 拿不到 chunk ⇒ 这两条更省的路都不通（评审 I1）。

**验收（可当场核对）**
- [x] 1.6 断言「构造即显式」：任一 `build_*` 产出的客户端超时非 `None`、SDK 重试为 0。
- [x] 1.7 断言分类行为：`ValueError`（失败耗时 ≈ 0）与 4xx **不重试**；429/超时**重试**且退避含抖动；`attempts` 有上界。
- [x] 1.8 断言 rerank 超时**可终止**：底层调用在超时后结束（不残留挂起调用），检索仍走降级路径；且**内层预算 < 外层 `wait_for`**。
- [x] 1.9 本阶段不改变任何降级行为（可独立交付的判据）。
- [x] 1.10 断言**"不得只关不加"**：主链路 SDK 重试为 0 **且**本层有界重试存在；**用流式用例**证"首个 chunk 前失败会重试、已产出后不重试（不重复输出）"。

## 2. P2 降级：动作 + 留痕 + 文案（依赖 P1 的异常分类）

**交付项**
- [x] 2.1 混合检索路级失败降级：并发取数改为「任一路失败仍返回另一路」，并**区分「失败」与「缺席」**（`hybrid-retrieval` delta；现状 `asyncio.gather(…)` 未带 `return_exceptions`）。**状态字段挂在既有混合检索事件上、不改 `search()` 返回签名** —— 改签名会波及 `api_contract.md` + 工具 + 评测 CLI。
- [x] 2.2 降级动作矩阵落地（D6）：rerank（已实现，对齐即可）/ embedding 查询 → 仅词法路 / 向量库 → "知识库暂不可用" / LLM → **已有检索结果时返回检索原文 + 明确告知；无检索结果时仅明确告知**（D9 收窄，评审 Q4）。
- [x] 2.3 两个出口收敛：事件进 `log_event_specs.EVENT_SPECS`（**不复用 `CAPABILITY_DEGRADED`**）；用户文案进 `src/config/const.py`。**`reason` 取值与文案一律取自规格 `specs/degradation-policy/spec.md` 的「降级原因与用户文案的取值集合」表，不得自造**。⚠ rerank 两行的**原因由既有事件名表达**（`rerank failed` / `rerank timeout`，两者字段均无 `reason`）⇒ **不新增事件、不给它们加 `reason`**（评审 I5）。
- [x] 2.4 上游**可用性类错误**（401 / 403）识别为不可重试类并驱动**单一**文案 `provider_unavailable`：**不区分额度与鉴权** —— 该判别与响应体取证**已裁定移出本变更**、归评测链 `deepeval-bridge-eval`（它有可读状态码/响应体的 hooks 证据机制，见 D5）。另补 **429 / 5xx 重试穷尽** 的 `upstream_exhausted`（与可用性类**不同**：该类**可**提示重试）。
- [x] 2.5 **前端消费链同步**：`deploy/nginx/html/chat.html` 的 `error` 分支改为**消费后端文案**（现硬编码 `showError('连接异常，请重试')`、**不消费** `data.error`）。**通道已在规格内点名（第三轮 B1）**：用户可见的降级文案 SHALL 走**既有 `status` 事件 + 专用 `stage` 常量**（**不复用** `retrieve` / `web_search` 的 stage —— 会让前端触发正文/旁白重分类；与 `turn-provenance` 的 `STAGE_*` 同构）⇒ 本项含"**新增 stage 常量 + 前端按 stage 分支呈现**"。

**验收**
- [x] 2.6 稠密路失败而词法路可用时，检索**返回词法结果**且降级可观测（状态字段为 `failed`，**非** `empty`）。
- [x] 2.7 LLM 首个 chunk 前失败**且已有检索结果** → 用户收到检索原文 + 告知；**无检索结果** → 明确告知，且**不落"空内容 + complete"**。
- [x] 2.8 可用性类错误的文案**不诱导重试**；401 / 403 走**同一** `provider_unavailable` 路径（**不再要求触发两类判据** —— 判别已移出本变更、归评测链）；`upstream_exhausted`（429/5xx 穷尽）**可**提示重试。
- [x] 2.9 降级事件字段可被判据**唯一匹配**（供 SLS / 云监控侧配规则；告警通道本身不属本变更，见 `H-12`）。
- [x] 2.10 **不存在"文案已定义但无通道送达"的项**：每个「用户可见 = 是」的取值都有明确的 SSE 通道 + 前端消费点（与 2.5 配套）。

> **P2 验收结论（2026-10-16，控制方自跑；证据见 `.superpowers/sdd/2026-10-16-timeout-and-degradation-p2/`）**：2.1–2.10 全部落地。**证据等级须如实分辨**（不混淆）：
> - **单测级**（真断行为、含先红后绿）：2.1 / 2.3 / 2.9，以及 2.8 的"401·403 归类"半边；
> - **运行时冒烟级**（起服务 + 真实 LLM 调用 + 按 trace_id 回查日志 + 查库）：2.2 的 rerank 降级、2.7 的"无检索结果"分支、2.6 的两路 `ok` 基线；
> - **仅 grep 断言级**（`deploy/nginx/html/` 无自动化测试设施）：2.5 / 2.10 的**前端半边**；
> - **未覆盖缺口（不假装覆盖，交后续实测或评测链）**：① **真实 401/403 端到端路径** —— 无稳定复现手段（单测用替身异常；`MAIN_TIMEOUT_READ=0.001` 走的是超时类、不经状态码判据）；② **`llm_unavailable_with_context`（有检索原文）分支的运行时路径** —— 需"先检索成功、再在生成时失败"，不可稳定构造，仅单测覆盖；③ **稠密路单路失败的运行时路径** —— 需让查询向量化或 pgvector 报错，仅单测覆盖。

## 3. P3 失控兜底：生成任务级 idle 保险丝（与 P1/P2 解耦）

**交付项**
- [x] 3.1 `_silence_watchdog` 转正为守卫：判据沿用"事件条数不增长"，**同一机制两档阈值**（首事件前 **非思考 60s / 思考 150s**；首事件后 **30s / 60s**）—— **两档都必须 < 传输层 read 180s**，否则该档永不负责（第三轮 B2/I1）。
- [x] 3.2 只做**档 1**（记事件 + 用户可见文案），**不动控制流**；档 2（取消 / 降级）登记为 follow-up。
- [x] 3.3 档 1 使用**自有降级事件**（`reason = generation_idle`）；**SHALL NOT** 套用 `DelegateStopReason`（其语义为"fork 结束原因"，档 1 并不中止）。档 2 引入中止时再评估是否复用 / 扩展该枚举（并同步 `delegate-execution-controls` 的"不得另起原因词"约束）。

**验收**
- [x] 3.4 首事件前超阈、中间静默超阈 各触发一次并留痕，`reason` 取自取值集合（`generation_idle`）。
- [x] 3.5 **SSE 订阅层对活跃任务仍不断流**（既有语义不变，不得因本阶段改动而回退 180s 空闲收流）。

> **P3 验收结论（2026-10-16，控制方自跑；证据见 `.superpowers/sdd/2026-10-16-timeout-and-degradation-p3/t5-acceptance.md`）**：3.1–3.5 全部落地。**证据等级须如实分辨**：
> - **单测级**（真断行为）：3.1（四值默认 + 启动期拦截 + 档位选择）、3.2 / 3.3（档 1 边界与原因词）、3.4 的"每静默段一条"与"缓冲清理即退出"、3.5（结构性防回归）；
> - **端到端级**（进程内、真实 `_run_generation`）：3.4 的"静默到点仍跑完"（两个 token 都到、函数正常返回）；
> - **运行时级**（起真实服务 + 真实 LLM 调用 + 按 trace_id 回查 + 查库）：3.4 的**两档都在真机触发**（`tier=gap idle_s=1` 落在 retrieve/rerank 窗口；`tier=first idle_s=0` 在首个事件之前），且两次都仍以 `done` 收尾（B 轮库里另有 1 条 `complete` 的 assistant 行）⇒ **档 1 不中止生成**有运行时证据；正常档本轮**无** `generation_idle`（无回归）；
> - **未覆盖 / 必须知道的**：① **`tier=first` 在正常轮次几乎不触发** —— 实测阈值 0.2 秒不触发、降到 0.02 秒才触发（首个 `status`（"正在思考…"）事件 ~100ms 内即到）⇒ 该档的真实语义是"**启动阶段完全卡住**"的守护，日常长静默由 `tier=gap` 负责；② **前端呈现无自动化守卫**（`deploy/nginx/html/` 无测试设施；本阶段前端**零改动**，复用 P2 的 `degraded` stage）；③ "长静默下只发一条"仅单测覆盖。

## 4. 可观测与文档同步

- [x] 4.1 补 `embedding` / `rerank` **单阶段耗时**（现有 `retrieve done` 只有合计，无法为二者单独定值）；请求级总耗时可选。
- [x] 4.2 **对照实测**：本地真实调用 20~50 次取分位，回填 `design.md` D11 取值表的「待校准」项。

> **P4 4.2 实测结论（2026-10-16，控制方自跑；原始记录见 `.superpowers/sdd/2026-10-16-timeout-and-degradation-p4/p4-measurement.md`）**：本机 30 次真实请求（10 次开思考）已取分位并回填 D11 —— **结论是「维持现值」**，因此 D11 的「待校准」被替换为「本机实测余量表 + 生产分位仍待」，而**不是**收紧数值。最要紧的三个余量：流间隔档约 2.2 倍（非思考最大 13.7 秒 vs 30 秒；思考最大 26.71 秒 vs 60 秒）、精排内层约 2.3 倍（p99 2177 毫秒 vs 5 秒，且**含重试预算**）、embedding 查询档约 42 倍（p99 360 毫秒 vs 15 秒）。**仍无实测支撑的项已如实标注**：`classify` / `summary`（`model turn` 不带 role，且二者都不落该事件 ⇒ 本机测不出，保留保守值）、`embedding` 入库档（本机未入库）。**口径局限**：首字节由「首事件延迟」近似（非整轮）、流间隔由**日志时间戳相减**得到（墙钟口径，WSL 有偶发前推墙钟 ⇒ 只作下界参考）、且本机为**单机 idle 无并发**（生产只会更大）。
- [x] 4.3 文档同步：`best-practices.md` §11 四行状态；`requirements_pool.md` A-03 / A-05 收尾；若新增 env ⇒ **两份模板都要改**（`.env.template` + `.env.example`）；`deploy-runbook.md` 若涉及运维动作。**另须登记 `docs/agents/logging-rules.md` 的「非代码可读的值域」**（先例：`agent resolved` 的 `source`、`rerank done` 的 `scored`）：`dense_status` / `sparse_status`（`ok`/`empty`/`failed`）与各降级 `reason` 取值集合（第三轮 I3 —— 此前漏登记）。
- [ ] 4.4 **新 ADR 建档**：在 `deepeval-bridge-eval` 之后取得可用编号（它已计划用新 ADR 取代 ADR-0024），且须与 `docs/adr/README.md` 索引**同一提交**（`check_adr` 要求编号连续）。

## 5. 遗留登记（不属本变更，须登记不丢）

- [ ] 5.1 idle **档 2**（取消 / 降级，接 `task_registry`，与 `request-abort` 契约交叉）
- [ ] 5.2 `embedding` 客户端**复用**（每次 `get_embeddings()` 新建实例、连接池不复用）
- [ ] 5.3 **告警出口** → 已登记需求池 `H-12`（方向：阿里云 Grafana + 阿里云日志 SLS）
- [ ] 5.4 `src/cli/symptom_metrics.py` 是否扩为耗时统计（现只统计 iteration limit / 检索次数 / `answer_len`）
- [ ] 5.5 **403 的额度 / 鉴权判别（含响应体取证）** → 归评测链 `deepeval-bridge-eval`（其 hooks 证据机制可读状态码/响应体）；本变更按**单一文案** `provider_unavailable` 落地；若它给出**可复用判据**，再回来把该行拆成两行并各自配文案

- [ ] 5.6 **两份 env 模板的存量缺口**（**不属本变更**，2026-10-16 用差集实测出来）：`src/config/settings.py` 引用 **124** 个 env 键，而它引用的键在两份模板里并不齐 —— **按本 change 补键前**：`.env.template` 出现 89 个（缺 **67**）、`.env.example` 出现 84 个（缺 **73**）；**补完本 change 的 16 个键后**：仍缺 **51** / **57**（「缺」= settings 引用了但模板里没有的键数；该口径与计数正则无关，可稳定复现）—— 缺的多数**有内置默认**（故不影响启动），但会让人以为「这些键不存在」。本变更只补了**自己新增的 16 个键**（两份模板各 +1 段），**存量缺口未动**（那会带来 60+ 键的未评审 diff）。⇒ 建议单开一次「模板与 settings 对齐」的清理。
