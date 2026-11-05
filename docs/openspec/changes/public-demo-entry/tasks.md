## 1. 配置与身份

- [ ] 1.1 `src/config/settings.py` 新增 `DEMO_MODE`、`DEMO_KB_ID`、`DEMO_SESSION_MAX_TURNS`、`DEMO_USER_DAILY_TURNS`、`DEMO_DAILY_TOKEN_BUDGET`、`DEMO_MAX_CONCURRENCY`，全部走 `os.getenv` 并给默认值
- [ ] 1.2 `src/config/const.py` 的 `Channel` 增加 `DEMO`；SSE/前端文案常量（额度用尽、范围声明）集中到 `const.py`
- [ ] 1.3 `src/middleware/auth.py` 增加 demo 判定：`DEMO_MODE` 且 Host 命中 demo 域名时设置 `request.state.is_demo=True`
- [ ] 1.4 demo 请求忽略 `token` Cookie，复用 `user_id` Cookie 并统一为 `demo_<uuid>` 格式
- [ ] 1.5 新增 `tests/middleware/test_demo_auth.py`：开关关闭不变、demo 域忽略 token、首次访问分配身份

## 2. 数据边界

- [ ] 2.1 `src/api/chat.py` 在取到 `kb_id` 后按 demo 身份强制覆盖为 `DEMO_KB_ID`（`DEMO_KB_ID` 为空则拒绝请求）
- [ ] 2.2 新增 demo 守卫（中间件或路由依赖）：上传、删除文档、创建/删除知识库、修改领域、评测、题集生成、配置类接口返回 403
- [ ] 2.3 demo 下 `/api/kbs/list` 只返回 `DEMO_KB_ID` 对应知识库（只读）
- [ ] 2.4 增加测试：demo 下传入其他 kb_id 时检索目标被覆盖；写接口 403 且无副作用

## 3. 配额与预算

- [ ] 3.1 Redis 计数器：`demo:turn:<session_id>`、`demo:user:<uid>:<yyyymmdd>`、`demo:tokens:<yyyymmdd>`，按 Asia/Shanghai 日界过期
- [ ] 3.2 轮数检查接入 `chat/stream` 入口（超限 409 + 可读文案），并在每轮结束后自增
- [ ] 3.3 token 累计落在 provider 层统一出口，流式结束后写入 Redis 日计数
- [ ] 3.4 并发限制：demo 同时只允许 `DEMO_MAX_CONCURRENCY` 个流，超出直接拒绝并提示
- [ ] 3.5 测试：轮数超限、预算耗尽、重启后计数仍在（Redis 保持）

## 4. 前端演示态

- [ ] 4.1 打开 demo 域时，`chat.html` 首屏展示范围声明（"本演示为腾讯 2024 年报资料"）
- [ ] 4.2 展示 6 个示例问题按钮，点击直接发起提问；问题文案集中配置，不在模板里散落
- [ ] 4.3 隐藏知识库管理、上传、评测入口；知识库选择器在 demo 下不渲染或固定为演示库
- [ ] 4.4 额度耗尽时禁用输入并给出可读提示（含"新建会话"引导）
- [ ] 4.5 429/503（网关限流）在页面上显示"稍后再试"，不表现为无响应

## 5. 演示语料与脚本

- [ ] 5.1 新建独立 demo KB（名称以 `demo-` 开头），记录 UUID 到部署配置
- [ ] 5.2 新增种子脚本：灌入 `data/test_docs/tencent_2024_annual.pdf` 并等待入库完成
- [ ] 5.3 新增重置脚本：只按 `DEMO_KB_ID` 清理文档、chunks 与 MinIO 对象，执行前校验库名前缀
- [ ] 5.4 6 个示例问题逐条验证（回答正确且带引用），不合格的换题或补语料
- [ ] 5.5 抽查分块质量：单块长度、表格是否完整、页级引用是否可用

## 6. 部署与关停

- [ ] 6.1 `deploy/nginx/nginx.conf` 新增 `demo.tangnie.cn` server 块，转发到同一 app 上游，复用 SSE 与上传上限配置
- [ ] 6.2 配置 `limit_req_zone`（按 IP）并在 demo server 上启用，阈值与规格一致
- [ ] 6.3 `deploy/nginx/edge.conf` 新增 demo 域名 server（443 + 证书），反代到应用机 80
- [ ] 6.4 DNS 新增 `demo` A 记录指向入口机；证书按通配符或单独签发就位
- [ ] 6.5 runbook 增加 demo 章节：上线步骤、毕业条件（有真实数据必须拆独立实例）、紧急关停/恢复命令

## 7. 观测

- [ ] 7.1 chat 入口按 `is_demo` 设置 `current_channel=Channel.DEMO`，日志与 Langfuse 可分流
- [ ] 7.2 检索零命中的演示提问打 `demo_miss` 事件（脱敏 query + trace_id），并在 `log_event_specs` 登记

## 8. 验收

- [ ] 8.1 范围内问题（示例问题 + 同义改写）正确率 ≥ 90%，全部带引用
- [ ] 8.2 范围外问题 100% 明确拒答，抽查无编造
- [ ] 8.3 乱问压测：30 个随机/刁钻问题，崩溃与超时 0 次
- [ ] 8.4 移动端与微信内置浏览器实测 SSE 可用（或给出可读降级提示）
- [ ] 8.5 关停演练：demo 域名切 503 再恢复，正式站全程不受影响
- [ ] 8.6 `POSTGRES_HOST=localhost pytest tests/ -v`、`ruff check .`、`pyright src/` 全绿；文档闸门无新增 error
