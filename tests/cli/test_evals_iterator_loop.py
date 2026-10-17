"""钉住 evals_iterator 在项目单个 asyncio.run 内重入同一事件循环（tasks 5.1 / design D6）。

机制：deepeval 的 `get_or_create_event_loop()` 在 `loop.is_running()` 时调
`nest_asyncio.apply()`，使内层 `run_until_complete` 在**同一个** loop 上重入、
不新建第二个 loop ⇒ 项目"单事件循环 + 引擎连接池"假设不破。

⚠️ `nest_asyncio.apply()` 是**进程级 monkeypatch**（替换 asyncio.run / Task / policy）；
仅因评测 CLI 是短命进程可接受（design D6）。
"""

import asyncio


def test_evals_iterator_reenters_running_loop_without_new_loop():
    """重入点：取到 loop 后再 `run_until_complete` 不得抛「already running」。

    若无 `nest_asyncio.apply()`，内层 `run_until_complete` 会抛
    `RuntimeError: This event loop is already running` ⇒ 本测试失败。
    """
    from deepeval.utils import get_or_create_event_loop

    seen: dict[str, bool] = {}

    async def _noop() -> None:
        return None

    async def _drive() -> None:
        loop = get_or_create_event_loop()
        seen["same_loop"] = loop is asyncio.get_running_loop()
        # 真正的重入点：未 apply nest_asyncio 时这里抛 "already running"
        loop.run_until_complete(_noop())

    asyncio.run(_drive())

    assert seen["same_loop"] is True, "须重入同一运行中的 loop，不得新建第二个事件循环"
