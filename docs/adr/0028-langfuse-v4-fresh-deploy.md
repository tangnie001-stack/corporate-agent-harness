# ADR-0028：自托管 Langfuse 升级到 v4，采用全新部署且不迁移历史数据

- **Status**：Accepted
- **Date**：2026-11-09
- **Deciders**：用户（拍板：不迁移、可停机、可清空历史）；Claude（调研、实测与实施）
- **关系**：**完全取代 ADR-0011**（v2 降级决策）；不取代 ADR-0013（清理机制由 **ADR-0029** 取代）；ADR-0004 的「一个 PG 实例、两个 database」仍成立（v4 的事务数据仍落 PG），但其「trace 在 ClickHouse」的陈述在本决策下**重新成立**。

## 背景与问题

v2.95.11 是 v2 线最后一版，已 EOL、无安全补丁承诺。更关键的是：**v2 开源版把 LLM-as-a-Judge 与人工标注队列做成 Cloud-only 的二进制授权项**（enabled-entitlements 表里没有这两项），而这两项正是「数据中心 + 测评中心」的地基：

- 评测资产目前散在本地 JSON（`data/deepeval/testset/*.json`），不可筛选、不可标注、不可跨版本对比；
- 生产 trace 上没有任何自动或人工分数（实测 668 条 score 全部挂在评测题 trace 上）。

v4 起这两项能力对自托管开源版开放（只限量、不限功能），并自带 Dataset 版本化（每次增改归档产生时间戳版本）。

迁移成本方面：现有 Langfuse 库仅 28 MB / 336 traces / 1151 observations / 668 scores，且 score 是 DeepEval 结果的副本（真源在 `eval_report` 表与报告文件）。为这点历史走 v2→v3→v4 三跳、并行期与四个后台迁移，收益与成本不成立。

## 决策

1. **目标版本**：Langfuse **v4 线最新稳定版**（实施时记录具体 tag），web 与 worker 必须同 tag。
2. **部署形态**：web + worker + ClickHouse（≥25.12，实施取官方 compose 对应版本）+ 既有 PostgreSQL/Redis/MinIO；ClickHouse 与 MinIO 使用命名卷；ClickHouse 与 PG 使用 UTC。
3. **全新部署**：新建 database `langfuse_v4`，v4 迁移从零执行；**不迁移任何历史 trace / observation / score / dataset**；旧库与 v2 容器在验收通过后删除。
4. **不设回滚路径**：允许停机、允许清空历史；失败时在窗口内修复或短时无观测运行，不恢复 v2 数据。
5. **SDK 同步升级到 5.x**，trace_id 改为 **32 位小写十六进制**（W3C Trace Context / OTLP 硬约束），生成链为前端 → nginx `$request_id` → 后端。
6. **评测与 trace 读取**只实现 v4 一套，不做 v2/v4 双兼容。

## 理由

1. **能力缺口是硬约束**：v2 开源版拿不到 judge 与标注队列，这不是配置问题；v4 把它们改为「默认可用、只限量」，是唯一低成本路径。
2. **数据量不支持迁移**：三跳迁移的成本（并行期、后台迁移、3 倍磁盘、不可自动回滚）与 336 条 trace 的浏览价值严重不匹配。
3. **SDK 与服务端强耦合**：v4 的 `events_only` 模式拒收 Python SDK v2（除 `score-create` 外全部 400），服务端与 SDK 无法分两次上线。
4. **trace_id 协议约束不可绕过**：v3+ SDK 对非 32hex 只 warning 并忽略，表现为「日志 id 查不到 trace」，比直接失败更危险；保留前缀需要额外映射层，长期有害。

## 被否的替代方案（记录以免日后重开）

- **(A) 继续留在 v2**：无 judge/标注队列，评测只能是「本地脚本 + 手动看分」，与要建的数据中心目标不符。
- **(B) 升到 v3 末版**：能力满足，但 v3 只提供安全补丁到 2027-01，且 v4 已改数据模型（observations-first），三年内还要再迁一次同一批文件。
- **(C) 走官方 v2→v3→v4 三跳并迁移历史**：为 28 MB 数据承担并行期、后台迁移、3 倍磁盘与不可自动回滚，成本远大于收益。

## 后果

**正面**：获得 judge/标注队列/Dataset 版本化；观测查询与长周期聚合由 ClickHouse 承担；与上游主线一致，后续可按小版本升级。

**负面 / 代价**：

- 新增两个常驻组件（ClickHouse + worker）与一套对象存储桶，运维面变大；
- 历史 trace 归零，无法回看 v2 期间的数据（已确认可接受）；
- SDK 迁移涉及 10 个源文件与 7 个测试文件，读路径（`eval_trace_io`）需重写；
- trace_id 契约变更一次（`X-Trace-ID` 响应头格式变为 32hex）。

## 复查条件

- 若 v4 在目标机上无法稳定运行（OOM / 迁移失败 / 登录不可用），重新评估是否退回 v2 或改用 Cloud；
- 若 12 个月内 Langfuse 再出大版本且数据模型再次变更，重估「跟主线」的成本；
- 若 trace 日增量达到 v3 级别的容量压力（日万级以上），重估 ClickHouse 资源档位与保留期。
