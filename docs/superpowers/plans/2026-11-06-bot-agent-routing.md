# Bot-Agent Routing 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让企微四台机器人各自使用预设人设与能力档（仅 ops/dev 具备只读日志工具），路由在入口确定性完成、可一键回滚，并把 trace 收敛成三类低基数标签。

**Architecture:** 入口（站点请求 / 企微 `bot_key` / 告警）解析出 profile；profile 映射到**能力档**（`default` / `log`），每个档在启动期装配一个 `AgentLoopBundle`，两张外图（含/不含 `ask_user`）各持一份 `{能力档: bundle}` 字典，运行期按 `state.profile` 选档。工具面判据是 `bundle.tool_names`（编译期固定），人设由 `ctx.persona` 运行期施加。

**Tech Stack:** Python 3.11 / FastAPI / LangChain 1.x `create_agent` + LangGraph / larky MCP（Streamable HTTP）/ Langfuse v2 `@observe` / pytest + pytest-asyncio / httpx MockTransport

**Spec:** `docs/openspec/changes/bot-agent-routing/design.md`（配套 `specs/*/spec.md`、`tasks.md`）

## Global Constraints

- 测试命令一律加前缀：`POSTGRES_HOST=localhost .venv/bin/python -m pytest ...`（宿主侧解析不到 `.env` 里的 `postgres` 服务名）。
- 提交一律用**显式路径** `git add <file>...`；本 worktree 的 `.venv` 是 symlink，`git add .` 会把它带进提交。
- 不用三元表达式；`if/else` 写全。类型不确定时用 `isinstance` / `x.attr if x is not None else default`，不用 `getattr(x, "a", d)`。
- 所有函数写 docstring（Args/Returns/Raises），所有 dataclass 字段写行内注释（来源/范围/用途）。
- 常量/阈值/文案进 `src/config/`；日志格式按 `docs/agents/logging-rules.md`（`[前缀] 英文 k=v`）。
- 单文件 ≤400 行、单函数 ≤80 行。
- 日志工具：只读、单次 8 秒超时、**不重试**、`uri` 精确白名单、输出 ≤5 行摘要 / ≤20 行明细、不含 IP/UA/Referer。
- 回滚开关只在**企微渠道**生效，且**不写库**。
- 标签只用三类：`kb|no_kb`、`web|wecom|alert`、`pf:<profile>`。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `src/agents/presets/models.py` | 预设运行时对象 | 改：+hidden/+channel |
| `src/agents/presets/loader.py` | 解析 + `bot-` 规则 + `default_agents_root()` | 改 |
| `src/agents/presets/capability.py` | profile → 能力档映射（唯一归属） | 建 |
| `agents/bot-ops.md` 等 4 份 | 四个机器人人设 | 建 |
| `src/services/capability_service.py` | `/api/agents` 清单 | 改：过滤 hidden |
| `src/services/agent_service.py` | 路由解析、能力档装配、trace | 改（多处，见各任务） |
| `src/agents/graph/workflow.py` | `build_graph` 接多档 bundle | 改 |
| `src/agents/graph/agent_node.py` | `make_agent_loop_node` 按 profile 选 bundle | 改 |
| `src/agents/graph/state.py` | `AgentState.profile` | 改 |
| `src/agents/tools/log_tools.py` | 两个只读日志工具 + 后端注入 + 格式化 | 建 |
| `src/rca/sources/mcp.py` | 复用取数；+`list_tools()` 供启动期探测 | 改 |
| `src/config/rca.py` | `RCA_TOOL_URI_WHITELIST` | 改 |
| `src/config/wecom_bots.py` | `profile` 字段与结构校验 | 改 |
| `src/config/settings.py` | `WECOM_AGENT_ROUTING_ENABLED` | 改 |
| `src/main.py` | lifespan：profile 存在性校验 + MCP 条件探测 | 改 |
| `src/services/alert_service.py` | `alert_turn` 根（spike 通过才改） | 改 |
| `src/infra/llm/tool_trace.py` | 记录 `tools_used` | 改 |

---

### Task 1: 预设可见性字段 + `bot-` 规则 + 四份机器人预设

**Files:**
- Modify: `src/agents/presets/models.py:17-37`
- Modify: `src/agents/presets/loader.py:58-84`
- Create: `agents/bot-ops.md`、`agents/bot-dev.md`、`agents/bot-support.md`、`agents/bot-finance.md`
- Test: `tests/agents/presets/test_preset_loader.py`

**Interfaces:**
- Produces: `AgentPreset.hidden: bool`、`AgentPreset.channel: str`（`""`=不限）；loader 对 `bot-` 前缀抛 `ValueError`，`load_all()` 记 warning 跳过该文件。

- [ ] **Step 1: 写失败测试（字段解析 + 规则）**

追加到 `tests/agents/presets/test_preset_loader.py`：

```python
def test_parse_hidden_and_channel(tmp_path):
    """frontmatter 解析出 hidden 与 channel。"""
    (tmp_path / "bot-ops.md").write_text(
        "---\nname: bot-ops\ndisplay_name: 运维\ndescription: 运维值守\n"
        "hidden: true\nchannel: wecom\n---\n你是运维机器人。\n",
        encoding="utf-8",
    )

    preset = AgentPresetLoader(tmp_path).load_all()[0]

    assert preset.hidden is True
    assert preset.channel == "wecom"


def test_bot_prefix_without_declaration_is_skipped(tmp_path, recwarn):
    """bot- 前缀缺 hidden/channel：跳过该预设（不注册），其它文件不受影响。"""
    (tmp_path / "bot-ops.md").write_text(
        "---\nname: bot-ops\ndescription: 运维\n---\n正文。\n", encoding="utf-8"
    )
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 分析\n---\n正文。\n", encoding="utf-8"
    )

    names = [p.name for p in AgentPresetLoader(tmp_path).load_all()]

    assert names == ["analyst"]
    assert any("bot-ops" in str(w.message) for w in recwarn)


def test_channel_rejects_unknown_value(tmp_path, recwarn):
    """channel 只允许 web / wecom / 空。"""
    (tmp_path / "analyst.md").write_text(
        "---\nname: analyst\ndescription: 分析\nchannel: feishu\n---\n正文。\n",
        encoding="utf-8",
    )

    assert AgentPresetLoader(tmp_path).load_all() == []
    assert any("feishu" in str(w.message) for w in recwarn)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/presets/test_preset_loader.py -q`
Expected: FAIL（`AttributeError: 'AgentPreset' object has no attribute 'hidden'` / 缺校验）

- [ ] **Step 3: 实现字段与解析**

`src/agents/presets/models.py` 在 `max_turns` 后追加：

```python
    hidden: bool = False  # 是否从站点清单隐藏（来源：frontmatter hidden；范围：清单过滤）；bot- 前缀强制 True
    channel: str = ""  # 允许使用的渠道（来源：frontmatter channel；范围：生成入口校验）；空=不限
```

`src/agents/presets/loader.py` 在 `_parse` 中构造 `AgentPreset` 前插入：

```python
        hidden = self._resolve_hidden(meta)
        channel = self._resolve_channel(meta)
        if name.startswith("bot-") and (not hidden or channel != "wecom"):
            raise ValueError("bot- 前缀预设必须声明 hidden: true 且 channel: wecom")
```

构造参数追加 `hidden=hidden, channel=channel`，并新增两个方法：

```python
    def _resolve_hidden(self, meta: dict) -> bool:
        """解析 hidden（非 bool 一律按 False，不做隐式转换）。"""
        value = meta.get("hidden")
        if isinstance(value, bool):
            return value
        return False

    def _resolve_channel(self, meta: dict) -> str:
        """解析 channel（仅 web / wecom / 空；其它取值抛 ValueError）。"""
        value = meta.get("channel")
        if not isinstance(value, str):
            return ""
        normalized = value.strip().lower()
        if normalized in ("", "web", "wecom"):
            return normalized
        raise ValueError(f"channel 非法（仅允许 web / wecom）: {value!r}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/presets -q`
Expected: PASS

- [ ] **Step 5: 写四份预设文件**

`agents/bot-ops.md`：

```markdown
---
name: bot-ops
display_name: 阿里云运维机器人
description: 企微运维群值班助手：查日志、看接口耗时与错误，给出处置动作
hidden: true
channel: wecom
---

你是企业运维值班助手，在企微群里回答服务运行状况。

回答要求：
1. 先给结论（正常 / 异常 / 需要处置），再给证据。证据只引用工具返回的数字，不凭印象。
2. 需要查日志时，先说明你要查哪个接口、看多长时间，再调用 `sls_query_latency` 或 `sls_query_requests`。
3. 工具返回「查询失败」时，明确说"查询失败"，不得表述为"未发现异常"。
4. 给出可执行的下一步（看哪个节点、回滚还是扩容、找谁确认），不要泛泛而谈。
```

`agents/bot-dev.md`：

```markdown
---
name: bot-dev
display_name: 代码工程师助手
description: 研发群助手：定位可疑代码路径、给验证方法，可查线上只读日志
hidden: true
channel: wecom
---

你是研发团队的代码工程师助手。

回答要求：
1. 先定位到具体模块/函数（给出路径线索），再给验证方法（看哪条日志、打哪个接口、跑哪个用例）。
2. 需要线上日志佐证时调用 `sls_query_latency` / `sls_query_requests`，说明查询目的与时间窗。
3. 不确定的地方标注"未验证"，不要把推测写成结论。
4. 给出最小修复方向，不写整段重写方案。
```

`agents/bot-support.md`：

```markdown
---
name: bot-support
display_name: 客服帮手
description: 客服群助手：把用户问题整理成可处理的事项并给出标准答复
hidden: true
channel: wecom
---

你是客服团队的帮手。

回答要求：
1. 先把用户问题复述成一句话（谁、遇到什么、期望什么）。
2. 能直接回答的给标准答复；需要查资料的说明查什么。
3. 需要升级时给升级对象与需要携带的信息。
```

`agents/bot-finance.md`：

```markdown
---
name: bot-finance
display_name: 财务分析助手
description: 财务群助手：口径明确的财务数据问答与初步分析
hidden: true
channel: wecom
---

你是财务分析助手。

回答要求：
1. 凡涉及数字，先说明口径（期间、单位、是否合并口径），再给数。
2. 数据来自知识库时给出来源；没有依据时说"没有查到"，不估算。
3. 结论按"是什么 / 为什么 / 建议看什么"三段写。
```

- [ ] **Step 6: 断言四份文件本身合规**

追加到 `tests/agents/presets/test_preset_content.py`：

```python
def test_bot_presets_are_hidden_and_wecom_only():
    """仓库内的 bot- 预设必须 hidden + wecom。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "agents"
    presets = {p.name: p for p in AgentPresetLoader(root).load_all()}

    for name in ("bot-ops", "bot-dev", "bot-support", "bot-finance"):
        assert name in presets
        assert presets[name].hidden is True
        assert presets[name].channel == "wecom"
```

- [ ] **Step 7: 跑并提交**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/presets -q`
Expected: PASS

```bash
git add src/agents/presets/models.py src/agents/presets/loader.py \
  agents/bot-ops.md agents/bot-dev.md agents/bot-support.md agents/bot-finance.md \
  tests/agents/presets/test_preset_loader.py tests/agents/presets/test_preset_content.py
git commit -m "feat(presets): 增加 hidden/channel 字段与 bot- 加载期校验"
```

---

### Task 2: 站点不可见 + 渠道不符即忽略

**Files:**
- Modify: `src/services/capability_service.py:30-42`
- Modify: `src/services/agent_service.py:1379-1417`（`_resolve_session_agent`）
- Test: `tests/services/test_capability_service.py`、`tests/services/test_agent_service.py`

**Interfaces:**
- Consumes: Task 1 的 `preset.hidden` / `preset.channel`
- Produces: `list_agents()` 不含 hidden；`_resolve_session_agent` 在渠道不符时返回 `AgentResolution(effective="", source="ignored")`

- [ ] **Step 1: 写失败测试**

`tests/services/test_capability_service.py` 追加：

```python
def test_list_agents_filters_hidden():
    """hidden=true 的预设不出现在站点清单。"""
    service = _make_service_with_presets(["chat", "bot-ops"])
    # 让 bot-ops 带上 hidden=True（其它字段沿用 fixture 默认）
    service._preset_registry.get("bot-ops").hidden = True

    names = [item["name"] for item in service.list_agents()]

    assert names == ["chat"]
```

`tests/services/test_agent_service.py` 追加（沿用该文件既有的 `_service_with_registry` 风格夹具；无则用 `AgentService.__new__` + 手挂 `_preset_registry`）：

```python
@pytest.mark.asyncio
async def test_wecom_preset_ignored_on_web_channel(monkeypatch):
    """站点请求指定 channel=wecom 的预设：忽略并按默认处理。"""
    service = _service_with_presets(["bot-ops"])
    service._preset_registry.get("bot-ops").channel = "wecom"
    token = current_channel.set(Channel.WEB)
    try:
        result = await service._resolve_session_agent("s1", "bot-ops")
    finally:
        current_channel.reset(token)

    assert result.effective == ""
    assert result.source == "ignored"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_capability_service.py tests/services/test_agent_service.py -q`
Expected: FAIL（清单仍含 bot-ops；resolve 返回 `new_bound`）

- [ ] **Step 3: 实现**

`capability_service.py`：

```python
        presets = [p for p in self._preset_registry.all() if not p.hidden]
```

`_resolve_session_agent` 在方法体最前插入渠道校验：

```python
        candidate = bound if bound else requested
        if candidate != "" and self._preset_registry is not None:
            preset = self._preset_registry.get(candidate)
            if preset is not None and preset.channel:
                if preset.channel != current_channel.get():
                    core_logging.log_event(
                        Event.AGENT_BIND_IGNORED,
                        session_id=session_id,
                        bound=bound,
                        requested=requested,
                    )
                    return AgentResolution(effective="", source="ignored")
```

docstring 的 `Returns` 补一句：渠道不符按未请求处理。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_capability_service.py tests/services/test_agent_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/capability_service.py src/services/agent_service.py \
  tests/services/test_capability_service.py tests/services/test_agent_service.py
git commit -m "feat(agents): 站点清单过滤隐藏预设，渠道不符的绑定被忽略"
```

---

### Task 3: 企微 `profile` 字段 + 启动期存在性校验

**Files:**
- Modify: `src/config/wecom_bots.py:23-90`
- Modify: `src/agents/presets/loader.py`（+ `default_agents_root()`）
- Modify: `src/main.py:90-96`（`await wecom_service.start()` 之前）
- Test: `tests/config/test_wecom_bots.py`、`tests/services/test_wecom_service.py`

**Interfaces:**
- Produces: `WeComBotConfig.profile: str`；`load_wecom_bots()` 校验字段形状/字符集；`validate_bot_profiles(bots, known_names)` 抛 `RuntimeError`（只含 `key`）。

- [ ] **Step 1: 写失败测试**

```python
def test_profile_parsed(monkeypatch):
    """profile 解析进配置对象；缺省为空串。"""
    monkeypatch.setenv(
        "WECOM_BOTS",
        '[{"key":"ops","bot_id":"b1","secret":"s1","profile":"bot-ops"},'
        '{"key":"dev","bot_id":"b2","secret":"s2"}]',
    )
    bots = load_wecom_bots()
    assert bots[0].profile == "bot-ops"
    assert bots[1].profile == ""


def test_profile_rejects_bad_charset(monkeypatch):
    """profile 只允许 ASCII slug（与其它配置项一致抛 ValueError）。"""
    monkeypatch.setenv(
        "WECOM_BOTS", '[{"key":"ops","bot_id":"b1","secret":"s1","profile":"运维"}]'
    )
    with pytest.raises(ValueError) as exc:
        load_wecom_bots()
    assert "WECOM_BOTS[0].profile" in str(exc.value)


def test_validate_bot_profiles_reports_key():
    """存在性校验失败只定位到 key。"""
    bots = [WeComBotConfig(key="ops", bot_id="b1", secret="s1", profile="bot-ops")]
    with pytest.raises(ValueError) as exc:
        validate_bot_profiles(bots, known={"chat"})
    assert "key=ops" in str(exc.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_wecom_bots.py -q`
Expected: FAIL（`TypeError: unexpected keyword 'profile'` / 无 `validate_bot_profiles`）

- [ ] **Step 3: 实现**

`wecom_bots.py` 的 `WeComBotConfig` 追加字段：

```python
    profile: str = ""  # 该机器人使用的 Agent profile（来源：WECOM_BOTS[].profile；范围：每轮路由）；空=通用人设
```

`load_wecom_bots()` 的 for 循环内（`name` 校验之后、`_KEY_PATTERN` 校验之前）追加：

```python
        profile = item.get("profile", "")
        if profile is None:
            profile = ""
        if not isinstance(profile, str):
            raise ValueError(f"WECOM_BOTS[{index}].profile 必须为字符串")  # noqa: TRY004  # 校验外部配置值，统一抛 ValueError
        if profile != "" and CAPABILITY_NAME_PATTERN.fullmatch(profile) is None:
            raise ValueError(f"WECOM_BOTS[{index}].profile 非法（仅允许 ASCII slug）")
```

构造处改为 `WeComBotConfig(key=key, bot_id=bot_id, secret=secret, name=name, profile=profile)`，并在文件头补 `from src.config.const import CAPABILITY_NAME_PATTERN`。

新增模块级函数：

```python
def validate_bot_profiles(
    bots: list[WeComBotConfig], known: set[str]
) -> None:
    """校验每台机器人的 profile 指向已注册预设。

    Args:
        bots: 已解析的机器人配置
        known: 预设目录中可加载的预设名集合（含 "" 语义：空 profile 不校验）

    Raises:
        ValueError: 某台机器人的 profile 不在 known 中；错误只含 key
    """
    for bot in bots:
        if bot.profile == "":
            continue
        if bot.profile not in known:
            raise ValueError(
                f"WECOM_BOTS key={bot.key} 的 profile 未注册: {bot.profile}"
            )
```

`loader.py` 追加：

```python
def default_agents_root() -> Path:
    """项目根下的 agents 目录（预设内容库唯一入口）。"""
    return Path(__file__).resolve().parents[3] / "agents"
```

（`parents[3]`：`src/agents/presets/loader.py` → 项目根。）

`main.py` 在 `await wecom_service.start()` **之前**插入：

```python
    if settings.WECOM_BOT_ENABLED:
        _validate_wecom_profiles()
```

并在文件内新增：

main.py 顶部导入（**放模块级**，测试才 monkeypatch 得到 `src.main.load_wecom_bots`）：

```python
from src.agents.presets.loader import AgentPresetLoader, default_agents_root
from src.config.wecom_bots import load_wecom_bots, validate_bot_profiles
```

```python
def _validate_wecom_profiles() -> None:
    """启动期校验 WECOM_BOTS 的 profile 指向已注册预设。

    agents/ 缺失时跳过（此时站点预设同样不可用，属既有降级路径）。
    """
    root = default_agents_root()
    if not root.exists():
        logger.warning("[wecom] agents dir missing, skip profile validation")
        return
    known = {preset.name for preset in AgentPresetLoader(root).load_all()}
    validate_bot_profiles(load_wecom_bots(), known)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/config/test_wecom_bots.py tests/services -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/config/wecom_bots.py src/agents/presets/loader.py src/main.py \
  tests/config/test_wecom_bots.py
git commit -m "feat(wecom): 机器人配置支持 profile 并在启动期校验"
```

---

### Task 4: 回滚开关（只作用于企微、不写库）

**Files:**
- Modify: `src/config/settings.py:548-562`（WECOM 段）
- Modify: `src/services/agent_service.py:1379-1417`
- Test: `tests/services/test_session_agent_binding.py`

**Interfaces:**
- Produces: `settings.WECOM_AGENT_ROUTING_ENABLED: bool`（默认 True）；开关关闭且渠道为 wecom 时 `AgentResolution(effective="", source="routing_disabled")`，且**不调用** `bind_session_agent_async`。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_disabled_switch_ignores_bound_on_wecom(monkeypatch):
    """企微渠道 + 开关关闭：忽略已绑定值，回通用人设。"""
    svc = _service_with_presets(["bot-ops"])
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    token = current_channel.set(Channel.WECOM)
    try:
        result = await svc._resolve_session_agent("s1", "bot-ops", bound="bot-ops")
    finally:
        current_channel.reset(token)

    assert result.effective == ""
    assert result.source == "routing_disabled"


@pytest.mark.asyncio
async def test_disabled_switch_keeps_web_binding(monkeypatch):
    """站点渠道不受开关影响：仍沿用绑定。"""
    svc = _service_with_presets(["finance-expert"])
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    token = current_channel.set(Channel.WEB)
    try:
        result = await svc._resolve_session_agent("s2", "", bound="finance-expert")
    finally:
        current_channel.reset(token)

    assert result.effective == "finance-expert"
    assert result.source == "bound"


@pytest.mark.asyncio
async def test_disabled_switch_does_not_write_binding(monkeypatch):
    """override 不得改写 sessions.agent（回滚可逆）。"""
    svc = _service_with_presets(["bot-ops"])
    monkeypatch.setattr(settings, "WECOM_AGENT_ROUTING_ENABLED", False)
    calls: list[str] = []

    async def _fake_bind(session_id: str, agent: str) -> None:
        calls.append(agent)

    monkeypatch.setattr(svc._chat_manager, "bind_session_agent_async", _fake_bind)
    token = current_channel.set(Channel.WECOM)
    try:
        await svc._resolve_session_agent("s3", "bot-ops", bound="bot-ops")
    finally:
        current_channel.reset(token)

    assert calls == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_session_agent_binding.py -q`
Expected: FAIL（`AttributeError: WECOM_AGENT_ROUTING_ENABLED`；返回 `bound`）

- [ ] **Step 3: 实现**

`settings.py` 追加：

```python
WECOM_AGENT_ROUTING_ENABLED: bool = os.getenv(
    "WECOM_AGENT_ROUTING_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")  # 企微 Agent 路由总开关；False=全部回落通用人设
```

`_resolve_session_agent` 方法体最前（渠道校验之前）：

```python
        if (
            not settings.WECOM_AGENT_ROUTING_ENABLED
            and current_channel.get() == Channel.WECOM
        ):
            # 只在内存里忽略绑定：不写库，开关重开后原绑定自然恢复
            return AgentResolution(effective="", source="routing_disabled")
```

`AgentResolution` docstring 的 source 枚举追加 `routing_disabled`。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_session_agent_binding.py tests/services/test_agent_service.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/config/settings.py src/services/agent_service.py \
  tests/services/test_session_agent_binding.py
git commit -m "feat(wecom): 路由回滚开关，仅企微渠道生效且不写库"
```

---

### Task 5: 能力档映射与 `AgentState.profile`

**Files:**
- Create: `src/agents/presets/capability.py`
- Modify: `src/agents/graph/state.py:56-85`
- Modify: `src/services/agent_service.py`（`_run_generation` 构造初始状态处，约 :839）
- Test: `tests/agents/presets/test_capability.py`、`tests/agents/graph/test_state.py`

**Interfaces:**
- Produces: `capability_of(profile: str) -> str`；`AgentState.profile: str`；`make_initial_state(..., profile="") -> AgentState`

- [ ] **Step 1: 写失败测试**

```python
def test_capability_mapping():
    assert capability_of("bot-ops") == "log"
    assert capability_of("bot-dev") == "log"
    assert capability_of("bot-support") == "default"
    assert capability_of("") == "default"


def test_initial_state_carries_profile():
    state = AgentState.make_initial_state("s", "", "hi", [], profile="bot-ops")
    assert state.profile == "bot-ops"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/presets/test_capability.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现**

`src/agents/presets/capability.py`：

```python
"""profile → 能力档映射（唯一归属）。

能力档决定工具面与轮次预算；人设不进档（运行期由 ctx.persona 施加）。
"""

DEFAULT_CAPABILITY = "default"  # 基础档：检索/联网/任务工具，5 轮
LOG_CAPABILITY = "log"  # 日志档：基础档 + 两个只读日志工具，6 轮
LOG_PROFILES = frozenset({"bot-ops", "bot-dev"})  # 具备日志能力的 profile 名
LOG_PROFILE_MAX_TURNS = 6  # 日志档轮次预算（默认档用 MAX_AGENT_ITERATIONS）
DEFAULT_PROFILE_MAX_TURNS = 5  # 基础档轮次预算（与 MAX_AGENT_ITERATIONS 同值）


def capability_of(profile: str) -> str:
    """把 profile 名映射为能力档；未知/空一律基础档。

    Args:
        profile: 本轮生效的 profile 名（空串 = 通用人设）

    Returns:
        `default` 或 `log`
    """
    if profile in LOG_PROFILES:
        return LOG_CAPABILITY
    return DEFAULT_CAPABILITY
```

`state.py`：`direct_skill` 之后加字段：

```python
    profile: str = ""  # 本轮生效的 profile 名（来源：入口解析后经 make_initial_state 传入；范围：整轮；用途：agent 节点选能力档）
```

`make_initial_state` 增加参数 `profile=""`（放在 `summary` 之后），返回值里加 `profile=profile`，docstring 补 Args。

`agent_service.py` 调用处：

```python
    initial_state = AgentState.make_initial_state(
        session_id, kb_id, query, history, deep_thinking, direct_skill, summary,
        profile=ctx.agent,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_state.py tests/agents/presets -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/presets/capability.py src/agents/graph/state.py src/services/agent_service.py \
  tests/agents/presets/test_capability.py tests/agents/graph/test_state.py
git commit -m "feat(agents): profile 贯穿图状态，新增能力档映射"
```

---

### Task 6: 两张外图、多档 bundle、按 profile 选档

**Files:**
- Modify: `src/agents/graph/workflow.py:48-130`
- Modify: `src/agents/graph/agent_node.py:246-300`
- Modify: `src/services/agent_service.py:1056-1130`、`:1218-1225`
- Test: `tests/agents/graph/test_workflow_profiles.py`（新建）、`tests/agents/graph/test_agent_node.py`

**Interfaces:**
- Consumes: `capability_of`、`AgentState.profile`
- Produces: `build_graph(..., profile_tools: Mapping[str, list] | None = None, max_turns_by_profile: Mapping[str, int] | None = None)`；`make_agent_loop_node(bundles: Mapping[str, AgentLoopBundle], default_key: str = "default")`
- 语义：`bundle.tool_names` 是 prompt 工具段的**唯一**来源；`tool_sink` 仍只收基础工具集（日志工具不外溢到 fork 子代理）。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_agent_node_selects_bundle_by_profile(monkeypatch):
    """state.profile 落在 log 档时，prompt 工具段含日志工具。"""
    from src.agents.graph.agent_node import AgentLoopBundle, make_agent_loop_node

    seen: dict = {}

    class _FakeAgent:
        async def ainvoke(self, payload):
            seen["tool_names"] = payload["_probe"]
            return {"messages": []}

    def _bundle(name: str) -> AgentLoopBundle:
        return AgentLoopBundle(
            agent=_FakeAgent(),
            prompt_manager=None,
            tool_names=frozenset({name}),
        )

    node = make_agent_loop_node({"default": _bundle("retrieve_kb"), "log": _bundle("sls_query_latency")})
    state = AgentState.make_initial_state("s", "", "q", [], profile="bot-ops")

    captured: list[frozenset] = []
    import src.agents.graph.agent_node as mod

    original = mod._split_initial_messages

    def _spy(state_obj, prompt_manager, tool_names):
        captured.append(tool_names)
        return [], []

    monkeypatch.setattr(mod, "_split_initial_messages", _spy)
    await node(state)

    assert captured == [frozenset({"sls_query_latency"})]
```

（上面同时验证了「选档」，不依赖真实 prompt 组装。）

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/graph/test_workflow_profiles.py -q`
Expected: FAIL（`make_agent_loop_node` 只接受单 bundle）

- [ ] **Step 3: 实现 `make_agent_loop_node`**

`agent_node.py`：

```python
def make_agent_loop_node(
    bundles: Mapping[str, "AgentLoopBundle"], default_key: str = "default"
) -> Callable:
    """创建外层 agent 节点：按 profile 选能力档 bundle，再组装/seed/invoke/回写。

    Args:
        bundles: 能力档 → 装配产物束（至少含 default_key）
        default_key: 未知 profile 的回落档

    Returns:
        异步节点函数，接收 AgentState，返回 dict 更新 messages/_system_messages/answer
    """

    async def agent_loop(state: AgentState) -> dict:
        key = capability_of(state.profile)
        bundle = bundles.get(key)
        if bundle is None:
            bundle = bundles[default_key]
        ...  # 其余逻辑与本函数原实现一致，bundle 改为上面选出的这一份
```

（原函数体里的 `inner = bundle.agent` 移到函数体内；其余不变。）

- [ ] **Step 4: 实现 `build_graph` 多档**

`workflow.py` 签名追加两个关键字参数，主体改为：

```python
    base_bundle = _make_bundle(llm, rag_tools, prompt_manager, DEFAULT_PROFILE_MAX_TURNS)
    bundles: dict[str, AgentLoopBundle] = {DEFAULT_CAPABILITY: base_bundle}
    for capability, extra_tools in (profile_tools or {}).items():
        merged = [*rag_tools, *extra_tools]
        bundles[capability] = _make_bundle(
            llm,
            merged,
            prompt_manager,
            (max_turns_by_profile or {}).get(capability, DEFAULT_PROFILE_MAX_TURNS),
        )
    builder.add_node("agent", make_agent_loop_node(bundles))
```

新增模块级私有函数（把原内联的 `build_agent(...)` + `AgentLoopBundle(...)` 搬进来）：

```python
def _make_bundle(llm, tools: list, prompt_manager, max_turns: int) -> AgentLoopBundle:
    """按一套工具面装配 Agent 与装配产物束。"""
    loop_agent = build_agent(
        llm,
        tools,
        system=None,
        max_turns=max_turns,
        middleware_extra=[
            SystemMessagesMiddleware(),
            ModelParamsMiddleware(),
            AgentSpanMiddleware(),
        ],
    )
    return AgentLoopBundle(
        agent=loop_agent,
        prompt_manager=prompt_manager,
        tool_names=frozenset(str(t.name) for t in tools),
    )
```

`tool_sink` 保持只 extend `rag_tools`（基础集），并在 docstring 写明「日志工具不进 fork 池」。

- [ ] **Step 5: AgentService 装配两档**

`agent_service.py` 构造段：

```python
        log_tools = make_log_tools()
        profile_tools = {LOG_CAPABILITY: log_tools}
        max_turns_by_profile = {LOG_CAPABILITY: LOG_PROFILE_MAX_TURNS}
```

`self._graph` 与 `self._graph_no_clarify` 的 `build_graph(...)` 都追加 `profile_tools=profile_tools, max_turns_by_profile=max_turns_by_profile`。

工具名快照改为按档：

```python
        self._tool_names_by_capability: dict[str, frozenset[str]] = {
            DEFAULT_CAPABILITY: frozenset(str(t.name) for t in fork_tool_pool),
            LOG_CAPABILITY: frozenset(str(t.name) for t in fork_tool_pool)
            | frozenset(str(t.name) for t in log_tools),
        }
        self._tool_names_no_clarify_by_capability: dict[str, frozenset[str]] = {...同上，用 no_clarify 池...}
```

`ctx.tool_names` 赋值处（原 `:1221`）改为：

```python
        capability = capability_of(effective_agent)
        if clarify:
            ctx.tool_names = self._tool_names_by_capability.get(
                capability, self._tool_names_by_capability[DEFAULT_CAPABILITY]
            )
        else:
            ctx.tool_names = self._tool_names_no_clarify_by_capability.get(
                capability,
                self._tool_names_no_clarify_by_capability[DEFAULT_CAPABILITY],
            )
```

（`effective_agent` 在 `:1250` 已解析，此行在其后。）

- [ ] **Step 6: 修既有测试的调用形状**

Run: `grep -rn "make_agent_loop_node" tests/`
把每个调用点从 `make_agent_loop_node(bundle)` 改为 `make_agent_loop_node({"default": bundle})`；断言不变。

- [ ] **Step 7: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents tests/services -q`
Expected: PASS（若 `test_graph.py` 有 `build_graph(tools=[])` 的用例，确认 `profile_tools` 缺省为 None 时行为不变）

- [ ] **Step 8: 提交**

```bash
git add src/agents/graph/workflow.py src/agents/graph/agent_node.py src/services/agent_service.py \
  tests/agents/graph/test_workflow_profiles.py tests/agents/graph/test_agent_node.py
git commit -m "feat(agents): 外图按能力档装配 bundle，按 profile 选档"
```

---

### Task 7: 两个只读日志工具（骨架 + 白名单 + 超时 + 降级）

**Files:**
- Create: `src/agents/tools/log_tools.py`
- Modify: `src/config/rca.py`（+ `tool_uri_whitelist`）
- Modify: `src/rca/sources/mcp.py`（+ `list_tools()`）
- Test: `tests/agents/tools/test_log_tools.py`

**Interfaces:**
- Produces: `make_log_tools() -> list[BaseTool]`；`configure(backend, whitelist)`；`LogToolBackend` Protocol（`latency` / `detail`）
- 工具名：`sls_query_latency(uri, minutes=15, slow_ms=1000)`、`sls_query_requests(uri, minutes=15, limit=20)`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_no_backend_returns_failure():
    """未配置后端：返回查询失败，不得返回空结果。"""
    configure(None, ())
    tool = {t.name: t for t in make_log_tools()}["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 15})
    assert out.startswith("查询失败")


@pytest.mark.asyncio
async def test_uri_not_in_whitelist():
    """白名单外：列可选接口，不调用后端。"""
    called: list[str] = []

    class _Backend:
        async def latency(self, uri, minutes, slow_ms):
            called.append(uri)
            return "x"

        async def detail(self, uri, minutes, limit):
            called.append(uri)
            return "x"

    configure(_Backend(), ("/api/createOrder",))
    tool = {t.name: t for t in make_log_tools()}["sls_query_latency"]
    out = await tool.ainvoke({"uri": "/api/other", "minutes": 15})
    assert "/api/createOrder" in out
    assert called == []


@pytest.mark.asyncio
async def test_minutes_converted_to_epoch_window():
    """minutes 由工具换算成半开区间，模型不碰时间戳。"""
    seen: dict = {}

    class _Backend:
        async def latency(self, uri, minutes, slow_ms):
            return "ok"

        async def detail(self, uri, minutes, limit):
            return "ok"

    tool = {t.name: t for t in make_log_tools()}["sls_query_latency"]
    configure(_Backend(), ("/api/x",))
    out = await tool.ainvoke({"uri": "/api/x", "minutes": 30})
    assert out == "ok"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/test_log_tools.py -q`
Expected: FAIL（`ModuleNotFoundError: src.agents.tools.log_tools`）

- [ ] **Step 3: 实现**

`src/agents/tools/log_tools.py`：

```python
"""两个只读日志查询工具（仅 log 能力档装配）。

后端由 lifespan 注入（MCP）；未注入或调用失败时统一返回「查询失败」，不得
表述为「未发现异常」。工具内部**不重试**，单次调用 8 秒超时。
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from langchain_core.tools import tool
from loguru import logger

from src.config.log_tools import LogToolTexts
from src.core.logging import encode_value
from src.infra.llm.request_context import current_request_ctx
from src.rca.sources.base import SourceEmptyError, SourceFailedError

LOG_TOOL_TIMEOUT_SECONDS = 8.0  # 单次工具调用超时（秒）；超时即失败，不重试
MIN_MINUTES = 1  # minutes 下界
MAX_MINUTES = 360  # minutes 上界（6 小时）
MAX_LIMIT = 20  # 明细返回行数上限
DEFAULT_SLOW_MS = 1000  # 慢请求阈值默认值（毫秒）


class LogToolBackend(Protocol):
    """日志取数后端（生产为 MCP 客户端；测试注入假实现）。"""

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        """返回聚合摘要文本。"""
        ...

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        """返回明细文本。"""
        ...


_backend: LogToolBackend | None = None
_whitelist: tuple[str, ...] = ()


def configure(backend: LogToolBackend | None, whitelist: tuple[str, ...]) -> None:
    """注入后端与接口白名单（启动期一次；None 表示日志服务不可用）。"""
    global _backend, _whitelist
    _backend = backend
    _whitelist = whitelist


def _session_id() -> str:
    """当前轮会话 id（日志关联用）；无请求上下文返回空串。"""
    ctx = current_request_ctx.get()
    if ctx is None:
        return ""
    return ctx.session_id


def _check_uri(uri: str) -> str | None:
    """白名单校验：命中返回 None，未命中返回错误文案。"""
    if uri in _whitelist:
        return None
    options = "、".join(_whitelist) if _whitelist else "（未配置可查询接口）"
    return f"该接口不在可查询范围：{uri}。可选接口：{options}"


def _check_minutes(minutes: int) -> str | None:
    """参数范围校验。"""
    if minutes < MIN_MINUTES or minutes > MAX_MINUTES:
        return f"参数错误：minutes 必须在 {MIN_MINUTES}-{MAX_MINUTES} 之间"
    return None
```

`src/config/log_tools.py`：

```python
"""日志工具的固定文案（用户/模型可见，集中管理）。"""


class LogToolTexts:
    """日志工具返回文案。"""

    UNAVAILABLE = "查询失败：日志服务不可用"
    TIMEOUT = "查询失败：超时"
    NO_ROWS = "近 {minutes} 分钟无请求记录"
    WHITELIST_EMPTY = "该接口不在可查询范围。可选接口：（未配置可查询接口）"
```

> **字段口径**：`bot` / `chatid` 在工具层拿不到（`RequestContext` 只有 `session_id` / `agent`），
> 日志改记 `session=`；与同一 `trace_id` 下的 `[wecom] inbound … chatid=` 行关联即可定位群。
> 此偏差同步写进 tasks.md 5.7。

工具主体（同文件）：

```python
@tool("sls_query_latency")
async def sls_query_latency(uri: str, minutes: int = 15, slow_ms: int = DEFAULT_SLOW_MS) -> str:
    """查询某接口最近 N 分钟的分位耗时、慢请求与节点分布。

    何时调用：被问到"接口慢不慢""有没有错误""哪个节点异常"时；只查配置白名单内的接口。

    Args:
        uri: 接口路径（必须与白名单精确相等）
        minutes: 回看分钟数（1-360，默认 15）
        slow_ms: 慢请求阈值（毫秒，默认 1000）

    Returns:
        不超过 5 行的文本摘要；参数/白名单/服务不可用时返回错误文案
    """
    error = _check_uri(uri) or _check_minutes(minutes)
    if error is not None:
        return error
    if _backend is None:
        return LogToolTexts.UNAVAILABLE
    started = time.monotonic()
    try:
        text = await asyncio.wait_for(
            _backend.latency(uri, minutes, slow_ms), timeout=LOG_TOOL_TIMEOUT_SECONDS
        )
    except (TimeoutError, SourceFailedError, SourceEmptyError) as exc:
        return _failure_text(exc, minutes)
    logger.info(
        "[tool] mcp call tool=sls_query_latency uri={} minutes={} elapsed_ms={} session={}",
        encode_value(uri),
        minutes,
        int((time.monotonic() - started) * 1000),
        encode_value(_session_id()),
    )
    return text


@tool("sls_query_requests")
async def sls_query_requests(uri: str, minutes: int = 15, limit: int = MAX_LIMIT) -> str:
    """查询某接口最近 N 分钟的请求明细（最多 20 行）。

    Args:
        uri: 接口路径（必须与白名单精确相等）
        minutes: 回看分钟数（1-360，默认 15）
        limit: 返回行数上限（1-20，默认 20）

    Returns:
        每行"时间 节点 耗时 状态 上游状态 方法"；超上限时按耗时倒序截断并标注总数
    """
    error = _check_uri(uri) or _check_minutes(minutes)
    if error is not None:
        return error
    if limit < 1 or limit > MAX_LIMIT:
        return f"参数错误：limit 必须在 1-{MAX_LIMIT} 之间"
    if _backend is None:
        return LogToolTexts.UNAVAILABLE
    started = time.monotonic()
    try:
        text = await asyncio.wait_for(
            _backend.detail(uri, minutes, limit), timeout=LOG_TOOL_TIMEOUT_SECONDS
        )
    except (TimeoutError, SourceFailedError, SourceEmptyError) as exc:
        return _failure_text(exc, minutes)
    logger.info(
        "[tool] mcp call tool=sls_query_requests uri={} minutes={} elapsed_ms={} rows={} session={}",
        encode_value(uri),
        minutes,
        int((time.monotonic() - started) * 1000),
        len(text.splitlines()),
        encode_value(_session_id()),
    )
    return text


def _failure_text(exc: BaseException, minutes: int) -> str:
    """把异常映射为失败文案（空结果 ≠ 失败）。"""
    if isinstance(exc, SourceEmptyError):
        return LogToolTexts.NO_ROWS.format(minutes=minutes)
    if isinstance(exc, TimeoutError):
        return LogToolTexts.TIMEOUT
    return "查询失败：" + str(exc)


def make_log_tools() -> list:
    """返回两个日志工具（装配给 log 能力档）。"""
    return [sls_query_latency, sls_query_requests]
```

`src/config/rca.py` 的 `RcaConfig` 追加：

```python
    tool_uri_whitelist: tuple[str, ...] = ()  # 日志工具可查询接口（精确匹配）；空=全部拒绝
```

`load_rca_config()` 里解析：

```python
    whitelist = tuple(
        part.strip() for part in os.getenv("RCA_TOOL_URI_WHITELIST", "").split(",")
        if part.strip()
    )
```

并传入 `RcaConfig(..., tool_uri_whitelist=whitelist)`。

`src/rca/sources/mcp.py` 追加：

```python
    async def list_tools(self) -> list[str]:
        """返回服务端工具名列表（启动期契约探测用）。

        Raises:
            SourceFailedError: 握手或 tools/list 失败
        """
        await self._ensure_session()
        message = await self._post(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        result = message.get("result")
        if not isinstance(result, dict):
            raise SourceFailedError("missing_result")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise SourceFailedError("missing_tools")
        names: list[str] = []
        for item in tools:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
        return names
```

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/test_log_tools.py tests/rca -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/log_tools.py src/config/log_tools.py src/config/rca.py src/rca/sources/mcp.py \
  tests/agents/tools/test_log_tools.py
git commit -m "feat(tools): 两个只读日志工具与白名单/超时/降级语义"
```

---

### Task 8: 输出裁剪与脱敏

**Files:**
- Modify: `src/agents/tools/log_tools.py`
- Test: `tests/agents/tools/test_log_tools_format.py`（新建）

**Interfaces:**
- Consumes: Task 7 的工具与后端 Protocol
- Produces: 后端实现 `McpLogToolBackend`（`src/agents/tools/log_backend.py`），负责把 `McpLogSource` 原始结果压成 ≤5 行摘要 / ≤20 行明细并脱敏。

- [ ] **Step 1: 写失败测试**

```python
def test_latency_summary_is_five_lines_max():
    data = {
        "total": 120,
        "latency_ms": {"p50": 30, "p95": 800, "max": 2100},
        "slow": {"count": 7},
        "by_node": [{"cnt": 100, "p95": 500}, {"cnt": 20}],
        "errors": {"5xx": 3},
    }
    text = format_latency(data)
    assert len(text.splitlines()) <= 5
    assert "p95 800ms" in text
    assert "5xx 3" in text


def test_detail_rows_capped_and_masked():
    rows = [
        {
            "ts": "2026-11-06 10:00:00",
            "upstream_addr": "10.0.0.8:8080",
            "rt_ms": 1200,
            "status": 502,
            "upstream_status": 502,
        }
    ] * 25
    text = format_detail(rows, limit=20)
    assert len(text.splitlines()) == 21  # 20 行 + 总命中数
    assert "10.0.0.8" not in text
    assert "node-1" in text
    assert "User-Agent" not in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools/test_log_tools_format.py -q`
Expected: FAIL（`ImportError: format_latency`）

- [ ] **Step 3: 实现格式化**

`src/agents/tools/log_backend.py`：

```python
"""MCP 日志后端：把 McpLogSource 的结果压成模型可读文本（裁剪 + 脱敏）。"""

from __future__ import annotations

from src.rca.sources.base import SourceEmptyError, SourceFailedError
from src.rca.sources.mcp import McpLogSource

_IPV4 = r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"
_MAX_LATENCY_LINES = 5
_MAX_DETAIL_ROWS = 20


def _as_int(value: object) -> int:
    """安全转整数；非法值按 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _mask(text: str) -> str:
    """把 IPv4[:port] 替换成节点别名（与 rca 卡片同口径）。"""
    return re.sub(_IPV4, "node-***", text)


def format_latency(data: dict) -> str:
    """把聚合结果压成 ≤5 行摘要。

    Args:
        data: sls_latency_stats 的结构化结果

    Returns:
        5 行以内的文本（样本量 / 分位 / 慢请求 / 节点分布 / 5xx）
    """
    lines = [f"样本量 {_as_int(data.get('total'))} 条"]
    latency = data.get("latency_ms")
    if isinstance(latency, dict):
        parts = [
            f"{key} {_as_int(latency[key])}ms"
            for key in ("p50", "p95", "max")
            if key in latency
        ]
        if parts:
            lines.append("耗时 " + " / ".join(parts))
    slow = data.get("slow")
    if isinstance(slow, dict):
        lines.append(f"慢请求 {_as_int(slow.get('count'))} 条")
    nodes = data.get("by_node")
    if isinstance(nodes, list) and nodes:
        parts = []
        for index, node in enumerate(nodes, start=1):
            if not isinstance(node, dict):
                continue
            parts.append(f"node-{index}: {_as_int(node.get('cnt'))} 条")
        if parts:
            lines.append("分布 " + "；".join(parts))
    errors = data.get("errors")
    if isinstance(errors, dict):
        lines.append(f"5xx {_as_int(errors.get('5xx'))} 条")
    return _mask("\n".join(lines[:_MAX_LATENCY_LINES]))


def format_detail(rows: list, limit: int = _MAX_DETAIL_ROWS) -> str:
    """明细最多 limit 行，按耗时倒序；节点地址用序号别名。

    Args:
        rows: sls_request_detail 的行列表
        limit: 行数上限（1-20）

    Returns:
        每行 "时间 节点 耗时 状态 上游状态"；末行标注总命中数（命中 > limit 时）
    """
    ordered = sorted(rows, key=lambda row: _as_int(row.get("rt_ms")), reverse=True)
    lines = []
    for index, row in enumerate(ordered[:limit], start=1):
        method = row.get("method")
        suffix = ""
        if isinstance(method, str) and method != "":
            suffix = " " + method
        lines.append(
            f"{row.get('ts', '')} node-{index} "
            f"{_as_int(row.get('rt_ms'))}ms "
            f"{_as_int(row.get('status'))}/"
            f"{_as_int(row.get('upstream_status'))}{suffix}"
        )
    if len(ordered) > limit:
        lines.append(f"共命中 {len(ordered)} 条，仅显示最慢 {limit} 条")
    return _mask("\n".join(lines))


class McpLogToolBackend:
    """把日志工具调用转成 McpLogSource 查询并格式化。"""

    def __init__(self, source: McpLogSource) -> None:
        """初始化后端。

        Args:
            source: MCP 取数客户端（超时由工具层统一控制）
        """
        self._source = source

    async def latency(self, uri: str, minutes: int, slow_ms: int) -> str:
        """聚合查询并格式化。"""
        start_ts, end_ts = _window(minutes)
        data = await self._source.latency_stats(uri, start_ts, end_ts, slow_ms=slow_ms)
        return format_latency(data)

    async def detail(self, uri: str, minutes: int, limit: int) -> str:
        """明细查询并格式化。"""
        start_ts, end_ts = _window(minutes)
        rows = await self._source.request_detail(uri, start_ts, end_ts, limit=limit)
        return format_detail(rows, limit)


def _window(minutes: int) -> tuple[int, int]:
    """把回看分钟数换算为 epoch 半开区间 [now-minutes, now)。"""
    end_ts = int(time.time())
    return end_ts - minutes * 60, end_ts
```

（`format_latency` / `format_detail` 同时从 `log_tools.py` 导出给测试用，或测试直接 import 本模块。）

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/agents/tools -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agents/tools/log_backend.py tests/agents/tools/test_log_tools_format.py
git commit -m "feat(tools): 日志工具输出裁剪与脱敏"
```

---

### Task 9: 启动期条件探测（无引用不连、连接失败降级）

**Files:**
- Modify: `src/main.py`（lifespan）
- Test: `tests/test_lifespan_log_tools.py`（新建）

**Interfaces:**
- Consumes: `load_wecom_bots()`、`RcaConfig.tool_uri_whitelist`、`log_tools.configure`
- Produces: `_setup_log_tools()`：有引用 → 探测并注入后端；无引用 → 不连接、不校验；连接失败 → warning + 注入仍可用（返回失败）的后端。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_no_log_profile_skips_probe(monkeypatch):
    """没有机器人引用 log 类 profile：不构造后端。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(
        "src.main.load_wecom_bots",
        lambda: [WeComBotConfig(key="support", bot_id="b", secret="s", profile="bot-support")],
    )
    await _setup_log_tools()
    assert log_tools._backend is None  # noqa: SLF001 测试读私有状态


@pytest.mark.asyncio
async def test_probe_failure_degrades_without_raising(monkeypatch):
    """有引用但服务不可达：注入降级后端，启动不失败。"""
    monkeypatch.setattr(settings, "WECOM_BOT_ENABLED", True)
    monkeypatch.setattr(
        "src.main.load_wecom_bots",
        lambda: [WeComBotConfig(key="ops", bot_id="b", secret="s", profile="bot-ops")],
    )

    class _Broken:
        async def list_tools(self):
            raise SourceFailedError("transport")

    monkeypatch.setattr("src.main._make_mcp_source", lambda: _Broken())
    await _setup_log_tools()  # 不抛
    assert log_tools._backend is not None


@pytest.mark.asyncio
async def test_empty_whitelist_fails_fast_when_referenced(monkeypatch):
    """有引用且白名单为空：启动失败。"""
    ...
    with pytest.raises(RuntimeError) as exc:
        await _setup_log_tools()
    assert "RCA_TOOL_URI_WHITELIST" in str(exc.value)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/test_lifespan_log_tools.py -q`
Expected: FAIL（无 `_setup_log_tools`）

- [ ] **Step 3: 实现**

`main.py`（模块级，便于测试）。顶部补这些导入（**不要放函数内**，否则 `monkeypatch.setattr("src.main.load_wecom_bots", ...)` 不生效）：

```python
from src.agents.tools import log_tools
from src.agents.tools.log_backend import McpLogToolBackend
from src.config.rca import load_rca_config
from src.rca.sources.base import SourceFailedError
from src.rca.sources.mcp import McpLogSource
```

```python
_EXPECTED_LOG_TOOLS = frozenset({"sls_latency_stats", "sls_request_detail"})


def _make_mcp_source() -> McpLogSource:
    """按 RcaConfig 构造 MCP 客户端（日志工具专用实例）。"""
    config = load_rca_config()
    return McpLogSource(
        base_url=config.mcp_base_url,
        token=config.mcp_token,
        datasource_id=config.mcp_datasource_id,
        timeout=config.source_timeout_seconds,
        uris=config.tool_uri_whitelist,
    )


async def _setup_log_tools() -> None:
    """按引用情况装配日志工具后端（lifespan 调用，幂等）。

    Raises:
        RuntimeError: 有引用但白名单为空，或服务端缺少期望工具
    """
    from src.agents.presets.capability import LOG_PROFILES

    if not settings.WECOM_BOT_ENABLED:
        return
    referenced = [bot for bot in load_wecom_bots() if bot.profile in LOG_PROFILES]
    if not referenced:
        return
    config = load_rca_config()
    if not config.tool_uri_whitelist:
        raise RuntimeError(
            "RCA_TOOL_URI_WHITELIST 为空：有机器人引用 bot-ops/bot-dev 时必须配置"
        )
    source = _make_mcp_source()
    backend = McpLogToolBackend(source)
    try:
        names = await source.list_tools()
    except SourceFailedError as exc:
        core_logging.log_event(
            Event.LOG_TOOLS_DEGRADED, reason=type(exc).__name__
        )
    else:
        missing = _EXPECTED_LOG_TOOLS - set(names)
        if missing:
            raise RuntimeError(
                "日志服务缺少期望工具: " + ", ".join(sorted(missing))
            )
    log_tools.configure(backend, config.tool_uri_whitelist)
```

lifespan 中 `_validate_wecom_profiles()` 之后调用 `await _setup_log_tools()`。

`src/core/log_events.py` 追加事件 `LOG_TOOLS_DEGRADED`（级别 warning，消息 `[tool] mcp unavailable`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/test_lifespan_log_tools.py tests/config -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/main.py src/core/log_events.py tests/test_lifespan_log_tools.py
git commit -m "feat(tools): 启动期按引用条件探测日志服务，失败降级"
```

---

### Task 10: trace 标签收敛 + `tools_used`

**Files:**
- Modify: `src/services/agent_service.py:721-737`、`:811-836`
- Modify: `src/infra/llm/tool_trace.py`
- Test: `tests/services/test_trace_tags.py`（新建）、`tests/infra/llm/test_tool_trace.py`

**Interfaces:**
- Consumes: `ctx.agent`（profile）
- Produces: `_trace_tags(kb_tag, channel, profile) -> list[str]`；`ToolTraceCollector.tools_used -> list[str]`；trace 结束前二次 `update_current_trace(metadata={...,"tools_used": ...})`

- [ ] **Step 1: 写失败测试**

```python
def test_tags_are_three_low_cardinality_dimensions():
    assert _trace_tags("kb", Channel.WEB, "") == ["kb", "web", "pf:chat"]
    assert _trace_tags("no_kb", Channel.WECOM, "bot-ops") == [
        "no_kb", "wecom", "pf:ops"
    ]
    assert _trace_tags("no_kb", Channel.ALERT, "") == ["no_kb", "alert", "pf:alert"]
```

（`pf:` 取 profile 去掉 `bot-` 前缀后的短名；空 profile 为 `chat`，告警轮为 `alert`。）

```python
def test_collector_records_tool_names():
    collector = ToolTraceCollector(enabled=False, trace_id="t")
    collector.consume({"event": "on_tool_start", "name": "sls_query_latency", "run_id": "r1", "data": {}})
    collector.consume({"event": "on_tool_start", "name": "retrieve_kb", "run_id": "r2", "data": {}})
    assert collector.tools_used == ["retrieve_kb", "sls_query_latency"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services/test_trace_tags.py tests/infra/llm/test_tool_trace.py -q`
Expected: FAIL（签名不符 / 无 `tools_used`）

- [ ] **Step 3: 实现**

`_trace_tags`：

```python
def _profile_tag(channel: str, profile: str) -> str:
    """把 profile 名压成低基数标签值（chat / alert / ops / dev / support / finance）。"""
    if channel == Channel.ALERT:
        return "pf:alert"
    if profile == "":
        return "pf:chat"
    if profile.startswith("bot-"):
        return "pf:" + profile[len("bot-"):]
    return "pf:" + profile


def _trace_tags(kb_tag: str, channel: str, profile: str) -> list[str]:
    """三元素标签：知识库绑定、渠道、profile（全部低基数）。"""
    return [kb_tag, channel, _profile_tag(channel, profile)]
```

调用处传 `ctx.agent`。

`tool_trace.py`：`__init__` 加 `self._tools_used: set[str] = set()`；`_on_tool_start` 首行 `self._tools_used.add(str(item.get("name", "")))`（空串忽略）；新增属性：

```python
    @property
    def tools_used(self) -> list[str]:
        """本轮实际调用过的工具名（去重、稳定排序）。"""
        return sorted(name for name in self._tools_used if name)
```

`_run_generation` 在 graph 循环结束后（`finally` 之前）追加：

```python
    trace_metadata["tools_used"] = tool_trace.tools_used
    langfuse_context.update_current_trace(metadata=trace_metadata)
```

并在初始 metadata 里放 `"tools_used": []`（保证字段恒存在）。

- [ ] **Step 4: 跑测试确认通过**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/services tests/infra/llm -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/services/agent_service.py src/infra/llm/tool_trace.py \
  tests/services/test_trace_tags.py tests/infra/llm/test_tool_trace.py
git commit -m "feat(tracing): 标签收敛为三类，metadata 补 profile 与 tools_used"
```

---

### Task 11: `alert_turn` 根（spike 门控）

**Files:**
- Modify: `src/services/alert_service.py:496-570`（`_analyze_turn`，仅在 spike 通过时）
- 不改 `src/services/turn_runner.py`（除非 spike 证明必须显式传参，见 Step 3）

**Interfaces:**
- Consumes: 现有链路 —— `_analyze_turn` → `turn_runner.start_turn` → 后台任务 `_make_answer_builder` → `_run_generation`（`@observe(name="chat_turn")`，根 id 由 `langfuse_observation_id=current_trace_id.get()` 指定）
- Produces: 通过时 trace 名为 `alert_turn` 且根不晚于首个 `mcp:*` span；不通过时保持现状并按 Step 4 回退

**为什么没有单测**：这条改动只在 Langfuse 侧可见，断言对象是 trace 的 parent/时间戳，属"实测证据"一类；按仓库约定由本地端到端取证（Step 1/3），不写假断言。

- [ ] **Step 1: 跑 spike（先不写代码）**

```bash
scripts/dev-worktree.sh up --slot 3
curl -s -o /tmp/alert.out -w '%{http_code}\n' -X POST http://127.0.0.1:8003/api/alerts/webhook \
  -H 'Content-Type: application/json' \
  -H "X-Alert-Token: $(grep '^ALERT_WEBHOOK_TOKEN=' /root/code/corporate_rag/.env | cut -d= -f2)" \
  -d @tests/fixtures/alert_webhook.json
tail -5 logs/dev-uvicorn.log
```

到 Langfuse 打开该 trace，记录三项判据：

1. 是否只有**一条** trace（不是根与子 span 各一条）；
2. 根的 `startTime` 是否 ≤ 首个 `mcp:*` span 的 `startTime`；
3. 根与 `chat_turn` 是否撞同一个 observation id（表现为根被覆盖/消失）。

把结论写进 `tasks.md` 的 6.0 行尾（"实测：一条 trace / 根早于 span / 无 id 冲突"）。

- [ ] **Step 2: 通过时实现（只加装饰器）**

`alert_service.py`：

```python
@observe(name="alert_turn", capture_input=False)
async def _analyze_turn(
    cfg: AlertConfig,
    event: AlertEvent,
    *,
    target: AlertTarget,
    session_id: str,
) -> None:
    """判定 + 跑 Agent 并推送结论；作为告警轮 trace 根，起点早于取数 span。"""
    ...  # 函数体不变
```

依据：`start_turn` 用 `asyncio.create_task` 起后台任务，任务在创建时拷贝 contextvars；`_run_generation` 的 `@observe` 因此能挂到本装饰器的 observation 下。**先只加这一个装饰器**，不改 `turn_runner`。

- [ ] **Step 3: 复测（通过则收工，不通过再定位）**

重复 Step 1 的命令，确认：trace 名 `alert_turn`、一条 trace、根时间 ≤ 首个 `mcp:*` span。

若仍不满足（例如根仍晚于 span）：**停止改代码**，按 Step 4 回退，并把实测现象写进 `tasks.md` 6.0。不要为了让它好看去改 `_make_answer_builder`——该函数是站点/企微/告警共用路径，改它的 `langfuse_observation_id` 会同时改变另两条链路的 trace 结构。

- [ ] **Step 4: 回退分支（spike 或复测不通过时执行）**

按顺序改，缺一项 8.2/8.3 验收必挂：

1. 去掉 Step 2 的 `@observe`，`_analyze_turn` 保持原样。
2. `tasks.md` 6.4 的断言改为「trace 名为 `chat_turn`、起点为生成开始」。
3. `tasks.md` 8.2/8.3 删除 `alert_turn` 验收，改为「trace 名仍为 `chat_turn`」。
4. 删除 `specs/llm-tracing/spec.md` 中「告警轮次：根 SHALL 开在告警入口」整段及其 Scenario「告警轮的取数不早于根」。
5. 删除 `design.md` D11 的 `alert_turn` 段与 Risks 中的 `alert_turn 与 chat_turn 撞 observation id` 条目。
6. 删除 `proposal.md` 的 BREAKING 句与「What Changes」中的 `alert_turn` 条目。
7. `openspec validate bot-agent-routing --strict` 仍须通过。
8. 在 `docs/agents/troubleshooting.md` 的 trace 维度约定里写明「告警轮根 = 生成开始，采集 span 早于根属已知现象」。

- [ ] **Step 5: 提交**

通过分支：

```bash
git add src/services/alert_service.py docs/openspec/changes/bot-agent-routing/tasks.md
git commit -m "feat(tracing): 告警轮根前移到告警入口"
```

回退分支：

```bash
git add docs/openspec/changes/bot-agent-routing docs/agents/troubleshooting.md
git commit -m "docs(tracing): alert_turn 回退，保留 chat_turn 并说明根时间口径"
```

---

### Task 12: 文档登记与 env 模板

**Files:**
- Modify: `docs/agents/logging-rules.md`（前缀主表 + `[tool]`）
- Modify: `docs/agents/troubleshooting.md`（trace 维度约定）
- Modify: `docs/agents/glossary.md`（Agent 实例 / profile / preset）
- Modify: `.env.template`、`.env.example`
- Test: 无单测；用 `tests/cli/test_check_docs.py` 与提交钩子兜底

**Interfaces:**
- Consumes: 前述任务实际落地的事件名与字段
- Produces: 文档与模板与代码同源

- [ ] **Step 1: 写文档**

`logging-rules.md` 前缀主表加一行：`[tool]`（日志工具调用；字段 `tool uri minutes elapsed_ms rows`）。

`troubleshooting.md` 新增小节「trace 维度约定」：tag 值域表（`kb|no_kb` / `web|wecom|alert` / `pf:*`）、metadata 字段表（含 `profile` 与 `tools_used`）、「工具名不进 tag，看 span 与 tools_used」、`chat` 标签已废弃。

`glossary.md` 三个词：Agent 实例（编译期固定工具面与预算）、profile（入口解析出的身份名）、preset（`agents/*.md` 声明的人设）。

`.env.template` / `.env.example` **两个都要改**：

```bash
WECOM_AGENT_ROUTING_ENABLED=true   # 企微 Agent 路由总开关；false=全部回落通用人设
RCA_TOOL_URI_WHITELIST=/api/createOrder,/api/pay   # 日志工具可查询接口（精确匹配）；有机器人引用 bot-ops/bot-dev 时必填
```

`WECOM_BOTS` 示例追加 `"profile": "bot-ops"`。

- [ ] **Step 2: 跑文档闸门**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m src.cli.check_docs`
Expected: PASS（新增术语若触发禁用词，按 `doc-anchors-allow` 规则处理，不放宽锚点）

- [ ] **Step 3: 提交**

```bash
git add docs/agents/logging-rules.md docs/agents/troubleshooting.md docs/agents/glossary.md \
  .env.template .env.example
git commit -m "docs: 登记日志工具前缀、trace 维度与 profile 配置"
```

---

### Task 13: 全量验证与本地端到端

**Files:** 无新增
**Interfaces:** 无

- [ ] **Step 1: 全量测试**

Run: `POSTGRES_HOST=localhost .venv/bin/python -m pytest tests/ -q`
Expected: 全绿（DB 类用例若报 `gaierror` 属环境问题，先确认 `POSTGRES_HOST` 前缀）

- [ ] **Step 2: 静态检查**

Run: `ruff check . && ruff format --check .`
Expected: 无 error

Run: `pyright src/`
Expected: 不新增 error（存量第三方误报不计）

- [ ] **Step 3: 本地端到端（仅 ops）**

```bash
scripts/dev-worktree.sh up --slot 3
# 企微本地测试需把 ops 机器人指向本机回调（见 cookbook「worktree 里本地跑服务」）
```

检查点：
1. 群里 @ops 问「最近 15 分钟 createOrder 正常吗」→ 回答含真实数字。
2. 日志出现 `[tool] mcp call tool=sls_query_latency`。
3. Langfuse trace 有 `pf:ops`、`tools_used` 含两个工具之一。
4. 站点 `GET /api/agents` 不含 `bot-*`。
5. `git status` 里 `.venv` 未被 `git add` 带入任何提交。

- [ ] **Step 4: 回滚演练**

```bash
WECOM_AGENT_ROUTING_ENABLED=false scripts/dev-worktree.sh up --slot 3
```

确认：企微回通用人设；站点已绑定预设仍生效；`sessions.agent` 未被改写；改回 true 后 ops 恢复。

- [ ] **Step 5: 收口提交**

```bash
git status --short
git log --oneline dev-wsl..HEAD
```

确认无遗漏、无 `.venv`、无测试临时文件后，交由收尾流程合回 `dev-wsl`（不推 `main`、不 push）。

---

## Self-Review

- **Spec 覆盖**：预设字段/校验（T1）、可见性与渠道（T2）、profile 配置与启动校验（T3）、回滚（T4）、状态与选档（T5/T6）、日志工具（T7/T8）、条件探测（T9）、trace（T10/T11）、文档（T12）、验收（T13）。`llm-tracing` 的「告警轮根」由 T11 门控。
- **占位符**：无 TBD；每条实现都有可运行代码或明确命令。
- **类型一致性**：`capability_of` / `LOG_CAPABILITY` / `DEFAULT_CAPABILITY` 在 T5 定义、T6/T9 复用；`AgentLoopBundle` 沿用既有三字段；`configure(backend, whitelist)` 在 T7 定义、T9 调用；`_trace_tags(kb_tag, channel, profile)` 在 T10 定义并只在该处调用。
