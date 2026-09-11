"""后台状态同步引擎：轮询 DeerFlow run 状态并回写 TASK 表.

会话隔离（CF1）
----------------
``_sync_once`` 先用只读短 session 查出 running 任务列表，随后**按任务开独立
短 session** 逐个同步（与 ``async_delegate._write_group_report`` 的 C2 模式
一致）：单任务的 commit 失败在物理上无法把 pending-rollback 状态带给同轮
其它任务。所有异常路径均先 ``rollback()`` 再落日志/计数。

同步错误分治（C3 + CF2）
--------------------------
``get_run`` 失败不能一律当"任务失败"：网关重启/网络抖动/SQLite 写锁期间，
run 在上游仍在正常执行，把它写成终态 ``failed`` 会造成不可恢复的误杀
（用户看到任务失败，但成员其实已交付）。因此按错误性质分三类处理：

1. **瞬时错误** ``_TRANSIENT_ERRORS``：记 warning + 回滚，**不改任务状态**，
   下一轮重试（自愈）；但计入独立的 ``_transient_failures`` 预算（CF2，
   默认 ``settings.sync_max_transient_failures``≈30 轮≈5min @10s interval），
   超阈后置 ``failed``（result_summary="Sync error: upstream unreachable"），
   避免网关长期不可达时任务永久停在 running（下游 delete_waker 永久 409、
   activity 永真、看板 running 列膨胀）。
2. **资源不存在** ``_DEAD_ERRORS``：上游 thread/agent 已消失，重试无意义 → 立即判死 ``failed``。
3. **其他未预期错误**：``logger.exception`` 保留完整栈 + 累计连续失败次数，
   达 ``_MAX_CONSECUTIVE_FAILURES`` 才判死 ``failed``；任何一次成功查询即清零。

终态写入失败不吞异常：记日志 + 回滚该任务的独立 session 后继续同轮其它
任务（CF1 隔离后不再需要"中止本轮"）；失败计数在 commit 成功后才清零
（CF5，避免提交失败时计数被抹掉导致无限重试），并在每轮末尾按当前
running 任务集裁剪 stale 键。
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.deerflow.client import DeerFlowClient
from app.deerflow.errors import (
    AgentNotFoundError,
    DeerFlowUnavailableError,
    ThreadNotFoundError,
)
from app.models.task import Task
from app.services.chat_reply import _extract_reply_from_state
from app.services.status_mapping import map_run_to_task_status

logger = logging.getLogger(__name__)

# 连续"未预期"失败阈值：达到后才把任务判死 failed。
# 注意：瞬时错误（_TRANSIENT_ERRORS）走独立的 _transient_failures 预算，
# 否则网关一次发布就会批量误杀任务。
_MAX_CONSECUTIVE_FAILURES = 3

# 瞬时错误（可自愈）：只记日志 + 下一轮重试，不改任务状态。
# - DeerFlowUnavailableError：网关 5xx / 不可达（发布、重启、过载）
# - httpx.RequestError：传输层故障（含 httpx.TimeoutException 等子类）
# - asyncio.TimeoutError / OSError：显式超时与套接字级故障
# - OperationalError：SQLite "database is locked" 等写锁竞争
_TRANSIENT_ERRORS = (
    DeerFlowUnavailableError,
    httpx.RequestError,
    asyncio.TimeoutError,
    OSError,
    OperationalError,
)

# 资源不存在（不可自愈）：上游 thread/agent 已消失，重试无意义 → 直接判死。
_DEAD_ERRORS = (ThreadNotFoundError, AgentNotFoundError)

# sync_engine 只关心终态，避免对 pending/running 做无意义回写
_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


class SyncEngine:
    """后台轮询 DeerFlow run 状态，回写 TASK 表（按任务独立短 session，CF1）."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        deerflow_client: DeerFlowClient,
        interval: float = 10.0,
        async_delegate_service=None,
        flow_engine=None,
        max_transient_failures: int | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._deerflow = deerflow_client
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._async_delegate_service = async_delegate_service
        self._flow_engine = flow_engine
        # CF2：瞬时错误重试预算（轮数）。默认取自 settings（≈30 轮≈5min
        # @10s interval），超阈后判死，避免网关长期不可达时任务永停 running。
        self._max_transient_failures = (
            max_transient_failures
            if max_transient_failures is not None
            else get_settings().sync_max_transient_failures
        )
        # 连续"未预期"失败计数（task_id -> 次数）。
        # 说明：更彻底的做法是给 Task 增加 sync_failures 列持久化，但 Task 模型
        # 不在本模块所有权范围内（且需要 ALTER TABLE 迁移）。该计数只用于在进程内
        # 区分"偶发抖动"与"持续异常"：进程重启后清零等价于重新给任务若干次重试
        # 机会，语义可接受；任务一旦终态或被成功查询即清除条目，并在每轮末尾
        # 按当前 running 集裁剪 stale 键（CF5），不会无界增长。
        self._sync_failures: dict[str, int] = {}
        # CF2：连续瞬时错误计数（task_id -> 轮数），成功同步即清零。
        self._transient_failures: dict[str, int] = {}

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
        """一轮同步（CF1：按任务独立短 session，单任务失败不影响同轮其它任务）:
        1. 只读短 session 查 status="running" 且 run_id 不为空的 task **id 列表**
        2. 对每个 id 开独立短 session，**在该 session 内重新加载** task（避免
           detached 对象修改无法随本 session commit 落库），再 get_run 并回写
        3. 轮末按当前 running id 集裁剪失败计数的 stale 键（CF5）
        """
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(Task.id).where(
                    Task.status == "running", Task.run_id.isnot(None)
                )
            )
            running_ids = [row[0] for row in result.all()]

        for task_id in running_ids:
            try:
                async with self._db_session_factory() as task_session:
                    task = (
                        await task_session.execute(
                            select(Task).where(Task.id == task_id)
                        )
                    ).scalars().first()
                    if task is None:
                        continue
                    try:
                        await self._sync_task(task_session, task)
                    except Exception:
                        # CF1：瞬时分支的 rollback 自身失败 / _persist_failed 的
                        # commit 失败都先在此回滚清理，再向上交给隔离层。
                        await self._rollback_quietly(task_session)
                        raise
            except Exception:
                # CF1：单任务异常不再中止本轮 —— 每个任务持有独立 session，
                # 失败已被物理隔离；记日志后继续同轮其它任务。
                logger.exception(
                    "Sync task %s aborted this round (session isolated)", task_id
                )

        # CF5：任务离开 running 后裁剪 stale 计数键，消除随历史任务单调增长的小泄漏。
        running_id_set = set(running_ids)
        for failures in (self._sync_failures, self._transient_failures):
            for stale_id in set(failures) - running_id_set:
                failures.pop(stale_id, None)

    async def _rollback_quietly(self, session: AsyncSession | None) -> None:
        """异常路径兜底回滚：回滚自身失败只记日志（session 随即被关闭丢弃）."""
        if session is None:
            return
        try:
            await session.rollback()
        except Exception:
            logger.warning("Rollback failed on isolated session", exc_info=True)

    async def _sync_task(self, session: AsyncSession, task: Task) -> None:
        """同步单个任务状态（独立短 session；错误分治策略见模块 docstring）."""
        # 主键在任何 rollback/expire 之前缓存：异常分支回滚后 task 对象被
        # expire，同步访问 task.id 会触发懒加载 IO（MissingGreenlet）。计数、
        # 日志与判死一律使用该缓存值。
        task_id = task.id
        try:
            run_resp = await self._deerflow.get_run(task.thread_id, task.run_id)
            # 查询成功 → 清零该任务的连续失败计数（统计的是"连续"而非累计）
            self._sync_failures.pop(task_id, None)
            self._transient_failures.pop(task_id, None)
            run_status = run_resp.get("status", "")

            # 状态映射 (仅终态触发更新)
            if run_status in _TERMINAL_RUN_STATUSES:
                new_status = map_run_to_task_status(run_status)
                task.status = new_status
                # 提取结果摘要（成功完成）：run 响应本身不含正文，需从成员
                # thread state 提取（与 chat_reply / mcp delegate 同一提取器）。
                if new_status == "done":
                    try:
                        state = await self._deerflow.get_thread_state(task.thread_id)
                        text, _ = _extract_reply_from_state(state, task.run_id or "")
                    except Exception:
                        logger.warning(
                            "Failed to extract result for task %s", task.id, exc_info=True
                        )
                        text = None
                    if text:
                        task.result_summary = text[:500] if len(text) > 500 else text
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
                        # CF1：flow engine 回调可能在同一 session 上留下失败事务，
                        # 显式回滚后再关闭（任务终态已在前面独立 commit）。
                        await self._rollback_quietly(session)
        except _DEAD_ERRORS as exc:
            # 上游资源已不存在（thread/agent 404）：重试无意义 → 判死
            logger.warning(
                "Upstream resource gone for task %s (%s): %s",
                task_id, type(exc).__name__, exc,
            )
            await self._persist_failed(
                session,
                task_id,
                f"Sync error: upstream resource not found ({type(exc).__name__})",
            )
        except _TRANSIENT_ERRORS as exc:
            # 瞬时错误（网关 5xx / 网络 / 超时 / SQLite 写锁）：先回滚清理
            # 可能的脏事务（CF1：commit 失败后不 rollback 会毒化 session，
            # 后续操作抛 PendingRollbackError），不改任务状态，下一轮重试。
            await self._rollback_quietly(session)
            # CF2：瞬时错误计入独立预算，超阈后判死 —— 否则网关长期不可达时
            # 任务永久停在 running（delete_waker 永久 409、看板 running 列膨胀）。
            transient = self._transient_failures.get(task_id, 0) + 1
            self._transient_failures[task_id] = transient
            if transient >= self._max_transient_failures:
                logger.error(
                    "Transient errors for task %s exceeded budget "
                    "(%d/%d rounds), marking failed: %s",
                    task_id, transient, self._max_transient_failures, exc,
                )
                await self._persist_failed(
                    session,
                    task_id,
                    "Sync error: upstream unreachable",
                )
            else:
                logger.warning(
                    "Transient error while syncing task %s "
                    "(%d/%d rounds), will retry next round: %s",
                    task_id, transient, self._max_transient_failures, exc,
                )
        except Exception:
            # 未预期错误：保留完整栈便于定位，连续达阈才判死；
            # 先回滚清理可能的脏事务（CF1）。
            await self._rollback_quietly(session)
            failures = self._sync_failures.get(task_id, 0) + 1
            self._sync_failures[task_id] = failures
            logger.exception(
                "Unexpected error while syncing task %s (consecutive=%d/%d)",
                task_id, failures, _MAX_CONSECUTIVE_FAILURES,
            )
            if failures >= _MAX_CONSECUTIVE_FAILURES:
                await self._persist_failed(
                    session,
                    task_id,
                    f"Sync error: {failures} consecutive unexpected sync failures",
                )

    async def _persist_failed(
        self, session: AsyncSession, task_id: str, summary: str
    ) -> None:
        """把任务判死为 failed 并清理失败计数.

        CF5：**先 commit 成功、再清计数** —— 旧实现先 pop 后 commit，提交
        失败时计数已归零，下一轮从头累计，导致永远达不到判死阈值（无限重试）。
        commit 失败时异常向上冒泡，由 ``_sync_once`` 的按任务隔离层记录并
        回滚，计数保留到下一轮继续累计。

        入参为 ``task_id`` 而非 ORM 对象：瞬时/未预期分支在调用前已 rollback，
        task 对象被 expire，此处用 ``session.get`` 以异步方式重新加载 attached
        对象再写属性，避免同步访问触发懒加载 IO（MissingGreenlet）。
        """
        task = await session.get(Task, task_id)
        if task is None:
            # 任务已不存在（可能被并发删除）：仅清理计数后返回。
            self._sync_failures.pop(task_id, None)
            self._transient_failures.pop(task_id, None)
            return
        task.status = "failed"
        task.result_summary = summary
        task.updated_at = datetime.now(UTC)
        await session.commit()
        self._sync_failures.pop(task_id, None)
        self._transient_failures.pop(task_id, None)
        logger.info("Task %s marked failed by sync engine: %s", task_id, summary)

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
