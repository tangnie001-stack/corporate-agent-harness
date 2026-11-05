"""SQLAlchemy 异步引擎与 Session 工厂。"""

from collections.abc import Awaitable
from typing import TypeVar

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from src.config import build_postgres_dsn

_T = TypeVar("_T")

DSN = build_postgres_dsn()

engine = create_async_engine(
    DSN,
    # 连接预算是共享的：同一 PostgreSQL 实例还承载 Langfuse。
    # prod 实为 --workers 4，4 × (10 + 10) ≈ 80 连接，须作为 RDS 规格的输入。
    pool_size=10,
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def run_and_dispose(coro: Awaitable[_T]) -> _T:  # noqa: UP047 — 存量泛型写法，改 PEP 695 需同步 3.11 兼容口径
    """在同一事件循环里 await coro，并在返回前释放连接池。

    asyncpg 的连接绑定创建它的事件循环；同一进程内第二次 asyncio.run 复用同一池
    会抛 "different loop"。在每次 run 的边界释放池，使下一次 run 重建连接。

    Args:
        coro: 该次 run 的顶层协程

    Returns:
        coro 的返回值
    """
    try:
        return await coro
    finally:
        await engine.dispose()
