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
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationMessage]:
        """列出会话消息（按时间升序）."""
        result = await self.db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
            .offset(offset)
            .limit(limit)
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
