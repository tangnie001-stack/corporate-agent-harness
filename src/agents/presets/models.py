"""智能体预设模型 — AgentPreset（agents/<name>.md 解析后的运行时对象）。

preset = 磁盘声明式智能体定义：frontmatter 声明元数据、正文是 system prompt（人设）。
与 skill 的区别：智能体是"谁在干活"（会话级身份），skill 是"按哪本手册干"（消息级内容）。
v1 不含 model（本项目模型不可选）。
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentPreset:
    """单个智能体预设的运行时对象。

    Attributes:
        name: 预设名（ASCII slug，注册表 key 与请求 `agent` 字段的值）
        display_name: 选择器展示名（frontmatter 缺省时等于 name）
        description: 何时使用（选择器副标题 / 委派参考）
        system_prompt: 正文，作为主 agent 的 system prompt 人设层
        tools: 该预设作为 fork 执行者时允许的工具名（空 list = 不收窄）
        skills: 预加载 skill 名（会话首轮注入一次）
        max_turns: 作为 fork 执行者时的最大轮次（空 = 用系统默认）
        hidden: 是否从站点智能体清单隐藏（企微机器人预设专用）
        channel: 允许使用的渠道（空 = 不限；wecom 仅机器人入口可用）
        source_path: md 文件绝对路径（懒重载 signature 用）
    """

    name: str  # 预设名（ASCII slug）
    display_name: str  # 展示名（缺省 = name）
    description: str  # 何时使用
    system_prompt: str  # 正文人设（注入主 agent system prompt）
    tools: list[str] = field(default_factory=list)  # fork 执行者工具白名单
    skills: list[str] = field(default_factory=list)  # 预加载 skill 名
    max_turns: int | None = None  # fork 执行者最大轮次，None=系统默认
    hidden: bool = False  # 是否从站点清单隐藏（来源：frontmatter hidden；范围：清单过滤；bot- 前缀预设必须为 True）
    channel: str = ""  # 允许渠道（frontmatter channel；生成入口校验）；空=不限
    source_path: Path = field(default_factory=Path)  # md 文件绝对路径
