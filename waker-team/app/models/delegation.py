"""DelegationLedger 模型 — 委派路径记录，环路检测用."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DelegationLedger(Base):
    __tablename__ = "delegation_ledger"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    ticket_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    source_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.id"), nullable=True)
    source_waker: Mapped[str] = mapped_column(String, nullable=False, comment="发起者 waker name")
    target_waker: Mapped[str] = mapped_column(String, nullable=False, comment="目标 waker name")
    group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    path_json: Mapped[str | None] = mapped_column(Text, nullable=True, comment="JSON array of waker names in chain")
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", comment="pending|completed|failed|cancelled"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
