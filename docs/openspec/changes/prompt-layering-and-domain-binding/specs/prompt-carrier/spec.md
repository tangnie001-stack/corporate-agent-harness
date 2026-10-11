## ADDED Requirements

### Requirement: YAML 载体与目录结构

系统的 prompt 内容 SHALL 存放在 **prompt 包**内的 YAML 文件中，而非 Python 源码字面量。

**包结构（`prompts.py` 升级为包）**：原 `src/config/prompts.py` SHALL 改为包 `src/config/prompts/`，内含：

| 路径 | 职责 |
|---|---|
| `src/config/prompts/__init__.py` | 保留原模块的公开符号（re-export），使既有 `from src.config.prompts import X` 的导入路径**不变**；保留的 7 条**行为键**常量（`VERIFY_*` / `FORK_*`）住在这里 |
| `src/config/prompts/templates/*.yaml` | 模板正文 |
| `src/config/prompts/` 包内其余模块 | 加载器实现 |

⚠ **不得让 `prompts.py` 与 `prompts/` 并存**：同名包会**遮蔽**同名模块 —— `from src.config.prompts import ...` 会解析到包，被遮蔽模块里的符号全部不可见。本项目有 8+ 个调用点（`rag/prompt.py`、`infra/llm/prompt_manager.py`、`verify/guardrails.py`、`verify/regen_decision.py`、`infra/search/query_router.py`、`infra/search/document_entity_extractor.py`、`cli/compare_rewrite.py` 等），并存会让它们**全数失效**。

每个模板文件 SHALL 以 `templates:` 为根。

每个模板 SHALL 包含：

- `id` —— kebab-case 唯一标识符（**仅为标识**，不承载"属于哪一段"的信息）
- `kind` —— 取值 `section` 或 `task`，区分"参与 system 组装的段模板"与"单独调用的任务模板"
- `content` —— 正文（YAML 块标量 `content: |`，保留原始换行与缩进，使文本与源码字符串逐字对应）

`kind: section` 的模板 SHALL 另含 `section` 字段（取值 `base` / `runtime_contract` / `sources` / `tools` / `output`）；`section: base` 的模板 SHALL 另含 `domain` 字段标明所属领域（通用 base 为保留值 `general`）。

**为什么用显式字段而不是命名约定或目录**：`section` 决定模板进哪一段、`domain` 决定 base 的三选一，二者都是**可校验的结构声明**。若改成从 `id` 字符串解析（如 `base-finance`），改名即静默失配；若改成按目录区分（`prompts/domains/`），"一个段有多个领域变体"会散进目录结构，反而难查。

**迁移范围**：`src/config/prompts.py` 的 19 个常量中，**12 条迁入 YAML**（5 条段模板来源 + 用户请求模板 + 6 条离线任务模板），**7 条保留为 Python 常量**（`VERIFY_*` 4 条、`FORK_*` 3 条）。

**为什么 `VERIFY_*` / `FORK_*` 不模板化**：它们不是文案，是**行为键** —— `VERIFY_*` 内嵌查重标记短语（`const.VERIFY_*_MARKER`，业务代码靠它判"是否已注入"），`FORK_EXECUTION_CONTRACT` 是子代理的执行契约（`prompts.py` 注释明说"不随执行者人设内容作者意愿而增减"）。模板化等于把行为键交给文案编辑者。

#### Scenario: 模板可从 YAML 读取
- **WHEN** 加载器读取 `src/config/prompts/templates/` 下的模板文件
- **THEN** SHALL 得到以 `id` 为键的模板映射，每个模板的 `content` 与 YAML 中的块标量逐字相同

#### Scenario: 包化不破坏既有导入路径
- **WHEN** `prompts.py` 升级为 `prompts/` 包后
- **THEN** `from src.config.prompts import X` SHALL 仍然可用（`__init__.py` re-export），SHALL NOT 出现模块被同名包遮蔽的情况

#### Scenario: 新增文案不改 Python
- **WHEN** 需要修改某个已迁入 YAML 的 prompt 文案
- **THEN** SHALL 只需修改 `src/config/prompts/` 下的 YAML 文件，不改动任何 `.py`

#### Scenario: 段归属由字段声明而非命名推断
- **WHEN** 检查任一 `kind: section` 的模板
- **THEN** 其 `section` 字段 SHALL 显式给出段名，加载器 SHALL NOT 从 `id` 字符串推断段名

#### Scenario: 领域 base 由字段标识
- **WHEN** 检查领域 base 模板
- **THEN** 它 SHALL 同时含 `section: base` 与 `domain: <领域名>`，使一个段可拥有多个领域变体

#### Scenario: 行为键不迁入 YAML
- **WHEN** 检查 YAML 模板集合
- **THEN** SHALL NOT 包含 `VERIFY_*` 与 `FORK_*` 的正文；它们 SHALL 保留为 Python 常量

### Requirement: 加载入口与占位符规则

系统 SHALL 提供唯一的 prompt 加载入口：YAML → 段映射 → 组装的 prompt 文本。加载入口 SHALL 是段模型唯一的读取点。

占位符 SHALL 只支持**仅标识符式**替换（形如 `{identifier}`）。系统 SHALL NOT 引入条件、循环或表达式语法；未在上下文中提供的占位符 SHALL 原样保留，不得被替换为空字符串。

#### Scenario: 未提供的占位符原样保留
- **WHEN** 模板含 `{query}` 而调用方未提供该变量
- **THEN** 输出文本 SHALL 原样包含 `{query}`，而非空串

#### Scenario: 不引入模板表达式语法
- **WHEN** 模板正文含 `{% if %}` 或 `{{ }}` 之类的表达式写法
- **THEN** 系统 SHALL 将其视为普通文本原样输出，不做求值

### Requirement: 占位符替换的唯一性与失败语义

占位符替换 SHALL 由加载入口统一完成，SHALL NOT 由消费点再用 `str.format` 等会抛错的机制二次替换。

未在上下文中提供的占位符 SHALL 原样保留；替换过程 SHALL NOT 因未知字段而抛异常或中断请求。

#### Scenario: 未提供的占位符不导致失败
- **WHEN** 模板含 `{current_time}` 而调用方未提供该变量
- **THEN** 请求 SHALL 正常完成，输出文本原样包含 `{current_time}`

#### Scenario: 消费点不再二次替换
- **WHEN** 检查 prompt 的消费点（system 组装、user 模板渲染）
- **THEN** 其中 SHALL NOT 存在对模板再调用 `str.format` 的替换

### Requirement: 读取路径唯一化

系统的 prompt 模板 SHALL 只经加载入口读取。迁移完成后 SHALL NOT 存在第二条读取路径。

`PromptManager` 现有的三项职责 SHALL 被分别处置：

| 现有职责 | 处置 |
|---|---|
| 远端拉取（Langfuse） | 3 个 name **出列**，见 `docs/adr/0010-delist-langfuse-prompts.md` |
| 本地兜底正文（`_FALLBACK_*`） | 由加载入口承担；`PromptManager` SHALL NOT 内另存一份副本 |
| 系统追加段（引用指令 / 委派引导 / 日期） | 移交给段组装器；`_with_current_date` 保留在既定位 |

**允许的形态**：把 `PromptManager` 收窄为加载入口的**门面**（`get_user_template` 等转发给加载入口），或直接退役。SHALL NOT 保留"两条路径并存"的形态 —— 那是双入口重复注入的来源。

#### Scenario: 不存在第二条读取路径
- **WHEN** 检查 prompt 模板的读取点
- **THEN** SHALL 只有加载入口一处；`PromptManager` SHALL NOT 再持有模板正文或其兜底副本

#### Scenario: 系统追加段只注入一次
- **WHEN** 组装一轮 system prompt
- **THEN** 引用指令 / 委派引导 / 日期 SHALL 各出现一次，SHALL NOT 因两条路径并存而重复

### Requirement: 载体替换的逐字不变量（P0 过渡；终态由结构契约承担）

把 prompt 从 Python 常量迁移到 YAML 的过程 SHALL 保持最终 prompt 文本逐字不变。

该不变量是 **P0（纯搬运、未引入段模型）阶段的过渡证明手段，不是终态契约**：其 golden 基线由一次性脚本从**迁移前的常量路径**（不经过 `PromptManager` 的远端分支）产出并提交入库（`e09e126` / `3d54630`）；逐字断言与段模型互斥，故随 P1 引入段模型后**退役**（`ad87dad`，task 2.28）——终态契约由 `prompt-composition` 的「默认行为逐字不变（端到端快照）」改写后的**结构不变**承担。

**仍然有效的约束（终态）**：任何承担该闸门职责的 prompt 组装测试 SHALL 只读取**本地模板**（经唯一加载入口），结果 SHALL 可重复，且 SHALL NOT 发起真实网络调用；`PromptManager` 的远端分支 SHALL NOT 成为测试的事实源。

**为什么基线必须来自迁移前的常量路径**：P0 之前 YAML 模板尚不存在 —— 此时"pin 到本地模板"不可能成立；唯一能同时覆盖迁移前后的可比对象，是"同一输入下的最终文本"那份 golden。

**为什么这条终态约束仍成立**：`tests/rag/test_prompt_layers.py:24-33` 原断言 `messages[0].content == PromptManager().get_system_prompt()`，而 `get_system_prompt()` 是**远端 + 本地兜底**且远端只取 latest（`src/infra/llm/prompt_manager.py`），默认 `LANGFUSE_ENABLE=true` —— 不可重复，且在 CI 中会发起真实网络请求。本变更 SHALL 消除该测试对网络的依赖（已实现：改用替身并 pin 本地模板）。

#### Scenario: 组装测试不依赖远端

- **WHEN** 运行 prompt 组装的契约测试
- **THEN** 该测试 SHALL 只读取本地模板（经唯一加载入口）
- **THEN** 结果 SHALL 可重复，且 SHALL NOT 发起网络请求

### Requirement: 载体数据源可替换（远端读取预留）

加载入口 SHALL 与数据源解耦：模板来源应可替换为远端数据源（面向产品侧维护），而段模型与组装逻辑不变。

系统 SHALL NOT 在本期内实现远端数据源，但加载入口的接口形态 SHALL 使其成为一个实现替换而非结构改动。

**唯一事实源**：迁移完成后，每个段/模板的正文 SHALL 只有一个来源（本地 YAML）。系统 SHALL NOT 保留与该来源并存的第二份正文副本（包括作为"回滚用"的 Python 常量）—— 并存即构成漂移风险与双轨制。回滚 SHALL 通过版本控制（`git revert`）而非保留副本实现。

#### Scenario: 数据源可替换而不改段模型
- **WHEN** 模板来源由本地文件改为远端数据源
- **THEN** 段模型、段归属与组装顺序 SHALL 保持不变，仅数据读取实现被替换

#### Scenario: 本期不实现远端读取
- **WHEN** 检查本期的实现范围
- **THEN** SHALL 只存在本地 YAML 数据源，不含远端服务的调用代码

#### Scenario: 每段只有唯一来源
- **WHEN** 任一次组装中检查某个段的正文来源
- **THEN** SHALL 只有一个来源；系统 SHALL NOT 存在与 YAML 并存的 Python 常量副本

#### Scenario: 回滚不依赖保留副本
- **WHEN** 需要回滚载体迁移
- **THEN** 手段 SHALL 是版本控制操作，而非切换到另一份保留的正文副本

### Requirement: i18n 结构预留但不启用

模板 SHALL 在结构上允许按语言键组织正文，但系统 SHALL NOT 在本期内引入多语言选择或语言回退逻辑。

#### Scenario: 本期按单语言解析
- **WHEN** 加载一个模板正文
- **THEN** 系统 SHALL 直接取单一正文，不进行语言选择

### Requirement: 模板加载的失败语义

模板 SHALL 在**进程启动时**加载并校验，校验项至少包括：`id` 唯一、`kind` / `section` / `domain` 字段合法、每个 `section` 至少有一个模板、段模板正文非空、**system 段总字符数的"事故兜底"上限**（默认 `50000` 字符）。

**为什么阈值是 50000 而不是按预算推导**：外部核对结果显示 —— ① **未找到**厂商给出"system prompt 应占 context window 百分之多少"的权威建议（Anthropic 官方只有定性的 "find the smallest possible set of high-signal tokens"）；② 同类生产项目的 system prompt 实测在 **6K–24K 字符**量级且**普遍无硬上限**（codex 的 md 源文件 6,621–24,026 字符；WeKnora 单个 `agent_system_prompt.yaml` 为 18,120 字符）。因此该上限 SHALL NOT 被当作**预算闸门**，只用于拦"整篇文档被误粘贴进模板"这类**明显损坏**；真正的体积判断交给「system 段占比观测」（见 `<prompt-composition>`）。

⚠ **不得引用 `claude-code/src/components/agents/validateAgent.ts:98-100` 的 10000 作为依据** —— 那是 Claude Code 校验**用户自定义 agent 表单输入**的上限，不是它自身 system prompt 的体积，用它做本项目 system 段上限属**层级错配**（本规格初期版本曾如此，已于 2026-09-21 更正）。

任一项校验失败时，进程 SHALL **启动失败**（透传异常并记录 `logger.exception`），SHALL NOT 提供"请求期降级到某份内嵌正文"的分支 —— 否则故障会在每个请求上重复出现且难以定位，与"唯一事实源"（见「载体数据源可替换」）也冲突。

**与 Langfuse 官方建议的差异（显式记录）**：Langfuse 官方文档（`prompt-management/features/guaranteed-availability`）主张"启动期预取 **+ 提供 fallback**"以保证 100% 可用。本系统 SHALL 采纳其**启动期预取**部分，SHALL NOT 采纳 fallback —— 该建议的前提是**远端源可能不可达**，而本系统的模板源是**打进镜像的本地文件**：它不可达意味着应用本身已损坏，此时静默降级到一份会漂移的内嵌副本，比启动失败更危险。

请求期 SHALL 只允许一种省略：因**条件不成立**（工具未注册 / 适用域不成立）而某段为空 → 该段不输出。

#### Scenario: 目录缺失或 YAML 非法则启动失败
- **WHEN** 模板目录不存在，或任一 YAML 无法解析
- **THEN** 进程 SHALL 启动失败并在日志中记录异常，SHALL NOT 以空 prompt 或内嵌副本继续提供服务

#### Scenario: id 重复则启动失败
- **WHEN** 两个模板声明了相同的 `id`
- **THEN** 进程 SHALL 启动失败

#### Scenario: 归属字段非法则启动失败
- **WHEN** 某个 `kind: section` 的模板缺少 `section` 字段，或 `section: base` 缺少 `domain` 字段，或某个 `section` 没有任何模板
- **THEN** 进程 SHALL 启动失败

#### Scenario: 明显损坏的模板体积则启动失败
- **WHEN** system 段正文总字符数超过**事故兜底**上限（默认 50000）
- **THEN** 进程 SHALL 启动失败，并在日志中给出各段字符数
- **AND** 正常范围内（例如同类实测的 6K–24K 字符）的体积变化 SHALL NOT 阻断启动

#### Scenario: 不做请求期模板级降级
- **WHEN** 处理一个请求
- **THEN** SHALL NOT 因模板不可用而回退到另一份正文；唯一的省略是条件段为空不输出
