## Why

当前 prompt 有三处结构性缺陷，且它们都源于**同一件事：没有任何文档规定"哪一类规则住哪一层"**（`docs/tmp/deep-research-prompt-management.md` 第一部分）。

**触发事件（溯源）**：本变更的直接起点是**用户在真实界面上的手工走查**，不是纸面推导。走查观察到三个现象：① 选定智能体后，内置的检索与联网兜底规则在最终 prompt 中消失（`trace_c54ce259` 的 `persona_source=preset` 为其日志证据）；② 同一个问题被反复检索、直到第 5 轮迭代上限仍无收敛（日志出现 `[agent] iteration limit`，即触顶）；③ 最终答案为空（`answer_len=0`）。

⚠ **因果范围（2026-09-21 评审后收窄，不要读成"单一同源"）**：`docs/tmp/deep-research-trace-c54ce259.md` 的复核给出更精确的链条 —— 该知识库**本身没有** 2023–2025 的数据（5 轮 query 完全不同、`retrieve done result_count` 恒为 2），这是"反复检索不收敛"的**另一个必要条件**；prompt 侧缺的则是**停止与完成条件**：检索阶梯被整体替换掉后，模型没有任何"证据不足即停止""完成即停调工具"的依据，于是换着问法直到触顶，而触顶时末轮仍是 tool_call、`agent_finalize` 取不到答案文本，故 `answer_len=0`。**本变更修的是 prompt 侧那一半**；"库内有没有数据"属 change `retrieval-fetch-and-dedup`。

该走查的有效步骤由其后的 change `e2e-playwright-regression` 固化为回归用例（其 proposal「承接 P1 各 change 的人工 E2E/验收场景」一节）。

1. **载体与代码混杂**：19 个 prompt 常量以 Python 字符串字面量写在 `src/config/prompts.py`（322 行），产品侧无法编辑，任何文案调整都要走代码评审与发版。
2. **检索协议住错了层，被 persona 覆盖掉**：`FINANCIAL_SYSTEM_PROMPT`（`prompts.py:42-64`）把 role + 处理流程 + 检索与联网兜底 + 回答规则**全塞在 base 层**，而 `src/rag/prompt.py:48-53` 的 `if persona: base = persona` 会**整体替换**它。后果已实证：`trace_c54ce259` 中 `persona_source=preset`，模型只拿到预设的 4 条工作原则 + 一句"必须先检索"，检索阶梯（规则 4–8，含唯一出口）**完全不在最终 prompt 里**。
3. **补丁复制时丢失出口**：`KB_BOUND_RETRIEVAL_DISCIPLINE`（`prompts.py:74-77`）是规则 2 的**部分拷贝** —— 保留了"必须先检索 / 不要预先猜测问题是否在知识库范围内"，却删掉了"全部明显不相关则按第 4 条处理"这句出口指引。同一事实有两个 owner，且拷贝版漂移。

同时，架构定位需要澄清：本项目**是通用问答助手，现阶段偏财务**，而非财报专用。目标形态是"选定知识库 → 加载该领域的 prompt；未选 → 通用回答"，后续可扩展到人事等领域。这要求 base 段**可被领域内容替换**，而运行时契约（工具、来源选择、完成条件、输出形态）**恒定不被覆盖**。

## What Changes

- **BREAKING** prompt 载体从 Python 字符串常量迁到 **YAML 文件**（`src/config/prompts/`）。P0 阶段要求**最终 prompt 逐字不变**（纯搬运）。终态预留远端读取（产品维护），本期为 Git 内文件、开发维护。
  - **落点必须放 `src/config/` 而非顶层 `config/`**：`Dockerfile:31-33` 只 `COPY src/ scripts/ deploy/`，`docker-compose.override.yml:4-7` 只挂 `src/ tests/ skills/ agents/` —— 顶层 `config/` 既不在镜像里也不在任何挂载内，容器读不到。放 `src/config/prompts/` 随 `COPY src/` 进镜像，且符合 CLAUDE.md 的「`src/config/` 硬编码集中管理」约定。
- **BREAKING** 确立 **6 段模型**（对齐 WeKnora 九段中本项目适用的 6 段），每段有唯一 owner：
  `base`（角色/领域方法）/ `runtime_contract`（数据·指令边界 + 完成条件）/ `sources`（来源选择：检索与联网）/ `tools`（通用工具约定）/ `skills`（按需加载的方法论）/ `output`（输出形态 + 引用编码）。
- **BREAKING** persona 语义由"整体替换 base"改为"**只填 `base` 段**，运行时各段恒定追加"。
- **检索阶梯从 `base` 搬到 `sources` 段**（它描述的是"跨调用的工具使用习惯"，不是"我是谁"）；`KB_BOUND_RETRIEVAL_DISCIPLINE` 的漂移拷贝随之消解（同一事实回到唯一 owner）。
- **领域 prompt 绑定**：`base` 段按三源优先级解析 —— 用户选定的智能体预设 **>** 知识库绑定的领域 prompt **>** 内置默认 base。未绑定知识库时用内置默认（通用）。
- **内容对齐 WeKnora（P2）**：段骨架与规则条目以 WeKnora 为主、正文译中，本项目独有规则作为补充；去重归并成一份自然正文，逐条对照见 `prompt-mapping.md`。含：base 瘦身并补「已返回的完整内容不必再读」与**指针句**（"先遵循运行时来源选择规则，再套用默认检索流程"）；`runtime_contract` 采用 WeKnora 原文口径的**完成条件**（`A progress update alone does not complete the task`）与跨路径共用的**数据·指令边界**；`output` 段补齐格式/图片/URL 保真/完成前自检四条。
- **`USER_PROMPT_TEMPLATE` 去策略化**：其末尾出口提示（`prompts.py:98`）与 `sources` 段构成同一事实的两个 owner，改为纯数据模板（参考资料 / 用户请求），策略只留 `sources` 段一份。
  - ⚠ **因果强度已按 2026-09-21 评审降级（原文档写过"永不触发"，不成立）**：`retrieve_kb` 在 `contexts` 为空时返回 `""`（`src/agents/tools/rag_tools.py:230-233`），**结构上可以返空**。该次 `trace_c54ce259` 中它每次返回 2 条非空结果，因此那句出口条件**当次未被触发**。"从不返空"是**转述中的推断**，不是代码保证 —— 删除该句的理由（重复 owner、与 `sources` 段冲突、把出口条件放在 user 侧比 system 侧更易被模型当作唯一依据）不依赖这个推断。
- **经典 RAG 路径与 agent 路径共用同一套段**：两者都经 `build_system_prompt`，仅条件不同；不另立第二套分层模型。
- **模板分类法**：`src/config/prompts/` 下分**段模板**（进 system 组装）与**独立任务模板**（classifier / rewrite / entity / verify / fork，各自单独调用）。两类共用加载入口，不共用段组装。
- **远端 3 个 prompt 出列**：`financial-system-prompt` / `user-prompt-template` / `classifier-prompt` 不再走 Langfuse，本地 YAML 成为唯一事实源——否则 P0 的"逐字不变"闸门靠一个 env 开关维持，不可重复。决定、代价与复查条件见 `docs/adr/0010-delist-langfuse-prompts.md`。
- **不做 i18n**：WeKnora 的模板含 4 语言块，本项目为中文单语。结构上预留位置，本期不引入多语言工程。

## Capabilities

### New Capabilities

- `prompt-carrier`: prompt 的载体形态与加载方式 —— YAML 文件结构、加载入口、占位符规则、i18n 与远端读取的预留位。

### Modified Capabilities

- `prompt-composition`: 把既有的"**三层组装**"细化为"**六段模型**"（② 环境约束层内部再分 `runtime_contract` / `sources` / `tools` / `output` 四段，① 人设层 = `base`，③ 运行时层 = `skills` + verify 指引）；人设层解析由隐含的单源改为显式的**三选一（替换）**并补知识库领域绑定；环境约束层补**完成条件**、**数据·指令边界**、**逐条条件注入**；新增段归属、检索饱和与不重复读取分属两段、分段字符数日志三条要求。

> ⚠ 归属说明：`prompt-composition` 是在效 capability（2026-09-18 由 `session-agent-and-skill-invocation` 归档时并入）。本变更**在其之上做增量**，不另立一套分层规范 —— 六段与三层是**嵌套**关系（见 spec 的表格与说明）。

## Impact

**代码**

- 新增 `src/config/prompts/` 下的**段模板**（base / runtime_contract / sources / tools / output；skills 段沿用现有 `skills/*/SKILL.md`）与**独立任务模板**（classifier / rewrite / entity）
- 新增 prompt loader（YAML → 模板映射 → 段映射 → 组装）；两类模板共用加载入口、不共用段组装
- **启动期加载与校验**：应用启动时加载全部模板并校验 `id` 唯一 / 段模板非空 / system 段总字节上限；任一失败 → **启动失败**（透传异常），不留请求期降级分支
- `src/config/prompts.py` — **19 个常量中的 12 条删除**（不是"迁出后保留全部"），唯一事实源变为 `src/config/prompts/*.yaml`；常量名不再作为回滚手段。
  - **迁移范围（12 迁 / 7 留）**：迁入 YAML 的是 5 条段模板来源 + 用户请求模板 + 6 条离线任务模板；**保留为 Python 常量的 7 条**是 `VERIFY_*`（4）与 `FORK_*`（3）—— 它们是**行为键**（marker 是查重的键、`FORK_EXECUTION_CONTRACT` 是执行契约），模板化等于把行为键交给文案编辑者。
- `src/cli/compare_rewrite.py:27` — 对 `CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_TEMPLATE` 的 import 改为读 loader（自有 `BUNDLED_CLASSIFY_*` 副本保留，它是对比的另一边）
- `src/infra/search/query_router.py`、`src/infra/search/document_entity_extractor.py` — 随离线任务模板迁移改为读 loader
- `src/rag/prompt.py:26-77` — `build_system_prompt` 改为按段组装 + `base` 三选一（**替换**）解析；persona 语义由"整体替换 base"改为"填 `base` 段"（语义未变，但 base 已被瘦身，故不再丢系统规则）
- `src/config/prompts.py:90-99` — `USER_PROMPT_TEMPLATE` 去掉末尾策略提示，改为纯数据模板（参考资料 / 用户请求）
- `src/infra/llm/prompt_manager.py:69-73` — 远端 prompt 名单出列，`get_system_prompt()` 的幂等追加职责收口（避免与新组装器两处追加）
  - **出列的连锁范围（不是"删 3 行名单"）**：`get_user_template`（`:198`，`_get` 在 `:211`）与 `get_classifier_prompt`（`:214`，`_get` 在 `:237-238`）仍按 `PROMPT_NAMES` 索引；`_FALLBACK_SYSTEM_PROMPT`（`:31`）以 `FINANCIAL_SYSTEM_PROMPT` 为前缀；`get_system_prompt()` 的追加逻辑在 `:191-196`。**保留远端读取实现**（只移名单），使终态是"加回名单 + 固定版本"而非重写。决定与代价见 `docs/adr/0010-delist-langfuse-prompts.md`
- `src/infra/llm/prompt_manager.py` — **读取路径唯一化**：`PromptManager` 收窄为加载入口的**门面**（或直接退役），不得保留"远端 + 本地兜底 + 幂等追加"与加载入口并存的第二条路径；`_with_current_date` 保留在既定位。见 spec `<prompt-carrier>`「读取路径唯一化」与 tasks 2.22
- `src/agents/graph/agent_node.py:80-88` — 从 `ctx` 读取领域解析结果
- `src/services/agent_service.py:919-921` 附近 — 解析绑定的 KB 领域并写入 `RequestContext`
- `src/infra/llm/request_context.py` — 新增领域字段（含来源/范围/用途注释 + `child()` 复制）
- `src/agents/graph/verify/regen_decision.py` — verify 注入的两段指引改为按启用工具条件渲染

**数据模型**

- `knowledge_base` 新增 `domain` 列（`nullable=False, server_default='general'`），作为"知识库 → 领域 prompt"的唯一事实源。四处落点都要改：ORM（`src/infra/db/models/kb.py`）、**新 migration**（`alembic/versions/`）、repo（`src/infra/db/repos/kb_repo.py`）、创建/更新入参（`src/services/kb_service.py`）。
- **领域识别判据 = "是否存在对应的 `base` 模板"**（模板 id 即领域名）；非法值在**写入前**拒绝。本期 SHALL 提供最小的 domain 写入口（KB 创建/更新参数或管理脚本），否则「按领域加载对应 base」无法端到端验收。

**测试**

- **`tests/rag/test_prompt_layers.py` 会被整文件打断**：`:7-11` 在**模块级** `import KB_BOUND_RETRIEVAL_DISCIPLINE`，而 task 2.3 删除该常量 → 整个文件 import 失败，其**全部 9 条**测试连带失败（不只是 `:24` 一条）。另有 `:24-33` 与 `:100-107` 构造**真实** `PromptManager()`，会发起真实网络请求。
- **⚠ 撤回一处判断**：本文档原称 `:59`（`assert "基础段正文" not in content`）"保护的正是被移除的缺陷、必然失败"。**该判断错误** —— D3 与 spec 都**保留**"预设替换 base"语义，P1 后 `base` 即预设正文，"基础段正文"仍不会出现，该断言继续成立。
- **真正需要同步的测试清单**（按机制列，原先的"6 个文件"既多又少）：

| 文件 / 位置 | 机制 |
|---|---|
| `tests/rag/test_prompt_layers.py:7-11`、`:72-78` | 模块级 import 被删常量 → 整文件失败 |
| `tests/rag/test_prompt_layers.py:24-33`、`:100-107` | 构造真实 `PromptManager()` → 真实网络请求 |
| `tests/config/test_prompt_web_search.py` | 9 个短语断言在 `FINANCIAL_SYSTEM_PROMPT` 常量上；拆段后短语散布到 `sources`/`base`，断言失效 → 改为对**组装结果**断言 |
| `tests/config/test_prompt_delegate.py:4`、`:15` | `DELEGATE_GUIDANCE_SECTION in FINANCIAL_SYSTEM_PROMPT`；拆段（13/14 → `tools`、15 → `output`）后不成立 |
| `tests/infra/llm/test_prompt_manager_fallback.py:4-5`、`:16`、`:21` | `_FALLBACK_SYSTEM_PROMPT.startswith(FINANCIAL_SYSTEM_PROMPT)`；随载体与远端出列而变 |
| `tests/infra/test_prompt_manager.py` | `get_system_prompt()` 的日期注入与幂等；task 2.22 收口后受影响 |
| 5 个 `PromptManager` 桩测试（`test_agent_node.py`、`test_graph.py`、`test_injected_history.py`、`test_prefix_cleaning.py`、`test_prompt_layers.py` 的 `_pm`） | `build_system_prompt` 签名新增工具集/领域参数后，桩需补齐 |
| **不需要改**：`tests/agents/skills/test_fork_sub_agent_contract.py`、`tests/agents/skills/test_first_batch_skills.py` | 前者只用 `FORK_*` 两个常量（保持常量形态）；后者仅在 docstring 里提及常量名 |
- 新增 prompt 契约测试（按外部第 2 层手段，请求载荷断言）：① 选中预设后最终 system 文本仍含检索阶梯要素；② 未注册的工具名不出现在最终 prompt；③ `build_system_prompt` 产出的工具名集合 ⊆ 实际注册工具名集合
- 新增态 A 结构断言与 `user` 模板无策略断言
- 段字节数断言（对齐 WeKnora 的 `[Agent][Prompt] section=... bytes=...`）

**文档**

- `CLAUDE.md` 文档组织表 — 若新增归属文档需登记
- `docs/agents/code-map.md` — 顶层目录与 `src/` 分层是**结构唯一归属文档**，新增 `src/config/prompts/` 与加载器职责必须在此登记（CLAUDE.md「表保鲜」）
- `prompt-mapping.md`（本 change 目录内）— 逐条 before/after 全文对照（原先 → 更改后 → WeKnora 出处 → 处置）
- `docs/agents/glossary.md` — 新增段模型相关术语（base / runtime_contract / sources / domain / 指针句 / 段模板 vs 独立任务模板）
- `docs/agents/logging-rules.md` — 在既有 `PROMPT_ASSEMBLED` 事件上登记**容器值字段** `section_chars`（紧凑 JSON、值为**字符数**），以及领域回退 / 预设与领域不一致的字段。⚠ 不新增逐段日志行（级别语义无 debug 档，5 段 × 每请求属噪声）
- `docs/adr/0009-prompt-base-three-way-replacement.md` — 把 ADR-0003 里以**原地修订**记下的"base 三选一（替换）"正式化为独立决策（恢复 `docs/adr/` 的"只追加"一致性），并登记对 ADR-0003 该处三处记录的更正
- `docs/adr/0010-delist-langfuse-prompts.md` — 远端 3 个 prompt 出列的决定、**接受的代价**（线上 prompt 热回滚 = 回滚镜像）与复查触发条件
- `docs/agents/requirements_pool.md:143-146` — 该处主张"prompt 不应与代码同库、Langfuse 唯一权威"。**本变更不改其方向，而是分两阶段实现它**（Git 内 YAML → 远端读取）；由 ADR-0002 与 ADR-0010 共同说明，避免被读成路线反复。

**需重采的基线**

- 端到端 prompt 快照（P1 完成后）
- RAGAS eval 基线（P2 完成后）

**跨变更的归属声明（防两边都改或都不改）**

`KB_BOUND_RETRIEVAL_DISCIPLINE` 常量与检索阶梯（`FINANCIAL_SYSTEM_PROMPT` 规则 4–8）的**段位与措辞**归**本变更**。change `retrieval-fetch-and-dedup` 只在 code 侧提供可用信号（精排分数、去重丢弃量、分路计数），**不修改这两处任何文本**。两者并行执行时文件不重叠。

**明确不在本变更范围**

- steering（用户插话）—— 中间插话的整体代码不存在
- handle / protocol 段 —— 已决定不抄（见 `docs/tmp/kb-boundary-visibility-problem.md` 附录 D）
- memory 段 —— 已由消息层 `_truncate_history`（`agent_node.py:30-59`）承担
- 远端 prompt 服务与产品侧编辑器 —— 终态，本期只预留读取接口；KB→domain 的前端编辑口随该阶段一并做（本期仅数据层）
- 检索取数口径（另见 change `retrieval-fetch-and-dedup`）
- `src/cli/eval_ragas_generate.py:118` 的内联 prompt、以及 `src/cli/compare_rewrite.py` 自有 `BUNDLED_CLASSIFY_*` 副本（`:42`、`:73`）的**内容清理** —— 属另一类"副本清理"。
  - ⚠ **但 `compare_rewrite.py:27` 对 `CLASSIFIER_*` 的 import 必须改**（删常量的连带，已纳入范围，见 tasks 1.12）—— 它不属于 Non-Goal，否则该 CLI 会 import 失败。
- fork 子代理的段位重构（含"子代理作废父委派指令"）—— 需先决定 fork 是否套用段模型
- classifier 的 `missing_entities` 与 `ask_user` 的重复 owner 闭环 —— 本期只在归属表登记为已知重复
- 工具 `description`（docstring）的文本改写 —— 本期只做审计并在归属表登记发现，不改文案
- 完整 section registry + 命名 order 表（deepseek-harness 路线，P3，暂不建议）
