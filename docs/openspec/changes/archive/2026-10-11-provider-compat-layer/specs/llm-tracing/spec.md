## ADDED Requirements

### Requirement: 模型定价的幂等播种

系统 SHALL 把 provider 注册表 `catalog` 中的模型定价幂等地播种为 Langfuse 模型定义，使成本计算不因"模型未 seed"而静默为 NULL。播种 SHALL 提供两条通道：手动 CLI（`seed_langfuse_models`）与应用**启动期自动 ensure**。

播种 SHALL 幂等（已存在的模型定义不重复创建），且在 Langfuse 不可达或被禁用时 SHALL 不影响应用启动。

#### Scenario: dry-run 与真跑共用判据

- **WHEN** 以 `--dry-run` 运行播种 CLI
- **THEN** 每一项的"创建 / 已存在 / 跳过"判定结果与真跑**完全一致**
- **THEN** 判定逻辑来自同一个纯函数

#### Scenario: dry-run 对未配价项如实报告

- **WHEN** 某模型在注册表中未配置定价（或不在 `catalog` 中），且以 `--dry-run` 运行
- **THEN** 该项被报告为"将跳过"
- **THEN** CLI 不对该项报告"将写入"

#### Scenario: 启动期 ensure 已配价模型

- **WHEN** 应用启动且某被绑定角色的模型在注册表中有定价、但 Langfuse 尚无对应模型定义
- **THEN** 系统在启动期创建该模型定义（幂等）

#### Scenario: 启动期遇到无价模型

- **WHEN** 应用启动且某被绑定角色的模型在注册表中无定价
- **THEN** 系统记录 WARNING（不静默），不创建零价模型定义

#### Scenario: 播种失败不阻断启动

- **WHEN** Langfuse 不可达，或 `LANGFUSE_ENABLE=false`
- **THEN** 播种降级（记 warning 或跳过），应用正常启动
