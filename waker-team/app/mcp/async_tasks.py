"""异步委派 MCP 工具业务逻辑.

提供 delegate_submit / delegate_status / delegate_cancel 的业务实现，
与 MCP server 解耦以便测试。
"""

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.mcp.tasks import _check_same_group
from app.services.async_delegate import AsyncDelegateService

logger = logging.getLogger(__name__)


class AsyncDelegateMCPService:
    """封装异步委派 MCP 工具的业务逻辑."""

    def __init__(
        self,
        async_delegate_service: AsyncDelegateService,
        db_session_factory: async_sessionmaker | None = None,
    ) -> None:
        self._service = async_delegate_service
        self._db_session_factory = db_session_factory

    async def delegate_submit(
        self,
        caller: str,
        target: str,
        instruction: str,
        group_id: str | None = None,
        source_task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """异步委派任务给指定 Waker，立即返回 ticket_id.

        Parameters
        ----------
        caller:
            发起者 waker 名（从 MCP context 提取）。
        target:
            目标 waker 名。
        instruction:
            委派指令内容。
        group_id:
            所属 group（可选）。
        source_task_id:
            源 task id（用于委派链追踪，可选）。
        conversation_id:
            发起会话（可选；成员完成后以【成员汇报】写回该会话）。

        Returns
        -------
        dict with ticket_id and status.
        """
        try:
            # 跨组校验
            if self._db_session_factory is not None:
                async with self._db_session_factory() as db:
                    cross_err = await _check_same_group(db, caller, target)
                    if cross_err is not None:
                        return {"ticket_id": None, "status": "error", "error": cross_err}

            ticket_id = await self._service.submit(
                source_waker=caller,
                target_waker=target,
                instruction=instruction,
                group_id=group_id,
                source_task_id=source_task_id,
                conversation_id=conversation_id,
            )
            return {
                "ticket_id": ticket_id,
                "status": "submitted",
                "message": "异步委派已提交，后台执行中",
            }
        except Exception as exc:
            logger.exception("delegate_submit failed")
            return {
                "ticket_id": None,
                "status": "error",
                "error": str(exc),
            }

    async def delegate_status(self, ticket_id: str) -> dict:
        """查询异步委派 ticket 的当前状态.

        Parameters
        ----------
        ticket_id:
            委派 ticket id。

        Returns
        -------
        dict with ticket status info.
        """
        return await self._service.get_status(ticket_id)

    async def delegate_cancel(self, ticket_id: str) -> dict:
        """取消一个待执行或执行中的异步委派.

        Parameters
        ----------
        ticket_id:
            委派 ticket id。

        Returns
        -------
        dict with cancellation result.
        """
        success = await self._service.cancel(ticket_id)
        if success:
            return {
                "ticket_id": ticket_id,
                "status": "cancelled",
                "message": "委派已取消",
            }
        return {
            "ticket_id": ticket_id,
            "status": "error",
            "error": "Ticket not found or already in terminal state",
        }
