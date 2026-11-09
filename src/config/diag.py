"""诊断链路的固定文案与展示映射（用户/模型可见）。

文案直接进入企微消息与工具返回，集中登记避免散落在判定/渲染代码里；
键为枚举的字符串值（`kind.value` / `owner.value`），本模块不 import src.rca 以免循环依赖。
"""

from __future__ import annotations

KIND_LABELS: dict[str, str] = {
    "answer": "答错/答不准",
    "5xx": "服务端错误",
    "4xx": "客户端侧错误",
}

OWNER_LABELS: dict[str, str] = {
    "ops": "运维",
    "dev": "研发",
    "kb": "知识库/检索",
}

CARD_HEADER = "**诊断 · {kind}**"  # 卡片首行；{kind} 已翻译成中文
CARD_TIER = "> 档位：{tier} ｜ 归属：{owner}"  # 第二行：档位与归属
CARD_CAUSE = "> 根因：{cause}"  # 第三行：根因或未判定原因
CARD_BASIS = "> 结论基于前 {rounds} 轮上下文"  # 追溯轮次（basis_rounds > 0 时出现）
CARD_BASIS_ITEM = "- {text}"  # 依据条目
CARD_BASIS_MORE = "- 另 {count} 条依据见详情"  # 依据超过上限时的收尾
CARD_EVIDENCE = "证据 {count} 条"  # 证据计数（与详情链接同行）
CARD_DETAIL_LINK = "[查看详情]({url})"  # 详情链接（需登录）
CARD_EVIDENCE_LIMIT = 3  # 卡片上最多列出的依据条数

INVALID_KIND = "问题分型只能是 answer / 5xx / 4xx，收到：{kind}"
INVALID_TRACE_ID = "trace_id 格式应为 trace_<uuid>，请检查后重试"
TRACE_NOT_FOUND = "未找到该 trace：可能超过 30 天保留期，或 trace_id 不存在"
TRACE_UNAVAILABLE = "trace 不可用：可能属于另一环境或检索服务异常"
ALERT_HEADER = (
    "**告警诊断 · 窗口内 {traces} 个错误 trace，{groups} 类**"  # 告警卡片首行
)
ALERT_SAME = "> 同类 {count} 次"  # 代表 trace 的同类计数（预留）
ALERT_EXTRA = "> 另 {n} 类未诊断：{labels}"  # 超出上限的类别与计数
ALERT_EMPTY = "窗口内没有 ERROR 记录：错误未落到应用层（可能来自网关 / 容器被杀 / 与本应用无关）"  # D9
ALERT_FAILED = "日志源不可用，未判定：{reason}"  # failed 态，不得并入空窗口结论
