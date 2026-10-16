## ADDED Requirements

### Requirement: 每个外部依赖都有明确的降级动作

系统 SHALL 为每个外部依赖定义"失败 → 动作"的降级路径，动作 SHALL 落在该依赖的调用点（每个依赖的降级语义不同，SHALL NOT 抽象为统一的降级管理器）：

| 依赖 | 失败判定 | 降级动作 |
|---|---|---|
| Rerank | 超时 / 异常 | 退回检索原始顺序（按向量距离），并记录降级 |
| Embedding（查询） | 超时 / 异常 | 跳过稠密路，**仅用词法路**返回结果 |
| 向量库 | 异常 | "知识库暂不可用" |
| LLM | 首个 chunk 之前失败 | **若本轮已取得检索结果** → 返回**已检索原文** + 明确告知；**否则**（如首次模型调用即失败）→ **仅明确告知** —— 此时并不存在"检索原文"，不得承诺返回它 |
| Prompt 来源 | 不适用（唯一事实源为 Git 内 YAML） | — |

#### Scenario: 查询向量化失败但检索仍可用
- **WHEN** 查询 embedding 调用失败或超时，而词法检索可用
- **THEN** 该次检索 SHALL 返回词法路结果，SHALL NOT 因稠密路失败而整体抛错或返回空

#### Scenario: LLM 首个 chunk 前失败且已有检索结果
- **WHEN** LLM 在首个 chunk 之前失败，且本轮已取得检索结果
- **THEN** 用户 SHALL 收到**检索原文**与明确告知，SHALL NOT 收到空回答

#### Scenario: 无检索结果且 LLM 失败
- **WHEN** LLM 失败且本轮无任何检索结果
- **THEN** 系统 SHALL 给出明确告知，SHALL NOT 落一条空内容却标记为正常完成的消息

### Requirement: 降级留痕的唯一口径

降级事件 SHALL 定义在 `src/core/log_event_specs.py` 的 `EVENT_SPECS`（观测字段口径的唯一归属）。每次降级的**依赖**与**原因** SHALL **可判定** —— 即可由**事件名本身**承载（如 rerank 的既有事件名 `rerank failed` / `rerank timeout` 已表达原因），**MUST NOT** 强求每个降级事件都挂 `reason` 字段；取值 SHALL 可被判据匹配（以便边缘侧配置告警规则）。

SHALL NOT 复用 `CAPABILITY_DEGRADED`（其语义为 `/skills`、`/agents` 能力清单读取失败）。

#### Scenario: 任一降级发生
- **WHEN** 任一依赖走到降级路径
- **THEN** 产生一条事件，且由**事件名或字段**能唯一判定"哪个依赖、什么原因"，并可被日志侧判据匹配

#### Scenario: 能力清单读取失败
- **WHEN** `/skills` 或 `/agents` 清单读取失败
- **THEN** 仍使用 `CAPABILITY_DEGRADED`，不与依赖降级事件混淆

### Requirement: 降级原因与用户文案的取值集合

降级事件的 `reason` 取值与用户可见文案 SHALL 取自**下表**；实现 SHALL NOT 自造取值或文案，新增取值 SHALL 先补本表。

| 依赖 / 情形 | `reason` 取值 | 用户可见 | 文案要点 |
|---|---|---|---|
| Rerank 失败 / 超时 | **原因由既有事件名表达**（`rerank failed` / `rerank timeout`；两者字段均**无** `reason`）⇒ **不另设 reason、不新增事件** | 否 | 内部降级，结果仍可用，无需告知 |
| 查询 embedding 失败 | `embedding_failed` | 否 | 同上（已退词法路） |
| 向量库失败 | `vector_store_failed` | 是 | "知识库暂不可用" |
| LLM 首个 chunk 前失败（**已有检索结果**） | `llm_unavailable_with_context` | 是 | "模型服务暂不可用；以下为已检索到的原文" |
| LLM 首个 chunk 前失败（**无检索结果**） | `llm_unavailable_no_context` | 是 | "模型服务暂不可用，请稍后再试" |
| LLM 流中中断（已开始输出） | `llm_stream_interrupted` | 是 | "回答未生成完整，请重试" |
| 上游可用性类 · **额度耗尽** | `provider_quota` | 是 | 明确"服务暂不可用"，**不得**出现"重试"类诱导 |
| 上游可用性类 · **鉴权失败** | `provider_auth` | 是 | 同上 |
| 上游 **429 / 5xx 重试穷尽**（已达 attempts 上限） | `upstream_exhausted` | 是 | "服务繁忙，请稍后再试" —— 该类**可**提示重试（重试有意义） |
| 生成任务静默（事件守护触发，**不中止**） | `generation_idle` | 是 | **过程**措辞（如"仍在处理中"）；SHALL NOT 用"已超时"这类**终态**措辞 —— 档 1 并不中止生成 |

**判据（评审 I3 + 第三轮 I2）**：`provider_quota` 与 `provider_auth` **都可能以 403 出现** ⇒ 二者 SHALL 依据**响应体**区分，SHALL NOT 只看状态码。**该判据 SHALL 先取证再实现**：现仓内唯一证据只记录了 dashscope 的一个标识串、**未记载体所在层**（HTTP body / 异常 message / `.json()`），且无 deepseek 形状；又因异常经 `create_agent` / LangGraph 包装后原始 `response` 未必存活 ⇒ **实测两 provider 的 403 响应体并落表是本条的实施前置**。判据 SHOULD 按各 provider 的 **code 字段**匹配，而非全局关键词。**退化条款**：若实测证明二者**不可区分** ⇒ SHALL 降为**单一文案**并在本表标注，**MUST NOT** 保留无法触发的两行。

**通道（评审 I2 + 第三轮 B1 —— 通道**在本规格内点名**，不留待实施自由发挥）**：本表「用户可见 = 是」的取值 SHALL 经**既有 `status` 事件 + 专用 `stage` 常量**送达（**SHALL NOT** 复用 `retrieve` / `web_search` 的 stage —— 会让前端触发正文/旁白重分类；与 `turn-provenance-observability` 的 `STAGE_*` 同构）。文案落 `src/config/const.py`，前端按 `stage` 分支呈现；前端 `error` 分支现**硬编码**文案、**不消费** `data.error`，须同步改造。

#### Scenario: 取值不得自造
- **WHEN** 任一降级发生
- **THEN** 事件 `reason` 与用户可见文案均取自上表取值，不存在表外自造

#### Scenario: 可用性类不得诱导重试
- **WHEN** `reason ∈ {provider_quota, provider_auth}`
- **THEN** 用户文案不包含任何"重试"语义（该类错误重试必然再失败）

#### Scenario: 额度与鉴权可区分
- **WHEN** 上游以 **403** 失败
- **THEN** 系统按**响应体**判定为 `provider_quota` 或 `provider_auth`（两者文案不同），SHALL NOT 仅凭状态码归为一类

#### Scenario: 无"有文案无通道"的项
- **WHEN** 检查本表「用户可见 = 是」的取值
- **THEN** 每行都能指出其 SSE 通道与前端消费点，不存在"文案已定义但送不到用户"的项

### Requirement: 降级对用户可见，且不诱导无效重试

降级 SHALL 对用户可见。文案 SHALL 按错误类别可区分；对于**重试必然再次失败**的类别（额度耗尽、鉴权失败等可用性类错误），文案 SHALL NOT 诱导用户重试。

#### Scenario: 可用性类错误
- **WHEN** 上游因额度耗尽返回 403
- **THEN** 用户收到的是"服务暂不可用"类**明确告知**，而 SHALL NOT 是"请重试"类诱导重试的通用文案

#### Scenario: 可恢复类错误
- **WHEN** 失败属于可重试类且重试已穷尽
- **THEN** 文案可提示重试

### Requirement: 不引入进程内模型 fallback

系统 SHALL NOT 在进程内自动切换模型（跨 provider 或换档）来掩盖不可用。

**抖动**（秒~分钟）SHALL 由有界重试处置；**长时不可用**（小时~天）SHALL 由"快速失败 + 明确告知 + 人工部署级切换"处置，SHALL NOT 由进程内自动 fallback 处置。

#### Scenario: provider 持续不可用
- **WHEN** 主模型长时间不可用（如额度耗尽持续存在）
- **THEN** 系统 SHALL 明确告知不可用/降级，SHALL NOT 静默切换到另一个模型继续作答

#### Scenario: 抖动
- **WHEN** 失败属于超时/限流等可重试类且重试成功
- **THEN** 无需切换模型

### Requirement: 生成任务级的失控兜底

主链路生成任务 SHALL 具备**不吐事件**的 idle 保险丝（与 `fork` 子代理已有的 idle/total 形状一致），且 SHALL 按**思考档位**分档取值（开思考时首个 chunk 可能显著更晚）。

**层间边界**：该保险丝作用于**生成任务**（主 agent 是否在吐事件），SHALL NOT 与 SSE 订阅层的空闲收流混为一谈 —— 后者对活跃任务保持连接（`sse-stream-resilience` 已固化，本次不改）。

**原因词的处理**：本要求的兜底（**档 1**）**只记录、不中止**，因此 SHALL NOT 声称"复用 `DelegateStopReason`" —— 该枚举的语义是「**fork 结束原因**」，套到"只记录一次静默"上名不正。档 1 SHALL 使用自有降级事件，`reason = generation_idle`（见「降级原因与用户文案的取值集合」表）。若将来引入"真正中止"（**档 2**），再评估是否复用或扩展该枚举，且届时须同步 `delegate-execution-controls` 的「不得另起原因词」约束。

#### Scenario: 主 agent 长时间不吐事件
- **WHEN** 生成任务在阈值内未产生任何事件
- **THEN** 记录一条自有降级事件（`reason = generation_idle`）并给出用户可见文案，SHALL NOT 中止生成

#### Scenario: 开思考档位
- **WHEN** 该轮开启思考
- **THEN** 使用与关闭思考不同的阈值档位

#### Scenario: 活跃任务长静默不断流
- **WHEN** 生成任务仍在运行且 SSE 订阅层期间无事件
- **THEN** 主 POST 流保持连接（不得因本次新增的保险丝而改变该既有语义）
