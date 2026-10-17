## ADDED Requirements

### Requirement: 客户端超时与重试的显式入参与取值归属

系统 SHALL 在构造三类模型的客户端时**显式传入**超时与重试参数，SHALL NOT 依赖 SDK 默认值兜底：`build_chat` / `build_embeddings` / `build_rerank` 三者是这两类参数的**唯一产地**。openai SDK 的自动重试 SHALL 关闭（`max_retries=0`），使「一个逻辑调用 = 一次 HTTP」——SDK 默认重试会把一次逻辑调用放大为多次真实请求，且该放大在日志中不可见。

超时与重试的**取值** SHALL 按**角色**（`main` / `classify` / `summary` / `embedding` / `rerank`）区分，SHALL 住 `src/config/settings.py` 并可被 `.env` 覆盖；SHALL NOT 写入 `registry.yaml`（该文件承载 provider 的**固有事实**，超时属**部署态策略**，见本规格「配置覆盖规则」）。

**`judge` 角色显式排除**：其超时真源属评测链（`RAGAS_*` 族），由在途 change `deepeval-bridge-eval` 处置 —— 本变更 SHALL NOT 为其新增角色级取值、亦 SHALL NOT 接管其现有键（与 `timeout-policy` 规格同口径）。

**键名归属（第三轮 I6）**：生产侧角色的超时/重试 SHALL 使用**自有键名**，**SHALL NOT 复用评测链的 `RAGAS_*` 键** —— 否则评测链调整那些键会**静默改到生产**。「沿用 ADR-0024」仅指沿用其**取值与形状**（细粒度 `connect/read/write/pool` + 关 SDK 重试），**不含键名**。

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

## MODIFIED Requirements

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
