"""审计日志写入工具."""

import json
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


async def log_audit(
    db_session: AsyncSession,
    action: str,
    actor: str,
    target: str,
    detail: dict | None = None,
) -> None:
    """写入一条审计日志并提交."""
    entry = AuditLog(
        action=action,
        actor=actor,
        target=target,
        detail=json.dumps(detail, ensure_ascii=False) if detail else None,
        created_at=datetime.now(UTC),
    )
    db_session.add(entry)
    await db_session.commit()
