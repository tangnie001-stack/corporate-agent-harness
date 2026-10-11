"""Task 12: 启动期幂等定价 ensure（非阻塞）单测。

覆盖四件事：
  1. 有价 → 幂等创建模型定义（同一模型被多角色绑定只创建一次）；
  2. 无价 → WARNING 且**不**创建零价定义（0 是"未配置"哨兵，写零价会静默低估成本）；
  3. Langfuse 后端不可用 → 不抛异常、不阻断启动（降级一行 warning）；
  4. LANGFUSE_ENABLE=false → 直接跳过，不触碰 client。

`ensure_model_pricing` 经 `resolve_role` 读 `os.environ`，故每个用例都先把 4 个绑定
角色的 provider/model 键钉到测试自建的 fake registry，避免本 worktree `.env`（软链，
已定义 `LLM_MODEL=kimi-k3` 等）污染解析。Langfuse 日志走 loguru（不传播到 stdlib），
断言告警不能用 caplog，改用 `logger.add` 收集器（沿用 test_settings_migration.py 手法）。
"""

from __future__ import annotations

from loguru import logger as _logger

from src.infra.llm.provider import pricing_seed
from src.infra.llm.provider.pricing_seed import ensure_model_pricing
from src.infra.llm.provider.spec import ModelFacts, ProviderSpec, ThinkingStyle


class _Resp:
    """models.list 的分页响应壳。"""

    def __init__(self, data):
        #: 本页返回的行
        self.data = data


class _FakeModels:
    """伪造 `client.api.models`：list 返回既有模型名，create 收集请求。"""

    def __init__(self, existing=None):
        #: Langfuse 侧已存在的模型名
        self.existing = list(existing or [])
        #: 本次 create 收到的请求
        self.created = []

    def list(self, page=1, limit=100):
        if page == 1:
            return _Resp(list(self.existing))
        return _Resp([])

    def create(self, *, request):
        self.created.append(request)


class _BoomModels:
    """list 即抛错，模拟 Langfuse 后端不可达。"""

    def list(self, page=1, limit=100):
        raise RuntimeError("langfuse down")


class _FakeApi:
    def __init__(self, models):
        self.models = models


class _FakeClient:
    def __init__(self, models):
        self.api = _FakeApi(models)


def _registry(pricing_by_model):
    """构造单 provider registry：main/classify/summary 绑 paid-x，judge 绑 free-x。

    Args:
        pricing_by_model: 模型名 → (input, output) 单价；None 表示 catalog 无定价。

    Returns:
        `{provider 名: ProviderSpec}`，可直接喂给 `ensure_model_pricing`。
    """
    catalog = {}
    for model, price in pricing_by_model.items():
        if price is None:
            catalog[model] = ModelFacts()
        else:
            catalog[model] = ModelFacts(pricing=price)
    spec = ProviderSpec(
        name="fake",
        base_url="http://fake",
        api_key_env="FAKE_API_KEY",
        thinking_style=ThinkingStyle.DASHSCOPE,
        reasoning_field=None,
        models={
            "main": "paid-x",
            "classify": "paid-x",
            "summary": "paid-x",
            "judge": "free-x",
        },
        catalog=catalog,
    )
    return {"fake": spec}


def _pin_env(monkeypatch):
    """把 4 个绑定角色的 provider 钉到 fake，并清掉模型 env 覆盖。"""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("CLASSIFY_PROVIDER", "fake")
    monkeypatch.setenv("SUMMARY_PROVIDER", "fake")
    monkeypatch.setenv("RAGAS_PROVIDER", "fake")
    for key in ("LLM_MODEL", "CLASSIFY_MODEL", "SUMMARY_MODEL", "RAGAS_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(pricing_seed.settings, "LANGFUSE_ENABLE", True)


def _collect_warnings():
    """挂一个 loguru WARNING sink，返回 (messages, sink_id)。"""
    messages: list[str] = []
    sink_id = _logger.add(lambda m: messages.append(str(m)), level="WARNING")
    return messages, sink_id


def test_paid_model_created_once(monkeypatch):
    """有价模型被 ensure；三个角色绑定同一模型只创建一次（幂等）。"""
    _pin_env(monkeypatch)
    models = _FakeModels(existing=[])
    reg = _registry({"paid-x": (0.000001, 0.000002), "free-x": None})

    ensure_model_pricing(reg, client=_FakeClient(models))

    assert [r.model_name for r in models.created] == ["paid-x"]


def test_no_price_warns_and_skips(monkeypatch):
    """catalog 无定价 → WARNING 且不创建任何零价定义。"""
    _pin_env(monkeypatch)
    models = _FakeModels(existing=[])
    reg = _registry({"paid-x": None, "free-x": None})
    messages, sink_id = _collect_warnings()
    try:
        ensure_model_pricing(reg, client=_FakeClient(models))
    finally:
        _logger.remove(sink_id)

    assert models.created == []
    assert any("[provider]" in m and "无定价" in m for m in messages)


def test_existing_model_not_recreated(monkeypatch):
    """同名定义已存在 → decide 判 exists，不重复创建。"""
    _pin_env(monkeypatch)
    models = _FakeModels(existing=["paid-x"])
    reg = _registry({"paid-x": (0.000001, 0.000002), "free-x": None})

    ensure_model_pricing(reg, client=_FakeClient(models))

    assert models.created == []


def test_backend_unavailable_is_swallowed(monkeypatch):
    """Langfuse 不可用 → 不抛异常，降级记一行 warning。"""
    _pin_env(monkeypatch)
    reg = _registry({"paid-x": (0.000001, 0.000002)})
    messages, sink_id = _collect_warnings()
    try:
        # 不抛即通过
        ensure_model_pricing(reg, client=_FakeClient(_BoomModels()))
    finally:
        _logger.remove(sink_id)

    assert any("[provider]" in m and "降级" in m for m in messages)


def test_disabled_langfuse_skips(monkeypatch):
    """LANGFUSE_ENABLE=false → 直接返回，不触碰 client。"""
    _pin_env(monkeypatch)
    monkeypatch.setattr(pricing_seed.settings, "LANGFUSE_ENABLE", False)
    models = _FakeModels(existing=[])
    reg = _registry({"paid-x": (0.000001, 0.000002)})

    ensure_model_pricing(reg, client=_FakeClient(models))

    assert models.created == []
