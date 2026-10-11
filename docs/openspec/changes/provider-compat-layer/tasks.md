# Tasks

> **本文件是「范围 / 验收索引」，不是执行步骤** —— 步骤在 plan，二者分工见 `docs/agents/dev-flow.md` ⑤「步骤单一真源」（plan 承载步骤、`tasks` 承载范围/验收）。
>
> **执行档**：`docs/superpowers/plans/2026-10-11-provider-compat-layer.md`（Part A Phase 1 → Part B Phase 2 → Part C Phase 3 边界；连续执行，仅 Part B 的两个人工闸门停下）。

## Phase 1 — 建层（等值重构 + D8 一处显式修复）

- [ ] **Phase 1 完成**
  - **范围**：`src/infra/llm/provider/`（registry + spec + thinking + chat + factory）；`src/models.py` 门面薄委托（含新增 `get_judge_llm`）；middleware / executor 接线；settings 指针与默认空；seed 判据纯函数；启动期校验 + 定价 ensure；embedding 维度常量；陈旧 docstring 修正；ADR-0021。
  - **验收**：`pytest tests/infra/llm/provider` 全绿；既有消费方零改动（`get_llm` 等调用点不变）；行为与今天等值，**唯一例外** = 主链路恢复 `preserve_thinking`（design D8）。
  - **执行档**：plan **Part A（Task 1–14）**。

## Phase 2 — 切 provider（计划外动作，本机一条龙）

- [ ] **Phase 2 完成**
  - **范围**：registry 加 `deepseek`；本机 `.env` 切 `LLM_PROVIDER=deepseek` + `DEEPSEEK_API_KEY`（保留 `DASHSCOPE_API_KEY`）；`docker compose up -d --force-recreate app`；冒烟。
  - **验收**：非思考 / 思考 / 工具多轮三场景回话正常；三台企微机器人正常；Langfuse 成本列非 NULL。
  - **执行档**：plan **Part B（B0–B5）**；含 **2 个人工闸门**：G1（切换活系统确认）、G2（机器人回话确认）。

## Phase 3 — 延后（本 change 不展开）

- [ ] **Phase 3（延后，不勾）**
  - **范围 / 指针**：更多 provider；`vision` / `thinking_style:none` / `supports_disable_thinking` 启用；`reasoning_effort` 归一化；`LLM_KWARGS` 废弃。
  - **执行档**：无（出现真实需求时另立 plan / change）。
