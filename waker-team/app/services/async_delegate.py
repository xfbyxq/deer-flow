"""异步委派核心逻辑：ticket 委派 + 唤醒 run 投递.

这是 P1 核心价值点：委派后立即返回 ticket，后台执行完成后
通过唤醒 run 将结果投递回源 agent。
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.models.conversation import Conversation, ConversationMessage
from app.models.delegation import DelegationLedger
from app.models.task import Task
from app.services.delegation_guard import DelegationBlockedError, DelegationGuard
from app.services.status_mapping import map_run_to_task_status
from app.services.wake_engine import (
    WakeEngine,
    WakeQueueFullError,
    WakeThrottledError,
)
from app.time_utils import to_iso_utc

logger = logging.getLogger(__name__)


def _clip_text(text: str | None, limit: int) -> str:
    """单行截断（成员汇报摘要用，与同步委派汇报风格一致）."""
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


class AsyncDelegateService:
    """异步委派服务：ticket 创建 + DeerFlow run 管理 + 唤醒投递."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        deerflow_client: DeerFlowClient,
        wake_engine: WakeEngine,
        delegation_guard: DelegationGuard,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._client = deerflow_client
        self._wake_engine = wake_engine
        self._guard = delegation_guard

    async def submit(
        self,
        source_waker: str,
        target_waker: str,
        instruction: str,
        group_id: str | None = None,
        source_task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """提交异步委派，返回 ticket_id.

        流程：
        1. delegation_guard.check_before_submit() — 校验
        2. 建 TASK(kind="async_delegate", ticket_id=uuid, parent_task_id=source_task_id)
        3. 建 delegation_ledger 记录（depth=parent.depth+1, path=parent.path+[source]）
        4. 创建 DeerFlow thread + run（target agent）
        5. 更新 TASK 的 thread_id / run_id
        6. 返回 ticket_id

        ``conversation_id``（可选）：发起会话，任务完成时以【成员汇报】写回群聊（成员在群里发声）。
        """
        async with self._db_session_factory() as db:
            # 1. 防护校验
            await self._guard.check_before_submit(
                db=db,
                source_waker=source_waker,
                target_waker=target_waker,
                group_id=group_id,
                source_task_id=source_task_id,
            )

            # 2. 生成 ticket_id 和 task_id
            ticket_id = str(uuid.uuid4())
            task_id = ticket_id  # ticket_id 即 task id
            now = datetime.now(UTC)

            # 3. 建 TASK
            task = Task(
                id=task_id,
                kind="async_delegate",
                ticket_id=ticket_id,
                parent_task_id=source_task_id,
                group_id=group_id,
                conversation_id=conversation_id,
                executor=target_waker,
                status="pending",
                input_text=instruction,
                created_by=source_waker,
                created_at=now,
                updated_at=now,
            )
            db.add(task)

            # 4. 建 delegation_ledger 记录
            # 查找父 ledger 获取 depth 和 path
            parent_depth = 0
            parent_path: list[str] = []
            if source_task_id is not None:
                parent_ledger_result = await db.execute(
                    select(DelegationLedger).where(
                        DelegationLedger.ticket_id == source_task_id
                    )
                )
                parent_ledger = parent_ledger_result.scalars().first()
                if parent_ledger is not None:
                    parent_depth = parent_ledger.depth
                    parent_path = (
                        json.loads(parent_ledger.path_json)
                        if parent_ledger.path_json
                        else []
                    )

            new_path = self._guard.build_path(parent_path, source_waker)
            ledger = DelegationLedger(
                ticket_id=ticket_id,
                source_task_id=source_task_id,
                source_waker=source_waker,
                target_waker=target_waker,
                group_id=group_id,
                depth=parent_depth + 1,
                path_json=json.dumps(new_path),
                status="pending",
            )
            db.add(ledger)
            await db.commit()

            # 5. 创建 DeerFlow thread + run
            thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"async-task-{task_id}"))
            run_body = {
                "input": {
                    "messages": [
                        {"role": "user", "content": instruction},
                    ]
                },
                "config": build_run_configuration(target_waker),
            }

            try:
                await self._client.create_thread(thread_id=thread_id)
                run_resp = await self._client.create_run(
                    thread_id=thread_id,
                    body=run_body,
                    idempotency_key=task_id,
                )
                run_id = run_resp.get("run_id")
            except Exception:
                task.status = "failed"
                task.result_summary = "Failed to start DeerFlow run for async delegate"
                task.updated_at = datetime.now(UTC)
                ledger.status = "failed"
                await db.commit()
                raise

            # 6. 更新 TASK
            task.status = "running"
            task.thread_id = thread_id
            task.run_id = run_id
            task.idempotency_key = task_id
            task.updated_at = datetime.now(UTC)
            ledger.status = "running"
            await db.commit()

            logger.info(
                "Async delegate submitted: ticket=%s source=%s target=%s depth=%d",
                ticket_id, source_waker, target_waker, parent_depth + 1,
            )
            return ticket_id

    async def get_status(self, ticket_id: str) -> dict:
        """查询 ticket 状态.

        Returns
        -------
        dict with keys: ticket_id, status, target_waker, result_summary, created_at
        """
        async with self._db_session_factory() as db:
            result = await db.execute(
                select(Task).where(Task.ticket_id == ticket_id)
            )
            task = result.scalars().first()
            if task is None:
                # 也尝试用 id 查（ticket_id 可能等于 task id）
                result2 = await db.execute(
                    select(Task).where(Task.id == ticket_id)
                )
                task = result2.scalars().first()

            if task is None:
                return {"ticket_id": ticket_id, "status": "not_found"}

            return {
                "ticket_id": ticket_id,
                "status": task.status,
                "target_waker": task.executor,
                "result_summary": task.result_summary,
                "created_at": to_iso_utc(task.created_at),
            }

    async def cancel(self, ticket_id: str) -> bool:
        """取消委派：cancel DeerFlow run + 更新 TASK + 更新 ledger.

        Returns
        -------
        True 如果成功取消，False 如果 ticket 不存在或已终态。
        """
        async with self._db_session_factory() as db:
            result = await db.execute(
                select(Task).where(Task.ticket_id == ticket_id)
            )
            task = result.scalars().first()
            if task is None:
                result2 = await db.execute(
                    select(Task).where(Task.id == ticket_id)
                )
                task = result2.scalars().first()

            if task is None:
                return False

            if task.status not in ("pending", "running"):
                return False

            # 取消 DeerFlow run
            if task.run_id and task.thread_id:
                try:
                    await self._client.cancel_run(task.thread_id, task.run_id, wait=True)
                except Exception:
                    logger.warning("Failed to cancel DeerFlow run for ticket %s", ticket_id, exc_info=True)

            task.status = "cancelled"
            task.updated_at = datetime.now(UTC)

            # 更新 ledger
            ledger_result = await db.execute(
                select(DelegationLedger).where(DelegationLedger.ticket_id == ticket_id)
            )
            ledger = ledger_result.scalars().first()
            if ledger is not None:
                ledger.status = "cancelled"

            await db.commit()
            logger.info("Async delegate cancelled: ticket=%s", ticket_id)
            return True

    async def on_run_completed(self, task: Task) -> None:
        """SyncEngine 发现 async_delegate run 终态后调用.

        流程（CF3：唤醒投递已解耦到 WakeEngine 后台 worker 池）：
        1. 提取结果摘要（从 task.result_summary，SyncEngine 已填充）
        2. 查找 parent_task → source_thread_id
        3. wake_engine.wake() **非阻塞入队**后立即返回（同步循环绝不被
           限速阻塞）；ledger 保持 running（待投递），投递结果由 worker
           回调在各自独立短 session 内回写：
           - 成功 → ``_mark_ledger_completed``：ledger=completed；
           - 真实投递失败 WakeDeliveryError → ``_on_wake_delivery_failed``：
             TASK.status=failed + ledger=failed；
           - 本地背压 WakeThrottledError / WakeQueueFullError（CF4）：
             **不改 TASK 终态、不覆盖 result_summary**（任务本身已 done
             且可能已在群里汇报成功），仅 ledger=failed 结束投递尝试。
        """
        async with self._db_session_factory() as db:
            # 获取当前 task（重新查询以获取最新状态）
            result = await db.execute(
                select(Task).where(Task.id == task.id)
            )
            current_task = result.scalars().first()
            if current_task is None:
                logger.warning("on_run_completed: task %s not found", task.id)
                return

            # 成员汇报写群：完成/失败时以成员身份在发起会话中发声
            # （异步委派此前只在后台静默执行，用户看不到成员参与）
            # 注意：汇报写入使用【独立短 session】，不复用此处的 db ——
            # 汇报是体验增强项，其 commit 失败绝不能污染主事务（见 _write_group_report）。
            await self._write_group_report(current_task)

            # 更新 ledger 状态
            ledger_result = await db.execute(
                select(DelegationLedger).where(DelegationLedger.ticket_id == current_task.ticket_id)
            )
            ledger = ledger_result.scalars().first()

            # 如果 run 失败，直接更新状态，不投递唤醒
            if current_task.status == "failed":
                if ledger is not None:
                    ledger.status = "failed"
                await db.commit()
                return

            if current_task.status == "cancelled":
                if ledger is not None:
                    ledger.status = "cancelled"
                await db.commit()
                return

            # 查找 parent task 获取 source thread 和 source agent
            if current_task.parent_task_id is None:
                # 无父 task，无需投递唤醒
                if ledger is not None:
                    ledger.status = "completed"
                await db.commit()
                return

            parent_result = await db.execute(
                select(Task).where(Task.id == current_task.parent_task_id)
            )
            parent_task = parent_result.scalars().first()

            if parent_task is None or parent_task.thread_id is None:
                # 父 task 不存在或无 thread，无法投递
                if ledger is not None:
                    ledger.status = "completed"
                await db.commit()
                logger.warning(
                    "Cannot deliver wake: parent task %s not found or has no thread",
                    current_task.parent_task_id,
                )
                return

            # 提取结果摘要
            result_summary = current_task.result_summary or "任务已完成"

            # 投递唤醒 run（CF3：非阻塞入队，真实投递在 WakeEngine worker 内完成）。
            # 入队成功后 ledger 保持 running（待投递），由回调推进终态。
            ticket_id = current_task.ticket_id or ""
            child_task_id = current_task.id
            try:
                await self._wake_engine.wake(
                    source_thread_id=parent_task.thread_id,
                    source_agent_name=parent_task.executor,
                    result_summary=result_summary,
                    ticket_id=ticket_id,
                    target_waker=current_task.executor,
                    on_delivered=lambda run_id: self._mark_ledger_completed(ticket_id),
                    on_failed=lambda exc: self._on_wake_delivery_failed(
                        child_task_id, ticket_id, exc
                    ),
                )
                await db.commit()
                logger.info(
                    "Wake delivery enqueued for ticket %s (parent task %s)",
                    ticket_id, current_task.parent_task_id,
                )
            except WakeQueueFullError as exc:
                # 本地队列满（背压）：不改任务终态，ledger 保持 running。
                # 注意：任务已终态，SyncEngine 不会再重试本回调，丢失的投递
                # 需人工/后续机制补偿 —— 但绝不能把已 done 的任务翻成 failed。
                await db.rollback()
                logger.error(
                    "Wake delivery queue full for ticket %s, deferred: %s",
                    ticket_id, exc,
                )

    async def _mark_ledger_completed(self, ticket_id: str) -> None:
        """唤醒投递成功回调：ledger → completed（worker 协程内，独立短 session）."""
        try:
            async with self._db_session_factory() as db:
                ledger = (
                    await db.execute(
                        select(DelegationLedger).where(
                            DelegationLedger.ticket_id == ticket_id
                        )
                    )
                ).scalars().first()
                if ledger is None:
                    return
                ledger.status = "completed"
                await db.commit()
            logger.info("Wake delivered, ledger completed: ticket=%s", ticket_id)
        except Exception:
            logger.warning(
                "Failed to mark ledger completed for ticket %s", ticket_id, exc_info=True
            )

    async def _on_wake_delivery_failed(
        self, task_id: str, ticket_id: str, exc: Exception
    ) -> None:
        """唤醒投递失败回调（worker 协程内，独立短 session）.

        CF4 语义分离：
        - ``WakeThrottledError``：本地背压/限速重试预算耗尽。任务本身已
          完成（可能已在群里汇报成功），**不得修改 task.status、不得覆盖
          result_summary**，仅把 ledger 置 failed 结束本轮投递尝试。
        - 其他 ``WakeDeliveryError``：真实上游投递失败（create_run 报错 /
          无 run_id），TASK → failed + ledger → failed（既有语义）。
        """
        throttled = isinstance(exc, WakeThrottledError)
        try:
            async with self._db_session_factory() as db:
                ledger = (
                    await db.execute(
                        select(DelegationLedger).where(
                            DelegationLedger.ticket_id == ticket_id
                        )
                    )
                ).scalars().first()
                if not throttled:
                    task = (
                        await db.execute(select(Task).where(Task.id == task_id))
                    ).scalars().first()
                    if task is not None:
                        task.status = "failed"
                        task.result_summary = f"Wake delivery failed: {exc}"
                        task.updated_at = datetime.now(UTC)
                if ledger is not None:
                    ledger.status = "failed"
                await db.commit()
            if throttled:
                logger.warning(
                    "Wake delivery throttled out for ticket %s: task %s keeps its "
                    "terminal state, ledger marked failed: %s",
                    ticket_id, task_id, exc,
                )
            else:
                logger.error(
                    "Wake delivery failed for ticket %s: task %s marked failed: %s",
                    ticket_id, task_id, exc,
                )
        except Exception:
            logger.warning(
                "Failed to persist wake delivery failure for ticket %s",
                ticket_id,
                exc_info=True,
            )

    async def _write_group_report(self, task: Task) -> None:
        """任务完成/失败时以【成员汇报】写回发起会话（成员在群里发声）.

        - 仅在任务携带 ``conversation_id`` 且状态为 done/failed 时写入（取消静默）；
        - **自开独立短 session**：不复用调用方（``on_run_completed``）的 session。
          复用调用方 session 时，汇报 commit 失败会把该 session 置于
          pending-rollback 状态，后续主流程（ledger 推进 / 唤醒投递）会抛
          ``PendingRollbackError``，导致 ledger 永停 running 且唤醒永不投递；
        - 任何失败都显式 ``rollback()`` 后仅记日志：汇报是体验增强，
          不影响任务状态/唤醒投递主流程。
        """
        if not task.conversation_id:
            return
        if task.status not in ("done", "failed"):
            return
        try:
            async with self._db_session_factory() as report_db:
                try:
                    conv = (
                        await report_db.execute(
                            select(Conversation).where(
                                Conversation.id == task.conversation_id
                            )
                        )
                    ).scalars().first()
                    if conv is None:
                        return

                    target = task.executor
                    instruction = _clip_text(task.input_text, 80)
                    if task.status == "done":
                        result = _clip_text(task.result_summary, 500) or "（无结果摘要）"
                        text = f"【成员汇报 · {target}】\n任务：{instruction}\n结果：{result}"
                    else:
                        err = _clip_text(task.result_summary, 200) or "未知原因"
                        text = (
                            f"【成员汇报 · {target}】\n任务：{instruction}\n"
                            f"状态：未能完成：{err}"
                        )

                    now = datetime.now(UTC)
                    report_db.add(
                        ConversationMessage(
                            conversation_id=task.conversation_id,
                            role="waker",
                            waker_id=target,
                            content_json=json.dumps(
                                {
                                    "text": text,
                                    "meta": {
                                        "kind": "report",
                                        "target": target,
                                        "status": task.status,
                                        "partial": True,
                                    },
                                },
                                ensure_ascii=False,
                            ),
                            created_at=now,
                        )
                    )
                    conv.updated_at = now
                    await report_db.commit()
                except Exception:
                    # 显式回滚：即使本 session 是独立的，也必须清理失败事务，
                    # 避免 close() 时的告警与残留连接状态。
                    await report_db.rollback()
                    raise
            logger.info(
                "Group report written: conversation=%s member=%s status=%s",
                task.conversation_id,
                task.executor,
                task.status,
            )
        except Exception:
            logger.warning(
                "Failed to write group report for task %s", task.id, exc_info=True
            )
