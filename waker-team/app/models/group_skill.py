"""GroupSkill 模型 — 群组关联技能."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GroupSkill(Base):
    """群组技能关联表."""

    __tablename__ = "group_skills"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid4()))
    group_id: Mapped[str] = mapped_column(String, ForeignKey("groups.id"), nullable=False)
    skill_name: Mapped[str] = mapped_column(String, nullable=False, comment="技能名称")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
