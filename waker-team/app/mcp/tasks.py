"""MCP tool business logic — separated from FastMCP for testability."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.deerflow.errors import AgentNotFoundError, TaskConflictError
from app.models.task import Task
from app.models.waker import Waker
from app.services.audit_service import log_audit
from app.services.group_service import GroupService
from app.services.status_mapping import map_run_to_task_status

logger = logging.getLogger(__name__)


async def _check_same_group(db: AsyncSession, caller_name: str, target_name: str) -> str | None:
    """校验 caller 与 target 是否在同一 group.

    Returns
    -------
    None  → 通过（同组或任一方不在任何组中，P0 兼容）。
    str   → 拒绝原因（跨组）。
    """
    gs = GroupService(db)
    caller_groups = await gs.get_waker_groups(caller_name)
    target_groups = await gs.get_waker_groups(target_name)

    if not caller_groups or not target_groups:
        return None  # 任一方不在任何组中 → 允许（P0 兼容）

    caller_group_ids = {g.id for g in caller_groups}
    target_group_ids = {g.id for g in target_groups}

    if not caller_group_ids & target_group_ids:
        return f"blocked: cross_group_delegation — {target_name} is not in your group"

    return None  # 同组 → 允许


class MCPService:
    """Encapsulates MCP tool business logic with injectable dependencies."""

    def __init__(self, db: AsyncSession, df: DeerFlowClient) -> None:
        self.db = db
        self.df = df

    async def query_group(self, group_id: str = "default") -> dict:
        """查询团队内 enabled 员工列表."""
        result = await self.db.execute(
            select(Waker).where(Waker.enabled == True)  # noqa: E712
        )
        wakers = result.scalars().all()
        return {
            "group_id": group_id,
            "members": [
                {"name": w.name, "description": w.description, "enabled": w.enabled}
                for w in wakers
            ],
        }

    async def delegate_to_agent(
        self,
        caller: str,
        group_id: str,
        target_agent: str,
        instruction: str,
        accept_criteria: str = "",
        sync: bool = True,
        sync_timeout: float = 60.0,
        poll_interval: float = 2.0,
    ) -> dict:
        """将任务委派给组内员工.

        Returns:
            sync 成功: {ticket_id, status:"done", result, run_id, thread_id}
            sync 超时: {ticket_id, status:"running", message}
        """
        # 0. 跨组校验
        cross_group_err = await _check_same_group(self.db, caller, target_agent)
        if cross_group_err is not None:
            return {"error": cross_group_err}

        # 1. 校验 target_agent enabled 且在同一 group
        result = await self.db.execute(
            select(Waker).where(Waker.name == target_agent)
        )
        waker = result.scalars().first()
        if waker is None:
            return {"error": f"Target agent not found: {target_agent}"}
        if not waker.enabled:
            return {"error": f"Target agent is disabled: {target_agent}"}

        # 2. 建 TASK(kind="delegate")
        task_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        task = Task(
            id=task_id,
            kind="delegate",
            group_id=group_id,
            executor=target_agent,
            status="pending",
            input_text=instruction,
            created_by=caller,
            created_at=now,
            updated_at=now,
        )
        self.db.add(task)
        await self.db.commit()

        # 3. 创建新 thread + run
        thread_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"task-{task_id}"))
        content = instruction
        if accept_criteria:
            content += f"\n\n交付标准: {accept_criteria}"
        run_body = {
            "input": {
                "messages": [{"role": "user", "content": content}]
            },
            "config": build_run_configuration(target_agent),
        }

        try:
            await self.df.create_thread(thread_id=thread_id)
            run_resp = await self.df.create_run(
                thread_id=thread_id, body=run_body, idempotency_key=task_id
            )
            run_id = run_resp.get("run_id")
        except Exception as exc:
            task.status = "failed"
            task.result_summary = f"Failed to start DeerFlow run: {exc}"
            task.updated_at = datetime.now(UTC)
            await self.db.commit()
            await log_audit(
                self.db,
                action="delegate.failed",
                actor=caller,
                target=task_id,
                detail={"target_agent": target_agent, "error": str(exc)},
            )
            raise

        # 4. 更新 TASK → running
        task.status = "running"
        task.thread_id = thread_id
        task.run_id = run_id
        task.idempotency_key = task_id
        task.updated_at = now
        await self.db.commit()

        # 5. sync 模式: 轮询 run 至终态
        if sync:
            max_polls = int(sync_timeout / poll_interval)
            for _ in range(max_polls):
                await asyncio.sleep(poll_interval)
                try:
                    run_info = await self.df.get_run(thread_id, run_id)
                except Exception:
                    logger.warning("Failed to poll run status", exc_info=True)
                    continue

                status = run_info.get("status", "")
                mapped = map_run_to_task_status(status)

                if mapped == "done":
                    task.status = "done"
                    task.result_summary = _extract_result(run_info)
                    task.updated_at = datetime.now(UTC)
                    await self.db.commit()
                    await log_audit(
                        self.db,
                        action="delegate.done",
                        actor=caller,
                        target=task_id,
                        detail={"target_agent": target_agent},
                    )
                    return {
                        "ticket_id": task_id,
                        "status": "done",
                        "result": task.result_summary,
                        "run_id": run_id,
                        "thread_id": thread_id,
                    }
                if mapped in ("failed", "cancelled"):
                    task.status = mapped
                    task.result_summary = f"Run ended with status: {status}"
                    task.updated_at = datetime.now(UTC)
                    await self.db.commit()
                    await log_audit(
                        self.db,
                        action="delegate.failed",
                        actor=caller,
                        target=task_id,
                        detail={"target_agent": target_agent, "run_status": status},
                    )
                    return {
                        "ticket_id": task_id,
                        "status": mapped,
                        "message": task.result_summary,
                        "run_id": run_id,
                        "thread_id": thread_id,
                    }

            # 超时 → 返回 ticket，TASK 保持 running
            await log_audit(
                self.db,
                action="delegate.timeout",
                actor=caller,
                target=task_id,
                detail={"target_agent": target_agent, "timeout": sync_timeout},
            )
            return {
                "ticket_id": task_id,
                "status": "running",
                "message": "后台继续，看板可查",
                "run_id": run_id,
                "thread_id": thread_id,
            }

        # 6. async 模式: 立即返回 ticket
        await log_audit(
            self.db,
            action="delegate.async",
            actor=caller,
            target=task_id,
            detail={"target_agent": target_agent},
        )
        return {
            "ticket_id": task_id,
            "status": "running",
            "message": "后台继续，看板可查",
            "run_id": run_id,
            "thread_id": thread_id,
        }


def _extract_result(run_info: dict) -> str:
    """从 run response 中提取结果摘要."""
    output = run_info.get("output")
    if isinstance(output, dict):
        messages = output.get("messages", [])
        if messages:
            last = messages[-1]
            if isinstance(last, dict):
                return last.get("content", str(output))
            return str(last)
        return str(output)
    if output is not None:
        return str(output)
    return "Run completed"
