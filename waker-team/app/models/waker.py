from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Waker(Base):
    __tablename__ = "wakers"

    name: Mapped[str] = mapped_column(String, primary_key=True, comment="DeerFlow agent_name")
    deer_user: Mapped[str] = mapped_column(String, default="", comment="DeerFlow 用户域")
    description: Mapped[str] = mapped_column(String, default="")
    soul_summary: Mapped[str] = mapped_column(String, default="", comment="SOUL 摘要/首行")
    home_thread_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="常驻个人 thread")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    presence: Mapped[str] = mapped_column(String, default="offline", comment="online|offline|busy")
    role: Mapped[str | None] = mapped_column(String, nullable=True, comment="角色")
    max_concurrent_tasks: Mapped[int] = mapped_column(Integer, default=3, comment="最大并发任务数")
    mcp_connectors: Mapped[str | None] = mapped_column(Text, nullable=True, comment="MCP 连接器配置 JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
