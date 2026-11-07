"""档位与归属：与根因分开计算（D4）。

档位回答"要不要处理"，归属回答"该谁看"，两者都由代码算，不依赖模型输出。
判定表（kind × root_cause）：

| kind | root_cause | tier | owner |
|---|---|---|---|
| 5xx | 任意 | 需要处理 | dev |
| 4xx | 任意 | 建议观察 | ops |
| answer | 四类已定义根因 | 需要处理 | kb |
| answer | undetermined | 建议观察 | ops |
"""

from __future__ import annotations

from src.rca.diagnose.models import DiagnosisKind, Owner, RootCause
from src.rca.models import Tier


def assess(kind: DiagnosisKind, cause: RootCause) -> tuple[Tier, Owner]:
    """返回 (档位, 归属)。

    Args:
        kind: 问题分型（入口判定，不由模型决定）
        cause: 根因判定结果

    Returns:
        档位与归属；对应关系见模块文档的判定表
    """
    if kind is DiagnosisKind.ERROR_5XX:
        return Tier.NEED_ACTION, Owner.DEV
    if kind is DiagnosisKind.ERROR_4XX:
        return Tier.WATCH, Owner.OPS
    if cause is RootCause.UNDETERMINED:
        return Tier.WATCH, Owner.OPS
    return Tier.NEED_ACTION, Owner.KB
