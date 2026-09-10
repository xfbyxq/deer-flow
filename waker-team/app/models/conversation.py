"""Conversation 与 ConversationMessage 模型."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Conversation(Base):
    """会话表."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String, nullable=False, comment="direct|group")
    waker_id: Mapped[str | None] = mapped_column(String, ForeignKey("wakers.name"), nullable=True, comment="直接会话的 waker")
    group_id: Mapped[str | None] = mapped_column(String, ForeignKey("groups.id"), nullable=True, comment="群组会话的 group")
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active", comment="active|archived|closed")
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="DeerFlow thread 关联")
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ConversationMessage(Base):
    """会话消息表."""

    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(String, ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, comment="user|waker|system")
    waker_id: Mapped[str | None] = mapped_column(String, ForeignKey("wakers.name"), nullable=True, comment="waker 发送时填写")
    content_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="结构化内容 JSON 数组")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
