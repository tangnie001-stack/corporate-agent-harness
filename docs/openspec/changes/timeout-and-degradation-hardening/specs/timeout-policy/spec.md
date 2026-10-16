## ADDED Requirements

### Requirement: 按角色的超时与重试唯一产地

三类模型的客户端构造（`build_chat` / `build_embeddings` / `build_rerank`）SHALL 显式传入超时与重试参数，SHALL NOT 依赖 SDK 默认值兜底。openai SDK 的自动重试 SHALL 关闭（`max_retries=0`），使"一个逻辑调用 = 一次 HTTP"。

超时与重试取值 SHALL **按角色**（`main` / `classify` / `summary` / `embedding` / `rerank`）配置，并 SHALL 住 `src/config/settings.py`（可从 env 覆盖）；SHALL NOT 写入 `registry.yaml`（该文件只装 provider/模型的固有事实：端点 / 密钥名 / 窗口 / 定价 / 分词比）。

**`judge` 角色显式排除在本变更之外**：其超时真源属**评测链**（`RAGAS_*` 族），由在途 change `deepeval-bridge-eval` 处置 —— 本变更 SHALL NOT 为其新增角色级取值或接管其现有键（评审 I6）。

#### Scenario: 构造任一角色客户端
- **WHEN** 经 `build_chat` / `build_embeddings` / `build_rerank` 构造任一角色的客户端
- **THEN** 该客户端的超时与重试参数均为显式传入（非 `None`），且 SDK 自动重试为 0

#### Scenario: 同 provider 的不同角色可取值不同
- **WHEN** 同一 provider 上 `main` 与 `classify` 两个角色的超时配置不同
- **THEN** 两者各自生效（证明配置粒度是**角色属性**，不是 provider 属性）

#### Scenario: judge 不在本变更范围
- **WHEN** 检查 `judge` 角色的超时来源
- **THEN** 它由评测链的 `RAGAS_*` 族提供，SHALL NOT 由本变更新增的角色级取值接管

### Requirement: 流式调用的超时按「传输层单一预算 + 业务层事件守护」两层表达

主链路（`main`）为流式调用，其超时 SHALL 由**两层**承担，SHALL NOT 使用"`read` 只表流间隔 + 另一个更大的独立首 token 超时"这类**不可满足**的组合 —— HTTP `read` 覆盖**首个字节之前的等待**（排队 / prefill 均计入），故任何更大的"首 token 超时"都不会先触发。

- **传输层（HTTP `read`）**：SHALL 只设**一个**值，作为「请求发出 → 字节到达」的**统一预算**。SHALL NOT 把该值解释为"仅流间隔"。
- **业务层（事件守护）**：用户可感的"卡住" SHALL 由**事件守护**判定（判据 = 事件条数不增长），并 SHALL 区分两档阈值：「首个事件之前的等待」与「首个事件之后的间隔」。
- **整请求**：SHALL NOT 设置（长回答天然数分钟，设置即为误杀）。

⇒ 传输层预算是**兜底**（防连接级挂死），业务层阈值才是**面向用户的判据**；两者不得互相声称对方的能力。任何新增超时值 SHALL 小于边缘代理的读超时（nginx `proxy_read_timeout`，当前 300s），以保证用户先收到应用级告知，而不是代理断连。

#### Scenario: 长回答不被误杀
- **WHEN** 一次生成持续数分钟且期间持续有输出
- **THEN** 不因"整请求超时"被中止

#### Scenario: 首 token 迟迟不到
- **WHEN** 请求已发出但长时间未收到首个 chunk
- **THEN** 由**业务层守卫**（"首个事件之前"那一档阈值）判定并处置；传输层 `read` 只作兜底

#### Scenario: 不得用单一 read 表达两种语义
- **WHEN** 为 `main` 设定超时
- **THEN** 不存在"`read` 只表流间隔、另有更大的一独立首 token 超时"的组合（该组合不可满足：`read` 必然先触发）

#### Scenario: 传输层超时值不得大于边缘代理读超时
- **WHEN** 为任一角色设定传输层 `read` 值
- **THEN** 该值小于 nginx `proxy_read_timeout`（300s）

### Requirement: 重试按异常类别分流，且有界、带抖动、不阻塞事件循环

重试 SHALL 按"重试是否会改变结果"分流，SHALL NOT 以 `Exception` 全量捕获作为可重试集合：

- **SHALL NOT 重试**：输入校验类（如 `ValueError`，特征为失败耗时 ≈ 0）、鉴权/权限类（401/403）、请求格式类（400/422）；
- **SHALL 重试**（有界）：超时、连接错误、429、5xx。

退避 SHALL 含**随机抖动**。异步调用点使用的重试 SHALL 以异步等待实现（SHALL NOT 使用同步 sleep 阻塞事件循环）。

**流式约束**：一旦流已开始向用户输出，SHALL NOT 重试（重试会导致重复输出）；可重试窗口**仅限首个 chunk 之前**。

**实施落点（评审 I1）**：主链路的本层重试 SHALL 实施为**模型对象级包装** —— 包住模型对象、**转发工具绑定**、并在**流式生成路径内**做"已产出即不重试"守卫。SHALL NOT 依赖以下两条更省的路（经核实均不通）：`Runnable.with_retry`（**明确不重试 stream**，`langchain_core/runnables/retry.py:378`）；循环中间件的 `awrap_model_call`（只拿得到 `ModelResponse`、**拿不到 chunk**）。

#### Scenario: 输入校验类错误（失败耗时 ≈ 0）
- **WHEN** 调用因输入校验失败（如"检索上下文为空"抛 `ValueError`）
- **THEN** 不进入重试，直接按失败处置

#### Scenario: 可用性类错误
- **WHEN** 上游返回 403（额度耗尽）或 401（鉴权失败）
- **THEN** 不重试（属不可重试类），直接进入降级与告知

#### Scenario: 限流
- **WHEN** 上游返回 429
- **THEN** 有界重试，且退避等待含随机抖动

#### Scenario: 流已开始输出
- **WHEN** 首个 chunk 已推送给用户后发生中断
- **THEN** 不重试，改由降级/告知路径处置

#### Scenario: 不得只关不加
- **WHEN** 检查主链路（`main`）的重试能力
- **THEN** SDK 自动重试为 0 **且**存在**我们这一层**的有界重试（可见 / 有界 / 可留痕）；两者缺一即为不满足 —— 只关 SDK 重试而不补本层重试，会把抖动类失败从"重试 2 次"降为"**零重试**"，与"抖动 → 重试"的目标相悖

### Requirement: 首版取值以保守值起步，可观测只补既有埋点的缺口

在缺少 SLO 与主链路实测分位的前提下，首版超时取值 SHALL 采用**保守值**（远大于预期 p99、明显小于 600s、且小于 300s）。

可观测的**增量**交付 SHALL **只限现有埋点未覆盖的部分**：**embedding 与 rerank 的单阶段耗时** —— 现有检索事件的耗时是"embedding + search + rerank"的**合计**，无法为二者单独定值。主链路模型轮次与检索合计的耗时**已有现成字段**，SHALL NOT 重复新增；"是否超时 / 是否降级"的留痕 SHALL 由降级事件承载（见 `degradation-policy`），SHALL NOT 另设通用逐调用字段。

取值依据 SHALL 以表格形式落在本变更的 `design.md`，SHALL NOT 仅以代码常量形式存在。

#### Scenario: 首版上线后收敛取值
- **WHEN** 积累了真实耗时分位数据
- **THEN** 可依据**已留痕**的数据调整取值，无需先补埋点

#### Scenario: 不重复新增已有埋点
- **WHEN** 检查"每次外部调用耗时"的落点
- **THEN** 主链路模型轮次与检索合计**不新增**埋点；只有 embedding / rerank 的单阶段耗时是新增项

### Requirement: 评测侧跟随配置形状、不跟随取值

评测链路的客户端 SHALL 复用与生产相同的**配置形状**（角色级、显式化、关闭 SDK 重试），但其超时**取值** SHALL 独立（离线批量与在线等待的代价不同）。

#### Scenario: 评测选手侧沿用生产模型但取值独立
- **WHEN** 评测的"选手"使用生产模型构造
- **THEN** 其超时形状与生产一致，但取值不与生产在线链路绑定（避免把"生产超时"当作"模型能力"计入质量指标）
