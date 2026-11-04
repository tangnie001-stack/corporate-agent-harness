# MCP 治理平台选型调研

> 场景：内部已有若干 MCP 服务，需要**提供给其他部门**使用，因此需要目录（谁能用哪些工具）、身份与授权、审计、限流四件事。
> 结论时效：2026-11-04。来源均为一手文档（厂商官方站点/仓库），次级对比文仅用于发现候选。

## 一、结论先行

| 排序 | 方案 | 适用理由 | 主要代价 |
|---|---|---|---|
| **1** | **阿里云 AI 网关 · MCP 网关管理** | 已在阿里云；托管免运维；原生支持"消费者身份"鉴权、REST→MCP 转换、SSE / Streamable HTTP；与 Nacos Registry 集成 | 按量计费；治理能力上限受产品迭代约束；跨云迁移不带走 |
| **2** | **Nacos 3.0（MCP Registry）+ Higress** | 开源、可自托管；Nacos 管注册与元数据、Higress 管流量与协议转换；国内生态成熟 | 需要自己运维；当前应用机 2C8G 余量紧张，需单独一台或上云托管版 |
| **3** | **IBM ContextForge** | 单个 Python 服务即得"注册中心 + 代理 + 治理"；联邦 MCP / A2A / REST / gRPC；有管理 UI | 社区项目，长期维护风险自担；与阿里云生态无协同 |
| 4 | **Kong AI Gateway** | 成熟的 API 网关加 MCP 能力：把 API 转成 MCP server，复用既有安全/限流/可观测插件 | 企业版能力为主；引入一套新网关 |
| 5 | **Docker MCP Gateway** | 容器隔离 + 密钥托管 + 统一入口，开发侧体验最好 | 面向单机/开发者，不是部门级多租户治理 |
| — | 官方 MCP Registry | 只是公共目录（发现用） | 不提供鉴权、限流、审计 |
| — | TrueFoundry / Speakeasy / Arcade | 商业 SaaS，开箱即用 | 数据出域；对敏感企业不适用 |

**一句话建议**：先在**阿里云 AI 网关**上试点（零运维、鉴权与审计现成），把"部门级"的授权模型（应用/部门 → 可用工具集）在网关侧跑通；只有在需要脱离阿里云或成本不可控时，才迁到 **Nacos + Higress** 自托管。

## 二、评估维度

治理平台要回答的不是"能不能连"，而是这四件事：

1. **目录与发现**：某个部门能看到、能调用哪些工具（按工具还是按虚拟服务分组）。
2. **身份与授权**：调用方是"人"还是"应用"；能否按部门/应用签发凭证并绑定工具集。
3. **审计**：谁、何时、调了哪个工具、参数摘要、返回码；能否导出给审计方。
4. **配额与稳定性**：QPS/并发/日额度、超时、熔断、灰度。

附加项：协议支持（SSE / Streamable HTTP）、后端形态（原生 MCP / REST 转 MCP）、部署形态（托管 / 自建）、可观测（指标、链路）。

## 三、候选详解

### 1. 阿里云 AI 网关 · MCP 网关管理

官方文档（更新 2026-05-06）明确的能力：

- **两种接入**：后端是 MCP Server → 直接代理；后端是 HTTP Server → **HTTP 转 MCP**（RESTful API 平滑迁移）。
- **两种传输**：SSE 与 Streamable HTTP。
- **鉴权**：集成**基于消费者身份的鉴权认证机制**——这是"给其他部门用"的关键，凭证按消费者发放而不是共享一个 key。
- **工具生成**：导入 Swagger/OpenAPI 即可生成或增量更新 MCP 工具列表，并可人工确认描述。
- **生态协同**：可与 **Nacos Registry** 深度集成，通过 Nacos 的 MCP Router 做服务注册发现与协议自动转换，结合 **Higress AI 网关**做动态治理与智能路由。

适配判断：与现有部署（阿里云 ECS + Docker Compose）同云，无需自建网关；缺点是计费与锁定，且"部门级多租户"的粒度要实测确认（消费者 ≠ 部门，可能需要一层映射）。

### 2. Nacos 3.0 MCP Registry + Higress

Nacos 3.0（2025-04 发布）把 MCP 作为一等公民：

- **MCP Registry**：MCP Server 的注册、编排、动态调试与管理；Tools 描述与列表可动态更新，无需重启。
- **多来源**：Local MCP Server、Remote MCP Server、MCP 代理网关统一纳管。
- **Nacos-MCP-Router**：动态发现、自动安装、代理 MCP Server。
- **协议转换**：普通 HTTP / RPC 服务可快速转为 MCP 服务。
- **安全**：3.0 起**默认开启鉴权**，支持动态数据源密钥等零信任方案。

Higress 负责数据面（AI 网关），Nacos 负责控制面（注册与元数据），这是国内最常见的开源组合。

适配判断：可控、可迁移、无厂商锁定；代价是要多运维两套组件，且 2C8G 的应用机上跑不动，需要独立机器。

### 3. IBM ContextForge

官方描述：**开源 registry + proxy，把 MCP、A2A、REST/gRPC 联邦成单一端点**，提供集中治理、发现与可观测：

- Tools Gateway（MCP / REST / gRPC → MCP 转换）、Agent Gateway（A2A、OpenAI 兼容、Anthropic 路由）、API Gateway（限流、鉴权）。
- 以 Python 包与容器分发（PyPI 包名 `mcp-contextforge-gateway`，另有容器镜像），单服务即可起。
- 插件机制，可扩展。

适配判断：如果不想绑定云厂商，这是"一个容器换一套治理"的最短路径；风险是社区项目，接口演进快，需要锁版本。

### 4. Kong AI Gateway

Kong 的定位是"把 API 变成 MCP server"：

- 通过 AI Gateway 把任意网关 API 转成 MCP server，再套用既有插件（认证、限流、可观测）。
- 另有 Dev Portal 生成 MCP server，供各团队自助接入。
- 强调生产级远程 MCP 流量：安全、性能、上下文传递、负载均衡、流量日志与指标。

适配判断：已有 Kong 的企业最划算；从零引入则是"为了 MCP 上一套 API 网关"，偏重。

### 5. Docker MCP Gateway

README 的定位很明确：解决开发者在把 MCP 工具接入生产流程时的三个障碍——服务生命周期、统一接口、认证与安全。

- MCP Server 跑在隔离容器里；npx/uvx 类服务给最小宿主机权限。
- 密钥不落环境变量，走 Docker 的 secrets 管理。
- 支持 OAuth 流程；多个客户端（VS Code / Cursor / Desktop）连同一个 Gateway，配置一致。

适配判断：适合"我们团队自己开发和调试"，不适合"提供给其他部门"——它没有部门级目录、配额与审计。

### 6. 官方 MCP Registry

`registry.modelcontextprotocol.io` 是协议官方的公共目录（按命名空间列出 server 与版本），用来做发现。它不提供鉴权、限流、审计——把它当 npm 而不是当网关。

### 7. 商业 SaaS（TrueFoundry / Speakeasy / Arcade 等）

这批产品的共同卖点：托管网关 + 目录 + 观测 + 策略，接入选即可用。对"数据敏感、要给内部部门供数"的场景，主要障碍是**调用与元数据都经过第三方**，与私有化诉求冲突。

## 四、针对本项目的落地建议

分三步，不一次性上平台：

**第一步：把 MCP 服务本体的契约定死**（与平台无关）
- 每个工具必须有：`name` / 描述 / 输入 schema / 输出 schema / 所需权限 / 数据分级 / 超时与配额。
- 服务端自己做**逐请求鉴权**（平台只管入口，不管业务数据权限）。

**第二步：用阿里云 AI 网关做入口**
- 后端是原生 MCP 的直接代理；后端是 REST 的走 HTTP→MCP 转换，避免为每个部门写一个 MCP server。
- 消费者身份按"部门/应用"发放，绑定可用工具集。
- 开启访问日志，按消费者维度做配额。

**第三步：评估是否要自建**
- 触发条件：网关费用超过自建运维成本、需要脱离阿里云、或需要跨云统一目录。
- 届时迁 Nacos + Higress（控制面/数据面分离，迁移路径清晰）。

**不建议**：为了"治理"自研一套网关。目录、鉴权、限流、审计这四件事任何平台都能做，自研只会得到一个没人维护的中间层。

## 五、未验证与风险

- **阿里云的"消费者鉴权"粒度**：能否直接映射到"部门"这一级、是否支持按工具粒度的授权，文档未写明，需在控制台实测。
- **Nacos + Higress 的资源占用**：单机 2C8G 下的实际内存/CPU 占用未测；按经验至少需要独立一台 2C4G。
- **ContextForge 的成熟度**：仓库活跃度与版本稳定性未做长周期观察，仅确认其为官方开源项目、可单容器启动。
- **MCP 协议本身仍在演进**（传输层从 SSE 向 Streamable HTTP 迁移），任何平台都可能出现"支持一半"的情况，选型时应以"协议实现是否符合当前规范"为硬指标。

## 来源

- 阿里云 API 网关 · MCP 网关管理 — https://help.aliyun.com/zh/api-gateway/ai-gateway/user-guide/mcp-service-management-1/
- Nacos 3.0 正式发布（MCP Registry / 默认鉴权 / MCP Router） — https://nacos.io/blog/nacos-gvr7dx_awbbpb_gg16sv97bgirkixe/
- Kong · MCP Traffic Gateway — https://developer.konghq.com/mcp/
- Docker MCP Gateway README — https://github.com/docker/mcp-gateway
- IBM ContextForge README — https://github.com/IBM/mcp-context-forge
- 官方 MCP Registry — https://registry.modelcontextprotocol.io/
- Speakeasy · Best MCP gateways for enterprise（候选发现） — https://www.speakeasy.com/blog/best-mcp-gateways-for-enterprise-2026
- Arcade · MCP Gateways, Runtimes & Registries（候选发现） — https://www.arcade.dev/blog/mcp-gateways-runtimes-registries-guide/
