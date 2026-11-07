"""节点脱敏：IPv4[:port] → 节点别名（日志工具与诊断卡片同源）。

同一份日志经两条链路（ops-log-tools / 诊断卡片）展示时，脱敏必须一致；
本模块是唯一实现，调用方不得各写一份正则。
"""

from __future__ import annotations

import re

MASKED_NODE = "node-***"  # 替代值：不暴露真实节点地址
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")


def mask_nodes(text: str) -> str:
    """把文本里的 IPv4[:port] 替换成节点别名。

    Args:
        text: 待脱敏文本

    Returns:
        替换后的文本；无匹配时原样返回
    """
    return _IPV4.sub(MASKED_NODE, text)
