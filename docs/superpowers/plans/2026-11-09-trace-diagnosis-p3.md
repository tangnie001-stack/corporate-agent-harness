# trace-diagnosis P3（日志可采集化 · 4xx 分类 · 告警定位器）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把诊断链路推到两个入口都可用的形态——应用机 nginx 的访问日志可采集（含 trace 关联字段），4xx 错误能给出分类与归属，告警侧能按时间窗定位代表 trace 并自动产出诊断卡片。

**Architecture:** 三段互相独立、可分批交付：① nginx 日志字段与落盘（部署面）；② `AccessLogSource` 读访问日志，给 4xx/5xx 补状态码证据（判定面）；③ `locator` 扫应用 ERROR 日志、按（错误类型, 接口, 错误码）聚类取代表，接进 `alert_service` 的 error 类告警分支（编排面）。判定与文案仍全部在代码里，模型不产出数字。

**Tech Stack:** Python 3.11+ / pytest / FastAPI / SQLAlchemy / nginx / Docker Compose / 企微 markdown

**Spec:** `docs/openspec/changes/trace-diagnosis/design.md`（D4 / D9 / D13）、`specs/trace-diagnosis/spec.md`

## Global Constraints

- 测试命令一律 `POSTGRES_HOST=localhost pytest ...`（宿主侧约定，见 CLAUDE.md「验证」）。
- 判定只用硬证据；证据不足一律 `undetermined`，不得推测。
- 模型不产出报告数字：卡片文本全部由代码拼装。
- 只读：诊断链路不得写业务数据（仅写诊断报告表）。
- 新配置/文案入 `src/config/`（`settings.py` / `diag.py` / `alert.py`），阈值标注"未标定"。
- 日志事件与文案遵循 `docs/agents/logging-rules.md`：事件名英文、`[diagnose]` 前缀。
- 单文件 400 行红线：`src/services/alert_service.py` 已 607 行，新增编排不得再往里堆，放 `src/services/alert_diagnosis.py`。
- 生产路径 `/data/nginx-logs` 是**宿主机绝对路径**（与 `app_logs` 命名卷不同），compose 里两个容器都要挂。
- 2.5 的采集器安装、机器组、SLS 配置、日志轮转属运维动作，只写 runbook，不进代码仓。

---

### Task 1: 补 spec delta（问题分型 + 告警定位器）

**Files:**
- Modify: `docs/openspec/changes/trace-diagnosis/specs/trace-diagnosis/spec.md`（追加在文件末尾）

**Interfaces:**
- Consumes: 无
- Produces: 两条新 Requirement，供 Task 4 / Task 5 的验收引用

**背景：** 现有 spec 只覆盖"检索事实 / 根因枚举 / 断言引用 / 只读"四条，6.x 与 4xx 分类没有 SHALL —— 归档时会出现"实现有、规格无"。本任务先补规格。

- [ ] **Step 1: 追加两条 Requirement**

在 `spec.md` 末尾追加：

```markdown
### Requirement: 问题分型决定判定分支

系统 SHALL 由入口给出 `kind ∈ answer | 5xx | 4xx`，并据此分派判定；错误类 MUST NOT 进入检索侧根因判定（检索事实与错误条目无因果关系）。

4xx 的分类 SHALL 只依据可获得的硬证据（nginx access 日志的 `status`）；读不到 access 日志时 MUST 输出"分类待日志"，不得按错误文案猜测业务原因。

#### Scenario: 4xx 有状态码即给分类与归属
- **WHEN** 入口分型为 `4xx` 且 access 日志中存在该 trace 的记录
- **THEN** 报告给出状态码对应的分类（参数 / 鉴权 / 限流 / 资源不存在 / 请求体超限）与归属 `ops`，根因仍为"未判定"

#### Scenario: 4xx 无 access 日志不猜
- **WHEN** 入口分型为 `4xx` 但 access 日志不可用或没有该 trace 的记录
- **THEN** 报告输出"分类待 access 日志"，不得给出具体分类

### Requirement: 告警定位器产出代表 trace

系统 SHALL 在告警时间窗内从应用 ERROR 日志定位带 `trace_id` 的错误行，按（错误类型, 接口, 错误码）聚类，每类只取**最早出现**的一个代表 trace；单次告警最多诊断 3 个代表，其余只输出类别计数。

三种数据形态 MUST 显式区分，不得合并：查询成功但 0 行 → "未落到应用层"；有行但无 `trace_id` → "无请求上下文"；日志源不可达 / 超时 / 参数错误 → "日志源不可用，未判定"。

#### Scenario: 空窗口是结论而不是缺证据
- **WHEN** 窗口内查询成功且没有 ERROR 行
- **THEN** 结论为"未落到应用层"，档位按 `建议观察`，不得输出"证据不足"

#### Scenario: 日志源失败不得落进空窗口结论
- **WHEN** 日志源抛出 `SourceFailedError`
- **THEN** 结论为"日志源不可用，未判定"，不得输出"未落到应用层"

#### Scenario: 代表数与上限
- **WHEN** 窗口内有 5 类错误
- **THEN** 只诊断最早的 3 类，卡片给出其余 2 类的类别与计数
```

- [ ] **Step 2: 校验**

Run: `openspec validate trace-diagnosis --strict`
Expected: `Change 'trace-diagnosis' is valid`

- [ ] **Step 3: 提交**

```bash
git add docs/openspec/changes/trace-diagnosis/specs/trace-diagnosis/spec.md
git commit -m "docs(spec): 补问题分型与告警定位器的 SHALL"
```

---

### Task 2: nginx 访问日志可采集化（2.5）

**Files:**
- Modify: `deploy/nginx/nginx.conf`（`log_format masked` + `access_log` 位置）
- Modify: `docker-compose.image.yml`（nginx 挂载 + app 只读挂载 + `DIAG_ACCESS_LOG_DIR`）
- Modify: `docker-compose.yml`（dev 同步，便于本地验证）
- Modify: `docs/agents/deploy-runbook.md`（采集与轮转的运维动作）

**Interfaces:**
- Consumes: 应用已在所有响应写 `X-Trace-ID`（`src/middleware/trace_id.py:43`）
- Produces: 每行访问日志形如
  `"GET /api/x HTTP/1.1" 200 123 "-" "ua" upstream=172.18.0.2:8000 upstream_status=200 trace=trace_... rt=0.012`
  —— Task 3 的解析器按这个字段顺序读

**注意：** 变量化 upstream + `resolver 127.0.0.11` 只在 compose 网络里能解析 `app`；本地验证必须在 dev compose 栈上做，不要用裸 `docker run`。

本地只重建 nginx 即可验证三点；app 容器的 `/data/nginx-logs` 只读挂载与 `DIAG_ACCESS_LOG_DIR` 要 `docker compose up -d app` 才生效（会重建 app 容器），本地可跳过——Task 3 的单测直接对宿主 `data/nginx-logs/access.log` 跑，生产由 compose 保证。

- [ ] **Step 1: 扩展 log_format**

把 `deploy/nginx/nginx.conf` 中的：

```nginx
log_format masked '$remote_addr - $remote_user [$time_local] '
                  '"$request_method $masked_uri $server_protocol" '
                  '$status $body_bytes_sent "$http_referer" "$http_user_agent"';
```

改为（字段顺序固定，解析器依赖）：

```nginx
# 末尾四个字段供诊断读取（change: trace-diagnosis 2.5）：
#   upstream / upstream_status：上游地址与状态码；**上游无响应时为空**（502/504 的判据）
#   trace：上游返回的 X-Trace-ID，用于把访问日志与诊断 trace 对齐
#   rt：请求总耗时（秒，三位小数）
# 注意：proxy_next_upstream 触发多次尝试时 upstream/upstream_status 会是逗号分隔列表。
log_format masked '$remote_addr - $remote_user [$time_local] '
                  '"$request_method $masked_uri $server_protocol" '
                  '$status $body_bytes_sent "$http_referer" "$http_user_agent" '
                  'upstream=$upstream_addr upstream_status=$upstream_status '
                  'trace=$upstream_http_x_trace_id rt=$request_time';
```

- [ ] **Step 2: access_log 上移到 http 级并双写**

删除 4 处 server 块里的 `access_log /dev/stdout masked;`（默认块 / www / api / langfuse），在 `log_format` 之后统一写一次：

```nginx
# 双写：/dev/stdout 保持 docker logs 习惯；文件供诊断与外部采集读取
access_log /dev/stdout masked;
access_log /var/log/nginx/access.log masked;
```

server 级不再声明 `access_log`，否则会覆盖 http 级、丢掉文件那一路。

- [ ] **Step 3: 容器挂载（生产 compose）**

`docker-compose.image.yml` 的 nginx 服务：

```yaml
    volumes:
      - ./deploy/nginx/nginx.conf:/etc/nginx/conf.d/default.conf
      - ./deploy/nginx/html:/usr/share/nginx/html
      # 访问日志落宿主机，供 app 侧诊断读取与外部采集（见 deploy-runbook）
      - /data/nginx-logs:/var/log/nginx
```

app 服务（只读，供 `AccessLogSource` 读）：

```yaml
    volumes:
      - app_logs:/data/logs
      - ./data/deepeval:/app/data/deepeval
      - /data/nginx-logs:/data/nginx-logs:ro
```

app 的 `environment` 增加：

```yaml
      # 访问日志目录；空串=不启用（本地 uvicorn 默认关闭）
      DIAG_ACCESS_LOG_DIR: /data/nginx-logs
```

- [ ] **Step 4: dev compose 同步**

`docker-compose.yml` 的 nginx 服务增加：

```yaml
    volumes:
      - ./data/nginx-logs:/var/log/nginx
```

（dev 的 conf 烤在镜像里，改 conf 要 `docker compose build nginx`。）

dev app 服务增加同样的只读挂载与环境变量：

```yaml
    volumes:
      - app_logs:/data/logs
      - ./data/deepeval:/app/data/deepeval
      - ./data/nginx-logs:/data/nginx-logs:ro
    environment:
      DIAG_ACCESS_LOG_DIR: /data/nginx-logs
```

- [ ] **Step 5: 本地验证（三点，不动正在跑的栈）**

用**临时 nginx 容器**挂新 conf，加入 compose 网络（`app` 能被 `127.0.0.11` 解析），监听 18080；不重建、不停现有 app/nginx。

```bash
mkdir -p data/nginx-logs
docker run -d --rm --name diag-nginx-verify \
  --network corporate_rag_network -p 18080:80 \
  -v "$PWD/deploy/nginx/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$PWD/data/nginx-logs:/var/log/nginx" \
  nginx:alpine
sleep 1
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: api.tangnie.cn' http://127.0.0.1:18080/api/health
tail -1 data/nginx-logs/access.log
```

Expected（正常请求）：`200`；行尾有 `upstream=172.x.x.x:8000 upstream_status=200 trace=trace_<uuid> rt=0.0xx`，且 `trace=` 与响应头 `X-Trace-ID` 一致。

```bash
# 502：复制一份 conf，把上游换成解析不到的名字
sed 's/"app:8000"/"app-down:8000"/' deploy/nginx/nginx.conf > /tmp/nginx-502.conf
docker run -d --rm --name diag-nginx-verify-502 \
  --network corporate_rag_network -p 18081:80 \
  -v /tmp/nginx-502.conf:/etc/nginx/conf.d/default.conf:ro \
  -v "$PWD/data/nginx-logs:/var/log/nginx" \
  nginx:alpine
sleep 1
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: api.tangnie.cn' http://127.0.0.1:18081/api/health
docker logs --tail 2 diag-nginx-verify-502
tail -1 data/nginx-logs/access.log
docker rm -f diag-nginx-verify-502
```

Expected（502）：返回 `502`；日志里 `upstream=-`、`upstream_status=-`、`trace=-`（**nginx 把空变量写成 `-`**，不是空串），`status=502`。

脱敏不变（仍用 18080）：

```bash
curl -s -o /dev/null -H 'Host: api.tangnie.cn' http://127.0.0.1:18080/api/alerts/webhook/SHOULD-NOT-APPEAR
tail -1 data/nginx-logs/access.log
docker rm -f diag-nginx-verify
```

Expected：路径记录为 `/api/alerts/webhook/***`，原文不出现。

**注意：** 两个临时容器都把 `access.log` 写进同一个目录，验证后看最后一行即可；结束务必删掉，避免它持续接 18080/18081。

- [ ] **Step 6: runbook 记运维动作**

`docs/agents/deploy-runbook.md` 增加一小节（标题自拟，放在"日常更新"之后）：

- 采集器（Logtail/LoongCollector）安装、机器组、采集路径 `/data/nginx-logs/access.log`、保留期；
- 轮转：nginx 官方镜像不含 logrotate，**上线前必须配**宿主机 cron（`logrotate` 或 `crond` 按天切分，保留 7 天）；未配则文件无限增长；
- 换机时 `/data/nginx-logs` 需要存在（docker 会自动建，但权限须与 nginx worker 一致）。

- [ ] **Step 7: 提交**

```bash
git add deploy/nginx/nginx.conf docker-compose.image.yml docker-compose.yml docs/agents/deploy-runbook.md
git commit -m "feat(nginx): 访问日志补 upstream/trace/耗时并落盘（诊断采集）"
```

---

### Task 3: AccessLogSource（访问日志解析与 4xx 分类）

**Files:**
- Modify: `src/config/settings.py`（`DIAG_ACCESS_LOG_DIR`）
- Create: `src/rca/diagnose/access.py`
- Test: `tests/rca/test_access_source.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class AccessRow:
      ts: datetime
      method: str
      uri: str            # 已脱敏的请求行路径
      status: int
      upstream_status: str  # 空串 = 上游无响应
      request_time: float
      trace_id: str         # 空串 = 无上游响应头

  class AccessLogSource:
      def __init__(self, log_dir: str) -> None: ...
      def row_for_trace(self, trace_id: str, at: datetime) -> AccessRow | None: ...

  def classify_4xx(status: int) -> str: ...
  ```
- Consumes: Task 2 的日志字段顺序

**注意：** 访问日志按天不轮转（单文件 `access.log`），读取时按行尾 `trace=` 匹配；文件缺失返回 `None`（等同于"没有 access 证据"，不是 failed）。

**已知边界（写进 docstring）：** 502/504 时上游没有响应头，`trace=` 为空，因此**按 trace 永远取不到这类行**。D9 的"未落到应用层"由定位器判（应用日志里根本没有这些 trace），不靠本源；本源只服务"上游有响应"的记录。

- [ ] **Step 1: 写失败测试**

`tests/rca/test_access_source.py`：

```python
"""访问日志解析与 4xx 分类。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.rca.diagnose.access import AccessLogSource, classify_4xx

_LINE = (
    '10.0.0.1 - - [09/Nov/2026:09:40:13 +0800] "GET /api/kbs HTTP/1.1" 401 87 '
    '"-" "curl/8.0" upstream=172.18.0.2:8000 upstream_status=401 '
    "trace=trace_11111111-1111-1111-1111-111111111111 rt=0.012\n"
)
_LINE_502 = (
    '10.0.0.1 - - [09/Nov/2026:09:40:14 +0800] "GET /api/chat HTTP/1.1" 502 157 '
    '"-" "curl/8.0" upstream=- upstream_status=- trace=- rt=0.003\n'
)


def _write(tmp_path: Path, *lines: str) -> AccessLogSource:
    path = tmp_path / "access.log"
    path.write_text("".join(lines), encoding="utf-8")
    return AccessLogSource(str(tmp_path))


def test_row_for_trace_parses_fields(tmp_path):
    source = _write(tmp_path, _LINE)
    row = source.row_for_trace(
        "trace_11111111-1111-1111-1111-111111111111", datetime.now(UTC)
    )
    assert row is not None
    assert row.status == 401
    assert row.upstream_status == "401"
    assert row.uri == "/api/kbs"
    assert row.request_time == 0.012


def test_502_rows_have_no_trace_and_are_not_matched(tmp_path):
    # 502/504 时上游无响应头 ⇒ trace= 为空，按 trace 永远取不到这类行（D9 由定位器判）
    source = _write(tmp_path, _LINE_502)
    assert source.row_for_trace("", datetime.now(UTC)) is None
    assert source.row_for_trace("trace_dead", datetime.now(UTC)) is None


def test_row_for_trace_missing_file_returns_none(tmp_path):
    assert AccessLogSource(str(tmp_path / "nope")).row_for_trace("trace_x", datetime.now(UTC)) is None


def test_classify_4xx():
    assert classify_4xx(400) == "参数错误"
    assert classify_4xx(422) == "参数错误"
    assert classify_4xx(401) == "鉴权失败"
    assert classify_4xx(403) == "鉴权失败"
    assert classify_4xx(404) == "资源不存在"
    assert classify_4xx(413) == "请求体超限"
    assert classify_4xx(429) == "限流"
    assert classify_4xx(418) == "其他 4xx"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/rca/test_access_source.py -q`
Expected: FAIL（`ModuleNotFoundError: src.rca.diagnose.access`）

- [ ] **Step 3: 实现 `src/rca/diagnose/access.py`**

```python
"""nginx 访问日志证据源：按 trace 取状态码与耗时。

日志行由 deploy/nginx/nginx.conf 的 masked 格式产生，末尾四个字段顺序固定：
    upstream=<addr> upstream_status=<code|空> trace=<X-Trace-ID|空> rt=<秒>
上游无响应（502/504 / 连接失败）时 upstream_status 与 trace 均为空，nginx 在
访问日志里写作 `-`，解析时归一为空串；这类行按 trace 取不到，属定位器
（应用日志无该 trace）的判定范围，不在本模块。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

# 只回看 trace 前后一小时；访问日志是单文件，不能无限扫
_LOOKBACK = timedelta(hours=1)
_LINE_RE = re.compile(
    r'"[A-Z]+ (?P<uri>\S+) HTTP/[\d.]+" (?P<status>\d{3}) \d+ "[^"]*" "[^"]*" '
    r"upstream=(?P<upstream>\S*) upstream_status=(?P<upstream_status>\S*) "
    r"trace=(?P<trace>\S*) rt=(?P<rt>[\d.]+)"
)
_TIME_RE = re.compile(r"\[(?P<ts>[^\]]+)]")


@dataclass(frozen=True)
class AccessRow:
    """一条访问日志事实（只保留诊断用得到的字段）。"""

    ts: datetime  # 请求时间（按日志里的本地时区解析）
    method: str  # HTTP 方法
    uri: str  # 已脱敏的请求路径（query 可能被掩）
    status: int  # 客户端看到的状态码
    upstream_status: str  # 上游状态码；空串 = 上游无响应
    request_time: float  # 请求总耗时（秒）
    trace_id: str  # 上游返回的 X-Trace-ID；空串 = 无响应头


class AccessLogSource:
    """读 nginx 访问日志；文件缺失等同"没有 access 证据"。"""

    def __init__(self, log_dir: str) -> None:
        """Args:
        log_dir: 访问日志目录（生产 /data/nginx-logs，测试注入临时目录）；空串表示未启用。
        """
        self._dir = Path(log_dir) if log_dir else None

    def row_for_trace(self, trace_id: str, at: datetime) -> AccessRow | None:
        """返回该 trace 在 at 附近最近的一条访问记录；取不到返回 None。"""
        if self._dir is None or not trace_id:
            return None
        path = self._dir / "access.log"
        if not path.is_file():
            return None
        best: AccessRow | None = None
        start = at - _LOOKBACK
        end = at + _LOOKBACK
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                match = _LINE_RE.search(line)
                if match is None or _norm(match.group("trace")) != trace_id:
                    continue
                ts = _parse_time(line)
                if ts is None or ts < start or ts > end:
                    continue
                row = AccessRow(
                    ts=ts,
                    method=line.split('"', 2)[1].split(" ", 1)[0],
                    uri=match.group("uri"),
                    status=int(match.group("status")),
                    upstream_status=_norm(match.group("upstream_status")),
                    request_time=float(match.group("rt")),
                    trace_id=_norm(match.group("trace")),
                )
                if best is None or abs(row.ts - at) < abs(best.ts - at):
                    best = row
        return best


def classify_4xx(status: int) -> str:
    """把 4xx 状态码映射成报告用的分类文案。"""
    if status in (400, 422):
        return "参数错误"
    if status in (401, 403):
        return "鉴权失败"
    if status == 404:
        return "资源不存在"
    if status == 413:
        return "请求体超限"
    if status == 429:
        return "限流"
    return "其他 4xx"


def _parse_time(line: str) -> datetime | None:
    """解析日志里的 [09/Nov/2026:09:40:13 +0800]；失败返回 None。"""
    match = _TIME_RE.search(line)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group("ts"), "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None
```

同时在 `src/config/settings.py` 的 DIAG 段（`DIAG_DETAIL_BASE_URL` 之后）加：

```python
# nginx 访问日志目录（生产由 compose 注入 /data/nginx-logs）；空串 = 不启用 4xx 分类
DIAG_ACCESS_LOG_DIR: str = os.getenv("DIAG_ACCESS_LOG_DIR", "")
```

- [ ] **Step 4: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/rca/test_access_source.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/rca/diagnose/access.py src/config/settings.py tests/rca/test_access_source.py
git commit -m "feat(diag): 访问日志源（状态码 / 上游状态 / 耗时）与 4xx 分类"
```

---

### Task 4: 错误类接入 access 证据（3.6 收尾）

**Files:**
- Modify: `src/rca/diagnose/judge.py`（`judge_error` 增加 access 参数）
- Modify: `src/rca/diagnose/pipeline.py`（`access_source` 入参、`A1` 证据、错误类断言）
- Modify: `src/agents/tools/diagnose_tools.py`（按配置构造 `AccessLogSource`）
- Test: `tests/rca/test_diagnose_judge.py`、`tests/rca/test_diagnose_pipeline.py`、`tests/agents/tools/test_diagnose_tool.py`

**Interfaces:**
- Consumes: `AccessRow` / `classify_4xx`（Task 3）
- Produces:
  ```python
  def judge_error(kind: DiagnosisKind, *, access: AccessRow | None = None) -> tuple[RootCause, str]
  def diagnose(..., access_source: object | None = None) -> DiagnosisReport
  ```
  证据 id `A1`，kind=`access`，summary 形如 `GET /api/kbs status=401 upstream=401 rt=0.012s`

- [ ] **Step 1: 写失败测试（judge）**

在 `tests/rca/test_diagnose_judge.py` 追加：

```python
def test_4xx_with_access_status_gives_category():
    from datetime import UTC, datetime

    from src.rca.diagnose.access import AccessRow

    row = AccessRow(
        ts=datetime.now(UTC),
        method="GET",
        uri="/api/kbs",
        status=401,
        upstream_status="401",
        request_time=0.01,
        trace_id="trace_x",
    )
    cause, reason = judge_error(DiagnosisKind.ERROR_4XX, access=row)
    assert cause is RootCause.UNDETERMINED
    assert "401" in reason
    assert "鉴权失败" in reason
    assert "ops" in reason


def test_4xx_without_access_keeps_placeholder():
    cause, reason = judge_error(DiagnosisKind.ERROR_4XX)
    assert cause is RootCause.UNDETERMINED
    assert "access" in reason
```

（若文件内未 import `judge_error` / `DiagnosisKind` / `RootCause`，按现有 import 风格补齐。）

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/rca/test_diagnose_judge.py -q`
Expected: FAIL（`judge_error() got an unexpected keyword argument 'access'`）

- [ ] **Step 3: 实现 judge 变更**

```python
def judge_error(
    kind: DiagnosisKind, *, access: AccessRow | None = None
) -> tuple[RootCause, str]:
    """错误类判定：检索事实与错误条目无因果关系，只给归属与分类。

    Args:
        kind: 5xx / 4xx
        access: 访问日志事实；None 表示读不到（不猜分类）

    Returns:
        恒为未判定；原因文案说明分类、归属与缺失的证据项
    """
    if kind is DiagnosisKind.ERROR_4XX:
        if access is not None:
            label = classify_4xx(access.status)
            return (
                RootCause.UNDETERMINED,
                f"客户端侧 {access.status}（{label}），归属 ops；未读请求体，不推断业务原因",
            )
        return (
            RootCause.UNDETERMINED,
            "4xx 属客户端侧，归属 ops 受理；分类待 access 日志状态码",
        )
    if access is not None:
        return (
            RootCause.UNDETERMINED,
            f"服务端 {access.status}（上游 {access.upstream_status}），归属 dev 排查",
        )
    return (
        RootCause.UNDETERMINED,
        "5xx 服务端错误，归属 dev 排查；具体原因待错误栈证据",
    )
```

文件顶部 import：

```python
from src.rca.diagnose.access import AccessRow, classify_4xx
```

`judge()` 的分派改为把 access 透传：

```python
def judge(..., access: AccessRow | None = None) -> tuple[RootCause, str]:
    if kind is not DiagnosisKind.ANSWER:
        return judge_error(kind, access=access)
```

- [ ] **Step 4: 实现 pipeline 变更**

`diagnose()` 签名增加 `access_source: object | None = None`；在取完日志之后：

```python
    access_row = None
    if kind is not DiagnosisKind.ANSWER and access_source is not None:
        try:
            access_row = access_source.row_for_trace(  # type: ignore[attr-defined]
                trace_id, now or datetime.now(UTC)
            )
        except SourceFailedError as exc:
            logger.warning(
                "[diagnose] access source failed trace_id={} err={}", trace_id, exc
            )
            access_row = None
    if access_row is not None:
        evidence = evidence + [_access_evidence(access_row)]
```

`judge(...)` 调用处补 `access=access_row`；`_assertions(...)` 调用处补 `access=access_row`。

新增：

```python
def _access_evidence(row: object) -> Evidence:
    """把访问日志事实转成证据（不含 query 原文）。"""
    status = getattr(row, "status", 0)
    upstream = getattr(row, "upstream_status", "") or "N/A"
    method = getattr(row, "method", "")
    uri = getattr(row, "uri", "")
    rt = getattr(row, "request_time", 0.0)
    ts = getattr(row, "ts", None)
    locator = ""
    if isinstance(ts, datetime):
        locator = f"access:{ts.strftime('%Y-%m-%d %H:%M:%S')}"
    return Evidence(
        id="A1",
        kind="access",
        locator=locator,
        summary=f"{method} {uri} status={status} upstream={upstream} rt={rt:.3f}s",
    )
```

`_assertions` 增加参数 `access: object | None = None`；把答案分支之后的**整段**替换为：

```python
    first_event = "unknown"
    if rows:
        first_event = getattr(rows[0], "event", "") or "unknown"
    access_ids = tuple(e.id for e in evidence if e.kind == "access")
    log_ids = tuple(e.id for e in evidence if e.kind == "log")
    out: list[tuple[str, tuple[str, ...]]] = []
    if access_ids:
        out.append((reason, access_ids))
    if log_ids:
        out.append((f"窗口内 ERROR {len(rows)} 条，首条事件 {first_event}", log_ids))
    return tuple(out)
```

（原来的 `if not log_ids: return ()` 必须删掉：只有 access 证据时也要有一条引用 A1 的断言。）

- [ ] **Step 5: 工具接线**

`src/agents/tools/diagnose_tools.py`：`_sources()` 返回三元组保持不变，另加：

```python
def _access_source() -> Any:
    """按配置构造访问日志源；目录为空时返回 None（不读，也不猜分类）。"""
    if not settings.DIAG_ACCESS_LOG_DIR.strip():
        return None
    return AccessLogSource(settings.DIAG_ACCESS_LOG_DIR)
```

`diagnose(...)` 两次调用都传 `access_source=_access_source()`（预取失败重跑那次同样传）。

- [ ] **Step 6: 补测试（pipeline / 工具）**

`tests/rca/test_diagnose_pipeline.py` 追加：

```python
def test_4xx_pipeline_adds_access_evidence():
    from datetime import UTC, datetime

    from src.rca.diagnose.access import AccessRow
    from src.rca.diagnose.models import DiagnosisKind, RootCause

    class _Access:
        def row_for_trace(self, trace_id, at):
            return AccessRow(
                ts=datetime.now(UTC),
                method="GET",
                uri="/api/kbs",
                status=429,
                upstream_status="429",
                request_time=0.02,
                trace_id=trace_id,
            )

    report = diagnose(
        "trace_11111111-1111-1111-1111-111111111111",
        kind=DiagnosisKind.ERROR_4XX,
        access_source=_Access(),
        now=datetime.now(UTC),
    )
    assert report.root_cause is RootCause.UNDETERMINED
    assert any(e.id == "A1" for e in report.evidence)
    assert "限流" in report.summary
    assert report.assertions  # 至少一条引用 A1
```

`tests/agents/tools/test_diagnose_tool.py` 追加一条：注入 `DIAG_ACCESS_LOG_DIR=""` 时工具不读访问日志（用 monkeypatch 改 settings 后断言 `_access_source() is None`）。

- [ ] **Step 7: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/rca tests/agents/tools/test_diagnose_tool.py -q`
Expected: 全绿

- [ ] **Step 8: 提交**

```bash
git add src/rca/diagnose/judge.py src/rca/diagnose/pipeline.py src/agents/tools/diagnose_tools.py tests/rca tests/agents/tools/test_diagnose_tool.py
git commit -m "feat(diag): 错误类补访问日志证据（4xx 分类、5xx 上游状态）"
```

---

### Task 5: 告警定位器（6.1 / 6.2 / 6.3）

**Files:**
- Create: `src/rca/locator.py`
- Test: `tests/rca/test_locator.py`（对应 8.2）

**Interfaces:**
- Consumes: `LogRow`（`src/rca/diagnose/logs.py`）、`AccessLogSource.row_for_trace`（Task 3）
- Produces:
  ```python
  @dataclass(frozen=True)
  class ErrorGroup:
      key: tuple[str, str, str]   # (错误类型, 接口, 错误码)
      trace_id: str               # 代表（该类最早的 trace）
      count: int                  # 该类涉及的去重 trace 数
      first_ts: datetime
      summary: str                # 事件名 + 错误码（不含原文）

  @dataclass(frozen=True)
  class LocateResult:
      status: str                          # ok / empty / failed
      groups: tuple[ErrorGroup, ...]       # 前 limit 类（诊断用）
      extra: tuple[ErrorGroup, ...]        # 超限的类别（只计数）
      traces_total: int                    # 窗口内去重后的 trace 数
      reason: str                          # empty/failed 的结论文案

  def locate(*, log_source, start: datetime, end: datetime,
             access_source=None, limit: int = 3) -> LocateResult
  ```

**聚类键的事实依据（已核对真实日志）**：应用 ERROR 行只有 `模块:函数:行` 与事件名，**没有 URL**；URL 只能从 access 日志取（按 trace 对齐）。因此接口维度优先取 `access.uri`，取不到退回模块名（`src.api.chat`），并在实现里注明这一偏差。

- [ ] **Step 1: 写失败测试**

`tests/rca/test_locator.py`：

```python
"""告警定位器：聚类、取代表、上限与三态。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.rca.diagnose.logs import LogRow
from src.rca.locator import locate
from src.rca.sources.base import SourceFailedError

_NOW = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
_T = "src.services.turn_runner:_launch_and_register:524"


def _row(trace_id: str, message: str = "Chat stream setup failed: boom", **kw) -> LogRow:
    ts = kw.pop("ts", _NOW - timedelta(minutes=5))
    return LogRow(
        ts=ts,
        level="ERROR",
        trace_id=trace_id,
        event=kw.pop("event", "unclassified"),
        raw=f"2026-11-09 09:55:00.000 | ERROR | {trace_id} |  | none | {_T} - {message}",
    )


class _Logs:
    def __init__(self, rows, exc=None):
        self._rows = rows
        self._exc = exc

    def errors_between(self, start, end):
        if self._exc is not None:
            raise self._exc
        return list(self._rows)


def _ctx(logs, access=None, limit=3):
    return locate(
        log_source=logs,
        start=_NOW - timedelta(minutes=15),
        end=_NOW,
        access_source=access,
        limit=limit,
    )


def test_empty_window_is_conclusion():
    result = _ctx(_Logs([]))
    assert result.status == "empty"
    assert "未落到应用层" in result.reason


def test_failed_source_is_not_empty():
    result = _ctx(_Logs([], exc=SourceFailedError("timeout")))
    assert result.status == "failed"
    assert "未判定" in result.reason


def test_rows_without_trace_id():
    result = _ctx(_Logs([_row("")]))
    assert result.status == "empty"
    assert "无请求上下文" in result.reason


def test_clusters_and_caps():
    logs = _Logs(
        [
            _row("trace_a", "code=SESSION_NOT_FOUND", event="biz error"),
            _row("trace_b", "code=SESSION_NOT_FOUND", event="biz error"),
            _row(
                "trace_c",
                "code=DOC_NOT_FOUND",
                event="biz error",
                ts=_NOW - timedelta(minutes=4),
            ),
            _row("trace_d", "err=ack timeout", ts=_NOW - timedelta(minutes=3)),
            _row("trace_e", "err=ack timeout", ts=_NOW - timedelta(minutes=2)),
        ]
    )
    result = _ctx(logs, limit=1)
    assert result.status == "ok"
    assert result.traces_total == 5
    assert len(result.groups) == 1
    assert result.groups[0].count == 2  # 同类两条 trace
    assert len(result.extra) == 2  # 其余两类只计数
    assert result.groups[0].trace_id == "trace_a"  # 同类取最早


def test_interface_prefers_access_uri():
    class _Access:
        def row_for_trace(self, trace_id, at):
            class _Row:
                uri = "/api/chat"

            return _Row()

    result = _ctx(_Logs([_row("trace_a")]), access=_Access())
    assert result.groups[0].key[1] == "/api/chat"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost pytest tests/rca/test_locator.py -q`
Expected: FAIL（`ModuleNotFoundError: src.rca.locator`）

- [ ] **Step 3: 实现 `src/rca/locator.py`**

```python
"""告警定位器：窗口内 ERROR 行 → 去重 trace → 聚类 → 每类取最早代表。

只读应用日志；access 日志仅用于补"接口"维度（取不到退回模块名）。
三态语义见 design D9：empty 的两种形态本身就是结论，failed 不得并入 empty。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.rca.sources.base import SourceFailedError

_CODE_RE = re.compile(r"(?:code|status)=([A-Za-z0-9_]+)")


@dataclass(frozen=True)
class ErrorGroup:
    """一类错误（按 类型/接口/错误码 聚合）。"""

    key: tuple[str, str, str]  # (错误类型, 接口, 错误码)
    trace_id: str  # 代表 trace（该类最早）
    count: int  # 该类涉及的去重 trace 数
    first_ts: datetime  # 该类最早出现的时刻
    summary: str  # 事件名 + 错误码（不含原文）


@dataclass(frozen=True)
class LocateResult:
    """一次定位的产物；status 决定调用方走哪条分支。"""

    status: str  # ok / empty / failed
    groups: tuple[ErrorGroup, ...]  # 前 limit 类
    extra: tuple[ErrorGroup, ...]  # 超限类别（只计数）
    traces_total: int  # 窗口内去重 trace 数
    reason: str  # empty/failed 的结论文案；ok 为空串


def locate(
    *,
    log_source: Any,
    start: datetime,
    end: datetime,
    access_source: Any = None,
    limit: int = 3,
) -> LocateResult:
    """在窗口内定位错误 trace 并聚类。

    Args:
        log_source: 具备 errors_between(start, end) 的日志源
        start: 窗口起点
        end: 窗口终点
        access_source: 具备 row_for_trace(trace_id, at) 的访问日志源；None 表示不补接口
        limit: 最多返回的类别数（其余进 extra）

    Returns:
        LocateResult；不抛异常
    """
    try:
        rows = log_source.errors_between(start, end)
    except SourceFailedError as exc:
        return LocateResult("failed", (), (), 0, f"日志源不可用，未判定：{exc}")
    except Exception as exc:  # noqa: BLE001  任何取数异常都按不可用处理
        return LocateResult("failed", (), (), 0, f"日志源不可用，未判定：{exc}")

    if not rows:
        return LocateResult(
            "empty",
            (),
            (),
            0,
            "窗口内无 ERROR 记录：错误未落到应用层（可能来自网关 / 容器被 OOM / 与本应用无关）",
        )

    first_by_trace: dict[str, Any] = {}
    for row in rows:  # rows 已按时间升序
        trace_id = getattr(row, "trace_id", "")
        if trace_id and trace_id not in first_by_trace:
            first_by_trace[trace_id] = row
    if not first_by_trace:
        return LocateResult(
            "empty",
            (),
            (),
            0,
            f"窗口内有 {len(rows)} 条 ERROR 但没有 trace_id：无请求上下文，未判定",
        )

    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trace_id, row in first_by_trace.items():
        message = _message(getattr(row, "raw", ""))
        event = getattr(row, "event", "") or "unclassified"
        interface = _interface(trace_id, getattr(row, "raw", ""), access_source)
        code = _code(message)
        key = (event, interface, code)
        bucket = grouped.setdefault(
            key,
            {"trace_id": trace_id, "first_ts": row.ts, "traces": [], "summary": f"{event} {code}"},
        )
        bucket["traces"].append(trace_id)
        if row.ts < bucket["first_ts"]:
            bucket["first_ts"] = row.ts
            bucket["trace_id"] = trace_id

    ordered = sorted(grouped.items(), key=lambda item: item[1]["first_ts"])
    groups = tuple(
        ErrorGroup(
            key=key,
            trace_id=data["trace_id"],
            count=len(data["traces"]),
            first_ts=data["first_ts"],
            summary=data["summary"].strip(),
        )
        for key, data in ordered[:limit]
    )
    extra = tuple(
        ErrorGroup(
            key=key,
            trace_id=data["trace_id"],
            count=len(data["traces"]),
            first_ts=data["first_ts"],
            summary=data["summary"].strip(),
        )
        for key, data in ordered[limit:]
    )
    return LocateResult("ok", groups, extra, len(first_by_trace), "")


def _split_raw(raw: str) -> tuple[str, str]:
    """把日志行拆成 (模块位置, 消息)。"""
    parts = raw.split(" | ", 5)
    if len(parts) < 6:
        return "", ""
    tail = parts[5]
    if " - " not in tail:
        return tail.strip(), ""
    head, message = tail.split(" - ", 1)
    return head.strip(), message.strip()


def _message(raw: str) -> str:
    """取消息体（用于提取错误码）。"""
    return _split_raw(raw)[1]


def _interface(trace_id: str, raw: str, access_source: Any) -> str:
    """接口维度：优先 access 日志的 uri，取不到退回模块名。"""
    if access_source is not None:
        try:
            row = access_source.row_for_trace(trace_id, _split_time(raw))
        except Exception:  # noqa: BLE001  接口维度是补强，失败退回模块名
            row = None
        if row is not None and getattr(row, "uri", ""):
            return str(row.uri)
    location = _split_raw(raw)[0]
    return location.split(":")[0] if location else ""


def _split_time(raw: str) -> datetime:
    """从日志行解析时间；失败返回当前时刻（只影响 access 的匹配窗口）。"""
    from datetime import UTC

    head = raw.split(" | ", 1)[0].strip()
    try:
        return datetime.strptime(head, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def _code(message: str) -> str:
    """从消息里取 code=/status= 的值；没有返回 '-'。"""
    match = _CODE_RE.search(message)
    return match.group(1) if match else "-"
```

- [ ] **Step 4: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/rca/test_locator.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/rca/locator.py tests/rca/test_locator.py
git commit -m "feat(diag): 告警定位器（聚类取代表、上限 3、三态分离）"
```

---

### Task 6: 登记定位事件（1.5 收尾）

**Files:**
- Modify: `src/core/log_events.py`（`TRACE_LOCATE_DONE`）
- Modify: `src/core/log_event_specs.py`（字段表）
- Modify: `docs/agents/logging-rules.md`（`[diagnose]` 前缀下的新事件）

**Interfaces:**
- Produces: `Event.TRACE_LOCATE_DONE = "trace locate done"`，字段 `traces / groups / status`

- [ ] **Step 1: 事件登记**

`log_events.py`（`DIAGNOSE_UNDETERMINED` 之后）：

```python
    TRACE_LOCATE_DONE = "trace locate done"
```

`log_event_specs.py`（`diagnose undetermined` 条目之后）：

```python
    "trace locate done": EventSpec(
        "trace locate done",
        "diagnose",
        "info",
        ("traces", "groups", "status"),
    ),
```

（字段顺序照该文件既有条目的写法调整；第三个参数是级别，必填。）

- [ ] **Step 2: 文档登记**

`docs/agents/logging-rules.md` 的 `[diagnose]` 小节增加一行：`trace locate done`（字段 `traces` 去重 trace 数、`groups` 类别数、`status` ok/empty/failed）。

- [ ] **Step 3: 跑闸门**

Run: `POSTGRES_HOST=localhost pytest tests/core -q && ruff check src/core`
Expected: PASS（事件登记有既有测试守护，见 `tests/core/test_log_event_specs.py`）

- [ ] **Step 4: 提交**

```bash
git add src/core/log_events.py src/core/log_event_specs.py docs/agents/logging-rules.md
git commit -m "feat(log): 登记 trace locate done 事件"
```

---

### Task 7: 告警接入（6.4）

**Files:**
- Modify: `src/config/alert.py`（`diagnose_alert_names` / `diagnose_window_minutes`）
- Modify: `src/config/diag.py`（告警卡片文案与三态文案）
- Modify: `src/rca/diagnose/render.py`（`render_alert_card`）
- Modify: `src/agents/tools/diagnose_tools.py`（抽出 `run_diagnosis`）
- Create: `src/services/alert_diagnosis.py`
- Modify: `src/services/alert_service.py`（分支接入）
- Test: `tests/rca/test_diagnose_render.py`、`tests/services/test_alert_diagnosis.py`

**Interfaces:**
- Consumes: `locate()`（Task 5）、`run_diagnosis()`（本任务抽出）
- Produces:
  ```python
  # diagnose_tools.py
  async def run_diagnosis(trace_id: str, kind: str = "answer", question: str = "") -> str

  # alert_diagnosis.py
  def should_diagnose_alert(alertname: str, cfg: AlertConfig) -> bool
  async def build_alert_card(event, *, window_minutes: int, limit: int) -> str
  ```

**灰度：** `DIAG_ALERT_NAMES` 逗号分隔的告警名白名单，**默认空 = 关闭**；命中才走定位+诊断分支，其余告警仍走 `rca-latency`。

- [ ] **Step 1: 配置**

`src/config/alert.py`：`AlertConfig` 末尾增加两个**带默认值**的字段（含行内注释，默认值保证既有测试的构造调用不破）：

```python
    diagnose_alert_names: frozenset[str] = frozenset()  # 走"定位+诊断"的告警名白名单；空集 = 关闭
    diagnose_window_minutes: int = 15  # 告警窗口回看的分钟数（首版未标定）
```

`load_alert_config()` 里：

```python
    names = frozenset(
        item.strip()
        for item in os.getenv("DIAG_ALERT_NAMES", "").split(",")
        if item.strip()
    )
```

并传入 `diagnose_alert_names=names`、`diagnose_window_minutes=_positive_int("DIAG_ALERT_WINDOW_MINUTES", 15)`。

`src/config/diag.py` 追加：

```python
ALERT_HEADER = "**告警诊断 · 窗口内 {traces} 个错误 trace，{groups} 类**"
ALERT_SAME = "> 同类 {count} 次"
ALERT_EXTRA = "> 另 {n} 类未诊断：{labels}"
ALERT_EMPTY = "窗口内没有 ERROR 记录：错误未落到应用层（可能来自网关 / 容器被杀 / 与本应用无关）"
ALERT_FAILED = "日志源不可用，未判定：{reason}"
```

- [ ] **Step 2: 抽出 run_diagnosis**

把 `diagnose_trace` 的函数体（参数校验之后的部分）原样移到：

```python
async def run_diagnosis(trace_id: str, kind: str = "answer", question: str = "") -> str:
    """执行一次诊断并落库，返回卡片文本；失败返回入口文案。

    与工具的区别只有入参校验位置：工具先校验格式，本函数假定入参已校验。
    """
```

`diagnose_trace` 只保留：

```python
    if not _TRACE_RE.match(trace_id.strip()):
        return INVALID_TRACE_ID
    parsed_kind = _KINDS.get(kind.strip().lower())
    if parsed_kind is None:
        return INVALID_KIND.format(kind=kind)
    return await run_diagnosis(trace_id, kind=kind, question=question)
```

（`run_diagnosis` 内部再取一次 `parsed_kind`，避免两处枚举映射不一致。）

- [ ] **Step 3: 写失败测试（渲染）**

`tests/rca/test_diagnose_render.py` 追加：

```python
def test_render_alert_card_lists_groups_and_extra():
    from src.rca.diagnose.render import render_alert_card

    text = render_alert_card(
        traces_total=22,
        cards=["**诊断 · 服务端错误**\n> 档位：需要处理 ｜ 归属：研发"],
        extra=[("biz error SESSION_NOT_FOUND", 3)],
    )
    assert "22 个错误 trace" in text
    assert "档位" in text
    assert "另 1 类" in text
```

- [ ] **Step 4: 实现 render_alert_card**

```python
def render_alert_card(
    *,
    traces_total: int,
    cards: list[str],
    extra: list[tuple[str, int]],
) -> str:
    """告警级卡片：代表 trace 的结论 + 超限类别计数（代码拼装）。

    Args:
        traces_total: 窗口内去重后的错误 trace 数
        cards: 每个代表 trace 的诊断卡片（已渲染，顺序与代表一致）
        extra: 未诊断类别 (摘要, 同类次数)

    Returns:
        企微 markdown；节点地址已脱敏
    """
    lines = [
        diag_texts.ALERT_HEADER.format(traces=traces_total, groups=len(cards) + len(extra))
    ]
    for index, card in enumerate(cards, 1):
        lines.append("---")
        lines.append(card)
    if extra:
        labels = "、".join(f"{label}×{count}" for label, count in extra)
        lines.append(diag_texts.ALERT_EXTRA.format(n=len(extra), labels=labels))
    return mask_nodes("\n".join(lines))
```

- [ ] **Step 5: 实现 alert_diagnosis.py**

```python
"""告警轮的"定位 + 诊断"编排（error 类告警）。

只做编排：定位、逐条诊断、拼卡片；推送由 alert_service 负责。
判定与文案仍全部在代码里，模型不参与。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.config import settings
from src.config.alert import AlertConfig
from src.config.diag import ALERT_EMPTY, ALERT_FAILED
from src.core.log_events import Event
from src.rca.diagnose.access import AccessLogSource
from src.rca.diagnose.logs import LocalLogSource
from src.rca.diagnose.models import DiagnosisKind
from src.rca.diagnose.render import render_alert_card
from src.rca.locator import LocateResult, locate

if TYPE_CHECKING:
    from src.services.alert_service import AlertEvent


def should_diagnose_alert(alertname: str, cfg: AlertConfig) -> bool:
    """灰度判断：告警名是否在"定位+诊断"白名单里。"""
    return alertname in cfg.diagnose_alert_names


async def build_alert_card(
    event: "AlertEvent", *, window_minutes: int, limit: int
) -> str:
    """定位窗口内的代表 trace 并逐条诊断，返回告警级卡片或结论文案。

    Args:
        event: 解析后的告警事件
        window_minutes: 回看分钟数
        limit: 最多诊断的类别数

    Returns:
        可直接推送的文本；任何失败都返回"未判定"类文案，不抛异常
    """
    now = datetime.now(UTC)
    result = locate(
        log_source=LocalLogSource(settings.DIAG_LOG_DIR),
        start=now - timedelta(minutes=window_minutes),
        end=now,
        access_source=_access_source(),
        limit=limit,
    )
    _log_located(result)
    if result.status == "empty":
        return ALERT_EMPTY
    if result.status == "failed":
        return ALERT_FAILED.format(reason=result.reason)

    from src.agents.tools.diagnose_tools import run_diagnosis

    cards: list[str] = []
    for group in result.groups:
        cards.append(
            await run_diagnosis(
                group.trace_id, kind=_kind_for(group).value, question=""
            )
        )
    extra = [(group.summary, group.count) for group in result.extra]
    return render_alert_card(
        traces_total=result.traces_total, cards=cards, extra=extra
    )


def _kind_for(group: Any) -> DiagnosisKind:
    """按错误码推断分型：数字 5xx → 5xx；数字 4xx → 4xx；其余按错误告警默认 5xx。"""
    code = group.key[2]
    if code.isdigit():
        status = int(code)
        if 400 <= status < 500:
            return DiagnosisKind.ERROR_4XX
    return DiagnosisKind.ERROR_5XX


def _access_source() -> AccessLogSource | None:
    """访问日志未配置时返回 None（接口维度退回模块名）。"""
    directory = settings.DIAG_ACCESS_LOG_DIR.strip()
    if not directory:
        return None
    return AccessLogSource(directory)


def _log_located(result: LocateResult) -> None:
    """记录定位结果（不抛异常，不阻断告警）。"""
    from src.core import logging as core_logging

    core_logging.log_event(
        Event.TRACE_LOCATE_DONE,
        traces=result.traces_total,
        groups=len(result.groups) + len(result.extra),
        status=result.status,
    )
```

- [ ] **Step 6: 接入 alert_service**

`_analyze_turn` 中 `query = build_query(event)` 之后：

```python
    if should_diagnose_alert(event.alertname, cfg):
        try:
            card = await build_alert_card(
                event,
                window_minutes=cfg.diagnose_window_minutes,
                limit=settings.DIAG_MAX_REPRESENTATIVE_TRACES,
            )
        except Exception:  # noqa: BLE001  诊断失败不得吃掉告警结论
            logger.exception("[alert] diagnosis failed alarm_id={}", encode_value(event.alarm_id))
            card = ALERT_FAILED.format(reason="内部错误")
        await _safe_send(target, card)
        return
```

import：`from src.config.diag import ALERT_FAILED`、`from src.services.alert_diagnosis import build_alert_card, should_diagnose_alert`、`from src.config import settings`（若未导入）。

**注意循环依赖**：`alert_diagnosis` 只在 `TYPE_CHECKING` 下 import `AlertEvent`，运行时用鸭子类型；`alert_service` 顶层 import `alert_diagnosis` 安全。

- [ ] **Step 7: 写服务测试**

`tests/services/test_alert_diagnosis.py`：

```python
"""告警诊断编排：白名单、三态与上限。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.config.alert import AlertConfig, AlertTarget
from src.services import alert_diagnosis


def _cfg(names: frozenset[str]) -> AlertConfig:
    return AlertConfig(
        token="t",
        target=AlertTarget(bot_key="dev", chatid="c1"),
        dedup_window_seconds=300,
        dedup_capacity=10,
        turn_timeout_seconds=10,
        fallback_max_chars=100,
        diagnose_alert_names=names,
        diagnose_window_minutes=15,
    )


def _event(name: str = "api-5xx") -> SimpleNamespace:
    return SimpleNamespace(
        alertname=name,
        alarm_id="A1",
        level="error",
        details="500",
        fingerprints=(),
        folder="",
        values=(),
    )


def test_gate_only_matches_whitelist():
    assert alert_diagnosis.should_diagnose_alert("api-5xx", _cfg(frozenset({"api-5xx"})))
    assert not alert_diagnosis.should_diagnose_alert("latency", _cfg(frozenset({"api-5xx"})))


@pytest.mark.asyncio
async def test_failed_source_returns_undetermined(monkeypatch):
    def _raise(**kwargs):
        from src.rca.locator import LocateResult

        return LocateResult("failed", (), (), 0, "日志源不可用")

    monkeypatch.setattr(alert_diagnosis, "locate", _raise)
    text = await alert_diagnosis.build_alert_card(
        _event(), window_minutes=15, limit=3
    )
    assert "未判定" in text
```

（`pytest.mark.asyncio` 的用法照 `tests/services/test_alert_orchestration.py`。）

- [ ] **Step 8: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/services tests/rca -q`
Expected: 全绿

- [ ] **Step 9: 提交**

```bash
git add src/config/alert.py src/config/diag.py src/rca/diagnose/render.py src/agents/tools/diagnose_tools.py src/services/alert_diagnosis.py src/services/alert_service.py tests/rca/test_diagnose_render.py tests/services/test_alert_diagnosis.py
git commit -m "feat(alert): error 类告警走定位+诊断（白名单灰度，默认关闭）"
```

---

### Task 8: 文档与任务回填

**Files:**
- Modify: `docs/openspec/changes/trace-diagnosis/tasks.md`（2.5 / 3.6 / 6.1-6.4 / 8.2 勾选与偏差）
- Modify: `docs/openspec/changes/trace-diagnosis/design.md`（聚类键的接口维度偏差）
- Modify: `docs/agents/code-map.md`（`src/rca/locator.py`、`diagnose/access.py`、`services/alert_diagnosis.py`）
- Modify: `docs/agents/troubleshooting.md`（告警诊断分支与"未落到应用层"）
- Modify: `docs/agents/glossary.md`（代表 trace / 同类计数，如缺）

- [ ] **Step 1: tasks.md 回填**

勾选 2.5 / 3.6（4xx 细分已落）/ 6.1 / 6.2 / 6.3 / 6.4 / 8.2，并在 2.5 条目补一句"app 容器只读挂载 /data/nginx-logs；轮转属运维，见 runbook"。

- [ ] **Step 2: design 补偏差**

D4 的聚类键说明后补一行：

> 实现偏差：应用日志没有 URL，接口维度优先取 access 日志的 `uri`（按 trace 对齐），取不到退回模块名（`src.api.chat`）。

- [ ] **Step 3: code-map / troubleshooting / glossary**

- code-map：在 RAG/告警落点表补一行"告警定位 / 4xx 分类 → `src/rca/locator.py`、`src/rca/diagnose/access.py`、`src/services/alert_diagnosis.py`"；
- troubleshooting：症状表补"告警只回一张'未落到应用层'"→ 查窗口内 ERROR 行与 nginx 502；
- glossary：补"代表 trace"（每类最早的一条，用于聚类展示）与"同类计数"。

- [ ] **Step 4: 跑文档闸门**

Run: `python src/cli/check_docs.py`（若入口不同，用 pre-commit 的 `check-docs` hook）
Expected: 通过

- [ ] **Step 5: 提交**

```bash
git add docs/agents docs/openspec/changes/trace-diagnosis
git commit -m "docs(diag): 登记定位器落点、偏差与排障入口"
```

---

### Task 9: 窄协议（2.4）

**Files:**
- Create: `src/rca/diagnose/protocols.py`
- Modify: `src/rca/diagnose/pipeline.py`（参数类型改为 Protocol）
- Modify: `src/agents/tools/diagnose_tools.py`

**Interfaces:**
- Produces:
  ```python
  class TraceFetcher(Protocol):
      def fetch(self, trace_id: str) -> TraceFacts: ...

  class ErrorLogReader(Protocol):
      def errors_between(self, start: datetime, end: datetime) -> list[LogRow]: ...

  class AccessReader(Protocol):
      def row_for_trace(self, trace_id: str, at: datetime) -> AccessRow | None: ...
  ```

- [ ] **Step 1: 写协议**

`src/rca/diagnose/protocols.py` 按上面三个 Protocol 实现（docstring 说明"只描述诊断所需的最小方法面，不扩展 LogSource"）。

- [ ] **Step 2: 替换 `object` 注解**

`pipeline.diagnose` 的 `trace_source / log_source / history_source / access_source` 参数由 `object | None` 改为对应 Protocol；删除函数体里的 `# type: ignore[attr-defined]`。

- [ ] **Step 3: 类型闸门**

Run: `pyright src/rca src/agents/tools/diagnose_tools.py`
Expected: 0 error（沿用仓库既有基线）

- [ ] **Step 4: 跑测试**

Run: `POSTGRES_HOST=localhost pytest tests/rca tests/agents/tools/test_diagnose_tool.py -q`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add src/rca/diagnose/protocols.py src/rca/diagnose/pipeline.py src/agents/tools/diagnose_tools.py
git commit -m "refactor(diag): 证据源收窄为 Protocol（2.4）"
```

---

## 自检

- **规格覆盖**：Task 1 的两条 Requirement 分别由 Task 4（分型）与 Task 5（定位器）实现；2.5 无规格（部署面），由 Task 2 与 runbook 承载；6.4 灰度默认关闭，验收时才打开。
- **不做**：8.5 回归集（需 `feedback` 样本与人工标注）、8.6 活体 P50、8.7 三项待验证 —— 见下节，属验收动作，不在代码任务内。
- **类型一致**：`AccessRow` / `ErrorGroup` / `LocateResult` 的字段名在 Task 3–7 全程一致；`run_diagnosis` 返回 `str`，与 `render_card` 同源。
- **循环依赖**：`alert_diagnosis` 只 `TYPE_CHECKING` import `AlertEvent`；`judge` → `access` 单向。

## 验收（不属于编码任务，执行时单独安排）

| 项 | 做法 | 判据 |
|---|---|---|
| 8.6 活体 | 应用机发布后，用真实 `trace_id` 调 `diagnose_trace`；再触发一条白名单内告警 | 卡片可点开详情页；单次 P50 < 30s |
| 8.5 回归集 | `feedback` 点踩 + `prod-incident-log` 取 15~20 条，人工标注根因 | 根因一致率 ≥ 80% |
| 8.7 待验证 | ① 真触发 `RERANK_OUTER_TIMEOUT` 看 `score_kind=timeout` 落库形态；② 多轮 trace 的 metadata 体积；③ 告警 `details` 可提取字段 | 结论回填 design「待验证」 |
| 2.5 采集 | 运维配 Logtail 与轮转后，查一条真实请求的 access 行 | `trace=` 有值且与响应头一致 |

## 执行顺序与依赖

```
Task 1（spec）
Task 2（nginx） ──► Task 3（access 源） ──► Task 4（4xx 证据） ──► Task 7（告警接入）
                                     └──► Task 5（定位器） ──────┘
Task 6（事件）可并行
Task 8（文档）最后
Task 9（2.4）独立，可最后做或跳过（跳过需在 tasks.md 写明理由）
```
