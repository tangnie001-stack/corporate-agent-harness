"""应用业务逻辑编排入口。

组合 KBService、DocumentService 两个子 service，
对外提供统一的业务接口。
"""

import json

from loguru import logger

from src.chat.manager import ChatManager
from src.infra.db.models.document import DocModel as DocEntity
from src.infra.db.repos import (
    ChatRepo,
    DiagRepo,
    DocumentRepo,
    EvalRepo,
    KbRepo,
    UserRepo,
)
from src.infra.db.vector_store import VectorStore
from src.infra.db.vector_store.types import ChunkQueryResult
from src.parsers.router import DocRouter
from src.services.agent_service import AgentService
from src.services.auth_service import AuthService
from src.services.document_service import DocumentService
from src.services.kb_service import KBService


class AppService:
    """UI 与后端之间的业务逻辑编排层。"""

    def __init__(
        self,
        session_factory=None,
        vector_store: VectorStore | None = None,
        router: DocRouter | None = None,
        chat_manager: ChatManager | None = None,
        agent_service: AgentService | None = None,
    ) -> None:
        if session_factory is None:
            from src.infra.db.engine import session_factory as _sf

            session_factory = _sf
        self.vector_store = vector_store or VectorStore()
        self.router = router or DocRouter()
        self.chat_manager = chat_manager or ChatManager()

        # Create repos from pool
        self._kb_repo = KbRepo(session_factory)
        self._doc_repo = DocumentRepo(session_factory)
        self._chat_repo = ChatRepo(session_factory)
        self._user_repo = UserRepo(session_factory)
        self._eval_repo = EvalRepo(session_factory)
        self._diag_repo = DiagRepo(session_factory)

        self.agent_service = agent_service or AgentService(
            vector_store=self.vector_store,
            chat_manager=self.chat_manager,
            kb_repo=self._kb_repo,
        )
        self.kb = KBService(self._kb_repo)
        self.document = DocumentService(self._doc_repo, self.vector_store, self.router)
        self._auth_service: AuthService | None = None

    # ==================== 认证 ====================

    @property
    def auth_service(self) -> AuthService:
        """延迟初始化的 AuthService 单例。"""
        if self._auth_service is None:
            from src.infra.redis_client import get_redis_client

            self._auth_service = AuthService(
                user_repo=self._user_repo,
                redis_client=get_redis_client(),
            )
        return self._auth_service

    # ==================== 诊断报告 ====================

    async def get_diag_report(self, diag_id: str) -> dict | None:
        """按 id 取诊断报告（详情页只读）。

        Args:
            diag_id: 诊断报告 id

        Returns:
            报告字典；不存在返回 None
        """
        row = await self._diag_repo.get(diag_id)
        if row is None:
            return None
        created = row.created_at.isoformat() if row.created_at else ""
        return {
            "diag_id": row.id,
            "trace_id": row.trace_id,
            "env": row.env,
            "trigger": row.trigger,
            "kind": row.kind,
            "tier": row.tier,
            "root_cause": row.root_cause,
            "owner": row.owner,
            "evidence": row.evidence or [],
            "assertions": row.assertions or [],
            "next_actions": row.next_actions or "",
            "basis_rounds": row.basis_rounds,
            "cost_ms": row.cost_ms,
            "created_at": created,
        }

    # ==================== 知识库 ====================

    async def list_knowledge_bases(self, user_id: str = "") -> list[dict]:
        return await self.kb.list_knowledge_bases(user_id)

    async def create_knowledge_base(
        self,
        name: str,
        description: str = "",
        user_id: str = "",
        domain: str = "general",
    ) -> tuple[str, bool]:
        """创建知识库；见 KBService.create_knowledge_base。"""
        return await self.kb.create_knowledge_base(name, description, user_id, domain)

    async def set_kb_domain(self, kb_id: str, domain: str) -> bool:
        """更新知识库领域；见 KBService.set_domain。"""
        return await self.kb.set_domain(kb_id, domain)

    async def delete_knowledge_base(self, kb_id: str) -> tuple[bool, str]:
        """删除知识库：同一事务内「软删文档 + 删分块 + 软删 KB」。

        三步必须原子：分块删除失败时不得软删知识库（否则留下永久孤儿分块）。
        删除失败 SHALL NOT 被吞掉 —— 静默降级会让孤儿长期存在且不可观测。

        Args:
            kb_id: 知识库 ID

        Returns:
            (是否成功, 提示文案)；知识库不存在时返回 (False, "知识库不存在")

        Raises:
            Exception: 任一步失败时整体回滚并向上抛
        """
        async with self._kb_repo.transaction() as session:
            await self._doc_repo.soft_delete_documents_by_kb(kb_id, session=session)
            deleted = await self.vector_store.delete_collection(kb_id, session=session)
            ok = await self._kb_repo.soft_delete_kb(kb_id, session=session)
        if not ok:
            logger.warning("Knowledge base '{}' not found for deletion", kb_id)
            return False, "知识库不存在"
        logger.info(
            "Knowledge base soft-deleted: {} (chunks deleted={})", kb_id, deleted
        )
        return True, "知识库已删除"

    # ==================== 文档 ====================

    async def get_documents(self, kb_id: str) -> list[dict]:
        return await self.document.get_documents(kb_id)

    async def get_document(self, kb_id: str, doc_id: str) -> DocEntity | None:
        """获取知识库下的单个文档。

        Args:
            kb_id: 知识库 ID
            doc_id: 文档 ID

        Returns:
            匹配且未被软删的文档 ORM 对象；不存在或不属于该知识库时返回 None
        """
        return await self.document.get_document(kb_id, doc_id)

    async def delete_document(
        self,
        kb_id: str,
        doc_id: str,
        user_id: str,
    ) -> dict:
        return await self.document.delete_document(kb_id, doc_id, user_id)

    # ==================== 问答 ====================

    # ===== Session/Message Delegates =====

    async def get_sessions(self, user_id: str = "") -> list[dict]:
        """获取最近 50 个会话。

        Args:
            user_id: 用户 ID，用于过滤属于该用户的会话
        """
        items = await self._chat_repo.get_sessions(user_id)
        return [
            {
                "id": s.id,
                "title": s.title,
                "kb_id": s.kb_id,
                "kb_name": s.kb_name,
                "message_count": s.message_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "agent": s.agent,
            }
            for s in items
        ]

    async def get_session_by_id(self, session_id: str) -> dict | None:
        """按 ID 查询会话。"""
        session = await self._chat_repo.get_session_by_id(session_id)
        if session is None:
            return None
        return {
            "id": session.id,
            "title": session.title,
            "kb_id": session.kb_id,
            "user_id": session.user_id,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    async def get_messages(self, session_id: str) -> list[dict]:
        """获取会话消息。

        sources 反序列化：库内 Text 列存 JSON 字符串（旧数据为扁平
        "url (第N页)" 字符串数组，新数据为结构化 dict 数组），读回后
        json.loads 还原为数组返回，避免前端收到双重转义字符串。
        """
        msgs = await self._chat_repo.get_messages(session_id)
        return [
            {
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "kb_id": m.kb_id,
                "sources": self._parse_sources(m.sources),
                "prompt_tokens": m.prompt_tokens,
                "completion_tokens": m.completion_tokens,
                "total_tokens": m.total_tokens,
                "model_name": m.model_name,
                "process": m.process,
                "status": m.status,
                "created_at": m.created_at,
            }
            for m in msgs
        ]

    @staticmethod
    def _parse_sources(raw: str | None) -> list:
        """解析库中 sources 为数组，兼容存量双层 JSON 转义，非法/空返回空列表。

        历史成因：persistence 先 json.dumps(list) 成 JSON 字符串，chat_repo
        save_message 又对其 dumps 一次 → 库内实际为双层转义的 JSON 字符串。
        故迭代解包：结果仍是 JSON 字符串则继续 loads，直到得到 list 为止；
        最终非 list（解析异常 / 非数组）返回空列表，不阻断消息读取。

        Args:
            raw: sources 原始值（Text 列存储值或 None）

        Returns:
            反序列化后的来源数组；无法解析为数组时返回空列表
        """
        if not raw:
            return []
        value = raw
        for _ in range(4):  # 上限防御，正常最多两层
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return []
            if isinstance(parsed, list):
                return parsed
            value = parsed
        return []

    async def delete_session_and_messages(self, session_id: str) -> bool:
        """删除会话及其消息。"""
        await self.chat_manager.clear_history_async(session_id)
        return await self._chat_repo.delete_session_and_messages(session_id)

    async def set_chat_repo(self) -> None:
        """设置 chat_manager 的 ChatRepo。"""
        self.chat_manager.set_chat_repo(self._chat_repo)

    @property
    def chat_repo(self) -> ChatRepo:
        """返回会话/消息 Repo，供 API 层写入答案反馈等。"""
        return self._chat_repo

    async def save_session_async(
        self, session_id: str, title: str, kb_id: str, user_id: str = ""
    ) -> None:
        """持久化保存会话。"""
        await self.chat_manager.save_session_async(session_id, title, kb_id, user_id)

    async def save_user_async(self, session_id: str, kb_id: str, user_msg: str) -> None:
        """请求开始时同步写入单条 user 消息到 MySQL。"""
        await self.chat_manager.save_user_async(session_id, kb_id, user_msg)

    async def save_assistant_async(
        self,
        session_id: str,
        kb_id: str,
        assistant_msg: str,
        sources: list[dict] | None = None,
        status: str = "complete",
        process_json: str | None = None,
        model_name: str | None = None,
    ) -> None:
        """流结束后异步写入单条 assistant 消息到 MySQL。

        Args:
            session_id: 会话 ID
            kb_id: 关联的知识库 ID
            assistant_msg: 助理回答内容
            sources: 来源引用列表
            status: 消息状态（complete/interrupted）
            process_json: 过程事件JSON（历史回放，None=存量语义）
            model_name: 实际回答模型名（复用既有列，None 落空串）
        """
        await self.chat_manager.save_assistant_async(
            session_id,
            kb_id,
            assistant_msg,
            sources,
            status,
            process_json,
            model_name,
        )

    async def save_messages_async(
        self,
        session_id: str,
        kb_id: str,
        user_msg: str,
        assistant_msg: str,
        sources: list[dict] | None = None,
    ) -> None:
        """批量持久化保存消息。"""
        await self.chat_manager.save_messages_async(
            session_id, kb_id, user_msg, assistant_msg, sources
        )

    # ==================== 配置 ====================

    @property
    def settings(self):
        """返回配置模块，允许 api 层通过 svc.settings.X 访问配置。"""
        import src.config.settings as _settings

        return _settings

    # ==================== 评估报告 ====================

    async def get_latest_eval_report(self, kb_id: str) -> dict | None:
        """获取知识库最新的 RAGAS 评估报告。"""
        report = await self._eval_repo.get_latest_report(kb_id)
        if report is None:
            return None
        return {
            "id": report.id,
            "kb_id": report.kb_id,
            "run_type": report.run_type,
            "qa_count": report.qa_count,
            "faithfulness": report.faithfulness,
            "answer_relevancy": report.answer_relevancy,
            "context_precision": report.context_precision,
            "context_recall": report.context_recall,
            "overall_score": report.overall_score,
            "passed": report.passed,
            "report_path": report.report_path,
            "triggered_by": report.triggered_by,
            "detail_json": report.detail_json,
            "eval_date": report.eval_date,
        }

    async def insert_eval_report(self, report) -> None:
        """插入 RAGAS 评估报告。"""
        await self._eval_repo.insert_report(report)

    # ==================== 知识库名称查询 ====================

    async def get_kb_by_name(self, user_id: str, name: str) -> str | None:
        """按名称查询知识库 ID。"""
        return await self._kb_repo.get_kb_by_name(user_id, name)

    # ==================== 分块查询 ====================

    async def get_chunks_paginated(
        self, doc_id: str, kb_id: str, page: int = 1, page_size: int = 50
    ):
        """分页查询文档的分块内容。"""
        result: ChunkQueryResult = await self.vector_store.get_chunks_paginated(
            doc_id,
            kb_id,
            page=page,
            page_size=page_size,
        )
        return result


# ── 单例访问器 ────────────────────────────────────────────────
# 下沉自 api/dependencies.py：channels 不得 import api，故访问器须住在 services。
_service: AppService | None = None


async def get_app_service() -> AppService:
    """提供 AppService 单例。

    延迟初始化：首次调用时创建实例，后续复用——避免模块导入阶段产生
    网络或数据库连接。api 层与通道层共用本函数。
    """
    global _service
    if _service is None:
        _service = AppService()
    return _service
