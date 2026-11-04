## 1. 骨架与配置

- [ ] 1.1 新建 `src/rca/` 包：`models.py`、`context.py`（规则反查）、`loader.py`（skill 解析）、`collector.py`、`judge.py`、`render.py`
- [ ] 1.2 在 `src/rca/models.py` 定义 dataclass：`AlertContext`（含 uri/阈值/for/数据源/values）、`EvidenceSpec`、`EvidenceResult`（三态）、`TriageVerdict`
- [ ] 1.3 新建 `src/config/rca.py` 承载本能力配置（开关、白名单、超时、并发、严重阈值、核心接口清单）——**不加进已超 400 行的 settings.py / const.py**
- [ ] 1.4 判定提示词进 `src/config/prompts/templates/`（YAML，`kind: task`），不在其内做条件拼接；证据包由代码组装成 user 消息

## 2. Grafana 客户端

- [ ] 2.1 新建 `src/infra/grafana_client.py`：Bearer 鉴权、超时、重试策略（仅对连接错误重试一次）、日志脱敏
- [ ] 2.2 实现规则反查：`GET /api/prometheus/grafana/api/v1/rules` → 按 `alertname + grafana_folder` 匹配
- [ ] 2.3 实现规则解析：从 `A.model.query` 提取 `request_uri` 与 `logstore`；从条件表达式取阈值；读 `duration`
- [ ] 2.4 实现 10 分钟缓存，键 `alertname|grafana_folder`；解析失败返回"语义未知"标记
- [ ] 2.5 实现告警历史：`GET /api/annotations?type=alert`，按 alertname 聚合为"出现分钟数"，处理分页与时间跨度上限
- [ ] 2.6 实现查询代理：`POST /api/ds/query`，**按 `frames[].data.values` 判空，不看 `status`**；按字段名索引列
- [ ] 2.7 补单测：成功但 status=500、frames 为空、列式取值、端口/路径可配置

## 3. 取数抽象

- [ ] 3.1 定义 `LogSource` 协议：`latency_stats` / `request_detail` / `business_impact`
- [ ] 3.2 实现 `GrafanaLogSource`（生产）：组装 SLS SQL → ds/query → 结构体
- [ ] 3.3 实现 `FixtureLogSource`（仅测试）：读 `tests/fixtures/rca/*.json`
- [ ] 3.4 生产未配置 Grafana 凭据时直接返回 `failed`，**禁止退回 fixture**
- [ ] 3.5 补测试：半开区间边界、秒→毫秒换算、空结果记 `empty` 而非失败

## 4. Skill 加载与匹配

- [ ] 4.1 `src/rca/loader.py`：解析 `skills/rca-*/` 的 frontmatter（match / evidence），做 schema 校验；非法 skill 拒绝加载并记 error
- [ ] 4.2 按 `alertname` 匹配（精确优先、其次正则）；未命中返回通用兜底清单并标记"未覆盖"
- [ ] 4.3 新增 `skills/rca-latency/SKILL.md`：声明 `user-invocable: false` 与 `disable-model-invocation: true`；evidence 含 `latency_stats`（required）、`request_detail`（`when: n < 20`）、`business_impact`（可选）、`alert_history`（可选）
- [ ] 4.4 补 `skills/rca-latency/references/tools.md`：可用工具、参数、返回字段、占位符说明
- [ ] 4.5 验证与既有 `src/agents/skills/loader.py` 互不影响：`rca-*` 不出现在可用技能列表与 `/命令` 中

## 5. 证据采集器

- [ ] 5.1 占位符严格解析（`${alert.*}`，缺字段记 `failed/missing_field`）
- [ ] 5.2 两波并行执行（先无 `when`，再按 `when` 结果），并发受配置限制
- [ ] 5.3 单源超时 + 总预算超时；超时取消在途查询，未完成标 `failed/total_timeout`
- [ ] 5.4 `required` 短路：必需源失败 → 只出"证据不足"，档位 `建议观察`
- [ ] 5.5 异常归一：采集/加载/求值抛错 → `failed/internal_error`，不中断判定
- [ ] 5.6 证据包裁剪：每源行数与字符上限

## 6. 判定

- [ ] 6.1 样本量校验：`n = 0` → "日志断流或服务不可用"；`1–19` → 逐条比对；`≥ 20` → 归因
- [ ] 6.2 代码定档：`无需处理` 需同时满足四条（n≥1、5xx=0、慢请求≤1、最大耗时<严重阈值）**且**命中一条正向证据
- [ ] 6.3 档位与模型文本冲突时以代码为准，记一条降级日志
- [ ] 6.4 补判定测试：零样本、单条慢请求、两条全慢、超严重阈值、必需源缺失、历史同模式自愈

## 7. 渲染

- [ ] 7.1 `src/rca/render.py`：定量字段（时间/影响量/分布/业务影响）**由证据直接填充**，模型只写定性段落
- [ ] 7.2 卡片固定包含：档位+置信、一句话结论、时间、影响量、分布、业务影响、历史、假设、反证、下一步、未取到
- [ ] 7.3 `failed` 与 `empty` 分列；业务结果不可见时输出"业务成功率未知"
- [ ] 7.4 脱敏：客户端 IP、节点内网地址脱敏，节点用别名
- [ ] 7.5 补渲染测试：字段齐全、缺失项展示、超长截断

## 8. 接入报警轮

- [ ] 8.1 输入构造改为「告警上下文 + 证据包 + 判定正文」
- [ ] 8.2 结论获取改为渲染文本；落库与推送同源；解析失败落中断态
- [ ] 8.3 开关与白名单：默认关闭；未在白名单走原行为；关闭态新格式不写会话历史
- [ ] 8.4 占位之后整段编排包统一 try/except → 兜底转发，保证"占位必有下文"
- [ ] 8.5 落库结构化判定（档位、证据摘要、窗口），供误拦回访

## 9. 测试与验证

- [ ] 9.1 端到端：fixture 复现 createOrder 场景（每分钟 2 单，avg 1057），断言 `无需处理` 且列出明细
- [ ] 9.2 端到端：必需源超时 → "证据不足"、无根因假设
- [ ] 9.3 端到端：规则反查失败 → "语义未知" + `建议观察`
- [ ] 9.4 回滚验证：关闭开关后行为与变更前一致，历史无 JSON 残留
- [ ] 9.5 `POSTGRES_HOST=localhost pytest tests/ -v` 全绿；`ruff check .` 无错误；无新增 pyright error

## 10. 文档

- [ ] 10.1 `docs/agents/glossary.md` 登记：三档判定、证据三态、证据包、严重阈值、语义未知
- [ ] 10.2 `docs/agents/defensive-patterns.md` 登记：低流量接口不得用分钟级分位数做告警口径；"未找到原因"不得等同于"无异常"；Grafana ds/query 的 status 不可信
- [ ] 10.3 `docs/agents/api_contract.md` 登记报警轮输入与结论形态变化
- [ ] 10.4 `docs/agents/code-map.md` 增加 `src/rca/` 与 `src/infra/grafana_client.py`
- [ ] 10.5 在 `docs/research/2026-11-04-grafana-arms-api-capabilities.md` 头部标注本次实现引用位置
