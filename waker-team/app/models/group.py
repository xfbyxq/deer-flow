"""Group 与 GroupMember 模型."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    leader_waker_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("wakers.name"), nullable=True, comment="Leader 是 waker name"
    )
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="DeerFlow project_id 关联")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="群组描述")
    sop_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="SOP 关联 ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[str] = mapped_column(String, ForeignKey("groups.id"), primary_key=True)
    waker_id: Mapped[str] = mapped_column(String, ForeignKey("wakers.name"), primary_key=True, comment="waker name")
    role: Mapped[str] = mapped_column(String, nullable=False, default="member", comment="member|leader")
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
