"""后台状态同步引擎：轮询 DeerFlow run 状态并回写 TASK 表."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.deerflow.client import DeerFlowClient
from app.models.task import Task
from app.services.status_mapping import map_run_to_task_status

logger = logging.getLogger(__name__)

# 连续查询失败阈值
_MAX_CONSECUTIVE_FAILURES = 3

# sync_engine 只关心终态，避免对 pending/running 做无意义回写
_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


class SyncEngine:
    """后台轮询 DeerFlow run 状态，回写 TASK 表."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        deerflow_client: DeerFlowClient,
        interval: float = 10.0,
        async_delegate_service=None,
        flow_engine=None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._deerflow = deerflow_client
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._async_delegate_service = async_delegate_service
        self._flow_engine = flow_engine

    async def start(self) -> None:
        """启动同步引擎."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("SyncEngine started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        """停止同步引擎."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SyncEngine stopped")

    async def _loop(self) -> None:
        """主循环."""
        while self._running:
            try:
                await self._sync_once()
            except Exception:
                logger.exception("SyncEngine cycle error")
            await asyncio.sleep(self._interval)

    async def _sync_once(self) -> None:
        """一轮同步:
        1. 查 TASK 表 status="running" 且 run_id 不为空
        2. 对每个 task，调 deerflow_client.get_run
        3. 状态映射并更新
        """
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(Task).where(Task.status == "running", Task.run_id.isnot(None))
            )
            running_tasks = result.scalars().all()

            for task in running_tasks:
                await self._sync_task(session, task)

    async def _sync_task(self, session: AsyncSession, task: Task) -> None:
        """同步单个任务状态."""
        try:
            run_resp = await self._deerflow.get_run(task.thread_id, task.run_id)
            run_status = run_resp.get("status", "")

            # 状态映射 (仅终态触发更新)
            if run_status in _TERMINAL_RUN_STATUSES:
                new_status = map_run_to_task_status(run_status)
                task.status = new_status
                # 提取结果摘要（如果是成功完成）
                if new_status == "done":
                    # 尝试从 run 响应提取结果
                    messages = run_resp.get("messages", [])
                    if messages:
                        last_msg = messages[-1]
                        content = last_msg.get("content", "")
                        if isinstance(content, str):
                            task.result_summary = content[:500] if len(content) > 500 else content
                task.updated_at = datetime.now(UTC)
                await session.commit()
                logger.info(
                    "Task %s synced: run_status=%s -> task_status=%s",
                    task.id, run_status, new_status,
                )

                # Async delegate 回调：当 task.kind == "async_delegate" 且 run 到达终态时
                if task.kind == "async_delegate" and self._async_delegate_service is not None:
                    try:
                        await self._async_delegate_service.on_run_completed(task)
                    except Exception:
                        logger.exception(
                            "Async delegate on_run_completed failed for task %s",
                            task.id,
                        )

                # Flow engine 回调：当 task.kind == "flow_node" 且 run 到达终态时
                if task.kind == "flow_node" and self._flow_engine is not None:
                    try:
                        await self._notify_flow_engine(session, task, new_status)
                    except Exception:
                        logger.exception(
                            "Flow engine on_node_completed failed for task %s",
                            task.id,
                        )
        except Exception:
            # 查询失败，累计失败次数
            # 由于 Task 模型没有 sync_failures 字段，我们使用简单策略：
            # 如果 run 不存在（404），直接标记失败
            logger.warning("Failed to sync task %s", task.id, exc_info=True)
            # 检查是否是连续失败（通过检查 run 是否存在）
            # 简化处理：如果 get_run 抛异常，标记为 failed
            task.status = "failed"
            task.result_summary = "Sync error: failed to query run status"
            task.updated_at = datetime.now(UTC)
            await session.commit()

    async def _notify_flow_engine(
        self, session: AsyncSession, task: Task, new_status: str
    ) -> None:
        """通知 FlowEngine 节点完成/失败.

        通过 task 关联的 node_run 查找 flow_run_id 和 node_key。
        """
        from sqlalchemy import select as sa_select

        from app.models.flow import NodeRun

        # 查找关联的 node_run
        nr_result = await session.execute(
            sa_select(NodeRun).where(NodeRun.task_id == task.id)
        )
        node_run = nr_result.scalars().first()
        if node_run is None:
            return

        flow_run_id = node_run.flow_run_id
        node_key = node_run.node_key

        if new_status == "done":
            outcome = {
                "status": "done",
                "result_summary": task.result_summary or "",
            }
            await self._flow_engine.on_node_completed(
                flow_run_id, node_key, outcome, session
            )
        elif new_status in ("failed", "cancelled"):
            error_msg = task.result_summary or f"Task {new_status}"
            await self._flow_engine.on_node_failed(
                flow_run_id, node_key, error_msg, session
            )
