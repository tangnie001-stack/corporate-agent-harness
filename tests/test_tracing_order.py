"""configure-before-flush 顺序契约（承接 Task 2 义务，R17）。

`flush_tracing()` → `langfuse_context.flush()` → `LangfuseSingleton().get()` 会
惰性构造客户端，落入 SDK 的默认 `enabled=True`，绕过 `settings.LANGFUSE_ENABLE`。
因此「configure 必须早于任何 flush」是开关真正生效的必要条件。既有用例只断言
两个函数名出现在源码里，未约束顺序；本文件把顺序钉成可回归的契约。

两条入口各一例：
  - 服务侧：`src/main.py` 的 lifespan —— configure 在 yield 前，flush 在关停半段；
  - CLI 侧：`src/cli/eval_deepeval.py` 的 main() —— 不经 lifespan，自行 configure/flush。

断言用记录器替换两个真实函数，全程不触碰 SDK、不发网络。
"""

import argparse

import pytest


@pytest.mark.asyncio
async def test_lifespan_configures_before_flushing(monkeypatch):
    """服务侧：进入 lifespan 体之前必须已 configure；flush 只在关停时发生。"""
    import src.main as main_mod

    calls: list[str] = []

    def _record_configure() -> None:
        """记录 configure 调用。"""
        calls.append("configure")

    def _record_flush() -> None:
        """记录 flush 调用。"""
        calls.append("flush")

    async def _noop_clear_stale_locks() -> None:
        """替掉真实 Redis 清理，避免测试触碰外部依赖。"""
        return

    def _stub_validate_all() -> dict[str, int]:
        """替掉 prompt 模板校验，避免导入/校验副作用。"""
        return {}

    monkeypatch.setattr(main_mod, "configure_tracing", _record_configure)
    monkeypatch.setattr(main_mod, "flush_tracing", _record_flush)
    monkeypatch.setattr(main_mod, "_clear_stale_chat_locks", _noop_clear_stale_locks)
    monkeypatch.setattr(main_mod.validation, "validate_all", _stub_validate_all)

    async with main_mod.lifespan(main_mod.app):
        # 此刻尚未关停：configure 必须已经发生，flush 必须还没有
        assert calls == ["configure"]

    assert calls == ["configure", "flush"]


def test_cli_main_configures_before_flushing(monkeypatch):
    """CLI 侧：main() 的 configure 必须早于 finally 里的 flush。

    用 `_load_latest_testset` 抛 FileNotFoundError 把流程停在第一次真正评估之前，
    这条路径会走 `sys.exit(1)` → `finally: flush_tracing()`。断言序列恰为
    configure → flush，即可证明 flush 没有先于 configure 构造客户端。
    """
    import src.cli.eval_deepeval as cli
    from src.cli import testset_io

    calls: list[str] = []

    def _record_configure() -> None:
        """记录 configure 调用。"""
        calls.append("configure")

    def _record_flush() -> None:
        """记录 flush 调用。"""
        calls.append("flush")

    def _raise_file_not_found(*_args: object, **_kwargs: object) -> None:
        """在加载测试集处中断，避免进入 RAGAS 评估与网络调用。"""
        raise FileNotFoundError("stop before any real evaluation")

    monkeypatch.setattr(cli, "configure_tracing", _record_configure)
    monkeypatch.setattr(cli, "flush_tracing", _record_flush)
    monkeypatch.setattr(testset_io, "_load_latest_testset", _raise_file_not_found)
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(
            list_kbs=False,
            kb_id="kb-order-test",
            generate=False,
            session_id=None,
            output=None,
            testset_version=None,
            deepeval_only=False,
        ),
    )

    with pytest.raises(SystemExit):
        cli.main()

    assert calls == ["configure", "flush"]
