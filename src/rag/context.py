"""RAG 上下文数据类 — 单个检索结果块的封装。"""

from dataclasses import dataclass, field

from src.config.const import (
    ENTITY_LABELS,
    ENTITY_RENDER_ORDER,
    SOURCE_TIER_LABELS,
    SSEInteractionTexts,
)


@dataclass(slots=True)
class RAGContext:
    """单个检索上下文分块 — 包含原文内容和来源元数据。"""

    content: str
    source: str
    page: int
    doc_id: str
    chunk_id: str
    parent_content: str | None = None
    score: float = 0.0
    score_kind: str = "none"  # 分数量纲：rerank=精排相关度 / fallback=精排失败回退为 1-distance / timeout=外层超时回退 / none=无分数
    entities: dict = field(default_factory=dict)  # 业务实体，来自 chunk.metadata
    kind: str = (
        SSEInteractionTexts.CITATION_KIND_KB
    )  # 引用来源类型：kb（知识库） / web（网络搜索），默认 kb
    tier: int | None = (
        None  # 来源权威档位（resolve_source_tier 产出：0=内部文档/1=官方/2=媒体/3=一般/4=UGC）；None=未定档（存量数据/防御默认），前端不显示徽标
    )

    def to_citation(self) -> str:
        """格式化为 Markdown 引用块。"""
        snippet = self.content[:200].replace("\n", " ")
        return f"> **来源:** {self.source} (第{self.page}页)\n> {snippet}\n"

    def to_prompt_text(self) -> str:
        """渲染为喂给生成模型的单个上下文文本。

        生产 prompt（prompt.format_context）与 RAGAS 评估的 NLI 上下文
        共用此格式，保证评估时 NLI 看到的上下文与线上生成时完全一致
        （含来源/页码锚点，如文件名里的期间），避免两处实现漂移。
        实体按 ENTITY_RENDER_ORDER 渲染存在的核心实体，无实体时保持原格式。
        tier 非 None 时来源括注内追加档位标签（KB=内部文档；与生产/RAGAS 共用，标注后为评估基线分界点）。
        """
        page_part = f"第{self.page}页"
        if self.tier is not None:
            label = SOURCE_TIER_LABELS.get(self.tier, "")
            if label:
                page_part = f"{page_part}, {label}"
        parts = [f"来源: {self.source} ({page_part})"]
        entity_parts = []
        for key in ENTITY_RENDER_ORDER:
            value = self.entities.get(key)
            if value:
                label = ENTITY_LABELS.get(key, key)
                entity_parts.append(f"{label}: {value}")
        if entity_parts:
            parts.append(" ".join(entity_parts))
        parts.append(f"内容: {self.content}")
        return "\n".join(parts)
