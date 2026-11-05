## ADDED Requirements

### Requirement: demo 入口的部署与限流归属
demo 入口 SHALL 作为独立 server（`demo.tangnie.cn`）由入口机与应用机 nginx 共同承载，与正式站共用同一 app 上游；请求频率限制 MUST 在 nginx 层生效，配额与预算 MUST 在应用层生效，两者不得互相替代。

#### Scenario: demo 域名路由到同一应用
- **WHEN** 访客访问 `https://demo.tangnie.cn`
- **THEN** 入口机完成 TLS 终结并转发到应用机 80，应用机按 Host 将请求交给 app，路径与正式站一致

#### Scenario: 限流在网关生效
- **WHEN** 单 IP 在窗口内请求次数超过阈值
- **THEN** nginx 直接返回限流响应，请求不进入应用进程

### Requirement: demo 紧急关停步骤
runbook SHALL 记录 demo 的紧急关停步骤：在网关层将 demo 域名切为维护响应，且不修改正式站配置、不重启 app。

#### Scenario: 按 runbook 关停并恢复
- **WHEN** 运维执行关停步骤
- **THEN** demo 域名返回维护响应，正式站域名不受影响；恢复步骤可原样反向执行

### Requirement: 演示数据与正式数据隔离
demo 演示知识库 SHALL 独立于业务知识库创建，其 UUID MUST 记录在部署配置中；演示语料与重置操作 MUST 只作用于该库。

#### Scenario: 重置演示数据不影响业务库
- **WHEN** 执行演示重置
- **THEN** 仅清理演示库的文档与会话，其他知识库与文档保持不变
