"""钉住 evals_iterator 在项目单个 asyncio.run 内重入同一事件循环（tasks 5.1 / design D6）。

机制：deepeval 的 `get_or_create_event_loop()` 在 `loop.is_running()` 时调
`nest_asyncio.apply()`，使内层 `run_until_complete` 在**同一个** loop 上重入、
不新建第二个 loop ⇒ 项目"单事件循环 + 引擎连接池"假设不破。
"""

import asyncio


def test_evals_iterator_reenters_running_loop_without_new_loop():
    """运行中取 loop 须返回当前运行中的同一个 loop，而非新建第二个。

    `get_or_create_event_loop()` 若未 apply nest_asyncio，会在 `asyncio.run` 内抛
    "This event loop is already running"，或返回一个未运行的 loop。
    """
    from deepeval.utils import get_or_create_event_loop

    seen: dict[str, bool] = {}

    async def _drive() -> None:
        """在运行中的 loop 内取 loop，并记录其身份与运行状态。"""
        loop = get_or_create_event_loop()
        seen["same_loop"] = loop is asyncio.get_running_loop()
        seen["running"] = loop.is_running()

    asyncio.run(_drive())

    assert seen["same_loop"] is True, (
        "须返回当前运行中的同一 loop，不得新建第二个事件循环"
    )
    assert seen["running"] is True, "须重入同一运行中的 loop，不得新建第二个事件循环"
