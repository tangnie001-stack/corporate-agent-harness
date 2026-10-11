# Tasks

> 分阶段交付。**Phase 1 是"等值重构 + 一处显式修复"**（全 dashscope；除 D8 恢复主链路 `preserve_thinking` 外行为不变；测试必须绿）；**Phase 2 才切 provider**。Phase 3 为延后指针，本 change 不展开。

## 1. 基础设施

- [ ] 1.1 在 `pyproject.toml` 显式声明 `pyyaml`（当前仅为传递依赖）
- [ ] 1.2 新建 `src/infra/llm/provider/` 包骨架（`__init__.py` / `spec.py` / `thinking.py` / `chat.py` / `factory.py` / `registry.yaml`）

## 2. 数据面（registry + spec）

- [ ] 2.1 编写 `registry.yaml`（Phase 1 只含 `dashscope`：`base_url` / `api_key_env` / `thinking_style` / `reasoning_field` / `capabilities` / `models` / `catalog`，其中 `catalog` 含 `context_window`、`pricing`、`tokens_per_cjk_char`、embedding 批大小与维度）
- [ ] 2.2 实现 `ProviderSpec` / `RoleBinding` / `ThinkingStyle` 与 YAML 加载
- [ ] 2.3 实现角色解析：`main`/`classify`/`summary` 跟随 `LLM_PROVIDER`；`embedding`/`rerank`/`judge` 独立缺省 `dashscope`；支持 `.env` 覆盖（`<ROLE>_MODEL` / `<PROVIDER>_BASE_URL`）；未设 `*_MODEL` → `registry.models[role]`
- [ ] 2.4 实现启动期 fail-fast 校验（未知 provider / 无分支的 `thinking_style` / 角色绑到缺能力 provider / 缺 `<PROVIDER>_API_KEY` / registry 缺字段或格式错）；校验**只在应用启动期执行、`import` 不触发**，并支持 `PROVIDER_VALIDATE=off` 跳过
- [ ] 2.5 实现启动期**幂等定价播种**（挂在应用启动期、D12 旁）：对被绑定角色所用模型调纯函数判据，registry 有价则 ensure Langfuse 模型定义存在、无价则记 WARNING；非阻塞（`LANGFUSE_ENABLE=false` 跳过；Langfuse 不可达降级为 warning，不影响启动，也不受 `PROVIDER_VALIDATE` 影响）；**须在 `configure_tracing()` 之后执行**、不得在 async lifespan 里 `asyncio.run`（嵌套事件循环）

## 3. 逻辑面

- [ ] 3.1 实现 `thinking_extra_body(spec, enabled)`：`dashscope` / `deepseek` 两种形状（`none` 等延后），产**完整** `extra_body`（含 preserve 标志）
- [ ] 3.2 把 `ReasoningPreservingChatQwen` 泛化为 provider-aware 的 `ReasoningPreservingChat`（形状 / 回传字段 / preserve 由 spec 驱动）
- [ ] 3.3 实现 `build_chat(role, *, thinking=None, **overrides)` / `build_embeddings()` / `build_rerank()`

## 4. 接线（等值重构）

- [ ] 4.1 `src/models.py`：`get_llm` / `get_classify_llm` / `get_summary_llm` / `get_embeddings` / `get_rerank` 改为薄委托；**签名参数改 `model: str | None = None`**，在函数体内解析（空 / None → registry）
- [ ] 4.2 `src/agents/graph/middleware.py:85`：改走 `thinking_extra_body`
- [ ] 4.3 `src/agents/skills/executor.py:437`：改走 `thinking_extra_body`
- [ ] 4.4 `src/cli/seed_langfuse_models.py`：定价改为从 registry 读取，seed `catalog` 中**全部**有定价的模型；把"创建 / 已存在 / 跳过"判据抽成**单一纯函数**，`--dry-run` 与真跑**共用**，dry-run 对"未配价 / 未知模型"如实报"将跳过"（不再假称"将写入"）
- [ ] 4.5 把 `chunk.py:52` 的 `Vector(1024)` 抽成单一常量；`src/rag/prompt.py` 的 `PROMPT_TOKENS_PER_CJK_CHAR`、`src/models.py` 的 `EMBEDDING_BATCH_SIZE` 改为经层取（registry 提供）；实现"registry 声明维度 == 列常量"的一致性校验
- [ ] 4.6 新增 `get_judge_llm()`（`role=judge`，签名 `model: str | None = None`，使 `eval_ragas_generate` 的 `--model` 覆盖自然透传），并把 `src/cli/eval_ragas.py:563`、`src/cli/eval_ragas_generate.py:354` 改为经它取裁判/生成模型；**同时处置两处空值守卫**（`eval_ragas.py:549`、`eval_ragas_generate.py:331-334`——`RAGAS_LLM_MODEL` 改空串后会提前 `exit(1)`）：改由 judge 角色解析（env 覆盖或 registry 默认），不再以"env 非空"作判死条件
- [ ] 4.7 **D8 显式修复**：主链路（`ModelParamsMiddleware`）与 fork 路径的 `extra_body` 均为**完整** dict（含 `preserve_thinking`），消除"请求期覆盖构造期丢字段"；单列验证带工具多轮的 preserve / 回传
- [ ] 4.8 启动期检测 `LLM_KWARGS` 是否含 `extra_body`，命中则告警（影子通道静默失效，见 Important 4）

## 5. 配置迁移

- [ ] 5.1 `src/config/settings.py`：新增 provider 指针与 `<PROVIDER>_API_KEY` 解析；`LLM_BASE_URL` / `LLM_API_KEY` **软迁移**（Phase 1 仍读、当默认 provider 的覆盖、打 deprecation warning；Phase 2 删）；`LLM_MODEL`/`CLASSIFY_MODEL`/`SUMMARY_MODEL`/`RAGAS_LLM_MODEL`/`EMBEDDING_MODEL`/`RERANK_MODEL` 默认值改**空串**（表示未配置）
- [ ] 5.2 更新 `.env.example`（删 `LLM_BASE_URL` / `LLM_API_KEY` / `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `RERANK_API_KEY` / `MODEL_*_PRICE_PER_TOKEN` / `PROMPT_TOKENS_PER_CJK_CHAR` / `EMBEDDING_BATCH_SIZE` / `MODEL_CONTEXT_WINDOW_TOKENS`；加 `LLM_PROVIDER` 等指针与 `DEEPSEEK_API_KEY` 占位；`*_MODEL` 标注为可选覆盖）
- [ ] 5.3 迁移本地 / 生产 `.env`（同上删除项；`LLM_BASE_URL` / `LLM_API_KEY` 暂留打 warn）；**前置核对** `LLM_API_KEY` / `EMBEDDING_API_KEY` / `RERANK_API_KEY` 是否相等（不等则保留 `<ROLE>_API_KEY` 覆盖，见 Important 1）；确认启动与今日行为一致（除 4.7 那处修复）

## 6. 陈旧修正

- [ ] 6.1 修正 `src/models.py` `get_classify_llm` docstring（移除对已生产的 `QueryRouter` 的描述）
- [ ] 6.2 修正 `src/agents/graph/state.py:92` 注释（移除不存在的 `nodes/query_router` 引用）
- [ ] 6.3 在 `docs/agents/requirements_pool.md` 登记 `QueryRouter` 灰区（生产死、CLI 活；供后续相关 change 评估并入）

## 7. 文档与 ADR

- [ ] 7.1 新增 ADR-0021（引入 provider 兼容层、拒绝网关），并给 ADR-0020 加反向指针；登记 README/code-map（**若改到 worktree 实施，先复核 dev-wsl 的 ADR 最大编号**）
- [ ] 7.2 更新 `docs/adr` 索引与 `glossary.md`（新增 Provider 兼容层 / 角色 / 思考形状 术语）

## 8. 测试

- [ ] 8.1 单测：`thinking_extra_body` 两种 style（含 `preserve` 完整性、构造/请求同源）
- [ ] 8.2 单测：registry 加载 + fail-fast（好 registry / 坏 registry 各分支）
- [ ] 8.3 单测：角色解析与覆盖优先级（主族跟随即覆盖 / embedding-rerank-judge 独立 / `.env` 覆盖 / 事实不可覆盖 / 未设 `*_MODEL` → registry 默认）
- [ ] 8.6 单测：`get_judge_llm()` 绑 `judge` 角色；校验时机（`import` 不触发 / `PROVIDER_VALIDATE=off` 可跳过）
- [ ] 8.7 单测：seed 判据纯函数（dry-run 与真跑一致；未配价 / 已存在 / 新建三分支）；启动期 ensure 幂等（重复启动不重复创建、无价记 WARNING、Langfuse 不可用不阻断）
- [ ] 8.4 改既有断言：`test_loop_middleware.py:114/142`、`test_skill_executor.py:282/315`、`test_reasoning_preserving.py`、`test_models.py`
- [ ] 8.5 集成：五个既有工厂在 `dashscope` 配置下产出与重构前等值（**除 D8 一处显式修复**）
- [ ] 8.8 单测/集成：带工具多轮下 `preserve_thinking` / `reasoning_content` 回传完整（主链路 + fork 两条路径）

## 9. Phase 2（切换 provider）

- [ ] 9.1 往 `registry.yaml` 加 `deepseek` 条目
- [ ] 9.2 `.env` 切 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`（embedding/rerank/judge 留百炼）；删除 `LLM_BASE_URL` / `LLM_API_KEY`
- [ ] 9.3 `--force-recreate` + 冒烟（非思考 / 思考 / 工具多轮）；确认启动期已自动 ensure 定价（无需手动 `seed`），成本列不再 NULL
- [ ] 9.4 实测语义差异：默认思考方向、思考档温度失效、上下文窗口

## 10. Phase 3（延后，指针，不在本 change 实现）

- [ ] 10.1 `LLM_KWARGS` blob 废弃（需先确认生产 `.env` 未设）
- [ ] 10.2 `vision` / `thinking_style:none` / `supports_disable_thinking` 启用（有真实 provider 需求时）；多模态实现；`reasoning_effort` 归一化
