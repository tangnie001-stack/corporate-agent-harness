"""AgentPresetLoader — 扫描 agents/*.md 并解析 frontmatter + 正文人设。

frontmatter 用**驼峰**键（`maxTurns`），与 skill 的连字符风格不同（照抄主流：
claude-code / codebuddy 的 agent 定义均为驼峰）。fail-open：单文件解析失败或
名称非法记 warning 并跳过；同名冲突由 AgentPresetRegistry fail-fast。
"""

import warnings
from pathlib import Path

import yaml

from src.agents.presets.models import AgentPreset
from src.config.const import CAPABILITY_NAME_PATTERN


class AgentPresetLoader:
    """从 agents_root 扫描并解析全部 .md 为 AgentPreset。"""

    def __init__(self, agents_root: Path) -> None:
        """初始化加载器。

        Args:
            agents_root: 智能体预设内容库根目录（含 <name>.md 平坦文件）
        """
        self.agents_root = agents_root

    def load_all(self) -> list[AgentPreset]:
        """扫描 agents_root 下全部 .md，解析为 AgentPreset 列表。

        Returns:
            解析成功的 AgentPreset 列表；目录不存在或为空时返回空列表。
            单文件解析失败（YAML 非法 / 名称非法 / IO 错误）记 warning 并跳过。
        """
        presets: list[AgentPreset] = []
        if not self.agents_root.exists():
            return presets
        for path in sorted(self.agents_root.glob("*.md")):
            try:
                presets.append(self._parse(path))
            except (yaml.YAMLError, ValueError, OSError) as exc:
                warnings.warn(f"智能体预设 {path.name} 解析失败，已跳过: {exc}")
        return presets

    def _parse(self, path: Path) -> AgentPreset:
        """解析单个 agents/<name>.md。

        Raises:
            yaml.YAMLError: frontmatter 不是合法 YAML
            ValueError: frontmatter 非 mapping / 名称为空或非 ASCII slug /
                channel 取值非法 / bot- 前缀预设未声明 hidden + wecom
            OSError: 读取文件失败
        """
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_frontmatter(raw)
        name = self._resolve_name(meta, path.stem)
        display_name = meta.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            display_name = name
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            description = self._first_paragraph(body)
        hidden = self._resolve_hidden(meta)
        channel = self._resolve_channel(meta)
        is_bot_prefixed = name.startswith("bot-") or path.stem.startswith("bot-")
        if is_bot_prefixed and (not hidden or channel != "wecom"):
            raise ValueError(
                "bot- 前缀预设（文件名或 name）必须声明 hidden: true 且 channel: wecom"
            )
        return AgentPreset(
            name=name,
            display_name=display_name,
            description=description,
            system_prompt=body.strip(),
            tools=self._resolve_list(meta, "tools"),
            skills=self._resolve_list(meta, "skills"),
            max_turns=self._resolve_max_turns(meta),
            hidden=hidden,
            channel=channel,
            source_path=path.resolve(),
        )

    def _resolve_name(self, meta: dict, fallback_name: str) -> str:
        """解析并校验预设名（ASCII slug 约束；覆盖 frontmatter name 与文件名）。"""
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            name = fallback_name
        if not CAPABILITY_NAME_PATTERN.match(name):
            raise ValueError(f"名称非法（仅允许 ASCII slug）: {name!r}")
        if not CAPABILITY_NAME_PATTERN.match(fallback_name):
            raise ValueError(f"文件名非法（仅允许 ASCII slug）: {fallback_name!r}")
        return name

    def _resolve_list(self, meta: dict, key: str) -> list[str]:
        """解析可选的字符串列表字段（逗号分隔字符串或 YAML 列表）。"""
        value = meta.get(key)
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            items = [str(part).strip() for part in value]
        else:
            items = []
        return [item for item in items if item]

    def _resolve_max_turns(self, meta: dict) -> int | None:
        """解析 maxTurns（bool 视为非法，回落 None）。"""
        value = meta.get("maxTurns")
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    def _resolve_hidden(self, meta: dict) -> bool:
        """解析 hidden（非 bool 一律按 False，不做隐式转换）。"""
        value = meta.get("hidden")
        if isinstance(value, bool):
            return value
        return False

    def _resolve_channel(self, meta: dict) -> str:
        """解析 channel（仅 web / wecom / 空；其它取值抛 ValueError）。

        Args:
            meta: frontmatter 解析结果

        Returns:
            归一化后的渠道；键缺省或空串返回 ""（不限渠道）

        Raises:
            ValueError: channel 非字符串，或取值不在 web / wecom / 空 之内
        """
        value = meta.get("channel")
        if value is None:
            return ""
        if not isinstance(value, str):
            # load_all 只把 ValueError 当作单文件解析失败并跳过，故不改成 TypeError
            raise ValueError(  # noqa: TRY004
                f"channel 非法（仅允许 web / wecom）: {value!r}"
            )
        normalized = value.strip().lower()
        if normalized in ("", "web", "wecom"):
            return normalized
        raise ValueError(f"channel 非法（仅允许 web / wecom）: {value!r}")

    def _split_frontmatter(self, raw: str) -> tuple[dict, str]:
        """把 md 拆成 (frontmatter dict, 正文)；无 frontmatter 时返回 (空 dict, 全文)。"""
        lines = raw.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                meta = yaml.safe_load("\n".join(lines[1:i]))
                body = "\n".join(lines[i + 1 :]).strip()
                if meta is None:
                    return {}, body
                if not isinstance(meta, dict):
                    raise ValueError("frontmatter 必须是 mapping")
                return meta, body
        return {}, raw

    def _first_paragraph(self, body: str) -> str:
        """取正文首段作 description 兜底（按空行切分取第一段，截断到 200 字）。"""
        if not body:
            return ""
        paragraph = body.split("\n\n")[0].replace("\n", " ").strip()
        return paragraph[:200]


def default_agents_root() -> Path:
    """项目根下的 agents 目录（智能体预设内容库的唯一入口）。

    Returns:
        `<项目根>/agents`；目录不存在时由调用方自行决定降级方式
    """
    return Path(__file__).resolve().parents[3] / "agents"
