"""Conversation 业务逻辑：CRUD + 消息管理."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import Conversation, ConversationMessage


class ConversationService:
    """会话管理服务."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------

    async def create_conversation(
        self,
        scope: str,
        waker_id: str | None = None,
        group_id: str | None = None,
        title: str | None = None,
        thread_id: str | None = None,
        created_by: str | None = None,
    ) -> Conversation:
        """创建会话."""
        conv = Conversation(
            scope=scope,
            waker_id=waker_id,
            group_id=group_id,
            title=title,
            thread_id=thread_id,
            created_by=created_by,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(conv)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def list_waker_conversations(self, waker_name: str) -> list[Conversation]:
        """列出某 waker 的**直接会话**（不含所属群组的群会话——内容隔离）.

        群会话只在群组页（list_group_conversations）展示；即使群会话
        带有 waker_id（owner 记录），也不混入直聊列表。
        """
        result = await self.db.execute(
            select(Conversation)
            .where(
                Conversation.waker_id == waker_name,
                Conversation.group_id.is_(None),
            )
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def list_group_conversations(self, group_id: str) -> list[Conversation]:
        """列出群组的所有会话."""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.group_id == group_id)
            .order_by(Conversation.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        """获取单个会话."""
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalars().first()

    # ------------------------------------------------------------------
    # Message CRUD
    # ------------------------------------------------------------------

    async def list_messages(
        self,
        conversation_id: str,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ConversationMessage]:
        """列出会话消息：倒序取「最近 limit 条」，再反转为时间升序返回.

        分页语义（CF19 文档化，参数名保持兼容）：

        * ``offset`` **从最新端跳过**，而非从最早端跳过。``offset=0`` 包含
          最新一条；``offset=10`` 跳过最新 10 条后、再往历史方向取 ``limit`` 条。
        * 返回结果是这一窗口按 ``created_at`` 升序（自然阅读顺序）。
        * ``limit`` 由 API 层 clamp 到 ``[1, 500]``（CONTRACT-LIMIT），本方法
          不重复校验，仅按传入值取数。

        这样「最新消息始终可见」——群会话消息超过 limit 后，新消息
        （含 Leader 最终回复）仍落在返回窗口内，不会被永久截断。
        """
        # 子查询：按时间倒序取最近 limit 条的 id（offset 从最新端跳过）
        recent_ids = (
            select(ConversationMessage.id)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(
                ConversationMessage.created_at.desc(),
                ConversationMessage.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).subquery()
        # 外层：仅取这些 id，并按时间升序返回（反转为自然阅读顺序）
        result = await self.db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.id.in_(select(recent_ids.c.id)))
            .order_by(
                ConversationMessage.created_at.asc(),
                ConversationMessage.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def create_message(
        self,
        conversation_id: str,
        role: str,
        waker_id: str | None = None,
        content_json: dict | list | None = None,
    ) -> ConversationMessage:
        """创建消息."""
        content_str = json.dumps(content_json, ensure_ascii=False) if content_json is not None else None
        msg = ConversationMessage(
            conversation_id=conversation_id,
            role=role,
            waker_id=waker_id,
            content_json=content_str,
            created_at=datetime.now(UTC),
        )
        self.db.add(msg)
        # 更新会话的 updated_at
        conv = await self.get_conversation(conversation_id)
        if conv is not None:
            conv.updated_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(msg)
        return msg
