## MODIFIED Requirements

### Requirement: 三类模型独立配置

系统 SHALL 支持 LLM、Embedding、Rerank 三类模型各自独立配置。配置来源为 provider 注册表（`registry.yaml`，见 `provider-compat` 规格）与 `.env` 指针：`.env` 用 `LLM_PROVIDER`（及各角色 `<ROLE>_PROVIDER`）选择 provider，用 `<PROVIDER>_API_KEY` 提供密钥，用 `<ROLE>_MODEL` / `<PROVIDER>_BASE_URL` 覆盖默认。系统 MUST NOT 再以单一 `LLM_BASE_URL` / `LLM_API_KEY` 作为跨角色的端点与密钥来源。

**超时与重试等调用策略值 SHALL NOT 视为本条所说的"模型配置来源"**：它们按角色住在 `src/config/settings.py`（见 `provider-compat` 规格「客户端超时与重试的显式入参与取值归属」），SHALL NOT 进注册表。本条约束的是"**连到哪个 provider 的哪个模型**"，策略值不是该问题的答案。

#### Scenario: provider 指针选择

- **WHEN** 开发者在 `.env` 中设置 `LLM_PROVIDER=deepseek` 与 `DEEPSEEK_API_KEY`
- **THEN** `main` / `classify` / `summary` 经兼容层解析到 `deepseek` 的端点与密钥

#### Scenario: 角色级覆盖

- **WHEN** 设置了 `LLM_PROVIDER=deepseek` 与 `CLASSIFY_PROVIDER=dashscope`
- **THEN** 分类角色使用 `dashscope`，主 / 摘要角色使用 `deepseek`；`judge` 缺省仍为 `dashscope`

#### Scenario: Embedding / Rerank / Judge 独立

- **WHEN** `LLM_PROVIDER=deepseek` 且未设 `EMBEDDING_PROVIDER` / `RERANK_PROVIDER` / `RAGAS_PROVIDER`
- **THEN** Embedding、Rerank、Judge 仍使用 `dashscope`（DeepSeek 无对应端点；judge 为离线评测，需与生产 provider 解耦）

#### Scenario: 密钥来源单一

- **WHEN** 某 provider 被某角色绑定
- **THEN** 其密钥取自该 provider 的 `<PROVIDER>_API_KEY`，注册表只写 env 名、不含密钥明文

#### Scenario: 策略值不属模型配置来源

- **WHEN** 检查某角色的超时 / 重试取值来源
- **THEN** 它取自 `settings`（`.env` 可覆盖），SHALL NOT 取自注册表

### Requirement: 配置默认值

系统 SHALL 在 `.env` 缺省时由注册表提供全部**模型固有事实**的默认值（默认 provider、各角色模型名、端点），保证 `.env` 仅含密钥/指针时系统可启动。模型 ID 随选型迭代，以 `registry.yaml` 为准。

**超时与重试等策略值的默认值 SHALL 由 `settings` 提供**（而非注册表），且 SHALL 在 `settings` 内含非空默认值 —— 即 `.env` 完全不设超时相关项时，各角色仍取到一首版保守默认值。

#### Scenario: 只设密钥

- **WHEN** `.env` 中只设置了 provider 密钥
- **THEN** 系统正常启动，各角色使用注册表的默认 provider 与模型

#### Scenario: 默认 provider

- **WHEN** 未设置 `LLM_PROVIDER`
- **THEN** 默认 provider 取注册表内置默认（`dashscope`）

#### Scenario: 默认值不含密钥

- **WHEN** 读取注册表提供的默认值
- **THEN** 返回的默认值不含任何密钥（密钥只来自 `.env`）

#### Scenario: 未设超时项时仍有默认

- **WHEN** `.env` 未设置任何超时 / 重试项
- **THEN** 各角色仍取得 `settings` 中的非空默认值，系统可启动且调用具备显式超时
