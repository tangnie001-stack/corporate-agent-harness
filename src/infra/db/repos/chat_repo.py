"""会话/消息 Repo — sessions 和 conversation_history 表 CRUD。"""

import json

from sqlalchemy import Row, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infra.db.models.chat import MessageModel, SessionModel
from src.infra.db.models.feedback import FeedbackModel
from src.infra.db.models.kb import KbModel
from src.infra.llm.trace_context import current_trace_id


class ChatRepo:
    """会话/消息 CRUD 仓库。"""

    def __init__(self, session_factory):
        self._sf = session_factory

    async def create_session(self, session) -> None:
        """session: 带 .id .user_id .title .kb_id .agent 属性的对象。

        幂等：同一 session_id 已存在（多轮对话重复持久化）时静默跳过。
        """
        agent = session.agent
        if agent is None:
            agent = ""

        async with self._sf() as s:
            stmt = (
                pg_insert(SessionModel)
                .values(
                    id=session.id,
                    user_id=session.user_id,
                    title=session.title,
                    kb_id=session.kb_id,
                    agent=agent,
                )
                # 已存在 → 保留原行，不改任何列
                .on_conflict_do_nothing(index_elements=[SessionModel.id])
            )
            await s.execute(stmt)
            await s.commit()

    async def bind_session_agent(self, session_id: str, agent: str) -> bool:
        """首次绑定会话智能体（bind-once）：仅当当前绑定为空时写入。

        Args:
            session_id: 会话 ID
            agent: 已校验的智能体预设名（ASCII slug）

        Returns:
            True=本次写入成功（此前未绑定）；False=已绑定，未改动
        """
        async with self._sf() as s:
            stmt = (
                update(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.agent == "")
                .values(agent=agent)
            )
            result = await s.execute(stmt)
            await s.commit()
            return result.rowcount > 0

    async def get_sessions(self, user_id: str = "") -> list[Row]:
        """返回带具名属性的 Row（`.id` / `.kb_name` / `.message_count` 可直接访问）。

        Args:
            user_id: 用户 ID；空串表示不过滤（管理侧视角）

        Returns:
            会话列表（含知识库名与消息数），按 updated_at 倒序、最多 50 条
        """
        async with self._sf() as session:
            stmt = (
                select(
                    SessionModel.id,
                    SessionModel.title,
                    SessionModel.kb_id,
                    SessionModel.agent,
                    SessionModel.created_at,
                    SessionModel.updated_at,
                    func.coalesce(KbModel.name, "未绑定知识库").label("kb_name"),
                    func.count(MessageModel.id).label("message_count"),
                )
                .outerjoin(
                    KbModel,
                    (SessionModel.kb_id == KbModel.id) & (SessionModel.kb_id != ""),
                )
                .outerjoin(MessageModel, MessageModel.session_id == SessionModel.id)
                .where(SessionModel.is_deleted == 0)
            )

            if user_id:
                stmt = stmt.where(SessionModel.user_id == user_id)

            stmt = (
                stmt.group_by(
                    SessionModel.id,
                    SessionModel.title,
                    SessionModel.kb_id,
                    SessionModel.agent,
                    SessionModel.created_at,
                    SessionModel.updated_at,
                    KbModel.name,
                )
                .order_by(SessionModel.updated_at.desc())
                .limit(50)
            )

            result = await session.execute(stmt)
            return list(result.all())

    async def get_session_by_id(self, session_id: str) -> SessionModel | None:
        async with self._sf() as session:
            return await session.get(SessionModel, session_id)

    async def get_messages(self, session_id: str) -> list[MessageModel]:
        async with self._sf() as session:
            stmt = (
                select(MessageModel)
                .where(MessageModel.session_id == session_id)
                .order_by(MessageModel.created_at.asc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def save_message(self, msg) -> None:
        """msg: 带 .session_id .role .content .sources 等属性的对象。"""
        async with self._sf() as session:
            sources_json = (
                json.dumps(msg.sources, ensure_ascii=False)
                if getattr(msg, "sources", None)
                else None
            )
            m = MessageModel(
                session_id=msg.session_id,
                kb_id=getattr(msg, "kb_id", ""),
                role=msg.role,
                content=msg.content,
                sources=sources_json,
                status=getattr(msg, "status", "complete"),
                prompt_tokens=getattr(msg, "prompt_tokens", 0),
                completion_tokens=getattr(msg, "completion_tokens", 0),
                total_tokens=getattr(msg, "total_tokens", 0),
                model_name=getattr(msg, "model_name", ""),
                # 无 trace 上下文（如批处理/脚本）时留空，不猜测
                trace_id=current_trace_id.get(),
                process=getattr(
                    msg, "process", None
                ),  # 白名单透传（既有 getattr 模式）
            )
            session.add(m)
            await session.commit()

    async def save_feedback(
        self,
        session_id: str,
        message_index: int,
        rating: str,
        comment: str,
        trace_id: str = "",
    ) -> None:
        """写入一条答案反馈记录到 feedback 表。

        Args:
            session_id: 会话 ID
            message_index: 会话内消息序号（前端消息数组索引，从 0 起）
            rating: 评分（positive/negative）
            comment: 用户评论
            trace_id: 全链路追踪 ID（还原生成链路用，空串 = 未捕获到）
        """
        async with self._sf() as session:
            fb = FeedbackModel(
                session_id=session_id,
                message_index=message_index,
                rating=rating,
                comment=comment,
                trace_id=trace_id,
            )
            session.add(fb)
            await session.commit()

    async def delete_session_and_messages(self, session_id: str) -> bool:
        async with self._sf() as session:
            await session.execute(
                delete(MessageModel).where(MessageModel.session_id == session_id)
            )
            session_obj = await session.get(SessionModel, session_id)
            deleted = session_obj is not None
            if session_obj:
                await session.delete(session_obj)
            await session.commit()
            return deleted
