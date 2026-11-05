"""profile → 能力档映射（唯一归属）。

能力档决定工具面与轮次预算；人设不进档（运行期由 ctx.persona 施加）。
基础档轮次预算直接用 MAX_AGENT_ITERATIONS，本模块不重复定义。
"""

DEFAULT_CAPABILITY = "default"  # 基础档：检索/联网/任务工具，5 轮
LOG_CAPABILITY = "log"  # 日志档：基础档 + 只读日志工具，6 轮
LOG_PROFILES = frozenset({"bot-ops", "bot-dev"})  # 具备日志能力的 profile 名
LOG_PROFILE_MAX_TURNS = 6  # 日志档轮次预算（默认档用 MAX_AGENT_ITERATIONS）


def capability_of(profile: str) -> str:
    """把 profile 名映射为能力档；未知/空一律基础档。

    Args:
        profile: 本轮生效的 profile 名（空串 = 通用人设）

    Returns:
        能力档名：default 或 log
    """
    if profile in LOG_PROFILES:
        return LOG_CAPABILITY
    return DEFAULT_CAPABILITY
