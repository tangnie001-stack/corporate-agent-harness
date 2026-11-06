# change-provenance Specification (Delta)

## ADDED Requirements

### Requirement: 变更必须被记录

系统 SHALL 记录发布、配置、知识库、模型、prompt 五类变更，每条含时间（UTC）、类型、目标与摘要；写入点至少覆盖发布脚本、KB 入库完成、配置/模型切换。

#### Scenario: 发布产生一条变更记录

- **WHEN** 执行一次应用发布
- **THEN** `change_event` 新增一条 `kind=deploy` 记录，含镜像标识与 UTC 时间

#### Scenario: 漏登记不影响发现

- **WHEN** 人为跳过变更登记，但实际修改了 prompt 或技能内容
- **THEN** 当前态指纹与上次记录不一致，系统能报告"存在未登记的变更"

### Requirement: 按时间窗提供候选变更

系统 SHALL 给定一个时间点，取该点前后窗口内的变更作为**候选证据**输出，SHALL NOT 直接断言变更是根因。

#### Scenario: 窗口内无变更

- **WHEN** 目标时间前后窗口内无变更记录
- **THEN** 输出"无候选变更"，不推导因果

#### Scenario: 窗口内有变更

- **WHEN** 窗口内存在一条或多条变更
- **THEN** 输出变更清单（类型、时间、目标、与目标时间的间隔），由下游判定使用
