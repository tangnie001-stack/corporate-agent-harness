# 参考项目清单

> 本地 `../github/` 下镜像的开源参考项目，用于在对应场景下优先参考其实现模式。
> 按**功能域**分组（要做什么 → 直接进对应域找参考），组内按价值排序。
> **排序权重（2026-10-08 修订）**：**生产验证 > 领域契合 > 可迁移深度 > 当前痛点匹配 > 技术栈契合**。生产验证提为首位——同域同类时，**已上线、在生产规模跑过**的实现优先于"同领域但未经验证"的实现；仅当该域内没有生产样本时，才退而按领域契合度排。
> **语言/框架不同不构成降级理由**：本项目从参考项目取的是**模式与边界划分**，不是代码。同领域、且已在生产规模上验证过的项目，排在语言相同但未经验证的演示项目之前；仅在"连模式都不适用"时才因技术栈差异后置。
> **许可证决定"能否取代码"**：权重衡量的是**参考价值**、不含许可证；但真要**借鉴代码**（非仅取模式）前必先看许可证——Apache/MIT 可借（需署名），AGPL/GPL 只能取模式。
> **同一项目可跨多个域登记**：项目体量大、横跨多个功能域时（如 WeKnora 同属域 2、域 3、域 4），在各域分别登记其**该域相关的那一面**，不压缩成一条。
> 本清单只承载"项目是什么 + 何时查阅 + 参考价值"；具体架构细节以对应仓库源码为准。
> **例外**：附录另收本地已安装的技能（`~/.agents/skills/`，非 `../github` 镜像），作为"标准 skill 长什么样"的范例。

## 目录

- [域 1：底层依赖（正在使用的官方库）](#域-1底层依赖正在使用的官方库)
- [域 2：同领域 RAG（最贴合参考）](#域-2同领域-rag最贴合参考)
- [域 3：Agent 编排 / harness（找模式）](#域-3agent-编排--harness找模式)
- [域 4：生产化模板 / 平台（参考思路）](#域-4生产化模板--平台参考思路)
- [域 5：IM 通道 / 企业微信接入（找模式）](#域-5im-通道--企业微信接入找模式)
- [域 6：RAG 引擎（文档处理互补）](#域-6rag-引擎文档处理互补)
- [域 7：清理候选](#域-7清理候选)
- [附：参考技能（本地已安装）](#附参考技能本地已安装非-github-镜像)

---

## 域 1：底层依赖（正在使用的官方库）

> 项目直接基于、当前在用的官方库。目的不是"找模式"而是"出问题查行为、查 API 用法"。

**langgraph-1.2.10**
- LangGraph 官方源码。StateGraph、流式事件（astream_events）、子图、Checkpoint、Human-in-the-loop。
- **注意**：`create_react_agent` 已废弃（官方迁至 `langchain.agents.create_agent`），参数名 `system_prompt`（非 `prompt`）；本项目 fork 子代理已改用 `create_agent`（调研结论见 change `session-agent-and-skill-invocation` D16）。
- **何时查阅**：改 `src/agents/graph/` 下 workflow/state/agent_node 时；排查 graph 执行事件、流式行为时。

**fastapi-0.141.1**
- FastAPI 官方源码。API 路由、中间件、依赖注入、异常处理、响应模型。
- **何时查阅**：改 `src/api/` 路由、`src/middleware/`、全局异常处理器时。

**ragas**
- **RAGAS 官方源码**（`vibrantlabsai/ragas`，Apache-2.0）。本项目**历史** RAGAS 评测链路的行为依据（ragas 桥已于 2026-10-17 退役，当前主分走 DeepEval 原生四指标，见 `docs/adr/0027-deepeval-native-metrics-ragas-retirement.md`）；出题模块已整文件删除。本地克隆 ref `298b682`（2026-02-23），与**当时在装版本**（`ragas==0.4.3`）的**真实差异仅 10 个文件**（testset 模块 4 个、最多 34 行）。⚠️ **克隆是 CRLF**：直接 `diff` 会把 239 个文件全报成差异，须加 `--strip-trailing-cr` 才看得出真差异。
- **成熟度与活跃度（2026-10-13 GitHub API 实测）**：**16,008 ⭐ / 1,758 fork**，创建于 2023-05，LLM 评测领域的**事实标准之一**（本域"广泛被用"的成熟库）。⚠️ **但最近一次提交就是 `298b682`（2026-02-24），约 8 个月无推送** ⇒ **成熟但开发停滞**：不要指望上游"很快修"下面这些已知缺口，要么接受、要么自建（出题无问题级校验、`evaluate()` 弃用与现代 API 长期并存）。
- **API 代际是本库最大的坑**（本项目评估层整层建在弃用面上）：`evaluate()` / `aevaluate()` 已弃用，官方指向 **`@experiment`**（`src/ragas/evaluation.py:448` / `:106`）；`from ragas.metrics import …` 的经典指标与 `LangchainLLMWrapper` / `LangchainEmbeddingsWrapper` 均已弃用，应换 `metrics.collections` + `llm_factory` / `embedding_factory`。**且现代指标进不了 `evaluate()`**——它要求 `isinstance(m, Metric)`（`evaluation.py:132`），而 collections 指标不是 `Metric` 子类 ⇒ **迁移必然要重写执行层**，不能只换 import。
- **出题链无"问题级"校验**：整个 `testset/` 对 `self-contained` / `QuestionFilter` **零命中**（克隆至今仍为零）；出题 prompt（`testset/synthesizers/single_hop/prompts.py`）只要求"答案忠于上下文"。旧版曾有 `QuestionFilter`（Independence + Clear Intent，不合格则重写），随 PR #1401（2024-10-02，＋101/−6785 行）整体替换时删除且**官方无任何说明**。⇒ **"选哪些文档"与"题面是否自足"是使用方职责**（决策见 `docs/adr/0022-ragas-testset-input-scope.md`）。
- **`MetricResult` 有 `reason` 通道，但经典指标不填**（`src/ragas/metrics/result.py:11`）：faithfulness / context_recall / context_precision 的裁判 LLM **已逐条产出 reason**（如 `StatementFaithfulnessAnswer.reason`），聚合时只取 verdict、把 reason 丢弃 ⇒ **要"低分能解释"必须换 rubric 类指标或自建诊断层**，官方不给。
- **自带 10 个官方示例工程**（`examples/ragas_examples/`，本地可直接读；`ragas quickstart <name>` 可生成）：与本项目最相关的是 `rag_eval`（RAG 评测，4 文件）、`agent_evals`（Agent 评测，3 文件）、`judge_alignment`（裁判与人工对齐）。⚠️ 官方**有意做极简**（PR #2319 明写"移除复杂的 async 模式、抽象与基础设施代码"，面向 beginner），**是教学脚手架、不是生产 harness**：它手写 3 条自足样本 + 自定准则指标，**不用出题器、也不评检索**。
- **何时查阅**：仅**追溯历史 RAGAS 口径 / 旧分数**时查（当前评测实现看 `src/cli/eval_scoring.py` 与 DeepEval 源码）；要看官方最佳实践样例就读 `examples/`。

---

## 域 2：同领域 RAG（最贴合参考）

**WeKnora**
- 腾讯开源的企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star，2025-07 起活跃）。**Go 1.26 后端 + Vue/TS 前端**，Python 仅出现在 `docreader/`（gRPC 文档解析服务）。**同领域 + 生产验证过**（非演示项目），按本清单权重排本域首位；语言不同只影响"是否抄代码"，不影响"是否抄模式"。
- **检索管线是插件式事件驱动**（`internal/application/service/chat_pipeline/chat_pipeline.go:11` 定义 `Plugin` 接口 + `EventManager`），每个阶段独立成文件：`filter_top_k` 截断 / `extract_entity` / `merge_expand`·`merge_faq`·`merge_overlap`·`merge_parent_resolve` 多路合并 / `load_history` / `into_chat_message` / `chat_completion_stream`；多库检索走 `knowledgebase_search_{fanout,fusion,faq,storegroup}.go`（扇出 + 融合）。可与本项目 `src/rag/` 逐段对照。
- **取数口径（2026-09-18 实测取得，本项目已据其改口径）**：候选池 `DefaultRetrievalTopK = 50`（`internal/types/retrieval_config.go:40-44`），精排后 `RerankTopK` 租户 10 / agent 5（`custom_agent.go:540-550`），实际过取 `max(MatchCount×5, 50) × KB数`、硬上限 500（`knowledgebase_search.go:191-195`）；三层绝对值阈值（向量 0.15 / 关键词 0.3 / 精排 0.2）；去重键为 **chunk id / parent id / (kb, chunk_index) + 内容签名**（`tools/knowledge_search.go:770-776`）—— **无"每文档保留 N 条"这类配额**，有效规则是"每父块 1 条"，多样性交给 **MMR λ=0.7**（`applyMMR`，Jaccard 冗余惩罚）。
- **context 渲染**：文档级元数据每篇只渲一次、chunk 只渲自己的内容（`writeKnowledgeMetadataHeader`，注释明写"repeated results from the same document do not waste model context"）—— 与"同一文档多条结果是否浪费"的解法直接相关。
- **GraphRAG 完整落地**（`application/service/graph.go` 的 `graphBuilder`）：LLM 抽实体/关系 → `calculateWeights`/`calculateDegrees` → `buildChunkGraph` 构图 → 关系反向回溯 chunk 参与召回，并以 `query_knowledge_graph` 工具暴露给 agent。
- **Wiki 模式**（`wiki_ingest*`/`wiki_linkify`/`wiki_lint`）：agent 把原始文档蒸馏成语义互链的 markdown 知识库并自维护，**反向生产**知识而非只消费——本项目无此形态。
- 长期记忆（`service/memory/` + `repository/memory_{vector,extraction,lifecycle}.go`）按 profile/preference/fact/task/interest 分层，有独立抽取与生命周期管理；本项目已决策不做长期记忆，可作反例复核。
- **何时查阅**：设计/重构检索管线（阶段拆分、多路合并、融合排序、取数口径、多样性机制）时；做 GraphRAG、Wiki 化知识生产时；判断"同领域生产系统在这个问题上怎么取值"时**优先查它**。
- Agent 编排/harness 机制（skill 沙箱、MCP、上下文压缩、审批闸门、prompt 分段）**另见域 3**；生产部署与异步任务治理**另见域 4**。

**financial_rag-main**
- 财税法务 RAG 知识库（Python + LangGraph）。与本站同领域，实现了本站缺失/待加强环节：`quality_nodes.py` FaithfulnessChecker（逐句核对 + score<0.7 重生成）、RetrievalGrader（CRAG 式检索评分分流）、`quality_review_function.py` 五维质量评审（最多 2 轮迭代 + needs_human_review）、`reflect_agent.py` 执行→反思→改进；多智能体协作、状态机、知识图谱、混合检索、三层记忆。
- **注意**：演示项目，无生产规模对照；prompt 以目录化 YAML 组织（`app/prompts/agents/<name>/{agent.yaml,system.md}` + `shared/*.yaml`），但其 `shared/tool_fallback.yaml` 是"提示词硬编码命令调用某工具、无可用性判断"的**反面案例**（`docs/tmp/deep-research-prompt-management.md` 引为同款缺陷）。
- **何时查阅**：做答案校验/评审环节、多智能体协作、混合检索、记忆分层时。

---

## 域 3：Agent 编排 / harness（找模式）

> 本站"主从委派 + skill 化业务解耦"升级路线的主参考域。
> **本域按"分工"排序，不是按价值** —— 各项目覆盖不同侧面（委派范式 / 机制层 / 预设内容 / 企业级实物 / 委派落地实物 / 宿主-执行器分层），互补而非可选，因此不适用域内的价值权重。
> 组内分工：claude-code 提供**委派范式**（AgentTool/skill fork）；deepseek-harness 提供**skill/配置机制层**（注册表/scope）；agency-agents 提供**智能体预设内容库**（现成的领域专家人格，可直接转成 `AgentPresetRegistry` 条目，见 change `session-agent-and-skill-invocation`）；WeKnora 提供**企业级机制实物**（skill 沙箱分发 / MCP OAuth / 压缩包 / 审批闸门 / **prompt 九段拼装**；Go 栈且**不含委派**）；**CowAgent / openakita 提供"主从委派落地实物"**（子代理上下文隔离、单跳工具黑名单、@寻址、跨进程防环）；**LangBot 提供"宿主-执行器分层与配置驱动编排"**（Runner 资源授权、配置化 stage 链、沙箱三层策略）；**hermes-agent 提供"技能自演化闭环 + 迭代预算兜底"**（skill 创建/管理、预算耗尽后的一次性 grace 调用、子代理 fresh budget；MIT，代码可借）。

**claude-code**
- Anthropic 终端编码 agent（闭源，~51 万行 TS；2026-03 因 npm source map 误发布泄露，本地为社区还原版 `claude-code-best/claude-code`）。
- 核心 `queryLoop()`（Async Generator + while(true)，状态机 Compaction→APICall→ToolExecution）+ 六层 harness：43+ 工具 / 5 种权限模式 / 26 事件 × 4 类型 Hooks / 沙箱 / 上下文工程（CLAUDE.md + 记忆 + 四级压缩）/ 多 agent 编排（coordinator，Orchestrator 模式 5 并行子代理）。
- **委派机制**：AgentTool 子代理（`subagent_type` 选预设 Explore/Plan/general-purpose，`whenToUse` 匹配）+ Skill 系统（SKILL.md frontmatter：`context: fork` 起子代理、`agent:` 指定预设、`model/allowed-tools` 控执行）。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统/主从委派/skill 化时优先参考（委派范式、hooks、权限、压缩、子代理隔离上下文）。

**deepseek-harness**
- DeepSeek 官方开源 agent harness（2026-08 公测，MIT）。TypeScript monorepo（~230 workspace），一切皆插件（Cordis 元框架）。
- 核心 `ReactLoopAgent`（turn/step 双层 ReAct）+ 事件钩子横切（waterfall/serial：plan-mode/goal/guard/compaction 挂 agent/pre-step 等边界）+ 工具内嵌子循环（subagent→独立 Agent、workflow→worker 线程 JS 脚本）+ 外包驱动（goal-round-driver）+ Session Log 事件溯源。
- **skill/配置机制**：system-prompt 注册表（PromptSection order band、插件贡献段落/工具/变量、scope 遮蔽、`{{var}}` 严格插值）——"多域行为协议可插拔"的机制层参考。
- **何时查阅**：做 skill 加载器/配置作用域/事件钩子/上下文压缩/委派执行器时优先参考（横切插件机制、scope 遮蔽、会话可重建日志；TS/Cordis 绑定，参考思路为主）。

**codex**
- OpenAI 官方开源 coding agent（Rust monorepo `codex-rs`，Codex CLI + Harness 全面开源）。核心 `session/turn.rs` ReAct 循环 + **编排工具化**（plan/multi_agents spawn-wait-send_input-resume-close/request_permissions 均为模型可调工具）+ 执行/沙箱/压缩（compact 8+ 变体）/hooks/记忆/worktree。
- **何时查阅**：设计 agent 混合编排/横切环节/工具系统时参考（编排工具化让模型自治、沙箱/权限/压缩；Rust，语言不同，参考模式）。

**WeKnora**
- 腾讯开源企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star）。**agent 编排是纯 Go 手写 ReAct**（无图框架、无 LangGraph 等价物）：`internal/agent/engine.go:271 Execute` → `:473 executeLoop` → `:588 runReActIteration`；`think.go`（`streamLLMToEventBus` / `callLLMWithRetry` / `watchStreamStall` 流停滞看门狗）/ `act.go:387 runToolCall`（`executeToolCallsParallel` 并行工具）/ `observe.go`（`manageContextWindow` / `analyzeResponse`）三文件即 Reason-Act-Observe。是"**不带框架手写 ReAct 长什么样**"的完整样本。
- **⚠ 无主从委派**：全仓 0 处 subagent / spawn / 子代理概念，skill 走"渐进披露 + 沙箱挂载"而非 fork 子代理——**域 3 只覆盖"skill 化"一半，"委派"一半它不覆盖**（委派仍看 claude-code / codex）。
- 机制侧四项深度（本项目均空白，实现位置已核）：**① skill 沙箱分发** — `internal/agent/skills/` 的 `Manager`（`Initialize`/`LoadSkill`/`ReadSkillFile`/`SandboxSkillDir`/`Reload`/`WithTenantSource`），来源支持 ClawHub / SkillHub / github / gitlab / skills.sh / zip / 直连 SKILL.md（`client/skill.go:55`），安装需起沙箱执行、以分钟计，故走异步 install-events 流。
- **② MCP 全套** — `agent/tools/mcp_{catalog,exposure,schema,tool,oauth}.go`，含 **OAuth2 授权**、按轮 `@MCP` 注入（`SetPinnedMentions`）、超时与图片结果处理。本项目目前只有 `src/agents/tools/registry.py:3` 一行预留注释。
- **③ 上下文压缩独立成包** — `agent/compaction/`（`cutpoint` 选切点 / `overflow` / `serialize` / `prepare` / `settings` / `prompts` / `fileops`），不塞在引擎里，配 `agent/token/estimator.go`；另有防御设计：`engine.go` 的 `overflowRecovered` 让一轮**只允许一次** compact-and-retry，二次溢出直接放弃（注释理由："说明历史长度从来不是问题"）。
- **④ 工具审批闸门** — `agent/approval/{gate,tool_policy}.go`。
- **⑤ prompt 九段拼装（2026-09-18 实测取得，本项目 prompt 重构的直接依据）** — 唯一入口 `internal/agent/prompts.go:400-466` `BuildSystemPromptSections`，九段 `base`/`steering`/`runtime_contract`/`sources`/`tools`/`skills`/`output`/`memory`/`protocol`，空段丢弃，每段打 `[Agent][Prompt] section=... bytes=...`。**base 极薄**（rag 模式仅 5 行，不含检索阶梯/联网规则/停止条件）；载体为 `config/prompt_templates/*.yaml`（含 `id`/`mode`/`default`/`i18n`）；自定义正文**只替换 base**，运行时各段恒定；条件注入按**实际 registry** 判断（注释明说"不用配置开关"）；规则归属表见 `docs/agent-prompt-assembly.md` 末节，并明写"不要把同一条规则复制到多个模板中来'加强优先级'"。三条本项目缺失的运行时规则在 `runtime_contract`：完成即停止调用工具 / 数据与指令边界（`types.SourceDataBoundaryPrompt`）/ "已返回的完整内容不需要再次读取"。
- 另有可借的横切件：`steer.go` 的 `SteerSink`（用户消息注入运行中的轮次，`maxSteerOverruns=1` 限一次）、`internal/event/` EventBus、`internal/modelcontext/` 模型句柄的单请求边界、`internal/tracing/langfuse` span、`internal/sandbox/`（Docker / Cube 远程 / desktop + `docker_idle_sweeper` 回收）。引擎**跨轮次无状态、历史每轮从 DB 重建**（`engine.go` 包注释），与本项目 session-agent 同思路。
- **何时查阅**：设计 skill 加载与沙箱隔离、MCP 接入与凭证生命周期、上下文压缩包、工具权限闸门、**prompt 分段与归属、prompt 载体形态**时（Go，参考机制与边界划分，不抄代码）。RAG 侧另见域 2；生产部署/队列治理/多租户另见域 4。

**Qwen-Agent**
- 阿里官方开源 Python agent 应用框架（`qwen_agent/` 仅 1.28 万行）。Agent 基类（269 行）封装工具调用模板/解析器；多智能体三件套：react_chat（ReAct）/ router（MultiAgentRouter 路由）/ group_chat（多 agent 群聊）；集成 RAG/代码解释器/MCP。无 compaction/hooks/沙箱等 harness 基础设施。
- **何时查阅**：做简单多智能体路由/群聊、工具调用模板时（Python 同语言，参考下限）。

**agency-agents**
- **智能体（子代理）预设内容库**（The Agency，msitarzewski/agency-agents，MIT）。纯内容仓库：19 个 division 目录（engineering/finance/security...），共 ~274 个"专家 agent" Markdown。**是智能体人设定义，不是 skill**（概念区分见 docs/agents/glossary.md「Agent / Skill / Tool」）。
- 每个文件 = frontmatter（`name`/`description` + 展示元数据 `color`/`emoji`/`vibe`，可选 `tools`/`services`/`author`）+ 正文（身份/使命/规则/交付物模板/工作流）。
- `scripts/convert.sh` 把同一份专家转成 16 种工具格式（claude-code `.claude/agents/*.md`、qwen `~/.qwen/agents/*.md`、codex TOML 等）；转 `skill-md`（antigravity/osaurus）时**只保留 `name` + `description`**——佐证 SKILL.md 的标准最小集。
- **与本项目关系**：`AgentPresetRegistry` 的预设内容可直接取自这些 markdown（description 作匹配、正文作 system_prompt）；**change `session-agent-and-skill-invocation` 的首批智能体预设计划取自 finance 域**（需中文化裁剪，仅取 identity / mission / critical rules 三段，避免 prompt 过长）。
- **何时查阅**：做子代理预设/专家人格库、或给委派方案找现成"首批子代理内容"时（内容搬运为主，不涉及机制）。

**CowAgent**（跨域登记：IM 通道另见域 5）
- chatgpt-on-wechat（CoW）改名后的形态（`github.com/zhayujie/CowAgent`，47.2k star，MIT，Python）。自我定位 "personal AI assistant & Agent Harness"，横跨 IM 通道（19 个）/多智能体/记忆/技能/自演化，另有 Tauri 桌面壳与 `app.py` 单体入口。
- **子代理隔离**：`agent/subagent/runner.py` 的 `SubagentSettings`（`max_depth=1`/`max_concurrent=3`/`timeout=300`）；子代理走 `ThreadPoolExecutor` + `copy_context` 继承 ContextVar，运行时 `_private_tools` 浅拷贝工具集，**不继承**父的记忆/人格/历史，父只看到 spawn 调用 + 摘要——"子代理该拿到什么、不该拿到什么"的完整样本。
- **@name 团队寻址**：`agent/team_addressing.py:60` `addressed_agent_id` 只认**行首** `@name`（名/id 双匹配、长标签优先、句中提及忽略）；名册独立成文件 `<instance root>/agents/team.json`（`agent/team.py:38`），并兼容旧版存在 `config.json` 里的形态（`:50`）——**从大配置里拆出来独立管理**。
- **跨进程委派防环**：`agent/multiagent/inbound.py:104` 用 trace 链做环检测，`depth = len(trace)-1`（**不信任 payload 里的 depth**），别名折叠；hand-off 有 delegate/speak/clear 三态。
- **路由 fail-loud**：`agent/routing.py:21` `AgentUnavailableError` + `:37` `_require_enabled`——路由到已停用/不存在的 agent 直接拒答并提示，**不静默回落默认人格**（与本站硬规则同）。
- **记忆 = 混合检索 + 长期巩固**：`agent/memory/manager.py` 向量+关键词融合、`min_score` 在 rerank 前生效、embedding 失败降级纯关键词；`summarizer.py` 的 `MemoryFlushManager` 做长期巩固。本站已决策不做长期记忆，**此处作对照/反例**。
- **反面**：`channel/wecom_bot/wecom_bot_channel.py` 单文件 1531 行、同文件塞两套传输（违反本站 400 行红线）；通道 `@singleton` + 工厂 `new_instance` 逃生舱（`channel/channel_factory.py:45`）是"单例面向多租户"的坏味道。
- **何时查阅**：设计子代理上下文隔离、@ 寻址、跨进程/多 agent 委派防环、IM↔agent 路由 fail-loud 时；复核"长期记忆"取舍时。企微通道另见域 5。

**openakita**（跨域登记：IM 通道另见域 5，生产化另见域 4）
- Python 3.11 + FastAPI 的多 agent 助手（`github.com/openakita/openakita`，AGPL-3.0，2k star）。核心 `src/openakita/`：`agent/`（Ralph Loop、brain、checkpoint、microcompact）、`agents/`（Orchestrator/Factory/Profiles/TaskQueue）、`prompt/`（compiler/builder）、`memory/`、`skills/`、`channels/`、`core/`（policy_v2、stream_accumulator、sse_throttle）。
- **Ralph 循环（外层重试 + 状态持久化）**：`agent/ralph.py:137` `RalphLoop` + `:98` `StopHook`，失败带状态重试、进度落 `MEMORY.md`（`:268` 读 / `:283` 写）。⚠ **但其"自适应分析"是 TODO 桩**（`:341-361` `_analyze_and_adapt` 仅 `asyncio.sleep(1)`），且 `_load_progress_sync:274` 读了文件**却没赋值**、内容被丢弃——"永不放弃/每次都带分析重试"名不副实，**引用时只取"外层重试 + 断点"骨架，别信其宣传语义**。
- **主从委派（单跳，双重强制）**：委派工具 `delegate_to_agent` / `delegate_parallel` / `delegate_to_pool` / `delegate_to_role`；**工具层硬拦**子代理再委派（`tools/handlers/agent.py:64` 查 `_is_sub_agent_call` 直接拒），另有策略表 `DYNAMIC_AGENT_POLICIES`（`max_agents_per_session:5`/`max_delegation_depth:5`/`forbidden_tools:{create_agent}`）。**提示词声明 + 工具层硬拦双保险**是本站可抄的关键点。委派结果经 `to_tool_response()` 回灌（`agents/orchestrator.py:318`）；`AgentOrchestrator:382` + `AgentMailbox:346` + `AgentHealth:268`（成功率/时延）；实例池 `agents/factory.py:623` `AgentInstancePool` 按 `{session_id}::{profile_id}` 复用、30min 空闲回收（`:23` `_IDLE_TIMEOUT_SECONDS`）；`agents/profile.py` 的 role（worker|coordinator）+ skills/tools/mcp 的 all|inclusive|exclusive 三元组做能力裁剪。
- **Prompt 分层装配 + 段预算**：`core/prompt_assembler.py:18` `PromptAssembler` + `prompt/builder.py`（system→runtime→developer→tool→memory→user，末尾 `apply_budget` 逐段截断），`PromptMode.MINIMAL`（子代理仅 Core+Runtime+Catalogs）、`resolve_tier(context_window)` 按窗口伸缩——**与本站 prompt 分段/P0-P1 同题**。身份四文件 `SOUL/AGENT/USER/MEMORY.md` 由 `prompt/compiler.py` 用 **LLM 编译**成带 token 上限的 core 文件（mtime 缓存 + schema 版本失效）。
- **技能加载优先序**：`skills/loader.py:85` 注释即顺序（`__builtin__` > `__user_workspace__` > workspace > `.cursor/skills` > `.claude/skills` > `skills/` > 全局 home），`:433` 每个含 `SKILL.md` 的子目录即一技能。
- **流式零件**：`core/sse_throttle.py`(273) / `core/stream_accumulator.py`(821) / `core/microcompact.py`(164) / `core/checkpoint.py`(266)——SSE 节流、流累积、微压缩、断点，与本站 SSE/压缩直接同域。
- **何时查阅**：设计主从委派（提示词+工具层双强制）、子代理能力裁剪与实例池、prompt 分层与按窗口伸缩、身份文件 LLM 编译、SSE 节流/流累积/断点时。企微通道另见域 5。

**LangBot**（跨域登记：IM 通道另见域 5，生产化另见域 4）
- Python 生产级多平台 IM 机器人平台（`github.com/langbot-app/LangBot`，18k star；Quart/Hypercorn + SQLModel；插件/Box 沙箱为独立 SDK 仓 `langbot-plugin-sdk`）。**先读其 `ARCHITECTURE.md`**（高信号，地图式）。
- **配置驱动 pipeline**：`pkg/pipeline/stage.py:14` `@stage_class(name)` 装饰器写入 `preregistered_stages`（`:11`），`pipelinemgr.py:781` 按 DB `pipeline_entity.stages` 顺序实例化、按 trigger/safety/ai/output 归并，`_execute_from_stage:297` 支持 stage 返回 async generator 的责任链。相对本站"固定编排"多一层"**配置可选 + 热装**"。
- **Host/Runner 分离**：Host 拥有工具/历史/策略/telemetry，插件 Runner 只执行。`pkg/agent/runner/host_models.py:99` `ResourcePolicy`（allowed_tool_names/mcp/kb/skill **显式授权**）、`:133` `StatePolicy`、`:148` `DeliveryPolicy`；`descriptor.py:14` `RunnerDescriptor`（capabilities/usages）；`orchestrator.py:49` `AgentRunOrchestrator`；`invoker.py:30` `RunnerInvoker` 只做 transport+deadline。与本站"主从委派 + host 持有工具"同构，**可佐证授权模型**。
- **skill 两段式（省 token）**：`pkg/skill/manager.py:110` `get_skill_index` 常驻 prompt 只给 name/display/description，`:121` 提示模型"匹配时调 `activate` 工具加载全文"；`activation.py:13` 注释明确"文本标记检测已移除，改由 Tool Call(activate) 触发"。
- **Box 沙箱三层正交策略**：`pkg/box/policy.py` 分 `SandboxPolicy`(在哪跑)/`ToolPolicy`(哪些工具)/`ElevatedPolicy`(单次提权)，规则 **deny>allow、空 allow=全放行、elevated 不能绕过 deny**；`box/admission.py:102` 控制面不可用时 **fail-closed**，云端权益投影成 ≤300s 短租约；`box/runner.py:29` 明确"事件里的 path 只是元数据，不是读宿主 FS 的权限"。
- **反面**：业务文件裸 `print(traceback.format_exc())`（`wecombot.py:305/730/750`，`line.py` 等共 4 处）、`is_muted` 空实现 `pass`（`wecombot.py:862`、`wecom.py:336`）、`run_async` 用 `while True: sleep(1)` 空转占协程（`wecombot.py:838`、`wecom.py:318`）——本站规范禁止 print/空实现，保活应走事件等待。
- **何时查阅**：设计 pipeline 阶段注册与配置驱动、宿主/执行器职责切分与资源授权、skill 渐进披露、沙箱策略与 fail-closed 时。企微通道另见域 5。

**hermes-agent**（跨域登记：IM 通道另见域 5）
- **Nous Research 的个人 AI agent**（`github.com/NousResearch/hermes-agent`，MIT，Copyright (c) 2025 Nous Research——**代码可借**）。**成熟度证据（2026-10-13 GitHub API 实测）**：**253,374 ⭐ / 54,942 fork**，创建于 2025-07、推送至今**活跃**（`pushed_at` 2026-10-13）。规模极大：**8452 个 .py / 约 227 万行**、29 个顶层目录，`agent/` 一个目录就 286 个 py（turn 被切成 ~30 个 `turn_*.py` 兄弟模块）。**先读它自带的两份架构地图**（根 `AGENTS.md` 11.9 KB 路由中枢 + `agent/AGENTS.md` 子系统地图），否则翻不完；扩展点优先级被刻意设计成"CLI 命令 + 技能 → 门控工具 → 插件 → MCP → 新核心工具"的**窄腰**结构（根 `AGENTS.md:24-26`）。
- **主循环（全同步、非 async）**：门面 `run_agent.py`（`AIAgent` 由 mixin 拼装）→ turn 入口 `agent/turn_facade.py:22`（先取 session turn lease 再转发）→ 真正循环 `agent/conversation_loop.py:1648-1650`，条件 `(api_call_count < max_iterations and iteration_budget.remaining > 0) or _budget_grace_call`。**可直接借鉴的兜底**：**预算耗尽后仍给一次"无工具 grace 调用"**（消费点 `agent/turn_iteration_prep.py:408-416`，设置点 `agent/turn_truncation.py:630-635`）+ 跨线程中断标志（`agent/interrupt_control.py:128`）+ `IterationBudget` 的线程安全 consume/refund（`agent/iteration_budget.py`）。
- **子代理委派与隔离（对标本仓 fork 子代理的高价值样本）**：`tools/delegate_tool.py:274` 构造**全新 `AIAgent`**；`skip_memory=True` / `skip_context_files=True`（`:279`）⇒ **不继承父的 messages 与记忆**，只继承 `prefill_messages`；工具面 = 父 toolsets 减去 `DELEGATE_BLOCKED_TOOLS`；**父只看到委派调用 + summary**，看不到子的中间工具调用与推理（`tools/delegate_tool.py:8-11` 模块 docstring）；并发默认 **10**、**深度扁平成 1**（`tools/delegate_tool_config.py:20`）。子代理有独立 `session_db`（`:271/284`）。
- **skill 自创 / 自改进闭环（本域唯一覆盖这一侧的项目）**：技能格式走标准 **SKILL.md + agentskills.io**（`skills_tool.py:594/648`）；管理入口 `skill_manage`（`skill_manager_tool.py:1-11`）；**触发来自后台 review fork + nudge 间隔 10**（`background_review.py:1-5`、`agent_init.py:1380`、`turn_finalizer.py:763`）；curator **只归档不删**（`agent/curator.py:1-8`）；`skills/` 为内置、`optional-skills/` 随包不发。⚠️ **"使用中自改进"未找到确定性改写路径**（疑似纯 LLM 自觉）⇒ 引用时按"有创建/管理闭环"理解，**别按 README 的 "improves them during use"**。
- **上下文压缩两层阈值**：agent 侧 50%（`agent/context_compressor.py:2804`）/ gateway 侧 85%（`gateway/run_turn.py:679`）；策略为先剪工具输出 → 选边界 → aux 摘要。
- **执行后端抽象（spawn-per-call）**：`tools/environments/base.py:1-7` 统一"每条命令新起 `bash -c`"；**7 个后端与 README 一致**（`tools/terminal_tool_backends.py:54` = local/docker/singularity/modal/daytona/vercel_sandbox/ssh）；serverless 持久化落 Modal 跨会话快照 / Daytona `persistent_filesystem`。本仓短期用不上 serverless。
- **trajectory 即数据集**：`agent/trajectory.py:38 save_trajectory()` 输出 **ShareGPT 格式 JSONL**（`trajectory_samples.jsonl` / 失败落 `failed_trajectories.jsonl`，带 flock 防交错），写入点 `agent/session_persistence.py:527`；`batch_runner.py` 多进程批量生成 + `--resume`。⚠️ README 说的 "trajectory compression for training the next generation of tool-calling models" **代码内无据**（只有生成/保存）。
- **⚠️ 自带文档与代码不符三处（引用前必看，这是"别信 README"的活样本）**：① `max_iterations` **三个数字打架**——`run_agent.py:241` = `sys.maxsize`、`tools/delegate_tool.py:105` = **250**、`agent/AGENTS.md:15` 写 **500**；② 同一注释称 "shared with subagents"，而 `tools/delegate_tool.py:286` 明确 `iteration_budget=None  # fresh budget per subagent`（**独立预算**）；③ README 宣传的 **Honcho 不在本仓**（`plugins/AGENTS.md:16-24` 明写已移入 plugin catalog）。⇒ 它的 `AGENTS.md` / README 都不能当事实，**只读代码**。
- **许可**：MIT（`LICENSE:1`）。⚠️ 但**无统一 `third_party/` 汇总**，多来源成分需逐项核对：外部 skill 各带自己的 `license` frontmatter、`native/fts5_cjk/` 由 PR 贡献、平台 SDK（Daytona/Modal/Vercel 等）走 `ensure_lazy_dep` 运行期懒加载（**不 vendored**）⇒ **真要借代码前，逐个看子目录/文件头**。
- **何时查阅**：设计**子代理上下文隔离**（拿到什么/不拿到什么）、**迭代预算 + grace 兜底**、skill 创建与管理闭环、上下文压缩阈值、多平台网关组织（另见域 5）、执行后端抽象与 registry 时。

---

## 域 4：生产化模板 / 平台（参考思路）

**WeKnora**
- 腾讯开源企业级知识平台（`github.com/Tencent/WeKnora`，MIT，23.4k star；Go 2248 文件 / 578K 行，docreader 为 Python gRPC 服务）。**按本清单权重排本域首位**：本域要的就是"生产化"，而它是本域唯一**已在生产规模验证过的产品级样本**（非模板、非演示）；语言不同不构成降级理由。**同一套 Go 二进制贯穿四种部署形态**：Lite 单机（SQLite，`docs/LITE.md` + `deploy/weknora-lite.service` 的 systemd 最小权限加固）/ Compose / K8s Helm / 沙箱集群（`docs/sandbox-cluster.md`）。镜像侧：`docker/Dockerfile.app` 多阶段 + 构建期注入 `VERSION/COMMIT_ID/BUILD_TIME` + 随镜像分发 golang-migrate + `gosu` 降权；`docker/Dockerfile.docreader` 把重解析依赖（libreoffice/Playwright）隔离成独立 gRPC 服务。
- **依赖矩阵与配置**：`docker-compose.yml`（1044 行 / 25+ 服务）用 **profiles** 表达可选依赖（minio/neo4j/qdrant/milvus/weaviate/doris/searxng/dex/langfuse…），`docker-compose.dev.yml` 只起基础设施、应用本地热重载。`.env.example`（783 行，A-J 十段）是"环境变量即文档"的范本；运行时配置为**四层优先级**（内置默认 → 模板 YAML → 环境变量 → DB 运行时设置，后者优先，多数开关免重启改）。Helm `templates/secrets.yaml` 用 `lookup` 复用已生成密钥，避免 `helm upgrade` 重滚导致加密数据不可读。
- **异步任务治理（本项目最缺的一块）**：`docs/worker-pool-governance.md` 定义 Core/PostProcess/Enrichment/Maintenance/Shared/Wiki **六池 worker** 拓扑与 sizing 公式；`internal/router/task.go` 起六个 asynq Server + 统一重试策略；`internal/middleware/asynqdl/` 死信中间件（**仅最终重试**落 `task_dead_letters` 并把知识行标 failed，让"卡在处理中"变可见）；`internal/types/interfaces/task_queue.go` 是「Redis 队列 + DB 持久化待办/死信」双层设计（`FOR UPDATE SKIP LOCKED` 认领 + 崩溃回收）；管理端队列控制台 `/system/admin/runtime/queues` 可取消/重试/删除。并发侧另有进程级每模型治理 `internal/models/limiter/governor.go`（只拦后台任务、故障 fail-open）与 Redis ZSET 滑动窗口限流 `internal/ratelimit/limiter.go`（无 Redis 降级本地内存）。
- **可观测**：`internal/tracing/langfuse/` 基于 OTel SDK + OTLP 接 Langfuse，`asynq.go` 把 traceparent 写进 payload，使 HTTP→异步任务落在**同一条 trace**；`internal/middleware/logger.go` 的 `X-Request-ID` 贯通请求 ID；`internal/logger/logger.go` 支持 `LOG_FORMAT` 占位符 + lumberjack 滚动。**缺口：无 Prometheus 运维指标**（`application/service/metric/` 是 RAG 质量指标，非运维指标）。
- **多租户与安全**：`internal/middleware/rbac.go` 四角色阶梯（viewer<contributor<admin<owner，带"只记不拦"灰度开关）、`internal/application/access/ownership.go` 资源归属链、`internal/middleware/api_key_gate.go` 机器主体按路由 **fail-closed** 策略、`internal/middleware/auth.go` JWT/X-API-Key/外部头多通道、通用审计表 `audit_logs`（含去重防刷）、`SYSTEM_AES_KEY` 字段级加密。设计文档 `docs/RBAC说明.md`、`docs/OIDC认证调用流程.md`。
- **迁移与启动引导**：96 个版本化 migration（另有 sqlite 17 个），`internal/database/migration.go` 的 dirty state 自动恢复 + 失败只告警**不阻断启动**（保 UI 可达以便诊断），`scripts/migrate.sh` / Makefile 提供 `up/down/force/goto`，排障手册 `docs/migration-troubleshooting.md`。运维 FAQ 40+ 条见 `docs/QA.md`，可当生产排障清单。
- **CI/CD**：`.github/workflows/` 分 `app.yml`（gofmt/vet/test/build + 第三方许可 bundle 校验）、`go-lint.yml`（`only-new-issues` + 按改动模块动态选 lint 目标）、`docker-image.yml`（多架构 Buildx + digest 合并 manifest）、`release-lite.yml`（四平台单二进制）；桌面分发走 `Formula/weknora-lite.rb`。
- **与本项目关系**：Go 栈、代码不可迁移，价值在机制与边界划分——**把"异步任务"当生产对象治理**（六池/死信/双层持久化/队列控制台）而非实现细节，这是本项目流水线最薄的一环。
- **何时查阅**：做生产化改造时——异步任务与队列可靠性、限流与并发治理、多形态部署与 Helm 幂等、DB 迁移与启动引导、RBAC/审计/多租户。RAG 侧另见域 2，Agent 编排/skill/MCP/压缩另见域 3。

**fastapi-langgraph-agent-production-ready-template**
- FastAPI + LangGraph 生产模板。LLM 容错、长期记忆、Rate Limiting、Eval 框架、生产监控。`app/api/v1 + core/langgraph(tools/prompts) + services/llm + models/schemas` 分层与本站同构。
- **何时查阅**：生产化改造（限流、容错、评估、监控、记忆）时；需要"同技术栈、可直接照搬目录结构"的模板时优先查它。

**dify-1.16.1**
- Dify 官方（LLMOps 平台）。AI 应用编排、工作流引擎、RAG 管道、插件体系。
- **何时查阅**：做可视化工作流编排、RAG 管道拆解、插件化扩展时（平台型，迁移成本高，参考思路为主）。

**LangBot**（跨域登记：域 3、域 5）
- **两级并发闸门 + 背压丢弃（本站最可抄的一块）**：`pkg/pipeline/controller.py:24` 全局 `Semaphore(concurrency.pipeline)` + 每会话 `Semaphore(concurrency.session)`（`provider/session/sessionmgr.py:323`）；`controller.py:138` consumer 优先挑"会话信号量未满"的 query，再抢全局槽；`pkg/pipeline/pool.py:186` `_admit_query_locked` 满载时**丢弃最旧排队 query**（默认全局 1000 / 每 workspace 100，`:129`），`QueryPoolCapacityError:39` 显式背压。**SSE 单进程下最值得抄的调度模型**。
- **跨进程运行时**：Plugin Runtime / Box 沙箱支持 stdio 或 WebSocket 两种控制传输（容器化走 `plugin.runtime_ws_url` + `--standalone-runtime`）。
- **持久化约定**：SQLite 默认、Postgres 可选；时区无关列存 UTC-naive，边界用 `as_naive_utc` 归一；Alembic 4.x 无旧链。
- **"agent 面是一等公民"**：`skills/`（单一真源）+ `/mcp` 服务端（**只暴露收敛子集**，`api/mcp/server.py:66` `stateless_http=True` 免粘性）+ `lbctl` + `AGENTS.md`/`ARCHITECTURE.md` 四者同步，明写 "drift is a bug"。
- **何时查阅**：做并发调度与背压、跨进程插件/沙箱运行时、多平台单进程部署、agent 面（API/MCP/skill/文档）一致性维护时。

**openakita**（跨域登记：域 3、域 5）
- 生产化零件：`core/checkpoint.py`（断点）、`core/microcompact.py`（微压缩）、`core/sse_throttle.py`（SSE 节流）、`core/conversation_metrics.py`（会话指标）、`core/policy_v2/classifier.py`（工具审批分级，把 `delegate_*` 归 `CONTROL_PLANE`）、`core/tool_interrupt_behavior.py`（打断时各工具 block/allow 策略矩阵）。同一核心多形态：服务走 FastAPI、桌面走 Tauri + React。
- **反面**：限频只告警不拦截——`channels/adapters/wework_ws.py:221` `_RateLimitTracker.check()` 仅 `logger.warning`，发送路径照发，30 条/24h 实际不拦超发；`response_url` 缓存无 TTL（`:2430` 仅按 200 条数量淘汰）。
- **何时查阅**：做断点/微压缩/SSE 节流、工具审批分级、打断行为矩阵、多形态发布时。

---

## 域 5：IM 通道 / 企业微信接入（找模式）

> 把外部 IM（重点：**企业微信智能机器人**）接到自有 agent 后端的参考。拆成三件事：**通道抽象**（平台差异挡在适配器内）、**传输**（URL 回调 Webhook vs WebSocket 长连接）、**流式呈现**（把 token 流映射成平台可渲染的帧）。
> **背景（2026-10 官方能力）**：企业微信"智能机器人"两种 API 模式——URL 回调（`Token`+`EncodingAESKey`，需公网可访问 URL、需加解密）与 WebSocket 长连接（`BotID`+`Secret`，`wss://openws.work.weixin.qq.com`，**免公网、免加解密、官方推荐**）。长连接硬约束：**单机器人单连接**（新连接踢旧连接）、**30s 心跳**、流式**无刷新回调**须服务端主动推（`stream.id`+`finish`）、首帧起 **6 分钟**必须收尾。官方 SDK：Node `WecomTeam/aibot-node-sdk`、Python `wecom-aibot-python-sdk`；协议文档 path/101463（长连接）/ path/100719（回调）。**模式互斥**，切换会使另一种立即失效。

**hermes-agent**（跨域登记：Agent 编排另见域 3）
- Nous Research 个人 agent（`github.com/NousResearch/hermes-agent`，**MIT，代码可借**）。**按本清单权重（生产验证 > 领域契合）排本域首位**：**253,374 ⭐ / 54,942 fork**（2026-10-13 GitHub API 实测），创建于 2025-07、推送至今活跃；且**企微通道近期仍在修生产缺陷**——`plugins/platforms/wecom` 最新提交到 **2026-10-08**，含 `a959a6e fix(wecom): split long messages instead of silently truncating them`（2026-10-01，长消息被静默截断）。⚠️ **残留不确定**：星数是**项目级**成熟度，其企微子通道的独立生产规模无公开数据。
- **与本域同源**：`plugins/platforms/wecom/plugin.yaml` 注册**两个平台**——`wecom`（**Smart Robot over WebSocket**）与 `wecom_callback`（自建应用走 HTTP 回调 + AES 消息加解密）；`adapter.py:1` 自述 "WeCom (Enterprise WeChat) **AI Bot adapter over the `openws` WebSocket gateway**"，`:49` `DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"`（**与本站同一端点**），协议命令 `aibot_subscribe` / `aibot_msg_callback` / `aibot_callback` / `aibot_event_callback`（`:51-54`）。凭据与本站同一套：`WECOM_BOT_ID` / `WECOM_SECRET`。
- **组织方式（本域唯一按关注点拆文件的）**：6 个文件 2094 行 —— `adapter.py`(47KB) / `streaming.py`(21KB) / `media.py`(22KB) / `callback_adapter.py`(16KB) / `send_queue.py` / `wecom_crypto.py`（回调加解密）+ `plugin.yaml`。**同目录双传输但按文件拆开**——不像 CowAgent 那样单文件塞两套（1531 行）。
- **流式**：`adapter.py:108` `SUPPORTS_NATIVE_STREAMING = True  # msgtype "stream" via aibot_respond_msg, not edit-based`——与本站已知的"每帧全量快照、非增量"同源；`streaming.py` 内有 `MAX_STREAM_CONTENT_LENGTH` / `STREAM_SAFE_DURATION_SECONDS` / `STREAM_KEEPALIVE_INTERVAL_SECONDS` 等常量，可与本站 6 分钟收尾约束逐项对照。
- **何时查阅**：写/改企微智能机器人适配器时**先读它**（生产验证最足 + 代码可借 + 关注点拆分最干净）；要看流式保活常量、回调加解密、双传输骨架时。**规格清单（逐条清单式）仍首选 openakita**。

**LangBot**（跨域登记：域 3、域 4）
- Python 生产级多平台 IM 机器人平台（`github.com/langbot-app/LangBot`，**Apache-2.0——代码可借鉴**；18k star）。**本域第 2 名**（首位让给 hermes-agent：后者项目级成熟度高一个数量级、同为宽松许可、且企微通道同样在修生产缺陷）——**生产在跑 + 授权宽松**，其 `wecombot` 适配器近期仍在修生产缺陷（`fix(wecombot): stop empty bubbles and premature stream close on blank final chunks #2561`）。
- **适配器已拆包（当前实现）**：`pkg/platform/adapters/wecombot/`，7 文件 959 行（`adapter.py` 仅 341 行），带 `manifest.yaml`；**旧实现 `pkg/platform/sources/wecombot.py`（1051 行）并存**，被新适配器以 `LegacyWecomBotAdapter` 复用其静态工具方法（`adapters/wecombot/adapter.py:3`）。**一个适配器双传输**：`adapters/wecombot/adapter.py:56` 按配置 `enable-webhook` 选 `WecomBotWsClient`（长连接，默认，需 `BotId`/`Secret`）或 `WecomBotClient`（回调，需 `Token`/`EncodingAESKey`/`Corpid`）。
- **随仓 vendored 的 Python 协议客户端**（`src/langbot/libs/wecom_ai_bot_api/`）：`ws_client.py`（1250 行，长连接）、`api.py`（2230 行，回调）、`WXBizMsgCrypt3.py`（278 行，腾讯官方回调加解密）、`wecombotevent.py`。**自研协议、不用官方 SDK**——与本站"用官方 `aibot` SDK"路线不同，可逐帧对照校验。
- **长连接生产不变量（行号已核，`ws_client.py`）**：`DEFAULT_WS_URL:40` / `CMD_SUBSCRIBE:43` / `CMD_HEARTBEAT:44`；认证走 **`_send_auth:782` → `_wait_for_auth:794`（等 10s 并校验 `errcode==0`）——恰是本站官方 SDK 缺的一环**（SDK 的 `connect()` 不等认证即返回，auth 失败只走 `on_error`）；`_heartbeat_loop:814`（30s ping，连续 2 次无 pong 判死）；指数退避重连（1s→30s，`max_reconnect_attempts=-1` 无限）；**`_dispatch_event:1129` 按 `msg_id` 去重**（`_msg_id_map`，上限 `_DEDUP_CACHE_MAX=4096:60`）；`_send_reply:1148` 对**同一 req_id** 建串行队列（`asyncio.Queue` + worker），`_reply_queue_worker:1183` 逐条发 + `_send_and_wait_ack:1218`（`_reply_ack_timeout=5.0:131`）。
- **流式硬约束（已实证，非推测）**：`push_stream_chunk:649` 每次发**累积全量快照**——`ws_client.py:718` 注释原文"**WeCom replaces the displayed stream content on each refresh, so every frame must contain the complete snapshot, not only a delta**"；内容上限 `_MAX_STREAM_CONTENT_CHARS=200000:65`（超限**保留尾部**）；内容未变即跳过、空白/ZWS 快照跳过、**空白 final 不关流**（防空气泡 + 答案被甩到另一条消息，即上述 #2561）。**并发/背压上限**：`_MAX_CALLBACK_TASKS=100:66` / `_MAX_REPLY_WORKERS=100:67` / `_MAX_REPLY_QUEUE_SIZE=100:68` / `_MAX_PENDING_ACKS=256:69`（超限丢弃并 warning）。`reply_stream:289` 组 `{id,finish,content,feedback}`；`upload_media:514` 三阶段（init→chunk 512KB→finish）。
- **模板卡片交互（"澄清卡片"的现成闭环）**：`adapters/wecombot/interaction.py` 把 `select` 字段 / ≤6 个 action 编成 `button_interaction` 卡，按钮 key 编码为 `lbi:{callback_token}:a:{i}`（action，`:39`）/ `lbi:{token}:f:0:{i}`（field 选项，`:53`）；点击后经 `template_card_event` 回推，`parse_callback_key:126` 解回、`interaction_event_from_native:146` 转上层事件；适配器侧 `_register_native_handlers:265` 订阅 `on_message('template_card_event')` → `_handle_interaction_event:274`。卡→点击→回填**全链路可直接对标**；`interaction_delivery_capabilities:13` 声明 `max_fields:1`、`supports_updates:True`。
- 对照：`sources/wecom.py:203` `WecomAdapter` 是**企业微信"应用"**（非智能机器人）纯 HTTP 回调（`unified_mode=True`、`handle_unified_webhook:303`）；legacy 适配器侧流式见 `sources/wecombot.py:487` `reply_message_chunk`（WS 走 `push_stream_chunk`，失败 `reply_text` 兜底）、`:570` `_handle_synthetic_chunk`（无 req_id 的合成事件如按钮点击 resume）、`:869` `_on_card_action`（模板卡片点击合成 query 重新入池）。
- **何时查阅**：**本域实现首选**——写企微智能机器人适配器、流式投影、卡片交互、去重与背压时先读它；要一份完整 Python 长连接/回调客户端做对照，或看"双模适配器骨架"时。设计期的规格枚举再配合 openakita 的 `WEWORK_WS_IM_NOTES.md`。

**CowAgent**（跨域登记：域 3）
- 前身 `chatgpt-on-wechat`（CoW），现 `github.com/zhayujie/CowAgent`（**47.2k star、MIT——代码可借**；Python，750 文件）。**本域排第 3**——生产验证与领域广度都赢过 openakita（唯一输在可迁移深度）：`channel/` 下 13 个通道目录，且**把微信/企微生态 5 种形态都覆盖了**（`wecom_bot` 智能机器人 / `wechatcom` 企业微信应用 / `wechatmp` 公众号 / `wechat_kf` 微信客服 / `weixin` 个人微信）。但**栈与本站不兼容**（线程 + 同步 WS，见下），故按新权重定位为**参照价值高、可照搬度低**：看广度用它，写实现用 hermes-agent / LangBot（规格清单用 openakita）。
- **单通道类双模**：`channel/wecom_bot/wecom_bot_channel.py:38` `WECOM_WS_URL=wss://openws.work.weixin.qq.com`、`:152` `self.mode = "websocket"`、`:167` 从配置 `wecom_bot_mode` 读（默认 websocket）；`wecom_bot_crypt.py` 负责回调加解密。⚠ 同文件塞两套传输、**1531 行**，违反本站 400 行红线。
- **同步线程模型（决定"只借语义不搬实现"）**：`import threading` + 同步 `websocket-client` + `run_forever`（`:257-265` 起独立线程），`_on_close:240` 固定 **5s** 重连（**非指数退避**）。本站是 asyncio/FastAPI，**借鉴协议而非实现**。
- `req_id` 关联 + 流式：每条命令带 `req_id`，`_send_and_wait:499`（默认 **15s**）用 `threading.Event` 等匹配响应、`_handle_ws_message:512` 按 `req_id` 回填 `_pending_responses:147`；流式是**同一条 stream 消息**（末尾 `finish=true`），节流 **≤1 推/100ms 且长度未变即跳过**（`_make_stream_callback:666`、判据 `:685`）；工具轮次用 `\n\n---\n\n` 分段（`:727`）；状态 `_stream_states:149`（`req_id → {stream_id, content}`）。
- **response_url 兜底**：`:156` `_callback_streams=ExpiredDict(60*10)`（10min），被动轮询窗口关闭/WS 失败时用一次性 `response_url` 主动补发；**判定以 `errcode` 为准而非 HTTP 200**。
- **反面**：① 去重仅内存 `received_msgs=ExpiredDict(60*60*7.1)`（`:141`，7.1h），**不持久、不跨实例**——本站有 Postgres 且会重启，需持久化去重；② **无模板卡片处理**（全文件无 `template_card`/`task_id`/卡点击），澄清卡片能力缺失。
- **何时查阅**：看"IM 通道广度（尤含企微生态 5 形态）""单类双传输""流式 `---` 分段""response_url 兜底"时；其企微实现**只借协议语义**，不照搬（线程模型 + 1531 行双传输）。子代理隔离另见域 3。

**openakita**（跨域登记：域 3、域 4）
- **本域排第 4**——生产验证最弱（2k star；域 3 另记其核心 Ralph 循环是 TODO 桩）、**AGPL-3.0-only 只能取模式不可抄代码**；唯一领先项是**可迁移深度**（asyncio 同栈 + 本域唯一成篇规格）。规格正文：`docs/WEWORK_WS_IM_NOTES.md`（520 行 / 27KB）把长连接适配器（`src/openakita/channels/adapters/wework_ws.py`，2434 行）的**功能清单 / 帧协议（10 个 cmd） / 9 条必须保持的逻辑约束 / 配置 / 数据流 / 已知限制 / 25 项修改检查清单**全列了出来——**可直接当实现规格/清单搬运**；第三节是"长连接 vs 回调"官方差异对照表。
- 两套适配器并存：`wework_ws.py`（长连接，`class WeWorkWsAdapter:514`）与 `wework_bot.py`（回调，`BotMsgCrypt:81` AES-256-CBC、`STREAM_TIMEOUT=330:280`、`StreamSession:288`）。
- 长连接实现要点（行号已核）：`_connection_loop:736`（指数退避 1s→30s）、`_send_auth:843`、`_heartbeat_loop:857`（30s；**发心跳前**查 missed_pong，连续 2 次判死）、`_route_frame:907`、`_handle_msg_callback_safe:962`（`asyncio.wait_for` 超时 + 异常兜底必发 `finish=true`）、`_send_stream_reply:1799`、`_send_reply_with_ack:2066`（同 `req_id` 串行 + 15s 回执）、`_stream_keepalive_loop:1977`（4min 发 `finish=false` 防 6min 超时）、`_ws_upload_media`（init/chunk/finish）。
- 防御件（可直接抄的模式）：`_seen_msg_ids:586`（OrderedDict，10min TTL + 500 上限双淘汰）、`_peer_locks:604`（按 chat_id 串行）、`_reply_locks:583`、`_pending_replies:607`（断线暂存、重连重试）、`_pending_media_msgs:601`、`MAX_INTERMEDIATE_STREAM_MSGS=85:101`、`_parse_quote_content:250`、`_normalize_think_tags:310`、`_WebhookSender:385`（WS 失败回退）。**被踢正确处置**：`disconnected_event` → `_displaced=True`（`:579`/`:1254`）→ **停止重连**（防被踢后无限重连互踢）。
- **通道抽象**：`channels/base.py:177` `ChannelAdapter(ABC)` + `capabilities` 声明式能力表（`:195`，默认全 False，`wework_ws` 覆写 `streaming=True`），抽象 `start/stop/send_message/download_media/upload_media` + `on_message/on_event/on_failure` 钩子；平台差异全在适配器内，业务侧只见统一消息。`base.py:43` 另有 `cooperative_shutdown`/`force_close_ws`，注释记录"pre-fix 三 bot 串行 stop 被最慢一个拖到 ~4s"的实测故障。**这套"能力位 + 钩子 + 统一消息 + 有界关闭"可直接对标本站通道层设计**。
- **何时查阅**：动手接企业微信智能机器人（长连接或回调）前，用它的 `WEWORK_WS_IM_NOTES.md` / 25 项检查表做**规格枚举与遗漏清单**；设计通道抽象、流式呈现（`channels/stream_presenter.py`）、文本切分（`channels/text_splitter.py`）时。

---

## 域 6：RAG 引擎（文档处理互补）

**ragflow-0.26.4**
- RAGFlow 开源 RAG 引擎。DeepDoc 文档解析、模板化分块、知识编译器、引用溯源、Agent 沙箱。
- **何时查阅**：文档解析、分块策略、引用溯源与质量时（与 `src/chunking/` 互补参考）。

---

## 域 7：清理候选

> 维持原判断（2026-09）：低价值或与现有规则重叠，暂不清理可留作参考。

- **awesome-llm-apps-main**：AI Agent/RAG 模板集（理念参考为主，直接可迁移的少）。头脑风暴 agent 功能形态时查阅。
- **full-stack-fastapi-template-0.10.0**：FastAPI 官方全栈模板（含前端）。本站纯后端 API，前端非重点；分层/认证已有成熟结构。
- **fastapi-best-architecture-1.15.0**：FastAPI 社区最佳实践。与 fastapi-0.141.1 官方文档重叠；统一响应/异常处理本站 `docs/agents/rules.md` 已覆盖。

---

## 附：参考技能（本地已安装，非 `../github` 镜像）

> 经 `npx skills add -g` 装进 `~/.agents/skills/`（多工具共享技能源库，symlink 进 CodeBuddy/Claude Code 等）的技能。
> 用途：作为"标准 skill 长什么样"的**契约范例**与**内容搬运来源**——与上文"智能体预设"（agency-agents）正好凑成 agent / skill 两类范例。

**financial-statement-analyzer**
- 中文「财务报表深度分析」skill（geeksfino/finskills，Apache-2.0）。法证财务分析师视角，9 步法：5 因子杜邦分解 → 盈利质量（应计比率 / 现金转化率红灯阈值）→ 财务健康评分（Altman Z / Piotroski F / Beneish M）→ 营运资本 CCC → 资产负债表隐性风险（商誉减值、关联交易、在建工程不转固、股权质押）→ 业务分部 → 同行基准 → 报告模板。
- **A 股特化**：CAS vs US GAAP 差异、扣非净利润、政府补助依赖、"读附注"提醒、商业承兑汇票坏账风险。
- **契约范例**：frontmatter 仅 `name` + `description` + `license`——与 claude-code / agency-agents 的 `skill-md` 转换一致，是"SKILL.md 标准最小集"的活样本。
- **与本项目关系**：中文财务域，其方法论 / 公式 / 报告模板可直接参考，用于增强或对标本项目 `skills/financial-statement-analyzer`；但它是**纯知识 skill**（无 `context`、无工具），搬运时需按本站契约（inline/fork、双轴）适配，且其"确认对象 → 取数"流程与本站"主 agent 先检索再委派"模式不同。
- **配套技能**：文末推荐 `findata-toolkit-cn`（A 股实时行情 / 财务指标 / 北向资金，免费无 API key）。
- **位置**（已安装）：`~/.agents/skills/financial-statement-analyzer/`（`SKILL.md` + `references/analysis-methodology.md` + `references/output-template.md`）
- **何时查阅**：写财务分析类 skill、或给本项目 `skills/financial-statement-analyzer` 补方法论 / 公式 / 报告模板时。
