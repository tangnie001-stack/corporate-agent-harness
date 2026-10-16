# Tasks

> **执行档索引** —— 可执行的步骤清单在计划文件里（避免与 `tasks.md` 出现两套步骤）。本文件只列执行档与其完成标准。
> 计划：`docs/superpowers/plans/2026-10-12-ragas-agent-eval.md`（Task 1–8，TDD、逐步提交）
> 规格：`specs/evaluation-pipeline/spec.md`、`specs/llm-tracing/spec.md`；决策：`design.md`（D1–D7）
> 排序声明：本 change 与在途 `prompt-layering-and-domain-binding` 落在 `evaluation-pipeline` 的**不同 requirement**（不动第 6 条）；归档按序 apply 即可，不互相阻塞。

## 执行档

- [x] **单一计划到底**：`docs/superpowers/plans/2026-10-12-ragas-agent-eval.md`
  - **完成标准**：计划内 **Task 8** 的两道闸门通过 —— ① `POSTGRES_HOST=localhost pytest tests/ -q` 全绿 + `ruff check .` 无错 + `pyright src/` 不新增 error + `openspec validate ragas-agent-eval --strict`；② 活体真库评测：有检索样本 `contexts` 非空、CSV/MD 含 `answer_correctness`/`sample_class`/`skipped`、含联网样本不进主分、Langfuse trace 上分数可见。
  - **执行器**：`subagent-driven-development`（推荐，逐任务派新子代理 + 两段评审）或 `executing-plans`。
  - **不拆分理由**：全为普通代码任务、执行器相同；无部署/破坏性操作；无独立交付边界（判据 A/B 均不命中）。
