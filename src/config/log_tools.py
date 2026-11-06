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
    LATENCY_TOTAL = "样本量 {total} 条"  # 聚合摘要第 1 行
    LATENCY_SPAN = "耗时 {parts}"  # 聚合摘要分位行，parts 形如 "p50 30ms / p95 800ms"
    LATENCY_SLOW = "慢请求 {count} 条"  # 超过 slow_ms 阈值的请求数
    LATENCY_NODES = "分布 {parts}"  # 节点分布行，parts 由 NODE_PART 拼接
    LATENCY_ERRORS = "5xx {count} 条"  # 上游 5xx 计数
    NODE_PART = "node-{index}: {count} 条"  # 节点分布中的单节点片段
    NODE_MORE = "另 {count} 个节点"  # 分布行超出 MAX_NODE_PARTS 时的收尾片段
    NODE_ALIAS = "node-{index}"  # 明细行的节点别名（按 upstream_addr 首次出现顺序编号）
    NODE_UNKNOWN = "node-?"  # 明细行缺 upstream_addr 时的占位别名（不臆造节点号）
    MASKED_NODE = "node-***"  # 兜底脱敏：任何漏出的 IPv4[:port] 替换值
    NO_STATS = "无可用统计"  # 聚合结果整段缺失时的返回值（区别于「各项为 0」）
    MISSING_VALUE = "N/A"  # 明细行数值字段缺失时的占位（不把「没采到」写成 0）
    DETAIL_TRUNCATED = (
        "本次取样 {sampled} 条，仅显示最慢 {shown} 条"  # 明细被裁剪时的末行
    )


LOG_TOOL_TIMEOUT_SECONDS: float = 8.0  # 单次工具调用超时（秒）；超时即失败，不重试
PROBE_TIMEOUT_SECONDS: float = 5.0  # 启动期探测整体超时（秒）；超时按服务不可用降级
MIN_MINUTES: int = 1  # minutes 下界（分钟）
MAX_MINUTES: int = 360  # minutes 上界（分钟，6 小时）
DEFAULT_MINUTES: int = 15  # minutes 默认值（模型未指定时回看 15 分钟）
MAX_LIMIT: int = 20  # 明细展示行数上限（工具参数 limit 的上界）
DETAIL_FETCH_LIMIT: int = 100  # 明细取数上限（服务端返回行数），与展示上限解耦
MAX_LATENCY_LINES: int = 5  # 聚合摘要行数上限（模型上下文预算）
MAX_NODE_PARTS: int = 5  # 分布行最多列出的节点数（其余以 NODE_MORE 收尾）
DEFAULT_SLOW_MS: int = 1000  # 慢请求阈值默认值（毫秒）
