"""委派失控防护（架构文档 §5.4.3）.

防止委派链失控：深度上限、环路检测、扇出限制。
"""

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.delegation import DelegationLedger
from app.models.task import Task

logger = logging.getLogger(__name__)


class DelegationBlockedError(Exception):
    """委派被防护拦截."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class DelegationGuard:
    """委派链安全校验."""

    MAX_DEPTH = 3           # 单次委派链 ≤ 3 层
    MAX_FANOUT_PER_RUN = 5  # 单 run（task）委派 ≤ 5 次
    MAX_FANOUT_PER_GROUP = 10  # 单 Group 进行中委派 ≤ 10

    async def check_before_submit(
        self,
        db: AsyncSession,
        source_waker: str,
        target_waker: str,
        group_id: str | None,
        source_task_id: str | None,
    ) -> None:
        """提交前校验：深度/环路/扇出。违反则 raise DelegationBlockedError.

        Parameters
        ----------
        db:
            当前数据库 session。
        source_waker:
            发起委派的 waker 名。
        target_waker:
            目标 waker 名。
        group_id:
            所属 group（可选，用于组级扇出检测）。
        source_task_id:
            源 task id（用于深度/环路/扇出检测）。
        """
        # 无 source_task_id 表示顶层委派，无需检测
        if source_task_id is None:
            return

        # 1. 查找父 ledger 记录，获取当前深度和路径
        parent_ledger_result = await db.execute(
            select(DelegationLedger).where(
                DelegationLedger.ticket_id == source_task_id
            )
        )
        parent_ledger = parent_ledger_result.scalars().first()

        # 如果父 task 没有 ledger 记录（可能是 manual task），视为 depth=0
        if parent_ledger is not None:
            current_depth = parent_ledger.depth
            parent_path = json.loads(parent_ledger.path_json) if parent_ledger.path_json else []
        else:
            current_depth = 0
            parent_path = []

        # 2. 环路检测：target 已在委派链上
        if target_waker in parent_path:
            raise DelegationBlockedError(
                f"Loop detected: {target_waker} already in delegation path {parent_path}"
            )

        # 3. 深度检测
        if current_depth >= self.MAX_DEPTH:
            raise DelegationBlockedError(
                f"Depth limit exceeded: current depth={current_depth}, max={self.MAX_DEPTH}"
            )

        # 4. 扇出检测：source_task_id 的子委派数
        fanout_result = await db.execute(
            select(func.count()).select_from(DelegationLedger).where(
                DelegationLedger.source_task_id == source_task_id,
                DelegationLedger.status.in_(["pending", "running"]),
            )
        )
        fanout_count = fanout_result.scalar() or 0
        if fanout_count >= self.MAX_FANOUT_PER_RUN:
            raise DelegationBlockedError(
                f"Fan-out limit per run: {fanout_count}/{self.MAX_FANOUT_PER_RUN}"
            )

        # 5. 组级扇出检测
        if group_id is not None:
            group_fanout_result = await db.execute(
                select(func.count()).select_from(DelegationLedger).where(
                    DelegationLedger.group_id == group_id,
                    DelegationLedger.status.in_(["pending", "running"]),
                )
            )
            group_fanout_count = group_fanout_result.scalar() or 0
            if group_fanout_count >= self.MAX_FANOUT_PER_GROUP:
                raise DelegationBlockedError(
                    f"Group fan-out limit: {group_fanout_count}/{self.MAX_FANOUT_PER_GROUP}"
                )

    def build_path(self, parent_path: list[str] | None, source_waker: str) -> list[str]:
        """构建委派路径.

        Parameters
        ----------
        parent_path:
            父委派路径（JSON array 解析后的 list）。
        source_waker:
            当前发起委派的 waker 名。

        Returns
        -------
        新的委派路径列表。
        """
        path = list(parent_path) if parent_path else []
        path.append(source_waker)
        return path
