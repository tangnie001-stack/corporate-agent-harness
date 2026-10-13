"""测试共享 fixture 和配置。

提供：
  - AppService / VectorStore 实例
  - 测试知识库生命周期（创建 / 销毁）
  - 测试文档路径和数据库验证辅助函数

并且在导入任何业务模块之前**关停 Langfuse tracing**（见下）。
"""

from __future__ import annotations

import os

# ⚠️ 必须位于所有 src.* 导入之前：src/config/settings.py 在**导入时**读取环境变量，
# 此后再设等于无效。没有这一行，测试会构造真实 Langfuse 客户端并向外上报。
os.environ["LANGFUSE_ENABLE"] = "false"

# 同理，把 provider 选择钉成 registry 默认（dashscope），使测试与开发者宿主
# `.env` 的 `LLM_PROVIDER` 解耦 —— 否则开发者一旦把 `.env` 切到别的 provider
# （如 Phase 2 切 deepseek），一批断言 provider 形状/默认模型的用例会随环境变红。
# `load_dotenv()` 默认**不覆盖**已存在的环境变量，故这一行（先于其执行）生效。
# 需要测其它 provider 的用例请显式 `monkeypatch.setenv("LLM_PROVIDER", …)`
# 或给 `resolve_role` / `build_*` 传 `env` 参数。
os.environ["LLM_PROVIDER"] = "dashscope"

# 同理清空裁判模型覆盖，使 judge 角色回落到 registry 默认（dashscope/qwen3.8-max）：
# `RAGAS_LLM_MODEL` 也是开发者会随宿主 `.env` 改的配置，留空即与 `.env` 解耦。
# 需要测覆盖行为的用例请显式 `monkeypatch.setenv("RAGAS_LLM_MODEL", …)`。
os.environ["RAGAS_LLM_MODEL"] = ""


def _is_resolvable(host: str, port: int) -> bool:
    """host:port 能否被本进程解析（只做 DNS，不发起连接）。

    Args:
        host: 主机名
        port: 端口

    Returns:
        True 表示可解析；False 表示解析不了。
    """
    try:
        socket.getaddrinfo(host, port)
    except OSError:
        return False
    return True


def pytest_configure() -> None:
    """宿主侧漏加 `POSTGRES_HOST=localhost` 前缀时，给一句可执行的报错。

    为什么需要它：`.env` 里 `POSTGRES_HOST` 是 compose 服务名 `postgres`，宿主解析不了；
    此时每个 DB 用例都会对着不可解析的主机名连接 —— 实测得到 65 个 `socket.gaierror`，
    整套从 4m54s 慢到 19 分钟。与其让 65 个用例各失败一次，不如在这里提前拦下并给出要敲的命令。

    容器内不受影响：容器里 `postgres` 可解析，本守卫直接放行。

    只跑非 DB 用例时可用 `SKIP_PG_HOST_GUARD=1` 显式跳过本检查（不做隐式自动纠正）。
    """
    if os.getenv("SKIP_PG_HOST_GUARD") == "1":
        return

    from src.config.settings import POSTGRES_HOST, POSTGRES_PORT

    if POSTGRES_HOST == "localhost":
        return
    if _is_resolvable(POSTGRES_HOST, POSTGRES_PORT):
        return
    pytest.exit(
        f"POSTGRES_HOST={POSTGRES_HOST!r} 解析不了（.env 里是 compose 服务名），"
        "宿主侧跑 pytest 请加前缀：\n"
        "    POSTGRES_HOST=localhost pytest tests/\n"
        "容器内跑不要加（容器用服务名）。\n"
        "本次只跑非 DB 用例、或容器/CI 自备连通性时，可设 SKIP_PG_HOST_GUARD=1 跳过本检查。\n"
        "约定见 CLAUDE.md「验证」与 docs/agents/cookbook.md「宿主跑必须 POSTGRES_HOST=localhost」",
        returncode=1,
    )


import asyncio
import socket
import uuid
from collections.abc import Generator

import pytest
from loguru import logger

from src.infra.db.vector_store import VectorStore
from src.services.app_service import AppService

# ==================== 路径常量 ====================

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_DOCS_DIR = os.path.join(PROJECT_ROOT, "data", "test_docs")

# ==================== 数据库直连验证 ====================


@pytest.fixture(scope="session")
def service() -> Generator[AppService, None, None]:
    """提供 AppService 实例，用于直接验证数据库状态。"""
    svc = AppService()
    yield svc


@pytest.fixture(scope="session")
def vector_store() -> Generator[VectorStore, None, None]:
    """提供 VectorStore 实例（PG 后端），用于验证分块存储状态。"""
    vs = VectorStore()
    yield vs


# ==================== 测试知识库生命周期 ====================


@pytest.fixture
def test_kb_name() -> Generator[str, None, None]:
    """生成唯一的测试知识库名称，teardown 时自动删除。

    每次调用生成形如 __test__<uuid6> 的唯一名称，
    确保并发测试时不会撞名。

    删除时同时清理数据库记录和分块数据。
    """
    unique_id = uuid.uuid4().hex[:8]
    name = f"__test__{unique_id}"
    yield name

    # Teardown：清理测试知识库
    _cleanup_kb(name)


def _cleanup_kb(name: str) -> None:
    """根据知识库名称删除对应的数据库和向量数据。"""
    try:
        svc = AppService()

        async def _do_cleanup():
            all_kbs = await svc._kb_repo.get_all_kb()
            for kb in all_kbs:
                if kb.name == name:
                    await svc._kb_repo.delete_kb(kb.id)
                    await svc.vector_store.delete_collection(kb.id)
                    logger.info("Cleaned up test KB: {} ({})", name, kb.id)
                    return

        asyncio.run(_do_cleanup())
    except Exception:  # noqa: BLE001
        logger.exception("Failed to cleanup test KB: {}", name)


# ==================== 测试文档路径辅助 ====================


def get_test_doc_path(filename: str) -> str:
    """获取测试文档的完整路径。

    Args:
        filename: 测试文档的文件名（如 sample.pdf）

    Returns:
        文件的完整路径

    Raises:
        FileNotFoundError: 文件不存在
    """
    path = os.path.join(TEST_DOCS_DIR, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"测试文档不存在: {path}")
    return path


@pytest.fixture
def corrupted_file_path(tmp_path) -> str:
    """生成一个损坏的文件（非法内容），用于测试异常上传场景。"""
    filepath = os.path.join(tmp_path, "corrupted.pdf")
    with open(filepath, "wb") as f:
        # 写入非 PDF 二进制头，不足以通过解析器校验
        f.write(b"\x00\x00\x00\x00corrupted content")
    return filepath


# ==================== Langfuse tracing 兜底关停 ====================


@pytest.fixture(scope="session", autouse=True)
def _disable_langfuse_tracing() -> Generator[None, None, None]:
    """兜底关停 tracing，并保证退出前把 SDK 缓冲清掉。

    上面那行环境变量是主手段（须早于导入）；本 fixture 是第一道保险 ——
    防止将来有人重构 conftest 的导入顺序时静默失效。真正需要会话级的是末尾的
    `flush()`（把 SDK 缓冲清掉只能在整个会话结束时做）。
    """
    from langfuse.decorators import langfuse_context

    from src.config import settings

    settings.LANGFUSE_ENABLE = False
    langfuse_context.configure(enabled=False)
    yield
    langfuse_context.flush()


@pytest.fixture(autouse=True)
def _rearm_langfuse_kill_switch() -> Generator[None, None, None]:
    """每个用例前重新压上关停开关（会话级那次会被中途重新武装掉）。

    为什么必须每例重设 settings 属性，而不是只靠上面的会话级 fixture：
    `tests/config/test_settings.py::test_langfuse_enable_default_true` 会
    `reload(src.config.settings)`（并临时 `pop` 掉 `LANGFUSE_ENABLE`）；reload 会
    重新执行模块里的 `os.getenv("LANGFUSE_ENABLE", "true")` 赋值，把
    `settings.LANGFUSE_ENABLE` 变回 `True` 并一直保留到会话结束。若只在会话开头
    关停一次，该用例之后的所有用例都会在 tracing 打开的状态下运行 —— 本任务要立
    的「恒为关停」不再成立。

    为什么 SDK 侧只在「已经是 enabled」时才重设：`configure()` 走
    `LangfuseSingleton.reset()` → `shutdown()` → `flush()` + `join()`，会阻塞等待
    SDK 消费线程从轮询超时（ingestion `flush_interval` 0.5s、media `queue.get` 1s）
    中醒来 —— 实测重新配置一次约 **2.0 s**（reset 约 2.0 s，客户端构建仅约 0.08 s）。
    若逐例无条件调用，1184 条用例会平白多出约 40 分钟。这一行不能删：Task 2 会故意
    在用例内打开 tracing，这道逐例的条件重设是约零成本的安全网，用来挡住那种会污染
    后续所有用例（真发网络）的泄漏。
    """
    from langfuse.decorators import langfuse_context

    from src.config import settings

    settings.LANGFUSE_ENABLE = False

    client = langfuse_context.client_instance
    if client.enabled:
        # 客户端被重新打开（如 Task 2 故意开启）：付约 2.0 s 的 reset 代价按回去
        langfuse_context.configure(enabled=False)
    yield
