## Why

当前"大模型底座"与代码绑定：provider 特有的思考参数（`enable_thinking`/`preserve_thinking`）、reasoning 回传策略、角色→模型/端点解析，散落在 **5 处代码**（`middleware.py:85`、`models.py:191/219`、`executor.py:437`、`reasoning_chat.py:86`）+ 单一 `LLM_BASE_URL` 配置里。切换 provider（如百炼 → DeepSeek）或新增一家，必须改代码、还容易漏（漏关思考即静默变贵）。ADR-0020 已否决用网关统一（覆盖不全 + 内存/耗时），故需要在应用内建**一个** provider 兼容层：把差异收敛到一层、把 provider 事实收进可评审的数据文件，使"切换 provider"回到纯配置。

## What Changes

- 新增 `src/infra/llm/provider/` 兼容层包：provider 数据（YAML）+ 思考参数归一化 + 角色解析 + 三类模型客户端工厂（对话 / 向量化 / 精排）。
- 新增 `src/infra/llm/provider/registry.yaml`：以 provider 为键，声明 `base_url`、思考形状、能力（`tools`）、各角色默认模型名、模型事实（定价 / 上下文窗口 / tokenizer 系数）、embedding 批大小与维度。
- `.env` 改为 **provider 指针**（`LLM_PROVIDER` + 角色级 `<ROLE>_PROVIDER`）+ 按 provider 命名的密钥（`<PROVIDER>_API_KEY`）+ 可选覆盖（`<ROLE>_MODEL` / `<PROVIDER>_BASE_URL`）。
- 5 处硬编码思考参数改走层的**单一纯函数**（这是一处**显式行为修复**：主链路恢复 `preserve_thinking`，故 Phase 1 为"等值重构 + 该修复"）；**新增 `get_judge_llm()`**（RAGAS 裁判显式绑 `judge` 角色，**缺省 provider 独立为 `dashscope`**，切生产 provider 不影响评测）；各工厂的 `model` 参数改 `str | None = None`（空 / None → registry 默认，使"改 `.env` 指针即切"真正成立）。
- 范围裁剪（YAGNI）：本 change 只实现 `dashscope` + `deepseek` 两种思考形状；`vision` / `thinking_style:none` / `supports_disable_thinking` 延后到有真实 provider 需求再加。
- 新增**启动期 fail-fast 校验**：未知 provider / 无对应分支的思考形状 / 角色绑到缺能力的 provider（如 `embedding→deepseek`）/ 被绑定 provider 缺 key / registry 格式或字段缺失。校验限**应用启动期**执行（`import` 不触发），并提供 `PROVIDER_VALIDATE=off` 开关。
- **定价播种**：`seed_langfuse_models` 的 dry-run 与真跑共用单一判据（抽纯函数），dry-run 对"未配价/未知模型"如实报"将跳过"；新增**应用启动期幂等 ensure**（有价则确保 Langfuse 模型定义存在、无价则 WARNING），换模型 / 切 provider 不再需要手动 seed，静默 NULL 从根上消除。
- reasoning 回传改为**按 provider 能力驱动**（`reasoning_field` + preserve 标志，从硬编码变数据驱动）；思考开关**永远显式发送**，不依赖 provider 默认值。
- 把 `PROMPT_TOKENS_PER_CJK_CHAR`、`EMBEDDING_BATCH_SIZE`、embedding `dimension` 纳入 registry；修正 `EMBEDDING_DIMENSION` 死变量——把 `chunk.py` 的 `Vector(1024)` 抽成常量并与 registry 声明做一致性校验（**本 change 内做成**，不改列、不迁移）。
- 顺带修正两处陈旧 docstring（`models.py` 把已生产的 `QueryRouter` 列为消费方；`state.py` 引用不存在的 `nodes/query_router` 节点）。
- 新增 ADR（记录"引入 provider 兼容层、拒绝网关"，并给 ADR-0020 加反向指针）；更新 `model-config` 规格。

**BREAKING（分两步）**：`.env` 中 `LLM_BASE_URL`、`LLM_API_KEY`、`EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`RERANK_API_KEY`、`MODEL_INPUT_PRICE_PER_TOKEN`、`MODEL_OUTPUT_PRICE_PER_TOKEN`、`PROMPT_TOKENS_PER_CJK_CHAR`、`EMBEDDING_BATCH_SIZE`、`MODEL_CONTEXT_WINDOW_TOKENS` 迁移（值改由 registry / `<PROVIDER>_API_KEY` 提供）；`*_MODEL` 保留为可选覆盖。为避免 Phase 1 部署 breaking，`LLM_BASE_URL` / `LLM_API_KEY` 采取**软迁移**：Phase 1 打 deprecation warning 并当覆盖用，Phase 2 删除。

## Capabilities

### New Capabilities

- `provider-compat`：单一 provider 兼容层——registry 数据面（provider 事实 + 模型目录）、思考参数归一化（on/off → 各 provider 形状）、角色→provider 解析、启动期校验、reasoning 回传按能力驱动。

### Modified Capabilities

- `model-config`：三类模型的配置与创建改为经 registry + provider 指针解析；新增 provider 指针与覆盖规则；LLM/Embedding/Rerank 创建统一走兼容层。
- `llm-tracing`：新增"模型定价的幂等播种（手动 CLI + 启动期 ensure）"要求；成本口径不因未 seed 而静默为 NULL。

## Impact

- **代码**：新增 `src/infra/llm/provider/`（`spec.py` / `thinking.py` / `chat.py` / `factory.py` / `registry.yaml`）；修改 `src/models.py`（含新增 `get_judge_llm`）、`src/agents/graph/middleware.py`、`src/agents/skills/executor.py`、`src/infra/llm/reasoning_chat.py`、`src/config/settings.py`、`src/cli/seed_langfuse_models.py`、`src/infra/db/models/chunk.py`（维度常量）；两处 stale docstring。
- **配置**：`.env` 迁移（删 `LLM_BASE_URL`/`LLM_API_KEY`/`MODEL_*_PRICE_PER_TOKEN`；加 `LLM_PROVIDER` 等指针与 `DEEPSEEK_API_KEY` 等）。
- **依赖**：`pyyaml` 需在 `pyproject.toml` 显式声明（现仅为传递依赖）。
- **部署**：registry.yaml 随镜像（`Dockerfile` 已有 `COPY src/ src/`，无需改 Dockerfile）；切 provider 仍需 `--force-recreate`。
- **文档/规格**：新增 ADR；`model-config` 与 `llm-tracing` 规格改写；`docs/agents/requirements_pool.md` 登记 `QueryRouter` 灰区（本 change 不实现，供后续相关 change 评估并入）。
