# provider-compat Specification

## Purpose
TBD - created by archiving change provider-compat-layer. Update Purpose after archive.
## Requirements
### Requirement: Provider 注册表（数据面）

系统 SHALL 维护一个 provider 注册表文件（`src/infra/llm/provider/registry.yaml`），以 provider 名为键声明：`base_url`、`api_key_env`、`thinking_style`、`reasoning_field`、能力声明（`tools`；`vision` / `supports_disable_thinking` 延后到有真实 provider 再加）、各角色的默认模型名（`models`）、以及模型目录（`catalog`：`context_window`、`pricing`、`tokens_per_cjk_char`、embedding 批大小与维度）。密钥本身 MUST NOT 出现在注册表中——注册表只引用密钥所在的 env 名。

#### Scenario: 注册表结构完整

- **WHEN** 系统启动并加载 `registry.yaml`
- **THEN** 每个 provider 条目被解析为 `ProviderSpec`，未知字段与缺失必填字段在校验阶段报错

#### Scenario: 密钥不入库

- **WHEN** 某个 provider 的 `api_key_env` 为 `DEEPSEEK_API_KEY`
- **THEN** 层从环境变量读取 `DEEPSEEK_API_KEY` 作为密钥
- **THEN** 注册表文件中不出现任何密钥明文

#### Scenario: 随镜像分发

- **WHEN** 构建应用镜像
- **THEN** `registry.yaml` 因 `COPY src/ src/` 被打入镜像，无需修改 `Dockerfile`

### Requirement: 思考参数归一化

系统 SHALL 通过单一函数把"思考开/关"（`thinking: bool`）归一化为目标 provider 的参数形状，MUST NOT 在调用点（middleware / 工厂 / 执行器）各自构造思考参数。

#### Scenario: DashScope 形状

- **WHEN** provider 的 `thinking_style` 为 `dashscope` 且思考开启
- **THEN** 产出 `extra_body` 为 `{"enable_thinking": true}`，并在思考开启时附加 `preserve_thinking` 标志

#### Scenario: DeepSeek 形状

- **WHEN** provider 的 `thinking_style` 为 `deepseek` 且思考开启/关闭
- **THEN** 产出 `extra_body` 为 `{"thinking": {"type": "enabled"}}` / `{"thinking": {"type": "disabled"}}`

#### Scenario: 扩展新形状

- **WHEN** 需要支持一种新的思考参数形状
- **THEN** 只需新增一个 `ThinkingStyle` 枚举成员与其翻译分支，并加一条注册表数据
- **THEN** 所有调用点无需改动

### Requirement: 思考开关始终显式

系统 SHALL 对每次模型调用显式发送思考开关，MUST NOT 依赖 provider 的默认值。

#### Scenario: 默认关思考的 provider

- **WHEN** 目标 provider 默认关闭思考且本次未启用深度思考
- **THEN** 收到显式的"关闭"参数

#### Scenario: 默认开思考的 provider

- **WHEN** 目标 provider 默认开启思考且本次未启用深度思考
- **THEN** 收到显式的"关闭"参数（避免静默变贵）

### Requirement: 角色解析与缺省

系统 SHALL 按角色（`main` / `classify` / `summary` / `judge` / `embedding` / `rerank`）解析目标 provider 与模型。`main` / `classify` / `summary` 的缺省 provider SHALL 跟随 `LLM_PROVIDER`；`embedding` / `rerank` / `judge` 的缺省 provider SHALL 独立为 `dashscope`，MUST NOT 跟随 `LLM_PROVIDER`（`judge` 是离线评测角色，跟随即会随生产 provider 切换而 404，且破坏"选手 / 裁判分离"）。未设 `<ROLE>_MODEL` 时，模型 SHALL 取自 `registry.models[<role>]`。

#### Scenario: 主族跟随全局指针

- **WHEN** `LLM_PROVIDER=deepseek` 且未设角色级 provider
- **THEN** `main` / `classify` / `summary` 解析到 `deepseek`

#### Scenario: 角色级覆盖

- **WHEN** `LLM_PROVIDER=deepseek` 但 `CLASSIFY_PROVIDER=dashscope`
- **THEN** `classify` 角色解析到 `dashscope`，主族其余仍为 `deepseek`

#### Scenario: 独立缺省角色不跟随

- **WHEN** `LLM_PROVIDER=deepseek` 且未设 `EMBEDDING_PROVIDER` / `RERANK_PROVIDER` / `JUDGE_PROVIDER`
- **THEN** `embedding` / `rerank` / `judge` 仍解析到 `dashscope`

#### Scenario: 未设角色模型则用注册表默认

- **WHEN** `.env` 未设 `<ROLE>_MODEL`（空或未配置）
- **THEN** 该角色使用 `registry.models[<role>]`，MUST NOT 回落到代码中的硬编码模型名

### Requirement: reasoning 内容回传按能力驱动

系统 SHALL 依据注册表的 `reasoning_field` 决定是否把 assistant 消息的思考内容回传给 provider；`preserve_thinking` 参数 SHALL 仅在 provider 声明需要时注入。构造时与请求时使用的思考参数 SHALL 由同一函数产出，MUST NOT 因两处 `extra_body` 相互覆盖而丢失字段。

#### Scenario: 回传字段存在

- **WHEN** provider 的 `reasoning_field` 为 `reasoning_content` 且历史 assistant 消息带有思考内容
- **THEN** 该内容被注入请求 payload 的对应字段

#### Scenario: 不回传字段

- **WHEN** provider 的 `reasoning_field` 为空
- **THEN** 系统不回传思考内容，也不注入多余的思考字段

#### Scenario: 构造与请求同源

- **WHEN** 一次调用同时需要构造期与请求期的思考参数
- **THEN** 两处使用同一函数的产出，完整保留包括 `preserve_thinking` 在内的全部标志

### Requirement: 配置覆盖规则

系统 SHALL 支持 `.env` 覆盖注册表默认值，优先级为"`.env` 若设，否则注册表默认"。可被 `.env` 覆盖的仅限部署态五类：provider 指针、角色模型名（`<ROLE>_MODEL`）、端点（`<PROVIDER>_BASE_URL`）、密钥（`<PROVIDER>_API_KEY`）、**超时与重试取值**。`thinking_style` / `capabilities` / `reasoning_field` 属 provider 事实，MUST NOT 被 `.env` 覆盖。当 `.env` 覆盖的模型名不在注册表 `catalog` 中时，系统 SHALL 记录告警并以"成本未知"（NULL）处理。

> **本条的判据是「事实 vs 策略」**：`thinking_style` / `capabilities` / `reasoning_field` 不可覆盖，因为它们刻画"该 provider 是什么"；**超时与重试刻画"我们打算怎么用它"**，属部署态取舍，与端点 / 模型名同类 ⇒ 并入可覆盖集合。

#### Scenario: 覆盖模型名

- **WHEN** `.env` 设 `LLM_MODEL=deepseek-v4-pro` 覆盖注册表默认
- **THEN** 该角色使用被覆盖的模型名

#### Scenario: 覆盖端点

- **WHEN** `.env` 设 `DEEPSEEK_BASE_URL` 指向自建代理
- **THEN** 该 provider 的请求发往被覆盖的端点

#### Scenario: 事实不可覆盖

- **WHEN** `.env` 试图通过环境变量改变 `thinking_style`
- **THEN** 该尝试无效，思考形状仍取自注册表

#### Scenario: 覆盖到未知模型

- **WHEN** `.env` 覆盖的模型名不在注册表 `catalog` 中
- **THEN** 系统记录告警，并按成本未知（NULL）处理

#### Scenario: 覆盖超时与重试取值

- **WHEN** `.env` 为某角色设置超时或重试取值
- **THEN** 该角色的客户端使用被覆盖的取值（覆盖生效，且不改变注册表中的任何字段）

### Requirement: 启动期校验（fail-fast）

系统 SHALL 在**应用启动期**校验配置，任一条不满足 SHALL 使启动失败并给出明确错误：引用了未知 provider；`thinking_style` 无对应翻译分支；某角色绑定的 provider 在 `models` 中缺少该角色（如 `embedding` 绑到无 embedding 的 provider）；被实际绑定的 provider 缺少 `<PROVIDER>_API_KEY`；注册表缺字段或格式非法。校验 SHALL NOT 在模块导入（`import settings` / `import models`）时触发；SHALL 支持 `PROVIDER_VALIDATE=off` 跳过校验（供测试或无需模型密钥的脚本），该开关**默认 `on`**，并在启动日志显式打印当前校验状态。

#### Scenario: 角色绑到缺能力的 provider

- **WHEN** 配置 `EMBEDDING_PROVIDER=deepseek`（DeepSeek 无 embedding）
- **THEN** 启动失败并提示该 provider 不具备 embedding 能力

#### Scenario: 缺密钥

- **WHEN** 某被绑定的 provider 未提供 `<PROVIDER>_API_KEY`
- **THEN** 启动失败并提示缺失的密钥环境变量名

#### Scenario: 未知 provider

- **WHEN** 某角色指向注册表中不存在的 provider
- **THEN** 启动失败并提示未知 provider 名

#### Scenario: 校验可关闭

- **WHEN** 设置 `PROVIDER_VALIDATE=off`
- **THEN** 跳过启动校验，进程不被配置错误阻断

#### Scenario: 导入不触发校验

- **WHEN** 仅导入 `settings` / `models`（未启动应用）
- **THEN** 不因缺密钥或配置错误抛出异常

### Requirement: 客户端超时与重试的显式入参与取值归属

系统 SHALL 在构造三类模型的客户端时**显式传入**超时与重试参数，SHALL NOT 依赖 SDK 默认值兜底：`build_chat` / `build_embeddings` / `build_rerank` 三者是这两类参数的**唯一产地**。openai SDK 的自动重试 SHALL 关闭（`max_retries=0`），使「一个逻辑调用 = 一次 HTTP」——SDK 默认重试会把一次逻辑调用放大为多次真实请求，且该放大在日志中不可见。

超时与重试的**取值** SHALL 按**角色**（`main` / `classify` / `summary` / `embedding` / `rerank`）区分，SHALL 住 `src/config/settings.py` 并可被 `.env` 覆盖；SHALL NOT 写入 `registry.yaml`（该文件承载 provider 的**固有事实**，超时属**部署态策略**，见本规格「配置覆盖规则」）。

**`judge` 角色显式排除**：其超时真源属评测链（`JUDGE_*` / `EVAL_*` 族），由已归档 change `deepeval-bridge-eval` 与 ADR-0027 处置 —— 本变更 SHALL NOT 为其新增角色级取值、亦 SHALL NOT 接管其现有键（与 `timeout-policy` 规格同口径）。

**键名归属（第三轮 I6）**：生产侧角色的超时/重试 SHALL 使用**自有键名**，**SHALL NOT 复用评测链的 `JUDGE_*` / `EVAL_*` 键** —— 否则评测链调整那些键会**静默改到生产**。「沿用 ADR-0024」仅指沿用其**取值与形状**（细粒度 `connect/read/write/pool` + 关 SDK 重试），**不含键名**。

精排（rerank）调用 SHALL 具备**可终止**的超时：底层调用在超时后自行结束，SHALL NOT 仅依赖外层放弃等待（外层放弃等待不会终止底层调用）。

Provider 层 SHALL NOT 承载降级动作（ADR-0020/0021：该层不是网关，不做路由 / fallback / 限流）。

#### Scenario: 构造即显式

- **WHEN** 经 `build_chat` / `build_embeddings` / `build_rerank` 构造任一角色的客户端
- **THEN** 其超时与重试参数均为显式传入（非 `None`），且 SDK 自动重试为 0

#### Scenario: 同 provider 的不同角色取值可不同

- **WHEN** 同一 provider 下 `main` 与 `classify` 两个角色的超时配置不同
- **THEN** 两者各自生效（配置粒度是**角色属性**，不是 provider 属性）

#### Scenario: 精排超时可终止

- **WHEN** 一次精排调用超时
- **THEN** 底层调用结束（不残留挂起的调用），本次检索走降级路径

#### Scenario: 超时取值不来自注册表

- **WHEN** 检查任一角色的超时取值来源
- **THEN** 它取自 `settings`（`.env` 可覆盖），SHALL NOT 取自 `registry.yaml`
