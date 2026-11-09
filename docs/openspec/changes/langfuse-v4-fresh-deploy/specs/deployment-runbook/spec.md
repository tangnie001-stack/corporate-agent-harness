## MODIFIED Requirements

### Requirement: 运行形态与资源档位

runbook SHALL 规定运行形态为 `docker-compose.image.yml`（镜像全部来自 ACR，**目标机不构建**），SHALL 给出各服务 `mem_limit` 与实测整栈占用，并 SHALL 明确 `docker-compose.prod.yml` 的限额**不适用**于 8 GiB 目标机。可观测后端改为 v4 后，服务集合 SHALL 增加 `langfuse-worker` 与 `clickhouse`，资源档位 SHALL 相应重算。runbook SHALL 写明镜像来源分两库：基镜像在公开库 `deploy_base`（免登录），app 在私有库 `deploy_store_local`（需 `docker login`），并 SHALL 登记本次新增的三个镜像（langfuse、worker、clickhouse）的拉取与推送方式。

#### Scenario: 选择资源档位

- **WHEN** 在 8 GiB 机器上部署
- **THEN** 采用 `docker-compose.image.yml` 的档位，且 runbook 写明整栈峰值内存与剩余余量
- **AND** 写明 ClickHouse 与 worker 的上限及 Node 堆上限

#### Scenario: 确认镜像来源与登录要求

- **WHEN** 读者要确认镜像从哪来、要不要登录
- **THEN** runbook 写明基镜像免登录、app 镜像需先 `docker login` 私有库
- **AND** 新增的三个 Langfuse 相关镜像已登记来源与版本

### Requirement: 回滚与备份口径

runbook SHALL 规定代码回滚方式（回指旧的镜像 tag / 重跑旧构建），SHALL 规定数据侧备份要求，并 SHALL 指出 MinIO 中的原始上传文件是**唯一不可再生的源头**。对于可观测后端，runbook SHALL 明确：此次切换为**停机式且不提供数据回滚**——切换前已确认历史 trace 可丢弃，失败时以"修复或保持无观测运行"处置，不恢复旧库。

#### Scenario: 执行回滚

- **WHEN** 发布后需要回滚应用代码
- **THEN** 按 runbook 的回滚步骤执行（镜像 tag = 构建号，ACR 中可回指）
- **AND** 明确可观测后端不随应用回滚一起回退

#### Scenario: 识别备份缺口

- **WHEN** 评审部署就绪度
- **THEN** runbook 已写明哪些数据必须备份、以及不备份的后果
- **AND** 明确 Langfuse 历史数据不在备份范围内（已确认可丢弃）

### Requirement: 冒烟清单可执行且作为发布成功判据

runbook SHALL 提供最小冒烟清单（健康检查 → 控制台登录 → 上传一份文档 → 等待就绪 → 提问并看到引用 → Langfuse 中能查到该次 trace 与成本 → 能对一条 trace 手工打分），并 SHALL 规定缺任一步不得判定发布成功。

#### Scenario: 判定发布成功

- **WHEN** 部署动作执行完毕
- **THEN** 逐项跑通冒烟清单才算发布成功，任一步失败即视为发布未完成
- **AND** 其中"Langfuse 可查 trace"与"可手工打分"两项用于证明观测链路与数据中心链路均可用

## ADDED Requirements

### Requirement: 可观测后端的停机式切换

可观测后端升级 SHALL 采用停机式切换，步骤顺序 SHALL 为：关闭应用观测开关 → 停止应用 → 停用旧后端并切换到新后端 → 启动应用 → 冒烟校验 → 重新打开观测开关。切换窗口内 SHALL NOT 允许应用向已停止的后端重试上报。

#### Scenario: 按序切换

- **WHEN** 执行可观测后端切换
- **THEN** 应用先以 `LANGFUSE_ENABLE=false` 停止上报，再停应用
- **AND** 新后端就绪并通过健康检查后才启动应用
- **AND** 应用日志中不出现成批的上报失败重试
