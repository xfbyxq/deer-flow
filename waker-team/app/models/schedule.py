"""调度定义与执行历史模型."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScheduleDef(Base):
    """调度定义表."""

    __tablename__ = "schedule_defs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    group_id: Mapped[str] = mapped_column(String, nullable=False)

    # 触发配置
    cron_expression: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)  # "flow" | "task"
    target_id: Mapped[str] = mapped_column(String, nullable=False)  # flow_id 或 waker_name
    target_input: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 任务指令等

    # 控制
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")  # active|paused|archived
    max_run_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # 状态
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class ScheduleRun(Base):
    """调度执行历史."""

    __tablename__ = "schedule_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    schedule_def_id: Mapped[str] = mapped_column(String, ForeignKey("schedule_defs.id"), nullable=False)

    trigger_type: Mapped[str] = mapped_column(String, nullable=False, default="cron")  # cron|manual
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    # 结果
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|running|success|failed
    flow_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
