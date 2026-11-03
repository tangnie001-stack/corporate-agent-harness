"""告警三档判定的值对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Tier(str, Enum):
    """判定档位。"""

    NO_ACTION = "无需处理"
    WATCH = "建议观察"
    NEED_ACTION = "需要处理"


class EvidenceStatus(str, Enum):
    """证据三态。"""

    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(frozen=True)
class EvidenceSpec:
    """skill 声明的一条证据需求。"""

    id: str  # 证据标识，渲染与日志引用
    tool: str  # 工具名（对应 LogSource 方法）
    params: dict[str, str] = field(  # 含 alert.* 占位符；不可 hash
        default_factory=dict, hash=False
    )
    required: bool = True  # 必需源失败则短路
    timeout: float = 8.0  # 单源超时（秒）
    when: str | None = None  # 简易条件，如 "stats.total < 20"


@dataclass(frozen=True)
class EvidenceResult:
    """一条证据的执行结果。"""

    id: str  # 与 EvidenceSpec.id 对应
    status: EvidenceStatus  # 三态
    data: dict | list | None  # 结构化结果；failed 时为 None
    elapsed_ms: int  # 执行耗时
    reason: str = ""  # failed 时的原因码


@dataclass(frozen=True)
class AlertContext:
    """补齐后的告警上下文。"""

    alertname: str  # 告警名
    folder: str  # Grafana 文件夹
    uri: str | None  # 接口路径；解析失败为 None
    logstore: str | None  # SLS logstore
    datasource_uid: str | None  # Grafana 数据源 uid
    threshold: float | None  # 告警阈值
    for_seconds: int | None  # 持续时间


@dataclass(frozen=True)
class TriageVerdict:
    """判定结果（档位由代码计算，解释由模型补充）。"""

    tier: Tier  # 档位
    reason: str  # 定档依据（代码生成，人可读）
    sample_size: int  # 窗口样本量
    severe_hit: bool  # 是否触及严重阈值
