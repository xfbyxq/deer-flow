"""UserSetting 模型 — 用户偏好设置."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserSetting(Base):
    """用户设置表."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, comment="用户 ID")
    default_model: Mapped[str | None] = mapped_column(String, nullable=True, comment="默认模型")
    density: Mapped[str | None] = mapped_column(String, nullable=True, comment="界面密度: compact|comfortable")
    notify_task: Mapped[bool] = mapped_column(Boolean, default=True, comment="任务通知")
    notify_mention: Mapped[bool] = mapped_column(Boolean, default=True, comment="@提及通知")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
