from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, comment="UUID")
    kind: Mapped[str] = mapped_column(String, comment="manual|delegate|async_delegate|schedule")
    group_id: Mapped[str | None] = mapped_column(String, ForeignKey("groups.id"), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String, nullable=True, comment="发起会话（异步委派完成时回写群汇报）"
    )
    ticket_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="async delegate ticket")
    parent_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("tasks.id"), nullable=True, comment="委派链父任务")
    executor: Mapped[str] = mapped_column(String, comment="waker name")
    status: Mapped[str] = mapped_column(String, default="pending", comment="pending|running|done|failed|cancelled")
    input_text: Mapped[str] = mapped_column(Text, comment="委托输入")
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="汇总结果")
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(String, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
