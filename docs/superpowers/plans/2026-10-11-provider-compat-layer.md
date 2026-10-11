# provider-compat-layer Implementation Plan（Part A Phase 1 → Part B Phase 2 → Part C Phase 3 边界）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个单一的 provider 兼容层包（`src/infra/llm/provider/`），把 provider 特有的思考参数形状、reasoning 回传、角色→模型/端点解析收敛到一层，使 handler 侧只认稳定接口；Phase 1 只接 `dashscope`，行为与今天一致（**唯一例外**：主链路恢复 `preserve_thinking`，见 Task 9）。

**本 plan 覆盖三个连续部分**，执行器应**连续推进、不在部分边界停留**，只在 Part B 的两个**人工闸门**（G1 切换确认 / G2 机器人回话确认）处停下：
- **Part A = Phase 1**（纯代码，TDD，Task 1–14）—— 建层，行为等值 + D8 一处修复。
- **Part B = Phase 2**（运维「计划外动作」，本机一条龙 B0–B5）—— 注册 deepseek + 切本机活系统 + 重建 + 冒烟。
- **Part C = Phase 3**（边界声明，**不执行**）—— 延后项，另行立 plan。

**Architecture:** 数据面 = `registry.yaml`（provider 事实 + 模型目录，随镜像）；逻辑面 = 一个纯函数 `thinking_extra_body(spec, enabled)`（唯一产生思考参数处）+ 一个 provider-aware 的 `ReasoningPreservingChat` + 三个 `build_*` 工厂。`src/models.py` 的既有门面（`get_llm` 等）改为薄委托，签名向后兼容。角色解析：`main`/`classify`/`summary` 跟随 `LLM_PROVIDER`；`embedding`/`rerank`/`judge` 独立缺省 `dashscope`。

**Tech Stack:** Python 3.12、langchain-openai 1.3.3、PyYAML、pytest、FastAPI lifespan。

**Spec:**
- `docs/openspec/changes/provider-compat-layer/proposal.md`
- `docs/openspec/changes/provider-compat-layer/design.md`（决策 D1–D14）
- `docs/openspec/changes/provider-compat-layer/specs/provider-compat/spec.md`
- `docs/openspec/changes/provider-compat-layer/specs/model-config/spec.md`
- `docs/openspec/changes/provider-compat-layer/specs/llm-tracing/spec.md`

## Global Constraints

- **ADR-0020**：直连 provider，**不启用** LiteLLM 网关；本层不做路由 / 跨 provider fallback / 限流 / 计费聚合。
- **密钥不入库**：`registry.yaml` 只写 env 名（`api_key_env`），密钥只来自 `.env`。
- **本 change 只实现两种思考形状**：`dashscope`、`deepseek`；`vision` / `thinking_style:none` / `supports_disable_thinking` 延后，**不要**预置。
- **思考开关永远显式发送**，不依赖 provider 默认值（Qwen 默认关、DeepSeek 默认开）。
- **启动期校验只在应用启动（FastAPI lifespan）执行**，`import settings` / `import models` 不触发；`PROVIDER_VALIDATE` 默认 `on`。
- **Phase 1 行为等值**，唯一例外：主链路请求体恢复 `preserve_thinking`（D8）。其余任何行为差异都视为 bug。
- Python `>=3.12`；`langchain-openai==1.3.3`；测试用 `pytest`，断言用 `pytest.warns` / `monkeypatch`。
- 处于 worktree：**禁用 `git add .` / `git add -A`**（`.venv` 是 symlink）；一律显式 pathspec。
- `src/config/__init__.py` 只 `from src.config.settings import *` —— **`const` 里的常量必须从 `src.config.const` 单独 import**。

---

## 文件结构（本阶段新建/修改）

| 文件 | 责任 |
|---|---|
| `src/infra/llm/provider/__init__.py` | 对外接口出口（re-export） |
| `src/infra/llm/provider/registry.yaml` | **数据面**：provider 事实 + 模型目录（Phase 1 只含 dashscope） |
| `src/infra/llm/provider/spec.py` | `ThinkingStyle` / `ProviderSpec` / `ModelFacts` / 加载 / 角色解析 / 校验 |
| `src/infra/llm/provider/thinking.py` | `thinking_extra_body(spec, enabled)` —— 唯一产生思考参数处 |
| `src/infra/llm/provider/chat.py` | `ReasoningPreservingChat`（provider-aware：回传字段 / 形状由 spec 驱动） |
| `src/infra/llm/provider/factory.py` | `build_chat` / `build_embeddings` / `build_rerank` |
| `src/models.py`（改） | 5 个门面改薄委托；新增 `get_judge_llm` |
| `src/agents/graph/middleware.py`（改） | `ModelParamsMiddleware` 改走 `thinking_extra_body` |
| `src/agents/skills/executor.py`（改） | fork 子代理改走 `build_chat(role="main", thinking=…)` |
| `src/config/settings.py`（改） | provider 指针 + `<PROVIDER>_API_KEY`；6 个模型常量默认改空串；软迁移 |
| `src/cli/seed_langfuse_models.py`（改） | 定价判据抽纯函数；从 registry 读；dry-run 诚实 |
| `src/main.py`（改） | lifespan 内加启动校验 + 启动期定价 ensure（`configure_tracing()` 之后） |
| `src/infra/db/models/chunk.py`（改） | `Vector(1024)` 抽常量 |
| `src/rag/prompt.py`（改） | `PROMPT_TOKENS_PER_CJK_CHAR` 改经层取 |
| `.env.example`（改） | 迁移示例 |

---

## Task 1: 依赖与包骨架

**Files:**
- Modify: `pyproject.toml`（`[project].dependencies`）
- Create: `src/infra/llm/provider/__init__.py`
- Create: `src/infra/llm/provider/registry.yaml`（最小可加载版本，Task 2 补全）
- Test: `tests/infra/llm/provider/__init__.py`（空包标记）

**Interfaces:**
- Produces: 包可 `import src.infra.llm.provider`。

- [ ] **Step 1: 显式声明 pyyaml**

在 `pyproject.toml` 的 `dependencies` 列表末尾（`wecom-aibot-python-sdk==1.0.2` 之后）加一行：

```toml
    # provider 兼容层数据面：src/infra/llm/provider/registry.yaml 用 YAML（带注释）。
    # 此前仅由传递依赖提供，直接 import yaml 故显式声明。
    "pyyaml==6.0.3",
```

- [ ] **Step 2: 建包与最小 registry**

```bash
mkdir -p src/infra/llm/provider tests/infra/llm/provider
: > tests/infra/llm/provider/__init__.py
```

`src/infra/llm/provider/__init__.py`：

```python
"""Provider 兼容层：把 provider 特有差异收敛到一层（见 design D1）。

对外只暴露：thinking_extra_body / build_chat / build_embeddings / build_rerank。
"""
```

- [ ] **Step 3: 冒烟验证包可导入**

Run: `.venv/bin/python -c "import src.infra.llm.provider"`
Expected: 无输出、退出码 0。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/infra/llm/provider/__init__.py tests/infra/llm/provider/__init__.py
git commit -m "chore(provider-compat): 显式声明 pyyaml 并建 provider 包骨架"
```

---

## Task 2: registry.yaml + spec.py（数据模型与加载）

**Files:**
- Create: `src/infra/llm/provider/registry.yaml`
- Create: `src/infra/llm/provider/spec.py`
- Test: `tests/infra/llm/provider/test_spec_load.py`

**Interfaces:**
- Produces:
  - `class ThinkingStyle(str, Enum): DASHSCOPE="dashscope"; DEEPSEEK="deepseek"`
  - `@dataclass(frozen=True) ModelFacts(context_window:int|None=None, pricing:tuple[float,float]|None=None, tokens_per_cjk_char:float|None=None, embedding_batch_size:int|None=None, embedding_dimension:int|None=None)`
  - `@dataclass(frozen=True) ProviderSpec(name, base_url, api_key_env, thinking_style, reasoning_field, supports_tools, models, catalog)`
  - `ROLES: tuple[str,...] = ("main","classify","summary","judge","embedding","rerank")`
  - `load_registry(path: Path | None = None) -> dict[str, ProviderSpec]`
- Consumes: 无。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_spec_load.py`：

```python
from pathlib import Path

import pytest

from src.infra.llm.provider.spec import ThinkingStyle, load_registry

REGISTRY = Path("src/infra/llm/provider/registry.yaml")


def test_load_dashscope():
    reg = load_registry(REGISTRY)
    assert "dashscope" in reg
    ds = reg["dashscope"]
    assert ds.thinking_style is ThinkingStyle.DASHSCOPE
    assert ds.reasoning_field == "reasoning_content"
    assert ds.api_key_env == "DASHSCOPE_API_KEY"
    assert ds.models["main"]  # 非空
    assert ds.models["embedding"] and ds.models["rerank"]
    assert ds.catalog[ds.models["embedding"]].embedding_dimension == 1024


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_registry(tmp_path / "nope.yaml")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_spec_load.py -v`
Expected: FAIL（`ModuleNotFoundError: src.infra.llm.provider.spec`）。

- [ ] **Step 3: 写 `registry.yaml`**

```yaml
# Provider 兼容层注册表（数据面）。随镜像打包（Dockerfile: COPY src/ src/）。
# 密钥不入库：本文件只写 env 名，真实密钥在 .env 的 <PROVIDER>_API_KEY。
#
# 角色图例：
#   main      主对话/生成（agent 主循环）
#   classify  轻量分类：相对时间词解析(rag_tools) + 文档实体抽取(document_service)
#   summary   跨轮历史摘要
#   judge     RAGAS 评估裁判（独立缺省 dashscope）
#   embedding 向量化
#   rerank    精排（DashScope 专有）
providers:
  dashscope:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    api_key_env: DASHSCOPE_API_KEY
    thinking_style: dashscope
    reasoning_field: reasoning_content
    capabilities:
      tools: true
    models:
      main: kimi-k3
      classify: kimi-k3
      summary: kimi-k3
      judge: qwen3.8-max
      embedding: qwen3.7-text-embedding-flash
      rerank: qwen3.7-text-rerank
    catalog:
      kimi-k3:
        context_window: 262144
        pricing: { input: 0.0000008, output: 0.0000027 }  # 每 token；沿用现 .env 口径
        tokens_per_cjk_char: 0.6
      qwen3.8-max:
        context_window: 262144
      qwen3.7-text-embedding-flash:
        embedding_dimension: 1024
        embedding_batch_size: 20
```

- [ ] **Step 4: 写 `spec.py`（加载部分）**

```python
"""Provider 兼容层数据模型与加载（design D2/D3）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

_DEFAULT_REGISTRY = Path(__file__).with_name("registry.yaml")

ROLES: tuple[str, ...] = ("main", "classify", "summary", "judge", "embedding", "rerank")


class ThinkingStyle(str, Enum):
    """思考参数形状族（design D5–D6）。新增形状 = 加成员 + 加翻译分支 + 加数据。"""

    DASHSCOPE = "dashscope"
    DEEPSEEK = "deepseek"


@dataclass(frozen=True)
class ModelFacts:
    context_window: int | None = None
    pricing: tuple[float, float] | None = None
    tokens_per_cjk_char: float | None = None
    embedding_batch_size: int | None = None
    embedding_dimension: int | None = None


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    base_url: str
    api_key_env: str
    thinking_style: ThinkingStyle
    reasoning_field: str | None
    supports_tools: bool = True
    models: dict[str, str] = field(default_factory=dict)
    catalog: dict[str, ModelFacts] = field(default_factory=dict)


def load_registry(path: Path | None = None) -> dict[str, ProviderSpec]:
    """加载并解析 registry.yaml。文件缺失抛 FileNotFoundError（供启动期 fail-fast）。"""
    path = path or _DEFAULT_REGISTRY
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs: dict[str, ProviderSpec] = {}
    for name, body in (raw.get("providers") or {}).items():
        catalog = {}
        for model_name, facts in (body.get("catalog") or {}).items():
            pricing = None
            if isinstance(facts.get("pricing"), dict):
                pricing = (float(facts["pricing"]["input"]), float(facts["pricing"]["output"]))
            catalog[model_name] = ModelFacts(
                context_window=facts.get("context_window"),
                pricing=pricing,
                tokens_per_cjk_char=facts.get("tokens_per_cjk_char"),
                embedding_batch_size=facts.get("embedding_batch_size"),
                embedding_dimension=facts.get("embedding_dimension"),
            )
        specs[name] = ProviderSpec(
            name=name,
            base_url=body["base_url"],
            api_key_env=body["api_key_env"],
            thinking_style=ThinkingStyle(body["thinking_style"]),
            reasoning_field=body.get("reasoning_field"),
            supports_tools=bool((body.get("capabilities") or {}).get("tools", True)),
            models=dict(body.get("models") or {}),
            catalog=catalog,
        )
    return specs
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_spec_load.py -v`
Expected: 2 passed。

- [ ] **Step 6: Commit**

```bash
git add src/infra/llm/provider/registry.yaml src/infra/llm/provider/spec.py tests/infra/llm/provider/test_spec_load.py
git commit -m "feat(provider-compat): registry.yaml 与 ProviderSpec 加载"
```

---

## Task 3: thinking.py —— 思考参数归一化（唯一产生处）

**Files:**
- Create: `src/infra/llm/provider/thinking.py`
- Test: `tests/infra/llm/provider/test_thinking.py`

**Interfaces:**
- Consumes: `ProviderSpec`, `ThinkingStyle`。
- Produces: `thinking_extra_body(spec: ProviderSpec, enabled: bool) -> dict`。返回**完整** extra_body（含 `preserve_thinking`），构造期与请求期同源（design D8）。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_thinking.py`：

```python
from src.infra.llm.provider.spec import ProviderSpec, ThinkingStyle
from src.infra.llm.provider.thinking import thinking_extra_body


def _spec(style: ThinkingStyle) -> ProviderSpec:
    return ProviderSpec(
        name=style.value, base_url="http://x", api_key_env="K",
        thinking_style=style, reasoning_field="reasoning_content",
    )


def test_dashscope_on_includes_preserve_thinking():
    assert thinking_extra_body(_spec(ThinkingStyle.DASHSCOPE), True) == {
        "enable_thinking": True, "preserve_thinking": True,
    }


def test_dashscope_off_omits_preserve_thinking():
    assert thinking_extra_body(_spec(ThinkingStyle.DASHSCOPE), False) == {
        "enable_thinking": False,
    }


def test_deepseek_on_off():
    assert thinking_extra_body(_spec(ThinkingStyle.DEEPSEEK), True) == {
        "thinking": {"type": "enabled"},
    }
    assert thinking_extra_body(_spec(ThinkingStyle.DEEPSEEK), False) == {
        "thinking": {"type": "disabled"},
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_thinking.py -v`
Expected: FAIL（`ModuleNotFoundError: ...thinking`）。

- [ ] **Step 3: 写实现**

```python
"""思考参数归一化：唯一产生 provider 思考参数的地方（design D8）。"""

from __future__ import annotations

from src.infra.llm.provider.spec import ProviderSpec, ThinkingStyle


def thinking_extra_body(spec: ProviderSpec, enabled: bool) -> dict:
    """把"思考开/关"翻译为目标 provider 的完整 extra_body。

    返回的是**完整** dict（含 preserve 标志），构造期与请求期同源使用，
    避免 per-call extra_body 覆盖构造期 extra_body 时丢字段。
    """
    if spec.thinking_style is ThinkingStyle.DASHSCOPE:
        body: dict = {"enable_thinking": enabled}
        if enabled:
            body["preserve_thinking"] = True
        return body
    if spec.thinking_style is ThinkingStyle.DEEPSEEK:
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}
    raise ValueError(f"未实现 thinking_style: {spec.thinking_style}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_thinking.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/thinking.py tests/infra/llm/provider/test_thinking.py
git commit -m "feat(provider-compat): thinking_extra_body 归一化（dashscope/deepseek）"
```

---

## Task 4: chat.py —— provider-aware 的 ReasoningPreservingChat

**Files:**
- Create: `src/infra/llm/provider/chat.py`
- Test: `tests/infra/llm/provider/test_chat.py`

**Interfaces:**
- Produces: `class ReasoningPreservingChat(ChatOpenAI)`，pydantic 字段 `reasoning_field: str | None = "reasoning_content"`；覆写 `_convert_chunk_to_generation_chunk`（流式提取思考内容）与 `_get_request_payload`（请求侧回传）。
- 行为与现有 `src/infra/llm/reasoning_chat.py:ChatQwenWithReasoning` 一致，但字段名来自 `self.reasoning_field`。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_chat.py`：

```python
from langchain_core.messages import AIMessage, HumanMessage

from src.infra.llm.provider.chat import ReasoningPreservingChat


def _llm(field="reasoning_content"):
    return ReasoningPreservingChat(
        model="kimi-k3", api_key="test", reasoning_field=field
    )


def test_roundtrips_reasoning_field():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", additional_kwargs={"reasoning_content": "思考"}),
    ]
    payload = _llm()._get_request_payload(msgs)
    assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "思考"


def test_empty_field_skips_injection():
    msgs = [AIMessage(content="hi", additional_kwargs={"reasoning_content": "x"})]
    payload = _llm(field=None)._get_request_payload(msgs)
    assert "reasoning_content" not in payload["messages"][0]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_chat.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 写实现**

迁移 `src/infra/llm/reasoning_chat.py` 的两个方法，去掉 DashScope 专有的 `__init__` 注入（形状改由 `thinking_extra_body` 提供），把硬编码 `"reasoning_content"` 换成 `self.reasoning_field`：

```python
"""provider-aware 的 ChatOpenAI 子类：保留/回传第三方思考内容（design D8/D9）。"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from loguru import logger


class ReasoningPreservingChat(ChatOpenAI):
    """保留 reasoning 字段的 ChatOpenAI；字段名来自 `reasoning_field`。"""

    reasoning_field: str | None = "reasoning_content"

    def _convert_chunk_to_generation_chunk(
        self, chunk: dict, default_chunk_class: type, base_generation_info: dict | None
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None or not self.reasoning_field:
            return generation_chunk
        choices = chunk.get("choices", [])
        if not choices and chunk.get("chunk"):
            choices = chunk["chunk"].get("choices", [])
        if not choices:
            return generation_chunk
        delta = choices[0].get("delta", {}) or {}
        reasoning = delta.get(self.reasoning_field) or delta.get("reasoning") or ""
        if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
            prev = generation_chunk.message.additional_kwargs.get("reasoning_content", "")
            generation_chunk.message.additional_kwargs["reasoning_content"] = prev + reasoning
        return generation_chunk

    def _get_request_payload(
        self, input_: LanguageModelInput, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if not self.reasoning_field:
            return payload
        payload_messages = payload.get("messages", [])
        if len(messages) != len(payload_messages):
            logger.warning(
                "reasoning injection skipped: message count mismatch ({} vs {})",
                len(messages), len(payload_messages),
            )
            return payload
        for msg, msg_dict in zip(messages, payload_messages):
            if msg_dict.get("role") != "assistant":
                continue
            reasoning = msg.additional_kwargs.get("reasoning_content", "")
            if reasoning and not msg_dict.get(self.reasoning_field):
                msg_dict[self.reasoning_field] = reasoning
        return payload
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_chat.py -v`
Expected: 2 passed。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/chat.py tests/infra/llm/provider/test_chat.py
git commit -m "feat(provider-compat): provider-aware ReasoningPreservingChat"
```

---

## Task 5: factory.py —— build_chat / build_embeddings / build_rerank

**Files:**
- Create: `src/infra/llm/provider/factory.py`
- Test: `tests/infra/llm/provider/test_factory.py`

**Interfaces:**
- Consumes: Task 2/3/4 的 `load_registry`/`thinking_extra_body`/`ReasoningPreservingChat`；`settings` 的 `LLM_TEMPERATURE`/`CLASSIFIER_TEMPERATURE`。
- Produces:
  - `resolve_role(role: str, registry: dict[str, ProviderSpec]) -> ResolvedModel`（Task 6 实现，本任务先用 `_resolve` 内部函数）
  - `build_chat(role: str, *, thinking: bool | None = None, model: str | None = None, **overrides) -> ChatOpenAI`
  - `build_embeddings() -> OpenAIEmbeddings`
  - `build_rerank() -> DashScopeRerank`

> ⚠️ 本任务先建立 factory 骨架并让 `build_chat` 可跑；角色解析（Task 6）先内置最小实现，Task 6 再抽到 `spec.py`。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_factory.py`：

```python
from langchain_openai import ChatOpenAI

from src.infra.llm.provider.factory import build_chat


def test_build_main_no_thinking_has_no_thinking_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    llm = build_chat("main")
    assert isinstance(llm, ChatOpenAI)
    assert "enable_thinking" not in (llm.extra_body or {})


def test_build_summary_thinking_false(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test")
    llm = build_chat("summary", thinking=False)
    assert llm.extra_body == {"enable_thinking": False}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_factory.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 写实现**

```python
"""三类模型工厂：handler 侧只认这三个接口（design D1）。"""

from __future__ import annotations

import json

from langchain_community.document_compressors.dashscope_rerank import DashScopeRerank
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import SecretStr

from src.config import settings
from src.infra.llm.provider.chat import ReasoningPreservingChat
from src.infra.llm.provider.spec import ProviderSpec, load_registry
from src.infra.llm.provider.thinking import thinking_extra_body

_REGISTRY = None


def _registry() -> dict[str, ProviderSpec]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


def _resolve_model(role: str, registry: dict[str, ProviderSpec]):
    """(ProviderSpec, model, base_url, api_key)。Task 6 会迁到 spec.resolve_role。"""
    from src.infra.llm.provider.spec import resolve_role

    return resolve_role(role, registry)


def _llm_kwargs() -> dict:
    return json.loads(settings.LLM_KWARGS)


def build_chat(
    role: str, *, thinking: bool | None = None, model: str | None = None, **overrides
):
    resolved = _resolve_model(role, _registry())
    extra_kwargs = _llm_kwargs()
    if thinking is not None:
        extra_kwargs["extra_body"] = thinking_extra_body(resolved.provider, thinking)
    extra_kwargs.update(overrides)
    return ReasoningPreservingChat(
        model=model or resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        reasoning_field=resolved.provider.reasoning_field,
        **extra_kwargs,
    )


def build_embeddings() -> OpenAIEmbeddings:
    resolved = _resolve_model("embedding", _registry())
    facts = resolved.provider.catalog.get(resolved.model)
    return OpenAIEmbeddings(
        model=resolved.model,
        api_key=SecretStr(resolved.api_key),
        base_url=resolved.base_url,
        check_embedding_ctx_length=False,
        chunk_size=(facts.embedding_batch_size if facts else None) or 20,
    )


def build_rerank(model: str | None = None, top_n: int | None = None) -> DashScopeRerank:
    import dashscope

    resolved = _resolve_model("rerank", _registry())
    return DashScopeRerank(
        model=model or resolved.model,
        top_n=top_n if top_n is not None else settings.TOP_K_RERANK,
        api_key=resolved.api_key,
        client=dashscope.TextReRank,
    )
```

- [ ] **Step 4: 跑测试确认通过（此时依赖 Task 6 的 resolve_role，先跳到 Task 6 再回来跑）**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_factory.py -v`
Expected: 2 passed（需 Task 6 的 `resolve_role` 已存在）。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/factory.py tests/infra/llm/provider/test_factory.py
git commit -m "feat(provider-compat): build_chat/build_embeddings/build_rerank 工厂"
```

---

## Task 6: 角色解析 + `.env` 覆盖规则

**Files:**
- Modify: `src/infra/llm/provider/spec.py`
- Test: `tests/infra/llm/provider/test_resolve.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ResolvedModel(role, provider: ProviderSpec, model: str, base_url: str, api_key: str)`
  - `resolve_role(role: str, registry: dict[str, ProviderSpec], env: Mapping[str,str] | None = None) -> ResolvedModel`
  - 覆盖规则：角色 provider env `<ROLE>_PROVIDER`（`main`→`LLM_PROVIDER`）→ 缺省；`embedding`/`rerank`/`judge` 缺省 `dashscope`，其余缺省 `env["LLM_PROVIDER"] or "dashscope"`；模型 env `<ROLE>_MODEL`（`judge`→`RAGAS_LLM_MODEL`）覆盖 `models[role]`；端点 `<PROVIDER>_BASE_URL` 覆盖 `base_url`；密钥 `<PROVIDER>_API_KEY`。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_resolve.py`：

```python
from src.infra.llm.provider.spec import load_registry, resolve_role

REG = load_registry()


def _env(**kw):
    return {k: v for k, v in kw.items()}


def test_main_follows_llm_provider():
    r = resolve_role("main", REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"})
    assert r.provider.name == "dashscope"
    assert r.model == REG["dashscope"].models["main"]
    assert r.api_key == "k"


def test_embedding_independent_default():
    r = resolve_role("embedding", REG, {"LLM_PROVIDER": "deepseek"})
    assert r.provider.name == "dashscope"  # 不跟随


def test_role_model_override():
    r = resolve_role("main", REG, {"LLM_PROVIDER": "dashscope", "LLM_MODEL": "kimi-k3"})
    assert r.model == "kimi-k3"


def test_provider_base_url_override():
    r = resolve_role("main", REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_BASE_URL": "http://proxy"})
    assert r.base_url == "http://proxy"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_resolve.py -v`
Expected: FAIL（`resolve_role` 未定义）。

- [ ] **Step 3: 追加实现到 `spec.py`**

```python
from collections.abc import Mapping

# 独立缺省 dashscope 的角色（不跟随 LLM_PROVIDER，design D4）
_INDEPENDENT_ROLES = ("embedding", "rerank", "judge")
_PROVIDER_ENV = {"main": "LLM_PROVIDER", "classify": "CLASSIFY_PROVIDER",
                 "summary": "SUMMARY_PROVIDER", "judge": "RAGAS_PROVIDER",
                 "embedding": "EMBEDDING_PROVIDER", "rerank": "RERANK_PROVIDER"}
_MODEL_ENV = {"main": "LLM_MODEL", "classify": "CLASSIFY_MODEL",
              "summary": "SUMMARY_MODEL", "judge": "RAGAS_LLM_MODEL",
              "embedding": "EMBEDDING_MODEL", "rerank": "RERANK_MODEL"}
_DEFAULT_PROVIDER = "dashscope"


@dataclass(frozen=True)
class ResolvedModel:
    role: str
    provider: ProviderSpec
    model: str
    base_url: str
    api_key: str


def resolve_role(
    role: str, registry: dict[str, ProviderSpec], env: Mapping[str, str] | None = None
) -> ResolvedModel:
    env = os.environ if env is None else env
    if role not in _PROVIDER_ENV:
        raise KeyError(f"未知角色: {role}")
    # provider 选择
    provider_name = env.get(_PROVIDER_ENV[role]) or ""
    if not provider_name:
        provider_name = _DEFAULT_PROVIDER if role in _INDEPENDENT_ROLES else (
            env.get("LLM_PROVIDER") or _DEFAULT_PROVIDER
        )
    if provider_name not in registry:
        raise KeyError(f"未知 provider: {provider_name}（角色 {role}）")
    spec = registry[provider_name]
    # 模型：.env 覆盖 else registry 默认
    model = env.get(_MODEL_ENV[role]) or spec.models.get(role) or ""
    if not model:
        raise KeyError(f"角色 {role} 在 provider {provider_name} 无可用模型")
    base_url = env.get(f"{provider_name.upper()}_BASE_URL") or spec.base_url
    api_key = env.get(spec.api_key_env) or ""
    return ResolvedModel(role=role, provider=spec, model=model, base_url=base_url, api_key=api_key)
```

- [ ] **Step 4: 跑两处测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_resolve.py tests/infra/llm/provider/test_factory.py -v`
Expected: 全部 passed（factory 的 `resolve_role` 现已可用）。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/spec.py tests/infra/llm/provider/test_resolve.py
git commit -m "feat(provider-compat): 角色解析与 .env 覆盖规则"
```

---

## Task 7: 启动期 fail-fast 校验

**Files:**
- Modify: `src/infra/llm/provider/spec.py`
- Test: `tests/infra/llm/provider/test_validate.py`

**Interfaces:**
- Produces: `validate_config(registry, env) -> None`；校验：未知角色 provider、缺 key、角色绑到 `models` 无该角色的 provider、`thinking_style` 有分支。`PROVIDER_VALIDATE=off` 时跳过。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_validate.py`：

```python
import pytest

from src.infra.llm.provider.spec import load_registry, validate_config

REG = load_registry()


def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        validate_config(REG, {"LLM_PROVIDER": "dashscope"})


def test_ok_with_key():
    validate_config(REG, {"LLM_PROVIDER": "dashscope", "DASHSCOPE_API_KEY": "k"})


def test_unknown_provider_raises():
    with pytest.raises(RuntimeError, match="未知 provider"):
        validate_config(REG, {"LLM_PROVIDER": "nope", "DASHSCOPE_API_KEY": "k"})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_validate.py -v`
Expected: FAIL。

- [ ] **Step 3: 追加实现**

```python
def validate_config(
    registry: dict[str, ProviderSpec], env: Mapping[str, str] | None = None
) -> None:
    """启动期 fail-fast。仅在应用启动（lifespan）调用；import 不触发（design D12）。"""
    env = os.environ if env is None else env
    if env.get("PROVIDER_VALIDATE", "on").lower() in ("0", "off", "false"):
        return
    for role in ROLES:
        try:
            resolved = resolve_role(role, registry, env)
        except KeyError as e:
            msg = str(e).strip("'\"")
            if msg.startswith("未知 provider"):
                raise RuntimeError(msg) from e
            raise RuntimeError(f"角色 {role} 解析失败: {msg}") from e
        if not resolved.api_key:
            raise RuntimeError(
                f"provider {resolved.provider.name} 缺密钥：请设置 {resolved.provider.api_key_env}"
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_validate.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/spec.py tests/infra/llm/provider/test_validate.py
git commit -m "feat(provider-compat): 启动期 fail-fast 校验"
```

> **备注（Task 7 之后）**：在 `src/main.py` 的 lifespan 内、`configure_tracing()` 之前加一行 `from src.infra.llm.provider.factory import _registry` + `from src.infra.llm.provider.spec import validate_config; validate_config(_registry())`。此步并入 Task 9 的 lifespan 改动一起提交（避免中间态）。

---

## Task 8: `src/models.py` 门面改薄委托 + 新增 `get_judge_llm`

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_models.py`（更新）+ `tests/infra/llm/provider/test_facade.py`
- Modify: `src/cli/eval_ragas.py:549,563`、`src/cli/eval_ragas_generate.py:331-334,354`

**Interfaces:**
- Consumes: `build_chat` / `build_embeddings` / `build_rerank`（Task 5）、`settings`（Task 10 前先不改 settings，本任务只改签名默认）。
- Produces: `get_llm(model: str | None = None, temperature=None)`、`get_classify_llm()`、`get_summary_llm()`、`get_judge_llm()`、`get_embeddings(model=None)`、`get_rerank(model=None, top_n=None)`。

- [ ] **Step 1: 写失败测试**

`tests/infra/llm/provider/test_facade.py`：

```python
from src.models import get_judge_llm, get_llm


def test_get_judge_llm_uses_judge_role(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory
    monkeypatch.setattr(factory, "_REGISTRY", None)
    llm = get_judge_llm()
    assert llm.model_name == "qwen3.8-max"  # registry.models[judge]


def test_get_llm_defaults_to_registry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory
    monkeypatch.setattr(factory, "_REGISTRY", None)
    assert get_llm().model_name == "kimi-k3"  # registry.models[main]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_facade.py -v`
Expected: FAIL（`get_judge_llm` 不存在）。

- [ ] **Step 3: 改 `src/models.py`**

保留 `with_retry` 不变；替换模型工厂为薄委托：

```python
def get_llm(model: str | None = None, temperature: float | None = None, **kwargs):
    from src.config import settings
    return build_chat("main", model=model, temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE, **kwargs)


def get_classify_llm():
    from src.config import settings
    return build_chat("classify", thinking=False, temperature=settings.CLASSIFIER_TEMPERATURE)


def get_summary_llm():
    from src.config.const import SUMMARY_MAX_TOKENS
    return build_chat("summary", thinking=False, temperature=0, max_tokens=SUMMARY_MAX_TOKENS)


def get_judge_llm(model: str | None = None):
    return build_chat("judge", model=model, temperature=0)


def get_embeddings(model: str | None = None) -> OpenAIEmbeddings:
    return build_embeddings()


def get_rerank(model: str | None = None, top_n: int | None = None) -> DashScopeRerank:
    return build_rerank(model=model, top_n=top_n)
```

并在文件顶部 `from src.infra.llm.provider.factory import build_chat, build_embeddings, build_rerank`。删除旧的 `ChatQwenWithReasoning`/`ReasoningPreservingChatQwen` 引用（Task 12 再删旧文件）。

- [ ] **Step 4: 处置 RAGAS 两处空值守卫**

`src/cli/eval_ragas.py:549-551` 与 `src/cli/eval_ragas_generate.py:331-335`：删掉 `if not settings.RAGAS_LLM_MODEL: … sys.exit(1)`，改为经 `get_judge_llm()` 解析潜规则（Task 10 后 `RAGAS_LLM_MODEL` 默认空，由 registry 兜底）。执行：

```bash
# eval_ragas.py: 删除 549-551 的守卫；563 行 llm_wrapper 的 judge 改：
#   judge_llm = get_judge_llm()
# eval_ragas_generate.py: 删除 331-335 守卫中的 exit；354 行改：
#   _langchain_llm = get_judge_llm(model=model or None)
```

- [ ] **Step 5: 更新既有测试断言**

`tests/test_models.py`、`tests/infra/test_reasoning_preserving.py` 中引用旧类/旧断言的用例，改指 `build_chat` / `ReasoningPreservingChat`。逐条：

```bash
.venv/bin/python -m pytest tests/test_models.py tests/infra/test_reasoning_preserving.py -v
```
Expected: 在改完断言后 passed。

- [ ] **Step 6: 跑 facade 测试**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_facade.py -v`
Expected: 2 passed。

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/cli/eval_ragas.py src/cli/eval_ragas_generate.py tests/infra/llm/provider/test_facade.py tests/test_models.py tests/infra/test_reasoning_preserving.py
git commit -m "refactor(provider-compat): models 门面薄委托 + 新增 get_judge_llm + RAGAS 空值守卫处置"
```

---

## Task 9: middleware + executor 接线（含 D8 显式修复）

**Files:**
- Modify: `src/agents/graph/middleware.py:77-91`（`ModelParamsMiddleware._settings`）
- Modify: `src/agents/skills/executor.py:404-438`（`_resolve_fork_llm`）
- Modify: `src/main.py`（lifespan 加启动校验）
- Test: `tests/agents/graph/test_loop_middleware.py`、`tests/agents/skills/test_skill_executor.py`

**Interfaces:**
- Consumes: `thinking_extra_body`（Task 3）、`resolve_role`（Task 6）、`validate_config`（Task 7）。
- Produces: 主链路每轮 `model_settings.extra_body` 为**完整** dict（含 preserve）；fork 子代理经 `build_chat("main", thinking=…)` 构造，同样完整。

- [ ] **Step 1: 写失败测试（主链路 preserve）**

在 `tests/agents/graph/test_loop_middleware.py` 加：

```python
def test_main_link_extra_body_includes_preserve_on_deep_thinking(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    from src.infra.llm.provider import factory
    monkeypatch.setattr(factory, "_REGISTRY", None)
    from src.agents.graph.middleware import ModelParamsMiddleware
    mw = ModelParamsMiddleware()

    class _Req:
        state = {"kb_id": "", "deep_thinking": True}
        model_settings = None
        def override(self, **kw):
            self.model_settings = kw["model_settings"]; return self

    mw._settings(_Req())  # type: ignore[arg-type]  # 触发出参
    # 断言 extra_body 同时含 enable_thinking 与 preserve_thinking
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py -k preserve -v`
Expected: FAIL（现实现只产 `{"enable_thinking": …}`）。

- [ ] **Step 3: 改 `middleware.py`**

```python
from src.infra.llm.provider.factory import _registry
from src.infra.llm.provider.spec import resolve_role
from src.infra.llm.provider.thinking import thinking_extra_body

    def _settings(self, request):
        ctx = current_request_ctx.get()
        kb_bound = ctx.kb_bound if ctx is not None else bool(request.state.get("kb_id"))
        deep_thinking = bool(request.state.get("deep_thinking"))
        spec = resolve_role("main", _registry()).provider
        extra_body = thinking_extra_body(spec, deep_thinking)
        if kb_bound:
            return {"extra_body": extra_body}
        return {"extra_body": extra_body, "temperature": settings.NON_KB_MAIN_TEMPERATURE}
```

- [ ] **Step 4: 改 `executor.py` 的 `_resolve_fork_llm`**

```python
        from src.infra.llm.provider.factory import build_chat
        ...
        if record_model is None and thinking is None:
            return self._main_llm
        return build_chat("main", thinking=thinking, model=record_model or self._main_model_name)
```

- [ ] **Step 5: 在 `main.py` lifespan 加启动校验（`configure_tracing()` 之前）**

```python
    from src.infra.llm.provider.factory import _registry
    from src.infra.llm.provider.spec import validate_config

    validate_config(_registry())
```

- [ ] **Step 6: 更新 fork 测试并跑**

```bash
.venv/bin/python -m pytest tests/agents/graph/test_loop_middleware.py tests/agents/skills/test_skill_executor.py -v
```
将 `test_skill_executor.py:282/315` 的 `{"enable_thinking": …}` 断言改为经 `build_chat` 后的形状（mock `build_chat` 或断言其 `extra_body`）。
Expected: passed。

- [ ] **Step 7: Commit**

```bash
git add src/agents/graph/middleware.py src/agents/skills/executor.py src/main.py tests/agents/graph/test_loop_middleware.py tests/agents/skills/test_skill_executor.py
git commit -m "fix(provider-compat): 主链路恢复 preserve_thinking（D8）+ middleware/executor 接线"
```

---

## Task 10: settings 指针 + 默认空 + 软迁移

**Files:**
- Modify: `src/config/settings.py`
- Modify: `.env.example`
- Test: `tests/infra/llm/provider/test_settings_migration.py`

**Interfaces:**
- Produces: `settings` 保留常量名、`LLM_MODEL` 等 6 个默认改 `""`；`LLM_BASE_URL`/`LLM_API_KEY` 保留读取但打 deprecation warning。

- [ ] **Step 1: 写失败测试**

```python
from src.config import settings


def test_model_defaults_are_empty():
    assert settings.EMBEDDING_MODEL == "" or settings.EMBEDDING_MODEL  # 空串表示未配置
```

> 真实断言改为：在无 `.env` 覆盖的测试进程里，`settings.LLM_MODEL == ""`（用 `monkeypatch.delenv` 隔离）。

- [ ] **Step 2: 改 `settings.py`**

将 6 个模型常量的默认改为 `""`：

```python
LLM_MODEL: str = os.getenv("LLM_MODEL", "")
CLASSIFY_MODEL: str = os.getenv("CLASSIFY_MODEL", "")
SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", LLM_MODEL)  # 保持"跟随 main"
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "")
RAGAS_LLM_MODEL: str = os.getenv("RAGAS_LLM_MODEL", "")
```

并在 `LLM_BASE_URL`/`LLM_API_KEY` 处加 deprecation warning（仅当被设置）：

```python
if os.getenv("LLM_BASE_URL") or os.getenv("LLM_API_KEY"):
    logger.warning("[config] LLM_BASE_URL/LLM_API_KEY 已废弃（Phase 2 删除），改用 <PROVIDER>_BASE_URL/<PROVIDER>_API_KEY")
```

- [ ] **Step 3: 跑全量 provider 测试**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider -v`
Expected: passed（defaults 改空后，registry 兜底生效）。

- [ ] **Step 4: Commit**

```bash
git add src/config/settings.py .env.example tests/infra/llm/provider/test_settings_migration.py
git commit -m "refactor(provider-compat): settings 模型默认改空 + provider 指针 + 软迁移"
```

---

## Task 11: seed CLI —— 纯函数判据 + registry 读取 + dry-run 诚实

**Files:**
- Modify: `src/cli/seed_langfuse_models.py`
- Test: `tests/cli/test_seed_decision.py`

**Interfaces:**
- Produces: `decide(model_name: str, input_price: float, output_price: float, existing: set[str]) -> str`（`"skipped"|"exists"|"created"`）。

- [ ] **Step 1: 写失败测试**

```python
from src.cli.seed_langfuse_models import decide


def test_zero_price_skips():
    assert decide("m", 0.0, 1.0, set()) == "skipped"


def test_existing_wins_over_create():
    assert decide("m", 1.0, 1.0, {"m"}) == "exists"


def test_new_creates():
    assert decide("m", 1.0, 1.0, set()) == "created"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/cli/test_seed_decision.py -v`
Expected: FAIL。

- [ ] **Step 3: 抽纯函数并让 dry-run 与真跑共用**

```python
def decide(model_name: str, input_price: float, output_price: float, existing: set[str]) -> str:
    if input_price == 0.0 or output_price == 0.0:
        return "skipped"
    if model_name in existing:
        return "exists"
    return "created"
```

`seed()` 与 `run()` 都改为调用 `decide`；`--dry-run` 打印 `decide` 的结果（`将跳过（未配价）` / `已存在` / `将创建`），不再无条件打印"将写入"。定价来源改为遍历 `registry.catalog` 的有价模型。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/cli/test_seed_decision.py -v`
Expected: 3 passed。

- [ ] **Step 5: Commit**

```bash
git add src/cli/seed_langfuse_models.py tests/cli/test_seed_decision.py
git commit -m "fix(provider-compat): seed CLI 判据抽纯函数，dry-run 诚实化 + registry 定价"
```

---

## Task 12: 启动期幂等定价 ensure

**Files:**
- Create: `src/infra/llm/provider/pricing_seed.py`
- Modify: `src/main.py`（lifespan，`configure_tracing()` **之后**）
- Test: `tests/infra/llm/provider/test_pricing_seed.py`

**Interfaces:**
- Produces: `ensure_model_pricing(registry, *, client) -> None`（幂等；无价 WARNING；异常吞掉降级）。

- [ ] **Step 1: 写失败测试**

```python
from src.infra.llm.provider.pricing_seed import ensure_model_pricing


class _FakeModels:
    def __init__(self): self.created = []
    def list(self, page=1, limit=100):
        class R: data = []
        return R()
    def create(self, request): self.created.append(request)


def test_warns_when_no_price(caplog):
    from src.infra.llm.provider.spec import load_registry
    # 用一个 catalog 无价的 registry 断言不抛、且不创建零价定义
    ...
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_pricing_seed.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现**

复用 `seed_langfuse_models.decide`；对每个**被绑定角色**的模型，有价则 ensure 定义，无价记 WARNING；整体非阻塞（`LANGFUSE_ENABLE=false` 跳过，异常记 warning）。

```python
def ensure_model_pricing(registry, *, client) -> None:
    from src.config import settings
    if not settings.LANGFUSE_ENABLE:
        return
    from src.infra.llm.provider.spec import resolve_role
    from src.cli.seed_langfuse_models import decide, build_model_request  # 复用纯函数
    try:
        existing = {m.model_name for m in _paginate(client.api.models)}
        for role in ("main", "classify", "summary", "judge"):
            r = resolve_role(role, registry)
            facts = r.provider.catalog.get(r.model)
            price = facts.pricing if facts else None
            if not price:
                logger.warning("[provider] 模型 {} 无定价，成本将为 NULL", r.model)
                continue
            if decide(r.model, price[0], price[1], existing) == "created":
                client.api.models.create(CreateModelRequest(**build_model_request(r.model, price[0], price[1])))
    except Exception as e:
        logger.warning("[provider] 启动期定价 ensure 降级: {}", e)
```

`main.py` lifespan 在 `configure_tracing()` 之后加：

```python
    from src.infra.llm.provider.pricing_seed import ensure_model_pricing
    from src.langfuse_singleton_or_client import ...  # 用 configure 出来的 client
    await asyncio.to_thread(ensure_model_pricing, _registry(), client=<configured client>)
```

> 注意：**不得**在 async lifespan 里直接 `asyncio.run`（嵌套事件循环）；用 `asyncio.to_thread` 或复用 async 客户端。

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_pricing_seed.py -v`
Expected: passed。

- [ ] **Step 5: Commit**

```bash
git add src/infra/llm/provider/pricing_seed.py src/main.py tests/infra/llm/provider/test_pricing_seed.py
git commit -m "feat(provider-compat): 启动期幂等定价 ensure（非阻塞）"
```

---

## Task 13: embedding 维度常量 + prompt/models 常量经层

**Files:**
- Modify: `src/infra/db/models/chunk.py:52`
- Modify: `src/rag/prompt.py:188`
- Test: `tests/infra/llm/provider/test_dimension_consistency.py`

**Interfaces:**
- Produces: `EMBEDDING_DIMENSION` 单一常量（`src/infra/db/models/chunk.py`）；启动校验"registry 声明维度 == 该常量"。

- [ ] **Step 1: 写失败测试**

```python
from src.infra.db.models.chunk import EMBEDDING_DIMENSION
from src.infra.llm.provider.spec import load_registry, resolve_role


def test_registry_dimension_matches_column():
    reg = load_registry()
    r = resolve_role("embedding", reg, {"EMBEDDING_PROVIDER": "dashscope"})
    assert r.provider.catalog[r.model].embedding_dimension == EMBEDDING_DIMENSION
```

- [ ] **Step 2: 改 `chunk.py`**

```python
EMBEDDING_DIMENSION = 1024
# ...
        Vector(EMBEDDING_DIMENSION), nullable=True, comment="DashScope text-embedding"
```

- [ ] **Step 3: 改 `prompt.py:188`**

`PROMPT_TOKENS_PER_CJK_CHAR` 改为经层取（`resolve_role("main", _registry()).provider.catalog[main_model].tokens_per_cjk_char or 0.6`）。

- [ ] **Step 4: 跑测试**

Run: `.venv/bin/python -m pytest tests/infra/llm/provider/test_dimension_consistency.py -v`
Expected: passed。

- [ ] **Step 5: Commit**

```bash
git add src/infra/db/models/chunk.py src/rag/prompt.py tests/infra/llm/provider/test_dimension_consistency.py
git commit -m "refactor(provider-compat): embedding 维度常量 + tokenizer 系数经层取"
```

---

## Task 14: 删除旧类、陈旧 docstring、ADR-0021、glossary、.env.example

**Files:**
- Delete: `src/infra/llm/reasoning_chat.py`（其功能已迁到 `provider/chat.py`）
- Modify: `src/models.py`（删旧 import）
- Modify: `src/agents/graph/state.py:92`（陈旧 query_router 注释）
- Create: `docs/adr/0021-provider-compat-layer.md`
- Modify: `docs/adr/README.md`（登记 0021）
- Modify: `docs/agents/glossary.md`
- Modify: `.env.example`

**Interfaces:** 无新增运行时接口。

- [ ] **Step 1: 删除旧类并修正引用**

```bash
git rm src/infra/llm/reasoning_chat.py
# 全仓确认无残留 import：
git grep -n "reasoning_chat\|ReasoningPreservingChatQwen\|ChatQwenWithReasoning"
```
Expected: 仅剩测试（一并改为 `provider.chat`）。

- [ ] **Step 2: 陈旧 docstring**

`src/models.py:181` 的 `get_classify_llm` docstring 去掉"意图/缺失维度判定（QueryRouter）"；`src/agents/graph/state.py:92` 去掉 `nodes/query_router`。

- [ ] **Step 3: ADR-0021**

写 `docs/adr/0021-provider-compat-layer.md`（引入 provider 兼容层、拒绝网关，给 ADR-0020 反向指针），并在 `docs/adr/README.md` 索引表登记。

- [ ] **Step 4: 跑全量 provider 测试 + 冒烟**

```bash
.venv/bin/python -m pytest tests/infra/llm/provider tests/agents/graph/test_loop_middleware.py tests/agents/skills/test_skill_executor.py tests/test_models.py -v
```
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add -A -- src/infra/llm src/models.py src/agents/graph/state.py docs/adr docs/agents/glossary.md .env.example
git commit -m "chore(provider-compat): 删旧类 + 陈旧 docstring + ADR-0021 + glossary"
```

---

## Part B — Phase 2：切换到 DeepSeek（运维「计划外动作」，本机一条龙）

> **执行器注意**：本段**不是 TDD 任务**，是**运维动作**，按 B0→B5 顺序做。**两个显式人工闸门（G1 / G2）** 处必须停下等用户确认 —— 这是本 plan 唯一的"打断点"。
> **前置**：Part A 的 Task 1–14 全部完成、`.venv/bin/python -m pytest tests/infra/llm/provider -q` 全绿、已提交。

### B0. 前置核对（Important 1：三 key 是否相等）

- [x] 读本机 `.env` 的三个 key：

```bash
grep -E '^(LLM_API_KEY|EMBEDDING_API_KEY|RERANK_API_KEY|DASHSCOPE_API_KEY)=' .env
```

- **判定**：若 `LLM_API_KEY` / `EMBEDDING_API_KEY` / `RERANK_API_KEY` **存在且互不相等** → 本段**保留** `<ROLE>_API_KEY` 覆盖，**不要删**（收敛会鉴权失败）；若三者相等（或都未设、只靠 `DASHSCOPE_API_KEY`）→ 可安全收敛。

### B1. registry 加 `deepseek`（数据 + 一个测试）

**Files:** Modify `src/infra/llm/provider/registry.yaml`；Create `tests/infra/llm/provider/test_deepseek_entry.py`

- [x] 在 `providers:` 下追加：

```yaml
  deepseek:
    base_url: https://api.deepseek.com
    api_key_env: DEEPSEEK_API_KEY
    thinking_style: deepseek
    reasoning_field: reasoning_content
    capabilities:
      tools: true
    models:
      main: deepseek-flash
      classify: deepseek-flash
      summary: deepseek-flash
      judge: deepseek-v4-pro
    # 无 embedding / rerank 角色 ⇒ 这两类不可绑 deepseek（启动校验拦截）
    catalog:
      deepseek-flash:
        context_window: 1000000
        pricing: { input: 0.00000015, output: 0.0000006 }  # 闲时价；峰时为 2×
        tokens_per_cjk_char: 0.6
      deepseek-v4-pro:
        context_window: 1000000
        pricing: { input: 0.00000066, output: 0.00000198 }
```

- [x] 写测试 `tests/infra/llm/provider/test_deepseek_entry.py`：

```python
from src.infra.llm.provider.spec import load_registry, resolve_role

REG = load_registry()


def test_deepseek_serves_main_family():
    for role in ("main", "classify", "summary", "judge"):
        r = resolve_role(role, REG, {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"})
        assert r.provider.name == "deepseek"


def test_embedding_rerank_judge_stay_dashscope():
    env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "k"}
    for role in ("embedding", "rerank", "judge"):
        assert resolve_role(role, REG, env).provider.name == "dashscope"
```

> ⚠️ 注意：`judge` 在 `test_embedding_rerank_judge_stay_dashscope` 里既被断言留在 dashscope（独立缺省），B1 又给 deepseek 配了 `models.judge` —— 这不矛盾：`judge` 缺省 dashscope，但**可**由 `RAGAS_PROVIDER=deepseek` 显式绑到 deepseek（故 registry 提供该角色模型是合理的）。

- [x] 跑：`.venv/bin/python -m pytest tests/infra/llm/provider/test_deepseek_entry.py -v` → **Expected: 2 passed**
- [x] 提交：

```bash
git add src/infra/llm/provider/registry.yaml tests/infra/llm/provider/test_deepseek_entry.py
git commit -m "feat(provider-compat): 注册 deepseek provider（Phase 2）"
```

### B2. 改本机 `.env`（＝ 修改活系统读的配置）

> ⚠️ 本 worktree 的 `.env` 是**指向宿主 repo 的 symlink** → 改的就是**宿主 `.env`**，也就是**本机在跑的服务**读的那份。这一步就是"切换活系统"。

- [x] 设 `LLM_PROVIDER=deepseek`。
- [x] 加 `DEEPSEEK_API_KEY=sk-...`（真实 key）——**若本地无真实 key，则 G1 闸门处必须先向用户取 key**。
- [x] 保留 `DASHSCOPE_API_KEY`（embedding / rerank / judge 仍用它）。
- [x] 按 B0 判定：可收敛则删 `LLM_BASE_URL` / `LLM_API_KEY`；否则保留。
- [x] （可选）清空 `LLM_MODEL` → 用 registry 默认 `deepseek-flash`。

### G1 ⛔ 人工闸门：确认切换到活系统

- [x] **停下**：向用户确认"是否现在把本机在跑的服务切到 DeepSeek"。**用户明确同意后**才继续 B3。

### B3. 重建容器

- [x] 重建并等启动：

```bash
docker compose up -d --force-recreate app
```

- [x] 等待启动完成（**约 2 分钟**：`prompt validated` → `startup complete`；勿只等 40s）。

```bash
docker compose logs --tail=80 app
```

- [x] 确认日志**无** `provider` 启动校验错误、无缺密钥错误、无 `thinking_style` 报错。

### B4. 冒烟（三场景 + 成本）

- [x] 非思考档：一次普通 KB 问答（不勾深度思考）→ 正常回话。
- [x] 思考档：一次勾深度思考的问答 → 正常回话（DeepSeek `thinking.type=enabled`）。
- [x] 工具调用多轮：一次触发检索/多轮的问答 → 无 400（`reasoning_content` 回传完整）。
- [x] Langfuse：确认 `deepseek-flash` 成本列**非 NULL**（启动期 ensure 生效）。
- [x] 站点入口 + 三台企微机器人回话正常。

### G2 ⛔ 人工闸门：确认机器人回话

- [x] **停下**：请用户观察三台企微机器人 / 站点回话，确认**无统一脱敏错误文案**。

### B5. 记录

- [x] 更新 memory：本机 `.env` 已切 `deepseek`（`LLM_PROVIDER` + `DEEPSEEK_API_KEY`）；`--force-recreate` 生效；Phase 2 完成。

---

## Part C — Phase 3（边界声明，**不执行**）

以下为**延后项**，本 plan **不执行、无步骤**（写出来会是 placeholder）。出现真实 provider 需求时**另立 plan**：

- 增加更多 provider（只需「加一条 registry 数据」）。
- 启用 `vision` / `thinking_style:none` / `supports_disable_thinking`（当前无真实消费者，YAGNI 延后）。
- `reasoning_effort` 归一化（本仓暂只用 on/off）。
- `LLM_KWARGS` blob 废弃（本 change 只对含 `extra_body` 的情形告警）。

---

## Self-Review

**1. Spec coverage（provider-compat / model-config / llm-tracing 的 SHALL 条目 → task）：**

| Requirement | Task |
|---|---|
| Provider 注册表 | Task 2 |
| 思考参数归一化 | Task 3 |
| 思考开关始终显式 | Task 3 + Task 5（`thinking` 显式传 `thinking_extra_body`） |
| 角色解析与缺省（含 judge 独立） | Task 6 |
| reasoning 回传按能力驱动 | Task 4 |
| 配置覆盖规则 | Task 6 + Task 10 |
| 启动期校验 fail-fast | Task 7 + Task 9（lifespan 接线） |
| 三类模型创建（含 get_judge_llm） | Task 5 + Task 8 |
| Embedding 维度 | Task 13 |
| Rerank 创建 | Task 5 |
| 配置默认值 | Task 10 |
| 模型定价幂等播种 | Task 11 + Task 12 |

**2. Placeholder scan：** 已逐条给出代码；Task 9 的 `test_main_link_extra_body_includes_preserve_on_deep_thinking` 末行断言需在执行时按 `_Req.override` 实际签名写实（标注了 `# 断言…`，属实现期需据代码补全的最小缺口）。**执行者须先读 `ModelParamsMiddleware._settings` 的真实签名再补该断言。**

**3. Type consistency：** `thinking_extra_body(spec, enabled)`、`resolve_role(role, registry, env=None)`、`build_chat(role, *, thinking=None, model=None, **overrides)`、`decide(model_name, input, output, existing)`、`ReasoningPreservingChat(reasoning_field=...)` 在 Task 3/4/5/6/11 间签名一致。

**4. Phase 覆盖：** Part A（Task 1–14）＝ Phase 1；Part B（B0–B5）＝ Phase 2（运维，含 G1/G2 人工闸门）；Part C ＝ Phase 3 边界（**不执行**）。三部分**连续推进**，仅在 G1/G2 停下。Phase 3 明细见 `design.md` Migration Plan。
