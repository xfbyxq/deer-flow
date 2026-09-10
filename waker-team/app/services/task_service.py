"""任务业务逻辑：创建/重试/取消."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.deerflow.errors import AgentNotFoundError, TaskConflictError, TaskNotFoundError
from app.models.task import Task
from app.models.waker import Waker
from app.services.audit_service import log_audit
from app.services.task_utils import task_to_dict

logger = logging.getLogger(__name__)


class TaskService:
    """任务管理服务."""

    def __init__(self, db_session: AsyncSession, deerflow_client: DeerFlowClient) -> None:
        self.db = db_session
        self.df = deerflow_client

    async def create_task(
        self,
        executor: str,
        input_text: str,
        group_id: str = "default",
        created_by: str = "system",
        kind: str = "manual",
    ) -> dict:
        """创建任务:
        1. 校验 executor 存在且 enabled
        2. 组装 Team Briefing
        3. 建 TASK 记录
        4. 启动 DeerFlow run
        5. 更新 TASK 状态
        6. 写审计日志

        Parameters
        ----------
        kind:
            任务类型：manual | flow_node。flow_node 类型由 FlowEngine 创建。
        """
        # 1. 校验 executor
        result = await self.db.execute(select(Waker).where(Waker.name == executor))
        waker = result.scalars().first()
        if waker is None:
            raise AgentNotFoundError(f"Executor not found: {executor}")
        if not waker.enabled:
            raise TaskConflictError(f"Executor is disabled: {executor}")

        # 2. 生成 task_id
        task_id = str(uuid.uuid4())

        # 3. 组装 Team Briefing
        collab_result = await self.db.execute(
            select(Waker).where(Waker.enabled == True, Waker.name != executor)  # noqa: E712
        )
        collab_wakers = collab_result.scalars().all()
        collab_names = [w.name for w in collab_wakers]
        if collab_names:
            briefing = f"你是 {executor}。当前可协作的同事: {', '.join(collab_names)}。请完成任务后简要汇报。"
        else:
            briefing = f"你是 {executor}。请完成任务后简要汇报。"

        # 4. 建 TASK 记录
        now = datetime.now(UTC)
        task = Task(
            id=task_id,
            kind=kind,
            group_id=group_id,
            executor=executor,
            status="pending",
            input_text=input_text,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self.db.add(task)
        await self.db.commit()

        # 5. 启动 DeerFlow run
        thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"task-{task_id}"))
        run_body = {
            "input": {
                "messages": [
                    {"role": "user", "content": briefing},
                    {"role": "user", "content": input_text},
                ]
            },
            "config": build_run_configuration(executor),
        }

        try:
            await self.df.create_thread(thread_id=thread_id)
            run_resp = await self.df.create_run(
                thread_id=thread_id, body=run_body, idempotency_key=task_id
            )
            run_id = run_resp.get("run_id")
        except Exception:
            # run 启动失败，标记任务失败
            task.status = "failed"
            task.result_summary = "Failed to start DeerFlow run"
            task.updated_at = datetime.now(UTC)
            await self.db.commit()
            raise

        # 6. 更新 TASK
        task.status = "running"
        task.thread_id = thread_id
        task.run_id = run_id
        task.idempotency_key = task_id
        task.updated_at = datetime.now(UTC)
        await self.db.commit()

        # 7. 写审计日志
        await log_audit(
            self.db,
            action="task.create",
            actor=created_by,
            target=task_id,
            detail={"executor": executor, "input_text": input_text[:200]},
        )

        return task_to_dict(task)

    async def get_task(self, task_id: str) -> dict:
        """获取单个任务详情."""
        result = await self.db.execute(
            select(Task).where(Task.id == str(task_id))
        )
        task = result.scalars().first()
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        return task_to_dict(task)

    async def retry_task(self, task_id: str, actor: str = "system") -> dict:
        """重试失败任务."""
        result = await self.db.execute(
            select(Task).where(Task.id == str(task_id))
        )
        task = result.scalars().first()
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if task.status != "failed":
            raise TaskConflictError(f"Task cannot be retried: status={task.status}")

        # 生成新 run
        new_task_id = str(uuid.uuid4())
        new_thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"task-{new_task_id}"))
        new_idempotency_key = new_task_id

        run_body = {
            "input": {
                "messages": [
                    {"role": "user", "content": f"你是 {task.executor}。请重试之前的任务。"},
                    {"role": "user", "content": task.input_text},
                ]
            },
            "config": build_run_configuration(task.executor),
        }

        await self.df.create_thread(thread_id=new_thread_id)
        run_resp = await self.df.create_run(
            thread_id=new_thread_id, body=run_body, idempotency_key=new_idempotency_key
        )
        run_id = run_resp.get("run_id")

        task.status = "running"
        task.thread_id = new_thread_id
        task.run_id = run_id
        task.idempotency_key = new_idempotency_key
        task.result_summary = None
        task.updated_at = datetime.now(UTC)
        await self.db.commit()

        await log_audit(
            self.db,
            action="task.retry",
            actor=actor,
            target=task_id,
            detail={"new_thread_id": new_thread_id, "new_run_id": run_id},
        )

        return task_to_dict(task)

    async def cancel_task(self, task_id: str, actor: str = "system") -> dict:
        """取消任务."""
        result = await self.db.execute(
            select(Task).where(Task.id == str(task_id))
        )
        task = result.scalars().first()
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")
        if task.status not in ("running", "pending"):
            raise TaskConflictError(f"Task cannot be cancelled: status={task.status}")

        # 调 DeerFlow 取消 run
        if task.run_id and task.thread_id:
            try:
                await self.df.cancel_run(task.thread_id, task.run_id, wait=True)
            except Exception:
                logger.warning("Failed to cancel DeerFlow run", exc_info=True)

        task.status = "cancelled"
        task.updated_at = datetime.now(UTC)
        await self.db.commit()

        await log_audit(
            self.db,
            action="task.cancel",
            actor=actor,
            target=task_id,
            detail=None,
        )

        return task_to_dict(task)
