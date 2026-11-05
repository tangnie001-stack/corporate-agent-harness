"""日志工具的固定文案与边界值（用户/模型可见，集中管理）。

文案随工具返回直接进入模型上下文，故统一在此登记，避免散落在工具实现里；
边界值与超时同属工具行为契约，一并放在本模块。
"""


class LogToolTexts:
    """日志工具返回文案；`{}` 占位由调用方填充。"""

    UNAVAILABLE = "查询失败：日志服务不可用"  # 未注入后端（含启动期 MCP 连接失败降级）
    TIMEOUT = "查询失败：超时"  # 单次调用超过 LOG_TOOL_TIMEOUT_SECONDS
    FAILED_PREFIX = "查询失败："  # 其它取数失败的统一前缀，后接异常原因
    NO_ROWS = "近 {minutes} 分钟无请求记录"  # 查到但无数据（empty ≠ failed）
    URI_NOT_ALLOWED = "该接口不在可查询范围：{uri}。可选接口：{options}"  # 白名单未命中
    NO_URI_CONFIGURED = "（未配置可查询接口）"  # 白名单为空时的可选接口占位
    MINUTES_OUT_OF_RANGE = "参数错误：minutes 必须在 {min_minutes}-{max_minutes} 之间"
    LIMIT_OUT_OF_RANGE = "参数错误：limit 必须在 1-{max_limit} 之间"


LOG_TOOL_TIMEOUT_SECONDS: float = 8.0  # 单次工具调用超时（秒）；超时即失败，不重试
MIN_MINUTES: int = 1  # minutes 下界（分钟）
MAX_MINUTES: int = 360  # minutes 上界（分钟，6 小时）
DEFAULT_MINUTES: int = 15  # minutes 默认值（模型未指定时回看 15 分钟）
MAX_LIMIT: int = 20  # 明细返回行数上限
DEFAULT_SLOW_MS: int = 1000  # 慢请求阈值默认值（毫秒）
