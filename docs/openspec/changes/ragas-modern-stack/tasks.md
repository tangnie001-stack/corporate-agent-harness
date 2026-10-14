# Tasks

> **执行档索引** —— 可执行的 TDD 步骤清单在计划文件里（避免与 `tasks.md` 出现两套步骤）。
> 计划：`docs/superpowers/plans/2026-10-14-ragas-modern-stack.md`（**实施时创建**；本 change 目前处于**设计阶段**）
> 规格：`specs/evaluation-pipeline/spec.md`（5 条 ADDED）；决策：`design.md`（D1–D10）
> 排序声明：本 change 与在途 `ragas-agent-eval` 都触及 `evaluation-pipeline`，但落在**不同 requirement**（见 `proposal.md`「排序声明」）；归档按序 apply 即可，不互相阻塞。

## 执行档

- [ ] **单一计划到底**：`docs/superpowers/plans/2026-10-14-ragas-modern-stack.md`（实施时创建）
  - **分组（按 design.md「Migration Plan」）**：
    - **G1 裁判构造 + 失败语义**（D6/D9/D2）：按角色集中构造 native client（`temperature=0` / 不发 `top_p` + 显式超时）；失败记 `error`（**只进 CSV/MD，stdout 形状不变**）。
    - **G2 执行内核迁移**（D1/D4/D5）：`@experiment` + `collections`；`Dataset` 用 **`inmemory`**；**行级**信号量 `RAGAS_EVAL_CONCURRENCY=5`；产出层改读新表结构；**验收通过后删除 legacy 路径**。
    - **G3 出题整改**（D8）：`default_transforms` 全量 + `RAGAS_SELF_CONTAINED_HINT`；重出测试集验证 abstract 恢复且题面自足。
    - **G4 模型版图**（D10）：registry 绑定（`judge→qwen3.8-flash`；`main`/`classify`/`summary→qwen3.7-flash`）+ `qwen3.7-flash` catalog 定价。
    - **G5 活体验收**：1 题冒烟 → 小批 → 全量；逐项核对与 legacy 的复现度（design.md「证据」节）。
  - **完成标准**：① `POSTGRES_HOST=localhost pytest tests/ -q` 全绿 + `ruff check .` 无错 + `pyright src/` 不新增 error + `openspec validate ragas-modern-stack --strict`；② 活体评测：无弃用警告、5 指标齐出分、失败行带 `error` 可见（**stdout 仍恰 5 行**）、出题 abstract 恢复且题面自足、评估检索**未**被收窄。
  - **执行器**：`subagent-driven-development`（推荐，逐任务派新子代理 + 两段评审）或 `executing-plans`。
  - **不拆分理由**：全为普通代码任务、执行器相同；无部署/破坏性操作；无独立交付边界。

## 已定（原未决，2026-10-14 经评审 + grilling 定稿）

- [x] **裁判模型**：`qwen3.8-flash`（实测结构化输出可靠）；主模型切 `qwen3.7-flash` ⇒ 裁判 ≠ 选手，无自评偏置（见 D10）。
- [x] **Dataset 落盘**：`backend="inmemory"`（不落目录），产物仍走既有 `data/ragas/reports/`（见 D4）。
- [x] **裁判思考档位**：默认开；实施时做一次同题 A/B（thinking on/off）确认分数稳定性/一致性。
- [x] **并发**：**行级**信号量 `RAGAS_EVAL_CONCURRENCY` 默认 5，行内 5 指标串行（见 D5）。
- [x] **legacy 处置**：验收通过后**删除**（不长期并存）（见 Migration Plan）。
- [x] **stdout 契约**：形状**永不变**（`compare_retrieval` 的解析契约 + 测试锁恰好 5 行）（见 D2）。
- [x] **`llm_context`**：定稿常量 `RAGAS_SELF_CONTAINED_HINT`（中英各一句）（见 D8）。
- [x] **连带面**：出题**共用** judge 角色；`classify`/`summary` **一并**降级（影响文档分类 + 历史摘要）（见 D10 与 proposal Impact）。
- [x] **回归基线**：**本变更不建**（门禁另案）；只留「迁移对照记录 + 首次 post-migration 分数快照」。
- [x] **`overall_score` 口径**（D11）：统一到主分（**纯 KB 五项均值**，含 `answer_correctness`）；`kb_eval` API 补 `answer_correctness`；通过阈值**单一来源**（API 下发，前端不硬编码）；MD 删「两口径分叉」Note。**实测新口径区间 ≈ 0.68~0.92**（n=7 粗参考），系统性低于旧口径 **0.03~0.13** ⇒ 阈值先维持 `0.70` 并标「待标定」。
- [x] **测试集版本**：删 `v2`/`v3`（冒烟临时）；留 `v1` 并在其 `metadata.note` 标「整改前（题面歧义）」；新出为 **v4**。
- [x] **出题验收判据**：三道一起 —— ① 生成日志**无** `Skipping multi_hop_abstract_query_synthesizer`；② 生成 JSON **含** `multi_hop_abstract` 样本 ≥1；③ 题面**粗筛含** KB 内任一公司名。
- [x] **迁移对照脚本**：**一次性**（跑完即弃、**不入库**）；对照数字落 change 验收报告 / `docs/research/`。
- [x] **catalog 定价**：补 `qwen3.7-flash` **与** `qwen3.8-flash` 的 `pricing`（数字取自百炼价目表）。
- [x] **提交/推送**：`eea91b3c` / `5f09931c` / `a9466d9f` **已推**（`77f65a13..a9466d9f`）；`a9466d9f` 的 judge 模型名由 **G4** 改回 `qwen3.8-flash`。
