"""tests/services 公共 fixture：企微服务模块级状态的跨模块复位。

`wecom_service` 的 `_drivers` / `_bot_key_by_aibotid` 是模块级全局，多个测试
模块都会改写它；用 autouse fixture 保证每条用例前后归零，避免相互污染。
"""

import pytest

from src.services import wecom_service


@pytest.fixture(autouse=True)
def _reset_state():
    """用例前后复位 wecom_service 的模块级注册表。"""
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}
    wecom_service._profile_by_key = {}
    yield
    wecom_service._drivers = {}
    wecom_service._bot_key_by_aibotid = {}
    wecom_service._profile_by_key = {}
