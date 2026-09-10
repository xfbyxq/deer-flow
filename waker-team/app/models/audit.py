from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String, comment="如 waker.create, task.create, delegate.call")
    actor: Mapped[str] = mapped_column(String, comment="操作者")
    target: Mapped[str] = mapped_column(String, comment="操作目标")
    detail: Mapped[str | None] = mapped_column(Text, nullable=True, comment="JSON 序列化详情")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
