# DeepEval Synthesizer v6 Testset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 DeepEval `Synthesizer` 从 KB 分块生成一份质量受检、语言一致、可审计的 v6 题集，并让评测报告能自证使用的题集版本。

**Architecture:** 从 `VectorStore` 读现有分块，按 `(doc_id, chunk_index)` 每 2 块成组并前置 `【公司 · 报告期】` 锚点；每个上下文单独调用 `a_generate_goldens_from_contexts`（外层 `gather(return_exceptions=True)` 隔离单点失败）；产出经自足/完整句/语言/去重/多跳五道确定性门后原子写入 `data/deepeval/testset/testset_<kb>_v6.json`。评测侧只新增 `testset_version`，不动指标与评分口径。

**Tech Stack:** Python 3.11+ / DeepEval 4.2.9 `Synthesizer` / LangChain `ChatOpenAI` / Rich `Progress` / pytest / argparse

**Spec:** `docs/openspec/changes/deepeval-synthesizer-testset/proposal.md`、`design.md`、`specs/testset-generation-deepeval/spec.md`、`specs/evaluation-pipeline/spec.md`、`tasks.md`

## Global Constraints

- 语言门：整体 CJK 占比 `>= 0.85`；句级（字母数 `>= 5`）`>= 0.5`；任一句「拉丁 ≥5 且 CJK=0」直接失败；分母 = CJK + 拉丁字母，0 分母判失败。
- 完整句门：长度 `>= 8`、以 `。！？.!?` 结尾、非纯数字标点；`numeric_risk` 时定位「后接单位/百分号/金额词」的数字，跳过日期与期间，要求数字前有主体词且整句含指标词。
- 多跳门：`Multi-context` = `multi_hop`，其余 `single_hop`；`multi_hop / total <= 1/3`；超出按 `sha1(user_input)` 升序截断。
- 题量：通过所有门后 `< 15` 以 `goldens_per_context=2` 重跑一次；仍不足失败且不写文件。
- 版本与语料：显式 `--version` 必须同时给 `--docs`；`--docs` 每一项必须匹配 ≥1 chunk。
- 并发：`max_concurrent=4`；每个 context 单独 `await a_generate_goldens_from_contexts([ctx], _reset_cost=False, _progress=..., _pbar_id=...)`；`a_generate_goldens_from_contexts` **没有** `_send_data` 参数。
- 重试：3 次重试（共 4 次尝试），只重试 `openai.APIStatusError` 的 429/5xx、`openai.APITimeoutError`、`openai.APIConnectionError`；async 用 `await asyncio.sleep`。
- 日志：`LOG_PREFIXES` 增加 `synth`；只登记 `info/warning` 事件（`_ALLOWED_LEVELS` 不含 debug）。
- 所有函数写 docstring；不用三元表达式；新增常量进 `src/config/`；测试不发起真实网络调用；只 `ruff format` 本任务改的 py 文件。
- 每个任务结束必须提交；不要全仓 `ruff format .`。

---

## Worktree 准备

`.env`、`data/`、`.venv` 都是 gitignored，worktree 不会继承；开工前先链接，否则 DB/报告目录不可用、pre-commit 会报 `.venv/bin/python not found`。

```bash
cd /root/code/corporate_rag-synth-v6
test -e .env || ln -s /root/code/corporate_rag/.env .env
test -e data || ln -s /root/code/corporate_rag/data data
test -e .venv || ln -s /root/code/corporate_rag/.venv .venv
```

---

## File Structure

**新建**
- `src/infra/llm/provider/deepeval_synth_llm.py` — schema 适配 + 传输层重试（继承 `BridgeDeepEvalLLM`）。
- `src/cli/synth_contexts.py` — 分块读取、分组、公司/报告期锚点、别名。
- `src/cli/synth_gates.py` — 语言/完整句/自足/去重/多跳五道门。
- `src/cli/synth_writer.py` — Golden→sample、元数据、原子写。
- `src/cli/testset_synth.py` — 编排、CLI、早停与验收。
- `tests/infra/llm/provider/test_deepeval_synth_llm.py`
- `tests/cli/test_synth_contexts.py`
- `tests/cli/test_synth_gates.py`
- `tests/cli/test_synth_writer.py`
- `tests/cli/test_testset_synth.py`

**修改**
- `src/config/settings.py`、`src/config/const.py`
- `src/cli/testset_io.py`（`_scan_versions` / `resolve_testset_version` / `next_testset_version`）
- `src/cli/eval_deepeval.py`（`build_report_meta` / `save_markdown_report` / `_build_eval_detail` / `_save_eval_report` / `main` / `--generate`）
- `src/config/response_codes.py`（`Code.GONE_MSG`）
- `src/core/log_events.py`、`src/core/log_event_specs.py`
- `.env.example`、`.env.template`
- `src/cli/README.md`、`docs/agents/code-map.md`、`api_contract.md`、`glossary.md`、`logging-rules.md`、`requirements_pool.md`、`eval-ledger.md`

---

## Task 1: 配置常量与合成适配器

**Files:**
- Modify: `src/config/settings.py`（`EVAL_METRIC_MAX_ATTEMPTS` 之后）
- Modify: `src/config/const.py`（`EVAL_PASS_THRESHOLD` 之后）
- Create: `src/infra/llm/provider/deepeval_synth_llm.py`
- Test: `tests/infra/llm/provider/test_deepeval_synth_llm.py`
- Modify: `.env.example`、`.env.template`

**Interfaces:**
- Produces: `SynthDeepEvalLLM(chat)`，`generate(prompt, schema=None) -> str | BaseModel`，`a_generate(prompt, schema=None) -> str | BaseModel`
- Produces constants: `EVAL_LANG_CJK_MIN_RATIO`、`EVAL_LANG_SENTENCE_CJK_MIN_RATIO`、`EVAL_HOP_MULTI_MAX_RATIO`、`EVAL_SYNTH_MIN_SAMPLES`、`EVAL_SYNTH_EXPECTED_TOOLS`、`EVAL_SYNTH_EVOLUTION_WEIGHTS`、`EVAL_NUMERIC_METRIC_WORDS`、`EVAL_SYNTH_SUBJECT_WORDS`
- Produces settings: `EVAL_SYNTH_CHUNKS_PER_CONTEXT`、`EVAL_SYNTH_GOLDENS_PER_CONTEXT`、`EVAL_SYNTH_MAX_CONCURRENT`

- [ ] **Step 1: 写失败测试**

```python
# tests/infra/llm/provider/test_deepeval_synth_llm.py
"""出题适配器：schema 解析 + openai 异常重试。"""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest
from deepeval.synthesizer.schema import Response, SyntheticData
from langchain_core.messages import AIMessage

from src.infra.llm.provider.deepeval_synth_llm import SynthDeepEvalLLM


def _rate_limit(retry_after: str = "12") -> openai.RateLimitError:
    """构造带 Retry-After 的真实 openai 429 异常。"""
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(
        429, request=request, headers={"retry-after": retry_after}
    )
    return openai.RateLimitError("rate limited", response=response, body=None)


class _FlakyChat:
    """前 failures 次抛指定异常，之后返回固定 JSON。"""

    model_name = "fake-judge"

    def __init__(self, error: Exception, failures: int = 1) -> None:
        self._error = error
        self._failures = failures
        self.calls = 0

    def invoke(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return AIMessage(content='{"response": "ok"}')

    async def ainvoke(self, prompt: str, **kwargs):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return AIMessage(content='{"response": "ok"}')


class _JsonChat:
    """返回可被 schema 解析的 JSON。"""

    model_name = "fake-judge"

    def __init__(self, content: str) -> None:
        self._content = content

    def invoke(self, prompt: str, **kwargs):
        return AIMessage(content=self._content)

    async def ainvoke(self, prompt: str, **kwargs):
        return AIMessage(content=self._content)


def test_generate_without_schema_returns_text():
    llm = SynthDeepEvalLLM(_FlakyChat(RuntimeError(), failures=0))
    assert llm.generate("prompt") == '{"response": "ok"}'


def test_generate_response_schema_has_response_attribute():
    llm = SynthDeepEvalLLM(_FlakyChat(RuntimeError(), failures=0))
    result = llm.generate("prompt", schema=Response)
    assert isinstance(result, Response)
    assert result.response == '{"response": "ok"}'


def test_generate_other_schema_returns_model_instance():
    llm = SynthDeepEvalLLM(_JsonChat('{"input": "问题"}'))
    result = llm.generate("prompt", schema=SyntheticData)
    assert isinstance(result, SyntheticData)
    assert result.input == "问题"


def test_invalid_json_raises_value_error_not_type_error():
    llm = SynthDeepEvalLLM(_JsonChat("not json at all"))
    with pytest.raises(ValueError):
        llm.generate("prompt", schema=SyntheticData)


def test_rate_limit_is_retried_with_retry_after(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(
        "src.infra.llm.provider.deepeval_synth_llm.time.sleep",
        lambda seconds: slept.append(seconds),
    )
    llm = SynthDeepEvalLLM(_FlakyChat(_rate_limit("12")))
    assert llm.generate("prompt") == '{"response": "ok"}'
    assert slept == [12.0]


def test_async_rate_limit_is_retried(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(
        "src.infra.llm.provider.deepeval_synth_llm.asyncio.sleep", fake_sleep
    )
    llm = SynthDeepEvalLLM(_FlakyChat(_rate_limit("3")))
    result = asyncio.run(llm.a_generate("prompt"))
    assert result == '{"response": "ok"}'
    assert slept == [3.0]


def test_non_rate_limit_4xx_is_not_retried():
    request = httpx.Request("POST", "https://example.com/v1/chat/completions")
    response = httpx.Response(404, request=request)
    error = openai.NotFoundError("not found", response=response, body=None)
    chat = _FlakyChat(error)
    llm = SynthDeepEvalLLM(chat)
    with pytest.raises(openai.NotFoundError):
        llm.generate("prompt")
    assert chat.calls == 1


def test_adapter_uses_passed_chat_not_default_model():
    chat = _FlakyChat(RuntimeError(), failures=0)
    llm = SynthDeepEvalLLM(chat)
    assert llm.model is chat
    assert llm.get_model_name() == "fake-judge"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_deepeval_synth_llm.py -q
```

Expected: `ModuleNotFoundError: src.infra.llm.provider.deepeval_synth_llm`.

- [ ] **Step 3: 加配置常量**

`src/config/settings.py` 在 `EVAL_METRIC_MAX_ATTEMPTS` 之后追加：

```python
# v6 出题：每个上下文包含的连续分块数
EVAL_SYNTH_CHUNKS_PER_CONTEXT: int = int(
    os.getenv("EVAL_SYNTH_CHUNKS_PER_CONTEXT", "2")
)
# v6 出题：每个上下文生成多少条题（低产出时升到 2）
EVAL_SYNTH_GOLDENS_PER_CONTEXT: int = int(
    os.getenv("EVAL_SYNTH_GOLDENS_PER_CONTEXT", "1")
)
# v6 出题：并发上下文数（默认 100 会打出 429 风暴）
EVAL_SYNTH_MAX_CONCURRENT: int = int(
    os.getenv("EVAL_SYNTH_MAX_CONCURRENT", "4")
)
```

`.env.example` / `.env.template` 追加：

```dotenv
EVAL_SYNTH_CHUNKS_PER_CONTEXT=2
EVAL_SYNTH_GOLDENS_PER_CONTEXT=1
EVAL_SYNTH_MAX_CONCURRENT=4
```

`src/config/const.py` 在 `EVAL_PASS_THRESHOLD` 之后追加：

```python
# ── v6 出题（change: deepeval-synthesizer-testset） ──
# 语言门：整体与句级的 CJK 占比下限
EVAL_LANG_CJK_MIN_RATIO: float = 0.85
EVAL_LANG_SENTENCE_CJK_MIN_RATIO: float = 0.5
# 多跳门：Multi-context 占比上限
EVAL_HOP_MULTI_MAX_RATIO: float = 1 / 3
# 题量下限：低于此值先升 goldens 重跑一次，仍不足则失败
EVAL_SYNTH_MIN_SAMPLES: int = 15
# 每题期望工具（一次 retrieve_kb 返回 top-k，多跳不重复标）
EVAL_SYNTH_EXPECTED_TOOLS: tuple[str, ...] = ("retrieve_kb",)
# evolution 权重：num_evolutions=1，键为 deepeval 的 Evolution.value
EVAL_SYNTH_EVOLUTION_WEIGHTS: dict[str, float] = {
    "Multi-context": 0.28,
    "Reasoning": 0.12,
    "Concretizing": 0.12,
    "Constrained": 0.12,
    "Comparative": 0.12,
    "Hypothetical": 0.12,
    "In-Breadth": 0.12,
}
# 数值型答案的指标词表（主体检查用）
EVAL_NUMERIC_METRIC_WORDS: tuple[str, ...] = (
    "营业收入",
    "收入",
    "营收",
    "营业额",
    "净利润",
    "归母净利润",
    "毛利",
    "毛利率",
    "经营盈利",
    "利润总额",
    "资产",
    "负债",
    "现金流",
    "资本开支",
    "研发支出",
    "持股比例",
    "持股数量",
    "每股收益",
)
# 数值型答案的主体词表
EVAL_SYNTH_SUBJECT_WORDS: tuple[str, ...] = (
    "公司",
    "集团",
    "本集团",
    "母公司",
    "股东",
)
# 适配器重试：3 次重试（共 4 次尝试）
SYNTH_RETRY_MAX_ATTEMPTS: int = 3
SYNTH_RETRY_BASE_DELAY: float = 0.5
SYNTH_RETRY_MAX_DELAY: float = 8.0
SYNTH_RETRY_AFTER_MAX: float = 30.0
```

- [ ] **Step 4: 实现适配器**

创建 `src/infra/llm/provider/deepeval_synth_llm.py`：

```python
"""出题用 DeepEval LLM 适配器：schema 解析 + 传输层重试。

为什么不能直接用 `BridgeDeepEvalLLM`：Synthesizer 对非原生模型执行
`res = model.generate(prompt, schema=Response); return res.response`，
而父类把 `schema` 吞进 `**kwargs` 并返回 `str` ⇒ `AttributeError`。
本类只覆盖 schema 分支与重试，指标路径仍走父类。
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel

# 必须先 import settings：它在 deepeval 之前设置 DEEPEVAL_DISABLE_DOTENV /
# DEEPEVAL_TELEMETRY_OPT_OUT，避免框架抢先加载 .env 与遥测。
from src.config import settings  # noqa: F401
from src.config.const import (
    SYNTH_RETRY_AFTER_MAX,
    SYNTH_RETRY_BASE_DELAY,
    SYNTH_RETRY_MAX_ATTEMPTS,
    SYNTH_RETRY_MAX_DELAY,
)
from src.infra.llm.provider.deepeval_llm import BridgeDeepEvalLLM, _truncate_prompt

from deepeval.metrics.utils.generation import trimAndLoadJson
from deepeval.synthesizer.schema import Response
from httpx import TimeoutException, TransportError


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否属于可重试的传输/限流失败。

    Args:
        exc: 单次调用抛出的异常

    Returns:
        True 表示 429/5xx/超时/连接失败
    """
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        if code == 429:
            return True
        if 500 <= code < 600:
            return True
        return False
    if isinstance(exc, (TimeoutException, TransportError)):
        return True
    return False


def _retry_delay(exc: BaseException, attempt: int) -> float:
    """计算第 attempt 次失败后的等待秒数。

    Args:
        exc: 本次异常
        attempt: 1-based 尝试序号

    Returns:
        优先 Retry-After（上限 30s），否则指数退避（上限 8s）
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    if raw is not None:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            delay = None
        if delay is not None and math.isfinite(delay) and delay >= 0:
            return min(delay, SYNTH_RETRY_AFTER_MAX)
    return min(SYNTH_RETRY_BASE_DELAY * (2 ** (attempt - 1)), SYNTH_RETRY_MAX_DELAY)


class SynthDeepEvalLLM(BridgeDeepEvalLLM):
    """给 Synthesizer 用的适配器：支持 schema，并对传输失败做有界重试。"""

    def _coerce(self, text: str, schema: type[BaseModel] | None) -> Any:
        """把文本按 schema 转成 DeepEval 期望的对象。

        Args:
            text: 裁判返回的原始文本
            schema: None / Response / 其它 Pydantic 模型

        Returns:
            str 或 Pydantic 实例

        Raises:
            ValueError: JSON 无法解析或字段不匹配
        """
        if schema is None:
            return text
        if schema is Response:
            return Response(response=text)
        return schema(**trimAndLoadJson(text))

    def _invoke(self, prompt: str) -> str:
        """同步调用并在可重试异常上退避。

        Args:
            prompt: 已截断的提示词

        Returns:
            模型文本

        Raises:
            最后一次失败的原异常
        """
        for attempt in range(1, SYNTH_RETRY_MAX_ATTEMPTS + 2):
            try:
                return self._content(self._chat.invoke(prompt))
            except Exception as exc:  # noqa: BLE001
                if attempt > SYNTH_RETRY_MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                time.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("unreachable")

    async def _ainvoke(self, prompt: str) -> str:
        """异步调用并在可重试异常上退避（不阻塞事件循环）。

        Args:
            prompt: 已截断的提示词

        Returns:
            模型文本

        Raises:
            最后一次失败的原异常
        """
        for attempt in range(1, SYNTH_RETRY_MAX_ATTEMPTS + 2):
            try:
                return self._content(await self._chat.ainvoke(prompt))
            except Exception as exc:  # noqa: BLE001
                if attempt > SYNTH_RETRY_MAX_ATTEMPTS or not _is_retryable(exc):
                    raise
                await asyncio.sleep(_retry_delay(exc, attempt))
        raise RuntimeError("unreachable")

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        """同步生成；schema 非空时返回 Pydantic 实例。"""
        return self._coerce(self._invoke(_truncate_prompt(prompt)), schema)

    async def a_generate(
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> Any:
        """异步生成；schema 非空时返回 Pydantic 实例。"""
        return self._coerce(await self._ainvoke(_truncate_prompt(prompt)), schema)
```

- [ ] **Step 5: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/infra/llm/provider/test_deepeval_synth_llm.py -q
```

Expected: `8 passed`.

- [ ] **Step 6: 格式化并提交**

```bash
ruff format src/config/settings.py src/config/const.py src/infra/llm/provider/deepeval_synth_llm.py tests/infra/llm/provider/test_deepeval_synth_llm.py
ruff check src/infra/llm/provider/deepeval_synth_llm.py tests/infra/llm/provider/test_deepeval_synth_llm.py
git add src/config/settings.py src/config/const.py src/infra/llm/provider/deepeval_synth_llm.py tests/infra/llm/provider/test_deepeval_synth_llm.py .env.example .env.template
git commit -m "feat(synth): add schema-aware adapter with bounded retry"
```

---

## Task 2: 分块与上下文构造

**Files:**
- Create: `src/cli/synth_contexts.py`
- Test: `tests/cli/test_synth_contexts.py`

**Interfaces:**
- Consumes: `ChunkResult`（`src/infra/db/vector_store/types.py`）
- Produces: `ContextGroup`（`chunks` / `source` / `doc_id` / `company` / `period` / `aliases` / `company_source`）
- Produces: `load_chunks(kb_id, docs) -> list[ChunkResult]`（async）
- Produces: `build_contexts(chunks, size) -> list[ContextGroup]`
- Produces: `render_context(group) -> list[str]`
- Produces: `company_aliases(name, raw_company) -> set[str]`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_synth_contexts.py
"""v6 上下文构造：分组、公司锚点、报告期与别名。"""

from __future__ import annotations

import pytest

from src.cli.synth_contexts import (
    build_contexts,
    company_aliases,
    derive_company,
    derive_period,
    render_context,
)
from src.infra.db.vector_store.types import ChunkResult


def _chunk(
    doc_id: str,
    source: str,
    index: int,
    *,
    company: str = "东软集团股份有限公司",
    period: str = "2025年第一季度",
    parent: str = "",
    content: str = "",
) -> ChunkResult:
    """构造最小 ChunkResult。"""
    return ChunkResult(
        id=f"{doc_id}:{index}",
        content=content or f"正文{index}",
        metadata={
            "doc_id": doc_id,
            "source": source,
            "chunk_index": index,
            "company": company,
            "report_period": period,
            "parent_content": parent,
            "year": "2024",
        },
    )


def test_grouping_drops_trailing_singleton():
    chunks = [_chunk("d1", "a.pdf", i) for i in range(13)]
    groups = build_contexts(chunks, size=2)
    assert len(groups) == 6
    used = [c.metadata["chunk_index"] for g in groups for c in g.chunks]
    assert 12 not in used
    assert len(used) == 12


def test_groups_are_keyed_by_doc_id_not_source():
    chunks = [
        _chunk("d1", "same.pdf", 0, company="甲公司股份有限公司"),
        _chunk("d1", "same.pdf", 1, company="甲公司股份有限公司"),
        _chunk("d2", "same.pdf", 0, company="乙公司股份有限公司"),
        _chunk("d2", "same.pdf", 1, company="乙公司股份有限公司"),
    ]
    groups = build_contexts(chunks, size=2)
    assert len(groups) == 2
    assert {g.doc_id for g in groups} == {"d1", "d2"}


def test_tencent_company_is_derived_from_any_chunk_in_document():
    chunks = []
    for i in range(38):
        parent = (
            "腾讯控股有限公司（港交所代码：00700）公布年度业绩"
            if i < 5
            else "腾讯公布二零二四年年度业绩"
        )
        chunks.append(
            _chunk(
                "d-tencent",
                "tencent_2024_annual.pdf",
                i,
                company="tencent",
                period="",
                parent=parent,
            )
        )
    name, source_kind = derive_company(chunks)
    assert name == "腾讯控股有限公司"
    assert source_kind == "parent_content"
    groups = build_contexts(chunks, size=2)
    assert all(g.company == "腾讯控股有限公司" for g in groups)
    assert all(g.period == "2024年年度" for g in groups)


def test_narrative_prefix_is_stripped():
    chunks = [
        _chunk(
            "d1",
            "neusoft.pdf",
            0,
            company="",
            parent="本公司的控股股东东软集团股份有限公司持有股份",
        ),
        _chunk("d1", "neusoft.pdf", 1, company="", parent="同上"),
    ]
    name, _ = derive_company(chunks)
    assert name == "东软集团股份有限公司"


def test_missing_chinese_company_raises():
    chunks = [
        _chunk("d1", "x.pdf", 0, company="acme"),
        _chunk("d1", "x.pdf", 1, company="acme"),
    ]
    with pytest.raises(ValueError):
        derive_company(chunks)


def test_period_uses_year_and_suffix_when_report_period_missing():
    chunks = [
        _chunk("d1", "tencent_2024_annual.pdf", 0, period=""),
        _chunk("d1", "tencent_2024_annual.pdf", 1, period=""),
    ]
    assert derive_period(chunks) == "2024年年度"


def test_aliases_strip_longest_suffix_first():
    assert company_aliases("腾讯控股有限公司", "tencent") == {
        "腾讯控股有限公司",
        "腾讯",
        "tencent",
    }
    assert "东软" in company_aliases("东软集团股份有限公司", "")


def test_render_context_prefixes_header_only_once():
    chunks = [
        _chunk("d1", "a.pdf", 0, content="第一块"),
        _chunk("d1", "a.pdf", 1, content="第二块"),
    ]
    group = build_contexts(chunks, size=2)[0]
    rendered = render_context(group)
    assert rendered[0].startswith("【")
    assert "·" in rendered[0]
    assert not rendered[1].startswith("【")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_contexts.py -q
```

Expected: `ModuleNotFoundError: src.cli.synth_contexts`.

- [ ] **Step 3: 实现 `synth_contexts.py`**

```python
"""v6 出题：从 KB 分块构造上下文（分组 + 公司/报告期锚点）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.infra.db.vector_store.types import ChunkResult

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COMPANY_RE = re.compile(
    r"[\u4e00-\u9fff]{2,12}(?:股份有限公司|有限责任公司|有限公司)"
)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_PERIOD_RE = re.compile(r"(annual|q[1-4]|h[12])", re.IGNORECASE)
_PERIOD_SUFFIX = {
    "annual": "年年度",
    "q1": "年第一季度",
    "q2": "年第二季度",
    "q3": "年第三季度",
    "q4": "年第四季度",
    "h1": "年上半年",
    "h2": "年下半年",
}
_NARRATIVE_PREFIXES = (
    "控股股东",
    "本公司",
    "子公司",
    "母公司",
    "股东",
    "公司",
    "持有",
    "的",
)
_COMPANY_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "控股有限公司",
    "有限公司",
    "集团",
)


@dataclass(frozen=True)
class ContextGroup:
    """一个出题上下文（2 个连续分块）。"""

    chunks: list[ChunkResult]
    """按 chunk_index 升序的分块。"""
    source: str
    """文档文件名，用于 --docs 过滤与 source_file。"""
    doc_id: str
    """文档 UUID，分组键。"""
    company: str
    """中文公司全称，写入 header。"""
    period: str
    """报告期，写入 header。"""
    aliases: set[str] = field(default_factory=set)
    """公司别名（含 ASCII 形），供语言门与自足门。"""
    company_source: str = "metadata"
    """公司名来源：metadata / parent_content / content。"""


def _has_cjk(text: str) -> bool:
    """文本是否含中文。"""
    return bool(_CJK_RE.search(text))


def _strip_narrative_prefixes(name: str) -> str:
    """反复剥掉「本公司/控股股东/公司持有」等叙述前缀。

    Args:
        name: 正则命中的原始片段

    Returns:
        剥净后的公司名
    """
    changed = True
    while changed:
        changed = False
        for prefix in _NARRATIVE_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix) :]
                changed = True
                break
    return name


def derive_company(chunks_of_doc: list[ChunkResult]) -> tuple[str, str]:
    """按文档推导一次中文公司名。

    Args:
        chunks_of_doc: 同一 doc_id 的全部分块

    Returns:
        (中文全称, 来源标记)

    Raises:
        ValueError: 全部分块都推导不出中文公司名
    """
    raw = str(chunks_of_doc[0].metadata.get("company") or "").strip()
    if len(raw) >= 4 and _has_cjk(raw):
        return raw, "metadata"

    ordered = sorted(
        chunks_of_doc, key=lambda c: int(c.metadata.get("chunk_index", 0))
    )
    for field_name in ("parent_content", "content"):
        for chunk in ordered:
            text = str(chunk.metadata.get(field_name) or chunk.content or "")
            for match in _COMPANY_RE.finditer(text):
                name = _strip_narrative_prefixes(match.group(0))
                if len(name) >= 4:
                    return name, field_name
    raise ValueError(
        f"无法从 doc_id={ordered[0].metadata.get('doc_id')} 推导中文公司名"
    )


def derive_period(chunks_of_doc: list[ChunkResult]) -> str:
    """按文档推导报告期（必须含年份）。

    Args:
        chunks_of_doc: 同一 doc_id 的全部分块

    Returns:
        报告期字符串，如 `2024年年度`

    Raises:
        ValueError: 既无 report_period 也无法从 source 推出年份
    """
    first = chunks_of_doc[0]
    raw = str(first.metadata.get("report_period") or "").strip()
    if raw:
        return raw

    source = str(first.metadata.get("source") or "")
    year_match = _YEAR_RE.search(source)
    period_match = _PERIOD_RE.search(source)
    if year_match is not None:
        year = year_match.group(0)
        if period_match is not None:
            return f"{year}{_PERIOD_SUFFIX[period_match.group(1).lower()]}"
        return f"{year}年"

    report_type = str(first.metadata.get("report_type") or "").strip()
    quarter = str(first.metadata.get("quarter") or "").strip()
    if report_type or quarter:
        return f"{report_type}{quarter}"
    raise ValueError(f"无法从 {source} 推导报告期")


def company_aliases(name: str, raw_company: str) -> set[str]:
    """构造公司别名集合（全称 + 逐级去后缀 + ASCII 形）。

    Args:
        name: 中文全称
        raw_company: metadata 里的原始 company 值

    Returns:
        至少包含 `name` 的别名集合
    """
    aliases: set[str] = {name}
    current = name
    changed = True
    while changed:
        changed = False
        for suffix in _COMPANY_SUFFIXES:
            if current.endswith(suffix) and len(current) > len(suffix):
                current = current[: -len(suffix)]
                if len(current) >= 2:
                    aliases.add(current)
                changed = True
                break
    raw = raw_company.strip()
    if raw and raw.lower() != name.lower():
        aliases.add(raw)
    return aliases


async def load_chunks(kb_id: str, docs: list[str] | None) -> list[ChunkResult]:
    """读取 KB 分块；docs 非空时按 source 过滤。

    Args:
        kb_id: 知识库 UUID
        docs: 允许的 source 列表；None/空表示全部

    Returns:
        按 (doc_id, chunk_index) 升序的分块
    """
    from src.infra.db.vector_store import VectorStore

    chunks = await VectorStore().get_all_chunks(kb_id)
    if docs:
        wanted = set(docs)
        chunks = [c for c in chunks if c.metadata.get("source") in wanted]
    return sorted(
        chunks,
        key=lambda c: (
            str(c.metadata.get("doc_id") or ""),
            int(c.metadata.get("chunk_index", 0)),
        ),
    )


def build_contexts(
    chunks: list[ChunkResult], size: int
) -> list[ContextGroup]:
    """按 doc_id 分组，每 size 块一个上下文，丢弃末尾不足 size 的块。

    Args:
        chunks: 已排序分块
        size: 每组块数

    Returns:
        上下文列表
    """
    by_doc: dict[str, list[ChunkResult]] = {}
    for chunk in chunks:
        doc_id = str(chunk.metadata.get("doc_id") or "")
        by_doc.setdefault(doc_id, []).append(chunk)

    groups: list[ContextGroup] = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda c: int(c.metadata.get("chunk_index", 0)))
        if len(items) < size:
            continue
        company, company_source = derive_company(items)
        period = derive_period(items)
        raw_company = str(items[0].metadata.get("company") or "")
        aliases = company_aliases(company, raw_company)
        source = str(items[0].metadata.get("source") or "")
        for start in range(0, len(items) - size + 1, size):
            groups.append(
                ContextGroup(
                    chunks=items[start : start + size],
                    source=source,
                    doc_id=doc_id,
                    company=company,
                    period=period,
                    aliases=aliases,
                    company_source=company_source,
                )
            )
    return groups


def render_context(group: ContextGroup) -> list[str]:
    """把组内分块渲染成带锚点头的字符串列表。

    Args:
        group: 出题上下文

    Returns:
        首块带 `【公司 · 报告期】` 头，其余原样
    """
    texts = [chunk.content for chunk in group.chunks]
    if texts:
        texts[0] = f"【{group.company} · {group.period}】\n{texts[0]}"
    return texts
```

- [ ] **Step 4: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_contexts.py -q
```

Expected: `8 passed`.

- [ ] **Step 5: 格式化并提交**

```bash
ruff format src/cli/synth_contexts.py tests/cli/test_synth_contexts.py
git add src/cli/synth_contexts.py tests/cli/test_synth_contexts.py
git commit -m "feat(synth): build anchored contexts from KB chunks"
```

---

## Task 3: 出题编排（Synthesizer + 单点隔离 + 进度）

**Files:**
- Create: `src/cli/testset_synth.py`（本任务只放 `_synthesize` 与构造参数）
- Test: `tests/cli/test_testset_synth.py`

**Interfaces:**
- Consumes: `ContextGroup`、`render_context`（Task 2）；`SynthDeepEvalLLM`（Task 1）
- Produces: `_build_synthesizer(judge) -> Synthesizer`
- Produces: `_synthesize(contexts, synth, goldens_per_context) -> tuple[list[tuple[Golden, ContextGroup]], int, str]`（返回 Golden 与所属组，转换留给 Task 6，避免与 writer 循环依赖）

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_testset_synth.py
"""出题编排：单 context 失败隔离、进度复用、Golden 字段映射。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

import pytest
from deepeval.dataset import Golden

from src.cli.synth_contexts import ContextGroup
from src.cli import testset_synth


@dataclass
class _FakeGroup:
    company: str = "东软集团股份有限公司"
    period: str = "2025年第一季度"
    source: str = "neusoft_2025_q1.pdf"
    doc_id: str = "d1"
    aliases: set[str] = field(default_factory=lambda: {"东软集团股份有限公司"})
    company_source: str = "metadata"
    chunks: list = field(default_factory=list)


class _FakeSynth:
    """一个 context 抛 ValueError，另一个正常返回 Golden。"""

    def __init__(self) -> None:
        self.calls = 0

    async def a_generate_goldens_from_contexts(self, contexts, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ValueError("bad json")
        return [
            Golden(
                input="东软集团2025年第一季度营业收入是多少？",
                expected_output="东软集团2025年第一季度营业收入为人民币18.5亿元。",
                context=contexts[0],
                additional_metadata={"evolutions": ["Concretizing"]},
            )
        ]


def test_one_context_failure_does_not_drop_others():
    contexts = [_FakeGroup(), _FakeGroup()]
    pairs, failed, last_error = asyncio.run(
        testset_synth._synthesize(contexts, _FakeSynth(), goldens_per_context=1)
    )
    assert failed == 1
    assert "bad json" in last_error
    assert len(pairs) == 1
    golden, group = pairs[0]
    assert golden.input.startswith("东软集团")
    assert group.company == "东软集团股份有限公司"


def test_all_contexts_failed_returns_zero_pairs():
    class _AllFail:
        async def a_generate_goldens_from_contexts(self, contexts, **kwargs):
            raise RuntimeError("boom")

    pairs, failed, last_error = asyncio.run(
        testset_synth._synthesize(
            [_FakeGroup()], _AllFail(), goldens_per_context=1
        )
    )
    assert pairs == []
    assert failed == 1
    assert "boom" in last_error


def test_synthesizer_uses_four_concurrency_and_configured_goldens(monkeypatch):
    captured: dict = {}

    class _FakeSynthesizer:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeJudge:
        model_name = "fake-judge"

    monkeypatch.setattr(testset_synth, "Synthesizer", _FakeSynthesizer)
    testset_synth._build_synthesizer(_FakeJudge())
    assert captured["max_concurrent"] == 4
    assert captured["async_mode"] is True
    assert captured["styling_config"] is not None
    assert captured["evolution_config"] is not None
    assert captured["filtration_config"] is not None


def test_async_signature_has_no_send_data():
    import inspect

    from deepeval.synthesizer import Synthesizer

    params = inspect.signature(
        Synthesizer.a_generate_goldens_from_contexts
    ).parameters
    assert "_send_data" not in params
    assert "_progress" in params
    assert "_pbar_id" in params
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_testset_synth.py -q
```

Expected: `ModuleNotFoundError: src.cli.testset_synth`.

- [ ] **Step 3: 实现构造与编排**

创建 `src/cli/testset_synth.py`（本任务先写以下内容，Task 6 再追加 CLI）：

```python
"""v6 出题：DeepEval Synthesizer 编排。"""

from __future__ import annotations

import asyncio
from typing import Any

from rich.progress import Progress

# 先 import settings，确保 DEEPEVAL_* 环境变量已设置
from src.config import settings
from src.config.const import EVAL_SYNTH_EVOLUTION_WEIGHTS

from deepeval.synthesizer import Synthesizer
from deepeval.synthesizer.config import EvolutionConfig, FiltrationConfig, StylingConfig
from deepeval.synthesizer.types import Evolution

from src.cli.synth_contexts import ContextGroup, render_context
from src.infra.llm.provider.deepeval_synth_llm import SynthDeepEvalLLM


def _evolution_weights() -> dict[Evolution, float]:
    """把字符串键的权重转成 Evolution 枚举键。

    Returns:
        `Evolution -> 权重` 字典

    Raises:
        ValueError: 权重键无法映射或和不为 1.0
    """
    by_value = {item.value: item for item in Evolution}
    result: dict[Evolution, float] = {}
    for key, weight in EVAL_SYNTH_EVOLUTION_WEIGHTS.items():
        if key not in by_value:
            raise ValueError(f"未知 evolution: {key}")
        result[by_value[key]] = weight
    total = sum(result.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"evolution 权重和必须为 1.0，当前 {total}")
    return result


def _styling_config() -> StylingConfig:
    """中文出题的硬约束。"""
    return StylingConfig(
        scenario="财务分析师阅读 A 股或港股上市公司的定期报告",
        task="基于给定的中文年报或季报上下文，提出可以用该上下文回答的问题",
        input_format=(
            "简体中文问句，一句话，必须包含公司名称与报告期，"
            "不得出现英文句子或中英拼接"
        ),
        expected_output_format=(
            "简体中文完整陈述句，包含主体、指标与数值单位（如适用），"
            "不得为裸数字，并以句号结尾"
        ),
    )


def _build_synthesizer(judge: Any) -> Synthesizer:
    """构造 Synthesizer。

    Args:
        judge: provider 层裁判（ChatOpenAI）

    Returns:
        配置好的 Synthesizer
    """
    critic = SynthDeepEvalLLM(judge)
    return Synthesizer(
        model=SynthDeepEvalLLM(judge),
        async_mode=True,
        max_concurrent=settings.EVAL_SYNTH_MAX_CONCURRENT,
        styling_config=_styling_config(),
        evolution_config=EvolutionConfig(
            num_evolutions=1, evolutions=_evolution_weights()
        ),
        filtration_config=FiltrationConfig(
            synthetic_input_quality_threshold=0.5,
            max_quality_retries=2,
            critic_model=critic,
        ),
    )


async def _synthesize(
    contexts: list[ContextGroup],
    synth: Synthesizer,
    *,
    goldens_per_context: int,
) -> tuple[list[tuple[Any, ContextGroup]], int, str]:
    """逐上下文出题，单点失败不拖垮整批。

    Args:
        contexts: 出题上下文
        synth: 已构造的 Synthesizer
        goldens_per_context: 每个 context 生成多少题

    Returns:
        `(pairs, failed_contexts, last_error)`；pairs 为 `(Golden, ContextGroup)`
    """
    semaphore = asyncio.Semaphore(settings.EVAL_SYNTH_MAX_CONCURRENT)
    progress = Progress(disable=True)

    async def _one(group: ContextGroup) -> list[tuple[Any, ContextGroup]]:
        async with semaphore:
            task_id = progress.add_task("synth", total=1)
            try:
                goldens = await synth.a_generate_goldens_from_contexts(
                    [render_context(group)],
                    include_expected_output=True,
                    max_goldens_per_context=goldens_per_context,
                    _reset_cost=False,
                    _progress=progress,
                    _pbar_id=task_id,
                )
            finally:
                progress.remove_task(task_id)
        return [(golden, group) for golden in goldens]

    results = await asyncio.gather(
        *(_one(group) for group in contexts), return_exceptions=True
    )
    pairs: list[tuple[Any, ContextGroup]] = []
    failed = 0
    last_error = ""
    for result in results:
        if isinstance(result, BaseException):
            failed += 1
            last_error = f"{type(result).__name__}: {result}"
            continue
        pairs.extend(result)
    return pairs, failed, last_error
```

- [ ] **Step 4: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_testset_synth.py -q
```

Expected: `4 passed`（`_synthesize` 只返回 `(Golden, ContextGroup)`，不依赖 writer）。

- [ ] **Step 5: 格式化并提交**

```bash
ruff format src/cli/testset_synth.py tests/cli/test_testset_synth.py
git add src/cli/testset_synth.py tests/cli/test_testset_synth.py
git commit -m "feat(synth): orchestrate per-context generation with isolation"
```

---

## Task 4: 质量门（语言 / 完整句 / 自足 / 去重 / 多跳）

**Files:**
- Create: `src/cli/synth_gates.py`
- Test: `tests/cli/test_synth_gates.py`

**Interfaces:**
- Consumes: `ContextGroup.aliases`、`eval_self_contained.enforce_self_contained`
- Produces: `compute_numeric_risk(reference) -> bool`
- Produces: `run_gates(samples, aliases_by_company) -> tuple[list[dict], dict]`
- Produces stats keys: `generated` / `kept` / `language_checked` / `self_contained_rewritten` / `failed_contexts` / `dropped{...}`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_synth_gates.py
"""v6 质量门：语言、完整句、自足、去重、多跳。"""

from __future__ import annotations

from src.cli.synth_gates import (
    check_complete_sentence,
    check_language,
    compute_numeric_risk,
    run_gates,
)


def _sample(
    question: str,
    reference: str,
    *,
    hop: str = "single_hop",
    contexts: list[str] | None = None,
) -> dict:
    """构造最小 sample。"""
    return {
        "user_input": question,
        "reference": reference,
        "reference_contexts": contexts
        or ["【东软集团股份有限公司 · 2025年第一季度】\n正文"],
        "hop_type": hop,
        "evolutions": ["Multi-context"] if hop == "multi_hop" else ["Concretizing"],
        "_company": "东软集团股份有限公司",
        "_entities": {"SAP SE", "Tencent", "东软集团股份有限公司"},
        "_original_user_input": question,
    }


def test_pure_english_is_rejected():
    assert not check_language(
        "What was the revenue?", "Revenue was 18.5 billion.", set()
    )


def test_mixed_sentence_is_rejected():
    assert not check_language(
        "what did the 经审核的财务报表 show", "东软集团营业收入为18.5亿元。", set()
    )


def test_parenthetical_name_is_tolerated():
    assert check_language(
        "腾讯控股有限公司（Tencent Holdings Limited）2024年营业收入是多少？",
        "腾讯控股有限公司2024年营业收入为人民币6,603亿元。",
        {"Tencent Holdings Limited"},
    )


def test_latin_entity_adjacent_to_cjk_is_tolerated():
    assert check_language(
        "东软集团2025年第一季度报告中SAP SE的持股比例是多少？",
        "东软集团2025年第一季度报告中SAP SE持股比例为1.2%。",
        {"SAP SE"},
    )


def test_zero_letter_reference_is_rejected():
    assert not check_language("东软集团2025年营业收入？", "18.5%。", set())


def test_year_is_not_the_numeric_fact():
    assert check_complete_sentence(
        "腾讯2024年营业收入为人民币18.5亿元。",
        numeric_risk=True,
        subject_aliases={"腾讯"},
    )


def test_date_is_not_the_numeric_fact():
    assert check_complete_sentence(
        "2024年12月31日，公司实现营业收入18.5亿元。", numeric_risk=True
    )


def test_metric_without_subject_is_rejected():
    assert not check_complete_sentence("营业收入18.5亿元。", numeric_risk=True)


def test_bare_number_is_rejected_and_attributed_to_sentence_gate():
    samples = [_sample("东软集团2025年营业收入？", "1,234,567.89")]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert kept == []
    assert stats["dropped"]["not_complete_sentence"] == 1
    assert stats["dropped"]["language"] == 0


def test_numeric_risk_flag():
    assert compute_numeric_risk("营业收入为人民币18.5亿元。")
    assert not compute_numeric_risk("公司董事长保证财务信息真实。")


def test_duplicate_is_dropped():
    samples = [
        _sample("东软集团2025年营业收入？", "东软集团2025年营业收入为18.5亿元。"),
        _sample("东软集团2025年营业收入?", "东软集团2025年营业收入为18.5亿元。"),
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert len(kept) == 1
    assert stats["dropped"]["duplicate"] == 1


def test_hop_ratio_is_enforced_by_hash_not_order():
    samples = [
        _sample(f"东软集团2025年问题{i}？", f"东软集团2025年营业收入为{i}亿元。", hop="multi_hop")
        for i in range(4)
    ] + [
        _sample(f"东软集团2025年单跳{i}？", f"东软集团2025年净利润为{i}亿元。")
        for i in range(2)
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    multi = [s for s in kept if s["hop_type"] == "multi_hop"]
    assert len(multi) <= 2
    assert stats["dropped"]["hop_ratio"] == 2


def test_self_contained_rewrite_is_counted():
    samples = [
        _sample("2025年第一季度营业收入是多少？", "东软集团2025年第一季度营业收入为18.5亿元。")
    ]
    kept, stats = run_gates(samples, {"东软集团股份有限公司": {"东软集团股份有限公司"}})
    assert stats["self_contained_rewritten"] == 1
    assert kept[0]["user_input"].startswith("东软集团股份有限公司")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_gates.py -q
```

Expected: `ModuleNotFoundError: src.cli.synth_gates`.

- [ ] **Step 3: 实现 `synth_gates.py`**

```python
"""v6 出题质量门（确定性，不调用 LLM）。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from src.config.const import (
    EVAL_HOP_MULTI_MAX_RATIO,
    EVAL_LANG_CJK_MIN_RATIO,
    EVAL_LANG_SENTENCE_CJK_MIN_RATIO,
    EVAL_NUMERIC_METRIC_WORDS,
    EVAL_SYNTH_SUBJECT_WORDS,
)
from src.cli.eval_self_contained import enforce_self_contained

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_SENTENCE_RE = re.compile(r"[。？！.!?]")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_MONEY_RE = re.compile(r"(?:元|万元|亿元|%|个百分点|倍)")
_NUMBER_RE = re.compile(r"\d[\d,.]*")
_DATE_NUMBER_RE = re.compile(r"(?:19|20)\d{2}(?=年)|\d{1,2}(?=[月日])")


def _ratio(text: str) -> tuple[float, int]:
    """计算 CJK 字母占全部字母的比例。

    Args:
        text: 待判定文本

    Returns:
        `(比例, 字母总数)`；0 分母时比例为 0
    """
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    total = cjk + latin
    if total == 0:
        return 0.0, 0
    return cjk / total, total


def strip_parentheticals(text: str) -> str:
    """去掉括号及其内容。"""
    return _PAREN_RE.sub("", text)


def strip_context_entities(text: str, entities: set[str]) -> str:
    """按整词、大小写不敏感剔除上下文实体与公司别名。

    Args:
        text: 原始文本
        entities: 实体集合（含 ASCII 与中文别名）

    Returns:
        剔除后的文本
    """
    result = text
    for entity in sorted(entities, key=len, reverse=True):
        if len(entity) < 2:
            continue
        pattern = rf"(?<![A-Za-z0-9]){re.escape(entity)}(?![A-Za-z0-9])"
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    return result


_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&.\- ]{1,40}")


def extract_context_entities(contexts: list[str]) -> set[str]:
    """从上下文提取拉丁实体词（用于语言门剔除）。

    Args:
        contexts: 该样本的 `reference_contexts`

    Returns:
        长度 ≥2 的拉丁 token 集合
    """
    entities: set[str] = set()
    for text in contexts:
        for match in _LATIN_TOKEN_RE.finditer(text):
            token = match.group(0).strip(" .-")
            if len(token) >= 2:
                entities.add(token)
    return entities


def _sentences(text: str) -> list[str]:
    """按中英文句末标点切句。"""
    return [part for part in _SENTENCE_RE.split(text) if part.strip()]


def check_language(question: str, reference: str, entities: set[str]) -> bool:
    """判定题面与参考答案是否符合中文要求。

    Args:
        question: 题面
        reference: 参考答案
        entities: 该样本的上下文实体与公司别名

    Returns:
        True 表示通过
    """
    for text in (question, reference):
        for sentence in _sentences(text):
            cjk = len(_CJK_RE.findall(sentence))
            latin = len(_LATIN_RE.findall(sentence))
            if latin >= 5 and cjk == 0:
                return False

    for text in (question, reference):
        no_paren = strip_parentheticals(text)
        for sentence in _sentences(no_paren):
            cjk = len(_CJK_RE.findall(sentence))
            latin = len(_LATIN_RE.findall(sentence))
            total = cjk + latin
            if total >= 5 and cjk / total < EVAL_LANG_SENTENCE_CJK_MIN_RATIO:
                return False
        ratio, total = _ratio(strip_context_entities(no_paren, entities))
        if total == 0 or ratio < EVAL_LANG_CJK_MIN_RATIO:
            return False
    return True


def compute_numeric_risk(reference: str) -> bool:
    """判断参考答案是否含需要单位/口径判定的数值事实。"""
    return bool(_NUMBER_RE.search(reference)) and bool(_MONEY_RE.search(reference))


def _first_numeric_fact(reference: str) -> re.Match[str] | None:
    """定位第一个「后接单位/百分号/金额词」的非日期数字。"""
    for match in _NUMBER_RE.finditer(reference):
        if _DATE_NUMBER_RE.fullmatch(match.group(0)):
            continue
        tail = reference[match.end() : match.end() + 4]
        if _MONEY_RE.match(tail):
            return match
    return None


def check_complete_sentence(
    reference: str,
    numeric_risk: bool,
    subject_aliases: set[str] | None = None,
) -> bool:
    """判定参考答案是否为自包含完整句。

    Args:
        reference: 参考答案
        numeric_risk: 是否含数值事实
        subject_aliases: 题干公司别名（数值答案的主体判定）

    Returns:
        True 表示通过
    """
    if len(reference) < 8:
        return False
    if reference[-1] not in "。！？.!?":
        return False
    if not re.search(r"[^\d\s\W_]", reference):
        return False
    if not numeric_risk:
        return True

    fact = _first_numeric_fact(reference)
    if fact is None:
        return False
    prefix = reference[: fact.start()]
    has_subject = any(word in prefix for word in EVAL_SYNTH_SUBJECT_WORDS)
    if not has_subject and subject_aliases:
        has_subject = any(alias in prefix for alias in subject_aliases)
    if not has_subject:
        return False
    return any(word in reference for word in EVAL_NUMERIC_METRIC_WORDS)


def _normalize_question(text: str) -> str:
    """题面去标点/空白后小写，用于精确去重。"""
    return re.sub(r"[\W_]+", "", text).lower()


def _hop_sort_key(sample: dict) -> str:
    """多跳截断的确定性排序键。"""
    return hashlib.sha1(sample["user_input"].encode("utf-8")).hexdigest()


def enforce_hop_ratio(samples: list[dict]) -> tuple[list[dict], int]:
    """把 multi_hop 占比截到 1/3。

    Args:
        samples: 已通过前四道门的样本

    Returns:
        `(kept, dropped_count)`
    """
    total = len(samples)
    if total == 0:
        return [], 0
    allowed = int(total * EVAL_HOP_MULTI_MAX_RATIO + 1e-9)
    multi = [s for s in samples if s.get("hop_type") == "multi_hop"]
    if len(multi) <= allowed:
        return samples, 0
    to_drop = len(multi) - allowed
    dropped_questions = {
        s["user_input"] for s in sorted(multi, key=_hop_sort_key)[:to_drop]
    }
    kept = [s for s in samples if s["user_input"] not in dropped_questions]
    return kept, to_drop


def run_gates(
    samples: list[dict], aliases_by_company: dict[str, set[str]]
) -> tuple[list[dict], dict[str, Any]]:
    """按「自足 → 完整句 → 语言 → 去重 → 多跳」执行质量门。

    Args:
        samples: `golden_to_sample` 产出的样本
        aliases_by_company: `{company_norm: aliases}`

    Returns:
        `(kept, stats)`
    """
    stats: dict[str, Any] = {
        "generated": len(samples),
        "kept": 0,
        "language_checked": 0,
        "self_contained_rewritten": 0,
        "failed_contexts": 0,
        "dropped": {
            "self_contained": 0,
            "not_complete_sentence": 0,
            "language": 0,
            "duplicate": 0,
            "hop_ratio": 0,
        },
    }

    for sample in samples:
        sample["_original_user_input"] = sample["user_input"]

    kept, dropped = enforce_self_contained(samples, aliases_by_company)
    stats["dropped"]["self_contained"] = len(dropped)
    stats["self_contained_rewritten"] = sum(
        1 for s in kept if s["user_input"] != s["_original_user_input"]
    )

    after_sentence: list[dict] = []
    for sample in kept:
        if not check_complete_sentence(
            sample["reference"],
            bool(sample.get("numeric_risk")),
            set(sample.get("_entities") or ()),
        ):
            stats["dropped"]["not_complete_sentence"] += 1
            continue
        after_sentence.append(sample)

    stats["language_checked"] = len(after_sentence)
    after_language: list[dict] = []
    for sample in after_sentence:
        entities = set(sample.get("_entities") or ())
        if not check_language(
            sample["user_input"], sample["reference"], entities
        ) or not check_language(
            sample["_original_user_input"], sample["reference"], entities
        ):
            stats["dropped"]["language"] += 1
            continue
        after_language.append(sample)

    seen: set[str] = set()
    after_dedupe: list[dict] = []
    for sample in after_language:
        key = _normalize_question(sample["user_input"])
        if key in seen:
            stats["dropped"]["duplicate"] += 1
            continue
        seen.add(key)
        after_dedupe.append(sample)

    kept_final, hop_dropped = enforce_hop_ratio(after_dedupe)
    stats["dropped"]["hop_ratio"] = hop_dropped
    stats["kept"] = len(kept_final)
    return kept_final, stats
```

- [ ] **Step 4: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_gates.py -q
```

Expected: `12 passed`.

- [ ] **Step 5: 格式化并提交**

```bash
ruff format src/cli/synth_gates.py tests/cli/test_synth_gates.py
git add src/cli/synth_gates.py tests/cli/test_synth_gates.py
git commit -m "feat(synth): add deterministic quality gates"
```

---

## Task 5: 样本转换与原子落盘

**Files:**
- Create: `src/cli/synth_writer.py`
- Test: `tests/cli/test_synth_writer.py`

**Interfaces:**
- Produces: `golden_to_sample(golden, group) -> dict`
- Produces: `build_metadata(...) -> dict`
- Produces: `write_testset(path, payload, *, force=False) -> None`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_synth_writer.py
"""v6 落盘：字段契约、原子写、覆盖保护、对账。"""

from __future__ import annotations

import json

import pytest
from deepeval.dataset import Golden

from src.cli.synth_contexts import ContextGroup
from src.cli.synth_writer import build_metadata, golden_to_sample, write_testset
from src.infra.db.vector_store.types import ChunkResult


def _group() -> ContextGroup:
    """构造单组上下文。"""
    chunk = ChunkResult(
        id="d1:0",
        content="东软集团2025年第一季度营业收入为18.5亿元。",
        metadata={"doc_id": "d1", "source": "a.pdf", "chunk_index": 0},
    )
    return ContextGroup(
        chunks=[chunk],
        source="a.pdf",
        doc_id="d1",
        company="东软集团股份有限公司",
        period="2025年第一季度",
        aliases={"东软集团股份有限公司", "东软"},
    )


def test_golden_to_sample_has_all_contract_fields():
    golden = Golden(
        input="东软集团2025年第一季度营业收入是多少？",
        expected_output="东软集团2025年第一季度营业收入为18.5亿元。",
        context=["正文"],
        additional_metadata={"evolutions": ["Multi-context"], "synthetic_input_quality": 0.9},
    )
    sample = golden_to_sample(golden, _group())
    for key in (
        "user_input",
        "reference",
        "reference_contexts",
        "expected_tools",
        "hop_type",
        "hop_basis",
        "evolutions",
        "source_file",
        "numeric_risk",
        "synth_quality",
    ):
        assert key in sample
    assert sample["expected_tools"] == ["retrieve_kb"]
    assert sample["hop_type"] == "multi_hop"
    assert sample["hop_basis"] == "evolution"
    assert sample["numeric_risk"] is True
    assert sample["synth_quality"] == 0.9
    assert sample["reference_contexts"][0].startswith("【")


def test_retrieval_context_none_does_not_break_conversion():
    golden = Golden(
        input="东软集团2025年第一季度营业收入是多少？",
        expected_output="东软集团2025年第一季度营业收入为18.5亿元。",
        context=["正文"],
    )
    assert golden.retrieval_context is None
    sample = golden_to_sample(golden, _group())
    assert sample["reference_contexts"]


def test_write_is_atomic_and_creates_parent(tmp_path):
    path = tmp_path / "testset_d1_v6.json"
    write_testset(path, {"samples": []})
    assert json.loads(path.read_text(encoding="utf-8")) == {"samples": []}
    assert not path.with_suffix(".json.tmp").exists()


def test_existing_file_requires_force(tmp_path):
    path = tmp_path / "testset_d1_v6.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_testset(path, {"samples": []})
    write_testset(path, {"samples": [1]}, force=True)
    assert json.loads(path.read_text(encoding="utf-8")) == {"samples": [1]}


def test_metadata_reconciles(tmp_path):
    meta = build_metadata(
        kb_id="kb",
        kb_name="test",
        version=6,
        samples=[{"user_input": "a"}, {"user_input": "b"}],
        stats={
            "generated": 3,
            "kept": 2,
            "language_checked": 2,
            "self_contained_rewritten": 1,
            "failed_contexts": 1,
            "dropped": {
                "self_contained": 0,
                "not_complete_sentence": 1,
                "language": 0,
                "duplicate": 0,
                "hop_ratio": 0,
            },
        },
        params={"goldens_per_context": 1},
        doc_names=["a.pdf"],
        doc_ids=["d1"],
        source_chunks=2,
        covered_chunks=2,
        company_source={"a.pdf": "metadata"},
        hop_stats={"single_hop": 2, "multi_hop": 0},
        elapsed_seconds=1.5,
    )
    assert meta["testset_size"] == 2
    assert meta["gate_stats"]["generated"] - sum(
        meta["gate_stats"]["dropped"].values()
    ) == meta["testset_size"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_writer.py -q
```

Expected: `ModuleNotFoundError: src.cli.synth_writer`.

- [ ] **Step 3: 实现 `synth_writer.py`**

```python
"""v6 出题：Golden → sample、元数据、原子写。"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.const import EVAL_SYNTH_EXPECTED_TOOLS
from src.cli.synth_contexts import ContextGroup, render_context
from src.cli.synth_gates import compute_numeric_risk, extract_context_entities

_SAMPLE_KEYS = (
    "user_input",
    "reference",
    "reference_contexts",
    "expected_tools",
    "hop_type",
    "hop_basis",
    "evolutions",
    "source_file",
    "numeric_risk",
    "synth_quality",
)


def golden_to_sample(golden: Any, group: ContextGroup) -> dict:
    """把 DeepEval Golden 转成 v6 sample，并挂内部判定字段。

    Args:
        golden: Synthesizer 产出的 Golden
        group: 该题所属上下文

    Returns:
        sample dict（含 `_company` / `_entities` / `_original_user_input` 内部键）
    """
    contexts = list(golden.context or render_context(group))
    if contexts and not contexts[0].startswith("【"):
        contexts[0] = f"【{group.company} · {group.period}】\n{contexts[0]}"
    metadata = golden.additional_metadata or {}
    evolutions = list(metadata.get("evolutions") or [])
    reference = golden.expected_output or ""
    return {
        "user_input": golden.input,
        "reference": reference,
        "reference_contexts": contexts,
        "expected_tools": list(EVAL_SYNTH_EXPECTED_TOOLS),
        "hop_type": "multi_hop" if "Multi-context" in evolutions else "single_hop",
        "hop_basis": "evolution",
        "evolutions": evolutions,
        "source_file": group.source,
        "numeric_risk": compute_numeric_risk(reference),
        "synth_quality": metadata.get("synthetic_input_quality"),
        "_company": group.company,
        "_entities": set(group.aliases) | extract_context_entities(contexts),
        "_original_user_input": golden.input,
    }


def public_sample(sample: dict) -> dict:
    """去掉内部键，只保留写盘契约字段。"""
    return {key: sample[key] for key in _SAMPLE_KEYS if key in sample}


def build_metadata(
    *,
    kb_id: str,
    kb_name: str,
    version: int,
    samples: list[dict],
    stats: dict,
    params: dict,
    doc_names: list[str],
    doc_ids: list[str],
    source_chunks: int,
    covered_chunks: int,
    company_source: dict[str, str],
    hop_stats: dict[str, int],
    elapsed_seconds: float,
) -> dict:
    """构造 v6 题集元数据。

    Returns:
        可直接写入 JSON 的 metadata 字典
    """
    return {
        "kb_id": kb_id,
        "kb_name": kb_name,
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "testset_size": len(samples),
        "doc_names": doc_names,
        "doc_ids": doc_ids,
        "source_chunks": source_chunks,
        "covered_chunks": covered_chunks,
        "company_source": company_source,
        "synth_params": {
            **params,
            "elapsed_seconds": round(elapsed_seconds, 1),
        },
        "hop_stats": hop_stats,
        "gate_stats": {
            "generated": stats["generated"],
            "kept": stats["kept"],
            "language_checked": stats["language_checked"],
            "self_contained_rewritten": stats["self_contained_rewritten"],
            "failed_contexts": stats["failed_contexts"],
            "dropped": dict(stats["dropped"]),
        },
    }


def write_testset(path: Path | str, payload: dict, *, force: bool = False) -> None:
    """原子写入题集 JSON。

    Args:
        path: 目标路径
        payload: 完整题集
        force: 是否允许覆盖

    Raises:
        FileExistsError: 目标已存在且未传 force
    """
    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"目标已存在：{target}（如需覆盖请加 --force）")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
```

- [ ] **Step 4: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_synth_writer.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: 格式化并提交**

```bash
ruff format src/cli/synth_writer.py tests/cli/test_synth_writer.py
git add src/cli/synth_writer.py tests/cli/test_synth_writer.py
git commit -m "feat(synth): convert goldens and write testsets atomically"
```

---

## Task 6: 版本解析、CLI 与早停

**Files:**
- Modify: `src/cli/testset_io.py`
- Modify: `src/cli/testset_synth.py`（追加 `parse_args` / `_run` / `main`）
- Test: `tests/cli/test_testset_synth.py`（追加 CLI 测试）
- Test: `tests/cli/test_testset_io.py`（新建）

**Interfaces:**
- Produces: `_scan_versions(kb_id) -> list[int]`
- Produces: `resolve_testset_version(kb_id, version) -> int`
- Produces: `next_testset_version(kb_id) -> int`
- Produces CLI: `python -m src.cli.testset_synth`

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_testset_io.py
"""测试集版本解析单一来源。"""

from __future__ import annotations

import pytest

from src.cli import testset_io


def test_scan_versions_ignores_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v6.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v7.json.tmp").write_text("{}", encoding="utf-8")
    assert testset_io._scan_versions("kb") == [5, 6]


def test_resolve_explicit_version_does_not_check_existence(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    assert testset_io.resolve_testset_version("kb", 6) == 6


def test_resolve_none_picks_max(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    (tmp_path / "testset_kb_v6.json").write_text("{}", encoding="utf-8")
    assert testset_io.resolve_testset_version("kb", None) == 6


def test_resolve_none_without_files_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        testset_io.resolve_testset_version("kb", None)


def test_next_version_is_max_plus_one(tmp_path, monkeypatch):
    monkeypatch.setattr(testset_io, "EVAL_TESTSET_DIR", str(tmp_path))
    (tmp_path / "testset_kb_v5.json").write_text("{}", encoding="utf-8")
    assert testset_io.next_testset_version("kb") == 6
```

```python
# tests/cli/test_testset_synth.py 追加
def test_explicit_version_requires_docs(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "argv", ["testset_synth", "--kb-id", "kb", "--version", "6"]
    )
    with pytest.raises(SystemExit) as excinfo:
        testset_synth.parse_args()
    assert excinfo.value.code == 2
    assert "--docs" in capsys.readouterr().err


def test_docs_without_version_uses_next_version(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["testset_synth", "--kb-id", "kb", "--docs", "a.pdf"]
    )
    args = testset_synth.parse_args()
    assert args.version is None
    assert args.docs == ["a.pdf"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_testset_io.py tests/cli/test_testset_synth.py -q
```

Expected: `AttributeError: module 'src.cli.testset_io' has no attribute '_scan_versions'`.

- [ ] **Step 3: 重构 `testset_io.py`**

```python
"""测试集文件加载与版本解析（评测/出题共用）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import EVAL_TESTSET_DIR


def _testset_path(kb_id: str, version: int) -> Path:
    """拼出某版本的题集路径。"""
    return Path(EVAL_TESTSET_DIR) / f"testset_{kb_id}_v{version}.json"


def _scan_versions(kb_id: str) -> list[int]:
    """扫描某 KB 的全部题集版本。

    Args:
        kb_id: 知识库 UUID

    Returns:
        升序版本号列表（忽略 .tmp 与其它文件）
    """
    pattern = re.compile(rf"^testset_{re.escape(kb_id)}_v(\d+)\.json$")
    directory = Path(EVAL_TESTSET_DIR)
    if not directory.exists():
        return []
    versions: list[int] = []
    for item in directory.iterdir():
        match = pattern.match(item.name)
        if match is not None:
            versions.append(int(match.group(1)))
    return sorted(versions)


def resolve_testset_version(kb_id: str, version: int | None) -> int:
    """解析评测要用的版本号。

    Args:
        kb_id: 知识库 UUID
        version: 显式版本；None 表示取最新

    Returns:
        实际版本号（显式版本不校验文件存在，由 loader 负责）

    Raises:
        FileNotFoundError: version=None 且没有任何题集
    """
    if version is not None:
        return version
    versions = _scan_versions(kb_id)
    if not versions:
        raise FileNotFoundError(
            f"No testset found for kb_id={kb_id}. "
            "请先运行 python -m src.cli.testset_synth 生成测试集"
        )
    return versions[-1]


def next_testset_version(kb_id: str) -> int:
    """出题用的下一个版本号（最大版本 + 1）。"""
    versions = _scan_versions(kb_id)
    return (versions[-1] + 1) if versions else 1


def load_testset_samples(kb_id: str, version: int | None = None) -> list[dict]:
    """加载指定知识库的测试集，返回完整 sample 列表。

    Args:
        kb_id: 知识库 UUID
        version: 指定版本号；None 表示自动取最新版本

    Returns:
        原始 sample 列表

    Raises:
        FileNotFoundError: 没有找到该知识库的测试集文件
    """
    resolved = resolve_testset_version(kb_id, version)
    target = _testset_path(kb_id, resolved)
    if not target.exists():
        raise FileNotFoundError(f"测试集文件不存在: {target}")
    data = json.loads(target.read_text(encoding="utf-8"))
    return list(data["samples"])


def _load_latest_testset(
    kb_id: str, version: int | None = None
) -> tuple[list[str], list[str]]:
    """加载测试集并适配评分的 (questions, ground_truth) 形状。"""
    samples = load_testset_samples(kb_id, version)
    return [s["user_input"] for s in samples], [s["reference"] for s in samples]
```

- [ ] **Step 4: 实现 CLI**

在 `src/cli/testset_synth.py` 追加：

```python
import argparse
import time
from pathlib import Path

from src.cli.synth_contexts import build_contexts, load_chunks
from src.cli.synth_gates import run_gates
from src.cli.synth_writer import (
    build_metadata,
    golden_to_sample,
    public_sample,
    write_testset,
)
from src.cli.testset_io import next_testset_version
from src.config.const import EVAL_SYNTH_MIN_SAMPLES
from src.infra.db.engine import run_and_dispose
from src.infra.llm.provider.factory import build_bridge_judge
from src.infra.llm.provider.http_observability import HookEvidence
from src.core import logging as core_logging
from src.core.log_events import Event


def parse_args() -> argparse.Namespace:
    """解析出题命令行参数。"""
    parser = argparse.ArgumentParser(description="DeepEval v6 测试集出题")
    parser.add_argument("--kb-id", required=True, help="知识库 UUID")
    parser.add_argument(
        "--version", type=int, default=None, help="版本号（省略=最大版本+1）"
    )
    parser.add_argument(
        "--docs", nargs="*", default=None, help="限定文档文件名（省略=全部）"
    )
    parser.add_argument("--kb-name", default="", help="写入元数据的知识库名")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在版本")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调用 LLM")
    args = parser.parse_args()
    if args.version is not None and not args.docs:
        parser.error("显式 --version 时必须同时提供 --docs（保证版本可复现）")
    return args


async def _run(args: argparse.Namespace) -> None:
    """执行出题（调用方负责 asyncio.run(run_and_dispose(...))）。"""
    started = time.monotonic()
    version = (
        args.version if args.version is not None else next_testset_version(args.kb_id)
    )
    chunks = await load_chunks(args.kb_id, args.docs)
    if args.docs:
        found = {str(c.metadata.get("source")) for c in chunks}
        missing = [name for name in args.docs if name not in found]
        if missing:
            raise SystemExit(f"error: --docs 未匹配到分块: {', '.join(missing)}")

    contexts = build_contexts(chunks, settings.EVAL_SYNTH_CHUNKS_PER_CONTEXT)
    covered = sum(len(g.chunks) for g in contexts)
    if args.dry_run:
        print(f"version={version}")
        print(f"docs={sorted({g.source for g in contexts})}")
        print(f"contexts={len(contexts)} chunks={len(chunks)} covered={covered}")
        print(f"calls=每 context 5-8 次，单轮约 {len(contexts) * 5}-{len(contexts) * 8}")
        target = Path(settings.EVAL_TESTSET_DIR) / f"testset_{args.kb_id}_v{version}.json"
        print(f"target={target} exists={target.exists()}")
        if target.exists():
            print("目标已存在：正式运行需 --force")
        return

    judge = build_bridge_judge(evidence=HookEvidence())
    synth = _build_synthesizer(judge)

    core_logging.log_event(
        Event.SYNTH_START,
        kb_id=args.kb_id,
        docs=len({g.source for g in contexts}),
        contexts=len(contexts),
    )

    async def _attempt(goldens_per_context: int) -> tuple[list[dict], dict, int, str]:
        pairs, failed, last_error = await _synthesize(
            contexts, synth, goldens_per_context=goldens_per_context
        )
        samples = [golden_to_sample(golden, group) for golden, group in pairs]
        aliases = {g.company.strip().lower(): set(g.aliases) for g in contexts}
        kept, stats = run_gates(samples, aliases)
        stats["failed_contexts"] = failed
        return kept, stats, failed, last_error

    kept, stats, failed, last_error = await _attempt(
        settings.EVAL_SYNTH_GOLDENS_PER_CONTEXT
    )
    used_goldens = settings.EVAL_SYNTH_GOLDENS_PER_CONTEXT

    if stats["generated"] == 0:
        raise SystemExit(
            f"error: 所有上下文都失败（failed={failed}），最后错误：{last_error}"
        )
    if stats["language_checked"] == 0:
        raise SystemExit("error: 没有样本进入语言门（语言失效）")
    language_rate = stats["dropped"]["language"] / stats["language_checked"]
    if language_rate > 0.5:
        raise SystemExit(
            f"error: 语言门丢弃率 {language_rate:.0%} > 50%，出题器语言约束失效"
        )
    if len(kept) < EVAL_SYNTH_MIN_SAMPLES:
        kept, stats, failed, last_error = await _attempt(2)
        used_goldens = 2
        if len(kept) < EVAL_SYNTH_MIN_SAMPLES:
            raise SystemExit(
                f"error: 仅 {len(kept)} 条通过质量门（< {EVAL_SYNTH_MIN_SAMPLES}），未写文件"
            )

    if failed > 0:
        core_logging.log_event(
            Event.SYNTH_CONTEXT_FAILED,
            err=f"count={failed} last={last_error}",
        )

    doc_names = sorted({g.source for g in contexts})
    doc_ids = sorted({g.doc_id for g in contexts})
    hop_stats = {
        "single_hop": sum(1 for s in kept if s["hop_type"] == "single_hop"),
        "multi_hop": sum(1 for s in kept if s["hop_type"] == "multi_hop"),
    }
    metadata = build_metadata(
        kb_id=args.kb_id,
        kb_name=args.kb_name,
        version=version,
        samples=kept,
        stats=stats,
        params={
            "chunks_per_context": settings.EVAL_SYNTH_CHUNKS_PER_CONTEXT,
            "goldens_per_context": used_goldens,
            "max_concurrent": settings.EVAL_SYNTH_MAX_CONCURRENT,
            "evolution_weights": dict(EVAL_SYNTH_EVOLUTION_WEIGHTS),
        },
        doc_names=doc_names,
        doc_ids=doc_ids,
        source_chunks=len(chunks),
        covered_chunks=covered,
        company_source={g.source: g.company_source for g in contexts},
        hop_stats=hop_stats,
        elapsed_seconds=time.monotonic() - started,
    )
    payload = {"metadata": metadata, "samples": [public_sample(s) for s in kept]}
    target = Path(settings.EVAL_TESTSET_DIR) / f"testset_{args.kb_id}_v{version}.json"
    write_testset(target, payload, force=args.force)
    core_logging.log_event(
        Event.SYNTH_GATE,
        generated=stats["generated"],
        kept=stats["kept"],
        dropped=stats["dropped"],
        failed_contexts=stats["failed_contexts"],
        language_checked=stats["language_checked"],
    )
    core_logging.log_event(Event.SYNTH_DONE, path=str(target), size=len(kept))


def main() -> None:
    """出题入口。"""
    asyncio.run(run_and_dispose(_run(parse_args())))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_testset_io.py tests/cli/test_testset_synth.py -q
```

Expected: PASS（Task 3 的测试继续通过）。

- [ ] **Step 6: 格式化并提交**

```bash
ruff format src/cli/testset_io.py src/cli/testset_synth.py tests/cli/test_testset_io.py tests/cli/test_testset_synth.py
git add src/cli/testset_io.py src/cli/testset_synth.py tests/cli/test_testset_io.py tests/cli/test_testset_synth.py
git commit -m "feat(synth): add CLI with version/corpus guards"
```

---

## Task 7: 旧入口文案与日志登记

**Files:**
- Modify: `src/config/response_codes.py`
- Modify: `src/cli/eval_deepeval.py`（`--generate` 分支）
- Modify: `src/core/log_events.py`
- Modify: `src/core/log_event_specs.py`
- Modify: `docs/agents/logging-rules.md`
- Test: `tests/cli/test_deepeval_generate_guard.py`、`tests/api/test_ragas_generate.py`

**Interfaces:**
- Produces events: `SYNTH_START` / `SYNTH_GATE` / `SYNTH_DONE` / `SYNTH_CONTEXT_FAILED`
- Produces `Code.GONE_MSG` 指向新命令

- [ ] **Step 1: 写/改测试**

`tests/cli/test_deepeval_generate_guard.py` 增加断言：

```python
def test_generate_guard_points_to_new_command(monkeypatch, capsys):
    """--generate 既要保留旧 token，也要给出新命令。"""
    import sys

    import pytest

    from src.cli import eval_deepeval

    monkeypatch.setattr(
        sys, "argv", ["eval_deepeval", "--kb-id", "kb", "--generate"]
    )
    with pytest.raises(SystemExit) as excinfo:
        eval_deepeval.main()
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "Synthesizer" in out
    assert "deepeval-synthesizer-testset" in out
    assert "python -m src.cli.testset_synth" in out
```

`tests/api/test_ragas_generate.py` 增加：

```python
def test_gone_message_points_to_new_command(auth_client):
    """410 的 message 来自 Code.GONE_MSG。"""
    response = auth_client.post(
        "/api/ragas/generate", json={"kb_name": "kb", "size": 1}
    )
    assert response.status_code == 410
    assert "python -m src.cli.testset_synth" in response.json()["message"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_generate_guard.py tests/api/test_ragas_generate.py -q
```

Expected: 新断言的文案检查失败。

- [ ] **Step 3: 改文案**

`src/config/response_codes.py`：

```python
GONE_MSG = (
    "RAGAS 出题已下线。请使用 DeepEval Synthesizer："
    "python -m src.cli.testset_synth --kb-id <uuid> --version <n> --docs <文件...>"
)
```

`src/cli/eval_deepeval.py` 的 `--generate` 分支：

```python
print(
    "error: --generate 已下线。出题由 change deepeval-synthesizer-testset 提供，"
    "使用 DeepEval Synthesizer：\n"
    "  python -m src.cli.testset_synth --kb-id <uuid> --version <n> --docs <文件...>"
)
sys.exit(1)
```

`src/cli/testset_io.py` 的 `FileNotFoundError` 文案已在 Task 6 改为新命令。

- [ ] **Step 4: 登记日志事件**

`src/core/log_events.py` 的 `LOG_PREFIXES` 加 `"synth"`；`Event` 加：

```python
    # [synth] v6 出题
    SYNTH_START = "testset synth start"
    SYNTH_GATE = "testset synth gate"
    SYNTH_DONE = "testset synth done"
    SYNTH_CONTEXT_FAILED = "testset synth context failed"
    EVAL_TESTSET_VERSION_MISSING = "eval testset version missing"
```

`src/core/log_event_specs.py` 的 `EVENT_SPECS` 加：

```python
    "testset synth start": EventSpec(
        "testset synth start", "synth", "info", ("kb_id", "docs", "contexts")
    ),
    "testset synth gate": EventSpec(
        "testset synth gate",
        "synth",
        "info",
        ("generated", "kept", "dropped", "failed_contexts", "language_checked"),
    ),
    "testset synth done": EventSpec(
        "testset synth done", "synth", "info", ("path", "size")
    ),
    "testset synth context failed": EventSpec(
        "testset synth context failed", "synth", "warning", ("err",)
    ),
    "eval testset version missing": EventSpec(
        "eval testset version missing", "cli", "warning", ("kb_id",)
    ),
```

`docs/agents/logging-rules.md` 前缀表加一行：

```markdown
| `[synth]` | v6 出题（cli/testset_synth + synth_*；事件：testset synth start / gate / done / context failed） |
```

- [ ] **Step 5: 跑测试与 import 校验**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_deepeval_generate_guard.py tests/api/test_ragas_generate.py tests/test_log_events.py -q
POSTGRES_HOST=localhost python -c "import src.core.log_events"
```

Expected: PASS；import 无 `AssertionError`。

- [ ] **Step 6: 格式化并提交**

```bash
ruff format src/config/response_codes.py src/cli/eval_deepeval.py src/core/log_events.py src/core/log_event_specs.py
git add src/config/response_codes.py src/cli/eval_deepeval.py src/core/log_events.py src/core/log_event_specs.py docs/agents/logging-rules.md tests/cli/test_deepeval_generate_guard.py tests/api/test_ragas_generate.py
git commit -m "feat(synth): point legacy generation entries to the new CLI"
```

---

## Task 8: 评测报告记录 `testset_version`

**Files:**
- Modify: `src/cli/eval_deepeval.py`
- Test: `tests/cli/test_eval_native_meta.py`、`tests/cli/test_eval_deepeval_detail.py`

**Interfaces:**
- Consumes: `testset_io.resolve_testset_version`
- Produces: `build_report_meta(..., testset_version: int | None = None)` 两个分支都含该键
- Produces: `save_markdown_report` 渲染 `**Testset version:**`
- Produces: `_build_eval_detail(..., testset_version=None)` 每条含该键
- Produces: `_save_eval_report(..., testset_version=None)` 透传且始终 insert

- [ ] **Step 1: 写失败测试**

```python
# tests/cli/test_eval_native_meta.py 追加
def test_report_meta_carries_testset_version_in_both_branches():
    from src.cli.eval_deepeval import build_report_meta

    normal = build_report_meta(
        judge_model="qwen",
        nan_stats={"nan_cells": 0, "denominator_cells": 4, "nan_rate": 0.0},
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        testset_version=6,
    )
    skipped = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        testset_version=6,
    )
    assert normal["testset_version"] == 6
    assert skipped["testset_version"] == 6


def test_markdown_report_renders_testset_version(tmp_path):
    from src.cli.eval_deepeval import build_report_meta, save_markdown_report

    meta = build_report_meta(
        judge_model="qwen",
        nan_stats=None,
        runner_versions={"deepeval": "4.2.9", "metrics": "native"},
        ragas_enabled=False,
        testset_version=6,
    )
    out = tmp_path / "r.csv"
    save_markdown_report(
        None,
        questions=["Q0"],
        output_path=str(out),
        sample_classes=["pure_kb"],
        report_meta=meta,
        behaviour_rows=[{"q_index": 0, "input": "Q0", "metrics": {}}],
    )
    assert "Testset version:** 6" in (tmp_path / "r.md").read_text(encoding="utf-8")
```

```python
# tests/cli/test_eval_deepeval_detail.py 追加
def test_detail_entries_carry_testset_version():
    import pandas as pd

    from src.cli.eval_deepeval import _build_eval_detail

    df = pd.DataFrame(
        [
            {
                "faithfulness": 1.0,
                "answer_relevancy": 1.0,
                "context_precision": 1.0,
                "context_recall": 1.0,
                "contextual_relevancy": 1.0,
                "non_advice": 1.0,
            }
        ]
    )
    detail, _ = _build_eval_detail(df, 1, None, testset_version=6)
    assert detail[0]["testset_version"] == 6
```

- [ ] **Step 2: 跑测试确认失败**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_native_meta.py tests/cli/test_eval_deepeval_detail.py -q
```

Expected: `TypeError: build_report_meta() got an unexpected keyword argument 'testset_version'`.

- [ ] **Step 3: 改 `build_report_meta`**

```python
def build_report_meta(
    *,
    judge_model: str,
    nan_stats: dict | None,
    runner_versions: dict,
    ragas_enabled: bool = True,
    testset_version: int | None = None,
) -> dict:
    """报告/台账元数据（口径版本、NaN 计数、模型显式性、题集版本）。"""
    if not ragas_enabled:
        return {
            "caliber": "deepeval-only",
            "judge_model": judge_model,
            "model_arg_explicit": True,
            "ragas_skipped": True,
            "testset_version": testset_version,
            "runner_versions": dict(runner_versions),
        }
    if nan_stats is None:
        raise ValueError("ragas_enabled=True 时 nan_stats 不得为 None")
    return {
        "caliber": "deep-eval-native",
        "judge_model": judge_model,
        "model_arg_explicit": True,
        "testset_version": testset_version,
        "nan_cells": nan_stats["nan_cells"],
        "nan_denominator_cells": nan_stats["denominator_cells"],
        "nan_rate": round(nan_stats["nan_rate"], 4),
        "runner_versions": dict(runner_versions),
    }
```

Docstring 的 Args 补一行 `testset_version: 实际加载到的题集版本`。

- [ ] **Step 4: 改 `save_markdown_report`**

在 `if report_meta is not None:` 分支内、`**QA Pairs:**` 之后加：

```python
        version = report_meta.get("testset_version")
        lines.append(f"**Testset version:** {version if version is not None else 'N/A'}")
```

- [ ] **Step 5: 改 `_build_eval_detail` 与 `_save_eval_report`**

```python
def _build_eval_detail(
    df: pd.DataFrame,
    qa_count: int,
    retrieval_details: list[list[dict]] | None,
    testset_version: int | None = None,
) -> tuple[list[dict], dict[str, float]]:
    ...
    for i in range(qa_count):
        entry = {"q_index": i, "question": "", "testset_version": testset_version}
        ...
```

```python
def _save_eval_report(
    ...,
    run_type: str = "manual",
    testset_version: int | None = None,
) -> None:
    ...
    detail, _avg = _build_eval_detail(
        df, qa_count, retrieval_details, testset_version=testset_version
    )
    if testset_version is None:
        core_logging.log_event(Event.EVAL_TESTSET_VERSION_MISSING, kb_id=kb_id)
    ...
```

`_save_eval_report` 仍然无条件 insert（不要因为版本为 None 跳过）。

- [ ] **Step 6: 改 `main()`**

在 `main()` 的测试集加载段：

```python
        from src.cli.testset_io import (
            _load_latest_testset,
            resolve_testset_version,
        )

        try:
            resolved_version = resolve_testset_version(kb_id, args.testset_version)
            questions, ground_truth = _load_latest_testset(
                kb_id, version=resolved_version
            )
        except FileNotFoundError as e:
            print(f"error: {e}")
            sys.exit(1)
```

`build_report_meta(...)` 调用加 `testset_version=resolved_version`；`_save_eval_report(...)` 调用加 `testset_version=resolved_version`。

**假 kb 的测试要同时 patch resolver**：`test_deepeval_only.py` / `test_tracing_order.py` 用假 kb_id，若只 patch `_load_latest_testset`，`resolve_testset_version` 会先因找不到文件抛 `FileNotFoundError`。在这两个测试里加：

```python
monkeypatch.setattr(
    "src.cli.testset_io.resolve_testset_version", lambda kb_id, version: 5
)
```

- [ ] **Step 7: 跑测试**

```bash
POSTGRES_HOST=localhost pytest tests/cli/test_eval_native_meta.py tests/cli/test_eval_deepeval_detail.py tests/cli/test_deepeval_only.py tests/cli/test_eval_overall_score.py tests/cli/test_tracing_order.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
ruff format src/cli/eval_deepeval.py tests/cli/test_eval_native_meta.py tests/cli/test_eval_deepeval_detail.py
git add src/cli/eval_deepeval.py tests/cli/test_eval_native_meta.py tests/cli/test_eval_deepeval_detail.py
git commit -m "feat(eval): record testset_version in report and detail"
```

---

## Task 9: 文档同步

**Files:**
- Modify: `src/cli/README.md`
- Modify: `docs/agents/code-map.md`
- Modify: `docs/agents/api_contract.md`
- Modify: `docs/agents/glossary.md`
- Modify: `docs/agents/requirements_pool.md`
- Modify: `docs/agents/eval-ledger.md`

- [ ] **Step 1: 更新 CLI 文档**

`src/cli/README.md` 新增「v6 出题」一节：

```markdown
## v6 出题（DeepEval Synthesizer）

```bash
python -m src.cli.testset_synth --kb-id <uuid> --version 6 \
  --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf --kb-name test
```

- `--version` 省略时取「现有最大版本 + 1」；显式传版本时必须同时传 `--docs`。
- `--docs` 每一项必须匹配 ≥1 个分块，否则报错。
- `--dry-run` 打印实际 `doc_names`、上下文数、覆盖块数、目标路径与调用量区间。
- `--force` 允许覆盖已存在的版本文件。
```

- [ ] **Step 2: 更新 code-map**

在 CLI 模块表加：

```markdown
| `synth_contexts.py` | v6 出题上下文：分块读取、分组、公司/报告期锚点 |
| `synth_gates.py` | v6 出题质量门：语言/完整句/自足/去重/多跳 |
| `synth_writer.py` | v6 样本转换、元数据、原子写 |
| `testset_synth.py` | v6 出题编排与 CLI |
```

- [ ] **Step 3: 更新 api_contract 与 glossary**

`api_contract.md` 补出题 CLI 契约（参数、退出码、`testset_version`、410 文案指向）。
`glossary.md` 补 `hop_type` / `hop_basis` / `multi_hop` / `expected_tools` / `numeric_risk`。

- [ ] **Step 4: requirements_pool 登记 v7**

```markdown
- **v7 题集扩文档**：v6 冻结在 v5 的两份文档（50/51 块），v7 再扩到全量 4 份（161 块），保持同出题器以单独归因。
```

- [ ] **Step 5: eval-ledger 预留 v6 行说明**

在台账「公共配置/口径」处补一句：

```markdown
v6 题集对应 `testset_version=6`；报告头与 DB `detail_json` 均记录该值。历史 `report_path` 指向 v5，二者不混用。
```

- [ ] **Step 6: 跑文档校验并提交**

```bash
POSTGRES_HOST=localhost python -m src.cli.check_docs
POSTGRES_HOST=localhost python -m src.cli.check_adr
git add src/cli/README.md docs/agents/code-map.md docs/agents/api_contract.md docs/agents/glossary.md docs/agents/requirements_pool.md docs/agents/eval-ledger.md
git commit -m "docs(synth): document v6 testset generation"
```

---

## Task 10: 静态闸门与全量单测

- [ ] **Step 1: 跑全量测试**

```bash
POSTGRES_HOST=localhost pytest tests/ -q
```

Expected: 全绿；若有失败，先修再继续。

- [ ] **Step 2: lint / type**

```bash
ruff format --check src/ tests/
ruff check src/ tests/
pyright src/
```

Expected: format 已格式化；`ruff check` 无新增错误；`pyright` 不新增 error。

- [ ] **Step 3: 结构自检**

确认：

- `api/` 没有 import `infra/` / `config/`；
- `testset_synth` 里没有三元表达式；
- 新增常量都在 `src/config/`；
- 单文件 ≤400 行、单函数 ≤80 行；
- 无 `print()` 调试残留（CLI 的用户输出除外）。

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "test(synth): pass full static gate"
```

---

## Task 11: 活体出题验收（v6）

- [ ] **Step 1: dry-run**

```bash
python -m src.cli.testset_synth --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --version 6 --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf \
  --kb-name test --dry-run
```

Expected: 25 个上下文、50/51 块、目标路径不存在、调用量区间 125–200。

- [ ] **Step 2: 正式出题**

```bash
python -m src.cli.testset_synth --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --version 6 --docs neusoft_2025_q1.pdf tencent_2024_annual.pdf \
  --kb-name test
```

记录耗时、`gate_stats`、`hop_stats`、`failed_contexts`。

- [ ] **Step 3: 核对题集**

```bash
python - <<'EOF'
import json
p="data/deepeval/testset/testset_4a1dcb8bb340473c8149ead5b7f75873_v6.json"
d=json.load(open(p))
m=d["metadata"]
print("size", m["testset_size"], "hop", m["hop_stats"], "gate", m["gate_stats"])
for s in d["samples"][:3]:
    print(s["hop_type"], s["hop_basis"], s["expected_tools"], "|", s["user_input"][:60])
EOF
```

Expected：`testset_size >= 15`；`multi_hop / total <= 1/3`；所有 sample 有 `expected_tools`；header 含中文公司名与年份。

- [ ] **Step 4: 校验 gate 对账**

```python
assert m["gate_stats"]["generated"] - sum(m["gate_stats"]["dropped"].values()) == m["testset_size"]
```

- [ ] **Step 5: 提交验收记录**

把 dry-run 与正式运行的数值写入 `docs/superpowers/plans/2026-10-18-deepeval-synthesizer-testset.md` 的「执行记录」小节，然后：

```bash
git add docs/superpowers/plans/2026-10-18-deepeval-synthesizer-testset.md
git commit -m "docs(plan): record v6 testset generation result"
```

---

## Task 12: 5 题冒烟、21 题 baseline 与收尾

- [ ] **Step 1: 5 题冒烟**

```bash
python -m src.cli.eval_deepeval --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 6 --run-type baseline --limit 5
```

Expected：无 400/超时；报告头 `Testset version: 6`；`tool_correctness` 仍为 `null`。

- [ ] **Step 2: 21 题全量 baseline**

```bash
python -m src.cli.eval_deepeval --kb-id 4a1dcb8bb340473c8149ead5b7f75873 \
  --testset-version 6 --run-type baseline
```

记录四项均值、诊断均值、NaN 格数与耗时；确认报告头与 DB `detail_json` 的 `testset_version=6`。

- [ ] **Step 3: 台账追加 v6 行**

在 `docs/agents/eval-ledger.md` 追加：

```markdown
| 7 | 2026-10-18 | deep-eval-native / v6 | <n> | <四项均值> | <诊断均值> | <NaN格数/分母> | 未启用 | --run-type baseline；题集差异单独归因 |
```

历史行不改；若 `kept` 在 15–18，标注「题量偏低」。

- [ ] **Step 4: 收尾校验**

```bash
POSTGRES_HOST=localhost pytest tests/ -q
POSTGRES_HOST=localhost python -m src.cli.check_docs
POSTGRES_HOST=localhost python -m src.cli.check_adr
openspec validate deepeval-synthesizer-testset --strict
git status --short
```

Expected：测试全绿；三项校验 0 error；工作区干净。

- [ ] **Step 5: 提交**

```bash
git add docs/agents/eval-ledger.md docs/superpowers/plans/2026-10-18-deepeval-synthesizer-testset.md
git commit -m "docs(eval): record v6 baseline and acceptance"
```

---

## Self-Review

**Spec coverage**

| Spec 要求 | 对应任务 |
|---|---|
| 从 KB 分块出题、默认全部文档、显式版本配 `--docs` | Task 2、6 |
| 题面/答案语言 = 上下文语言 | Task 4 |
| 参考答案自包含完整句 / 数值主体 | Task 4 |
| 多跳 ≤1/3 + `hop_basis` | Task 4 |
| `expected_tools` 统一 `retrieve_kb` | Task 5 |
| `numeric_risk` 登记 | Task 4、5 |
| 覆盖可追溯 / `company_source` | Task 2、5 |
| schema 适配 + 有界重试 + 单 context 隔离 | Task 1、3 |
| 原子写 / `--force` / `<15` 重跑 / 语言早停 | Task 5、6 |
| 日志 `synth` 前缀 + 三个 info + 失败 warning | Task 7 |
| `testset_version` 报告/MD/detail | Task 8 |
| v5 保留、v6 默认最新 | Task 6、12 |

**Placeholder scan**

- 无 TBD/TODO/「类似 Task N」。
- 所有测试与实现均给出可执行代码。
- 文档任务给出具体段落与命令。

**Type consistency**

- `build_contexts` 返回 `list[ContextGroup]`；`_synthesize` 接收同一类型。
- `golden_to_sample` 返回 dict；`run_gates` 与 `_synthesize` 使用同一 dict 形状。
- `run_gates` 的 `stats` 键与 `build_metadata` 读取的键一致。
- `resolve_testset_version` / `next_testset_version` 分别用于评测与出题，不混用。

---

## 执行记录（Task 11，2026-10-18）

### dry-run

```
contexts=25 chunks=51 covered=50
calls=每 context 5-8 次，单轮约 125-200
```

生成前目标文件不存在；复跑 dry-run 时 v6 已存在，提示需 `--force`，与预期一致。

### 正式出题

`EVAL_SYNTH_GOLDENS_PER_CONTEXT=1` 首轮，`kept < 15` 时按 2 重跑一次。

| 运行 | generated | kept | 结果 |
|---|---|---|---|
| 1（旧门，无锚定） | 25 | 16 | 写出 v6；发现跨期/跨公司穿门，补 `check_grounding` |
| 2（首轮 13 → 重跑） | — | 13 | `13 < 15`，未写文件，exit 1 |
| 3（重跑 goldens=2） | 50 | 22 | 写出 v6，耗时 146.3s，`failed_contexts=0` |

第 3 次 `gate_stats`：

```
generated=50 kept=22 language_checked=25 self_contained_rewritten=4
dropped={self_contained:0, grounding:5, not_complete_sentence:20,
         language:3, duplicate:0, hop_ratio:0}
```

### 核对（22 题）

- `testset_size == len(samples) == kept == 22`；`generated - Σdropped = 50 - 28 = 22`。
- `hop_stats`：`single_hop=16`、`multi_hop=6`，占比 27.3% ≤ 1/3。
- 文档覆盖：`neusoft_2025_q1.pdf` 7 题、`tencent_2024_annual.pdf` 15 题，两文档均覆盖。
- 每个样本 `reference_contexts` 均为 2 块；首行全部为 `【中文公司全称 · 报告期】`，年份与文档一致。
- `expected_tools` 全部为 `["retrieve_kb"]`；`source_chunks=51`、`covered_chunks=50`（余 1 块为尾单块）。

### 已知质量问题（不阻塞验收，评测时需计入噪声）

- 语义重复：第 14、15 题问同一组「盈利 / 资本开支」，去重门按题干归一化无法识别。
- 期间错标：第 14 题题干写「2024年」，参考答案用的是 Q4 数据（盈利 515 亿、资本开支 366 亿），与第 9 题的全年口径（+66% / +221%）冲突；锚定门只校验年份与实体出现，校验不了口径。
- 缺单位：第 20–22 题参考答案只有数字（如「1,284,815」），没带千元 / 百万元单位。
- 题干预设因果：第 8 题问「如何推动收入增长 8%」，参考答案只给营收数，未回答因果。

`not_complete_sentence` 丢弃 20/50（40%）为当前提示词下的稳定现象，两次运行分别 36% / 40%。

---

## 执行偏差（2026-10-18）

### 1. Task 3 返回 `(Golden, ContextGroup)` 而非 sample

`_synthesize` 返回 pairs，转换留给 Task 6 的 `_run`。原因：Task 3 若直接 import Task 5 的 `golden_to_sample` 会形成循环依赖。

### 2. 新增锚定门（Task 4）

v6 首轮实测发现两类题穿过了语言/自足门：

- 「东软集团**2024年**第一季度…」但上下文是 2025 Q1；
- 「腾讯…**贵州茅台2024年**年报…」上下文里没有贵州茅台。

因此 Task 4 增加 `check_grounding`，门顺序变为「自足 → 锚定 → 完整句 → 语言 → 去重 → 多跳」，`gate_stats.dropped` 增加 `grounding`。已回写 `design.md` D9 与 `specs/testset-generation-deepeval/spec.md`。

### 3. CLI 参数拆出 `testset_synth_args.py`

`testset_synth.py` 原为 405 行（>400 红线），`parse_args` 拆到独立模块后为 380 行。

### 4. Worktree 链接 gitignored 资源

执行环境中 worktree 缺少 `.env` / `data` / `.venv`，已创建软链；否则 DB、报告目录与 pre-commit 不可用。

### 5. 提示词收紧与失败分支补 `SYNTH_GATE`

第 2 次出题 `kept=13` 失败时只有一行 `error:`，拿不到门统计。因此在早停失败分支补记 `SYNTH_GATE`，并把 `StylingConfig` 的 `task` / `input_format` 明确为「只许上下文所属公司 + 报告期年份、不得提及其他公司、不得使用不一致年份」。调整后第 3 次出题 `kept=22`。
