## Why

诊断“为什么答错 / 为什么报错”时，最常见的真实原因是**刚刚变过**：知识库重建、prompt 改了、模型切了、配置调了、发了版。但本仓现在拿不到这些事实：

- `document` / `chunk` **没有创建时间**，判不了“这个 KB 最近一次入库是什么时候”；
- 没有变更流水，判不了“这条 trace 发生时，系统跑在什么配置上”；
- `trace-diagnosis` 的 `kb_stale` 因此是个**没有证据源的空枚举**，已被暂时移除。

业界做法一致：Datadog Change Tracking、PagerDuty Recent Changes、Rootly deploys/commits、Azure 部署关联，都把变更当一等证据（见 `docs/research/2026-11-06-ai-sre-incident-feature-matrix.md`）。

## What Changes

- **补数据债**：`document` / `chunk` 增加 `created_at`（新数据自动填，存量不回填）。
- **变更流水表** `change_event`：`id / ts / kind / target / detail / actor`，`kind ∈ deploy | config | kb | model | prompt`；三个写入点——发布脚本、KB 入库完成、配置/模型切换。
- **当前态指纹**：镜像 tag、容器启动时间、生效模型、prompt/技能内容哈希、KB 文档数与最近入库时间。
- **`ChangeSource`**：给定 trace 时间，取窗口内变更（默认前后各 2 小时），产出“候选变更”证据。
- **回填诊断**：`trace-diagnosis` 增加 `kb_stale` / `config_changed` 两类根因，判据来自本变更的证据。

## Capabilities

### New Capabilities

- `change-provenance`：变更记录与当前态指纹的采集、按时间窗取候选变更、作为诊断证据的输出契约。

### Modified Capabilities

- `chunk-data-model`（或对应文档模型能力）：文档/分块增加创建时间。
- `trace-diagnosis`：新增两类根因与对应证据源（该变更先行落地后再回填）。

## Impact

**代码**

- 迁移：`document` / `chunk` 加 `created_at`
- 新增 `change_event` 表与仓库层
- 新增 `src/rca/sources/change.py`（指纹 + 窗口比对）
- 发布脚本 `deploy.sh` / 流水线增加一条“变更登记”

**依赖**

- 无跨团队依赖；`trace-diagnosis` P1 先落地（否则没有消费方）

**风险**

- 变更登记漏写导致“假阴性”（实际变了但没记录）：以**指纹对比**兜底，不只依赖人工登记
- 指纹粒度选择：镜像 tag 可能相同而内容不同 → 用内容哈希（prompt/skills/agents）+ 迁移版本号
- 时间对齐：容器与 DB 时钟偏差会误判窗口 → 统一用 UTC，报告中标注时间来源

## 非目标

- 不接全量 CI/CD、不做代码 diff 分析
- 不做自动回滚或自动修复
- 不做跨系统（支付等）的变更溯源

## 待定

1. `change_event` 保留期（建议 180 天，与诊断报告一致）
2. 指纹粒度：镜像 tag vs git sha vs 内容哈希的组合
3. 变更窗口大小（前后各 2 小时是否足够，需用历史事故回测）

## 验收判据

1. 发布一次，`change_event` 自动多一条 `kind=deploy` 记录
2. 入库一个文档后，该 KB 的“最近入库时间”随之变化
3. 给定 trace 时间，`ChangeSource` 能取回窗口内的变更并作为证据输出
4. 人为漏登记一次变更，指纹对比仍能发现差异
