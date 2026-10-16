"""DeepEval 环境变量兜底：必须在 `deepeval` 被 import 之前立起来。"""

import importlib
import os

import src.config.settings as settings_mod


def test_deepeval_env_defaults_are_set_on_settings_import():
    """settings 被 import/reload 时，两个 DeepEval 兜底变量必须就位。

    先 pop 再 reload（而非裸 import）：模块若已被本会话其它用例 import，
    裸 import 命中缓存不会重跑模块级 `os.environ.setdefault`，用例会随执行顺序失效。
    """
    os.environ.pop("DEEPEVAL_DISABLE_DOTENV", None)
    os.environ.pop("DEEPEVAL_TELEMETRY_OPT_OUT", None)

    importlib.reload(settings_mod)

    assert os.environ["DEEPEVAL_DISABLE_DOTENV"] == "1"
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"


def test_autoload_dotenv_is_disabled(tmp_path, monkeypatch):
    """flag 为真时，`deepeval` 的 autoload 不得把 cwd 的 .env 注入 process env。

    直接调 `deepeval.config.settings.autoload_dotenv()`（`deepeval/__init__.py:12`
    在 import 时调的就是它），并在临时目录放一个真 .env + `chdir` 过去——
    否则断言会变成"恒真"的空测试（仓库里没有 `SENTINEL_FROM_DOTENV` 这种键）。
    """
    # 自足前提：不依赖 test 1 先跑，用 reload 强制模块级 setdefault 重放
    os.environ.pop("DEEPEVAL_DISABLE_DOTENV", None)
    importlib.reload(settings_mod)

    (tmp_path / ".env").write_text("SDD_DOTENV_SENTINEL=1\n")
    monkeypatch.chdir(tmp_path)
    os.environ.pop("SDD_DOTENV_SENTINEL", None)

    from deepeval.config.settings import autoload_dotenv

    autoload_dotenv()

    assert "SDD_DOTENV_SENTINEL" not in os.environ, "flag 为真时 autoload 必须早退"
