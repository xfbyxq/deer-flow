"""看板聚合查询服务."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.services.task_utils import task_to_dict

logger = logging.getLogger(__name__)


class BoardService:
    """看板聚合查询服务."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db = db_session

    async def get_board(
        self,
        status: str | None = None,
        waker: str | None = None,
        kind: str | None = None,
        group_id: str | None = None,
        days: int = 30,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """看板聚合:
        1. 查 TASK 表，按 status/waker/kind/group_id 筛选
        2. 计算 counts: {pending: N, running: N, done: N, failed: N, cancelled: N}
        3. 计算 needs_action: pending + running 中需要人工介入的任务数
        4. 分页返回 items + total
        """
        # 日期范围过滤
        since = datetime.now(UTC) - timedelta(days=days)

        # 构建基础查询
        base_query = select(Task).where(Task.created_at >= since)
        count_query = select(func.count()).select_from(Task).where(Task.created_at >= since)

        # 应用筛选条件
        if status:
            base_query = base_query.where(Task.status == status)
            count_query = count_query.where(Task.status == status)
        if waker:
            base_query = base_query.where(Task.executor == waker)
            count_query = count_query.where(Task.executor == waker)
        if kind:
            base_query = base_query.where(Task.kind == kind)
            count_query = count_query.where(Task.kind == kind)
        if group_id:
            base_query = base_query.where(Task.group_id == group_id)
            count_query = count_query.where(Task.group_id == group_id)

        # 获取总数
        total_result = await self.db.execute(count_query)
        total = total_result.scalar() or 0

        # 获取分页数据
        items_query = base_query.order_by(Task.created_at.desc()).offset(offset).limit(limit)
        items_result = await self.db.execute(items_query)
        items = [task_to_dict(t) for t in items_result.scalars().all()]

        # 计算 counts（不受筛选条件影响的全局统计，但受日期范围限制）
        counts_query = (
            select(Task.status, func.count())
            .where(Task.created_at >= since)
            .group_by(Task.status)
        )
        counts_result = await self.db.execute(counts_query)
        counts = {
            "pending": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for row_status, row_count in counts_result.all():
            if row_status in counts:
                counts[row_status] = row_count

        # 计算 needs_action: pending 超过 1 天 + running 超过 4 小时的任务数
        action_cutoff_pending = datetime.now(UTC) - timedelta(days=1)
        action_cutoff_running = datetime.now(UTC) - timedelta(hours=4)
        needs_action_query = select(func.count()).select_from(Task).where(
            Task.created_at >= since,
            (
                ((Task.status == "pending") & (Task.created_at <= action_cutoff_pending))
                | ((Task.status == "running") & (Task.created_at <= action_cutoff_running))
            ),
        )
        needs_action_result = await self.db.execute(needs_action_query)
        needs_action = needs_action_result.scalar() or 0

        return {
            "counts": counts,
            "items": items,
            "total": total,
            "needs_action": needs_action,
        }
