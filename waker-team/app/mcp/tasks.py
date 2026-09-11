"""MCP tool business logic — separated from FastMCP for testability."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.deerflow.errors import AgentNotFoundError, TaskConflictError
from app.models.group import Group, GroupMember
from app.models.task import Task
from app.models.waker import Waker
from app.services.audit_service import log_audit
from app.services.chat_reply import _extract_reply_from_state  # 复用 run 回复提取（thread state 兑底）
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


async def _group_member_names(db: AsyncSession, group_ids: list[str]) -> set[str]:
    """返回一组群组的成员名字集合 = ``GroupMember`` ∪ ``Group.leader_waker_id``.

    CF12：``GroupService.get_waker_groups`` 已声明「leader 由 ``leader_waker_id``
    关联、不要求同时在 group_members 中；同事查询与跨组校验均应将 leader 视为
    组内成员」。原实现只 join ``GroupMember``，导致 Leader 在同事查询中不可见
    （成员无法把结果汇总给 Leader、Leader 也看不到自己），与该不变量矛盾。
    """
    if not group_ids:
        return set()

    member_rows = await db.execute(
        select(GroupMember.waker_id).where(GroupMember.group_id.in_(group_ids))
    )
    names = {name for name in member_rows.scalars().all() if name}

    leader_rows = await db.execute(
        select(Group.leader_waker_id).where(
            Group.id.in_(group_ids),
            Group.leader_waker_id.isnot(None),
        )
    )
    names |= {name for name in leader_rows.scalars().all() if name}
    return names


class MCPService:
    """Encapsulates MCP tool business logic with injectable dependencies.

    每次工具调用使用独立短生命周期 session（factory 模式），不复用长活 session：
    历史故障中一次 flush 失败（UPDATE 0 rows matched）会使 session 进入
    pending-rollback 毒化状态，后续所有工具调用持续报错直到进程重启
    （表现为 query_group 间歇性 "Error executing tool"）。
    """

    def __init__(self, session_factory, df: DeerFlowClient) -> None:
        self.session_factory = session_factory
        self.df = df

    async def query_group(self, group_id: str = "default", caller: str | None = None) -> dict:
        """查询团队内 enabled 员工列表（按群组成员关系过滤）.

        同事 = 与你同群组的 enabled 员工（不再返回全体员工）：
        - caller 存在：以 caller 所在组为范围；显式传入的 group_id 在
          caller 的组里时聚焦该组，否则覆盖 caller 的全部组；
        - caller 不存在（管理/无身份视角）：按显式 group_id 过滤。

        成员集合为 ``GroupMember`` ∪ ``Group.leader_waker_id``（CF12）：Leader
        即使没有 group_members 行也属组内成员，与 ``get_waker_groups`` 一致。
        """
        async with self.session_factory() as db:
            if caller:
                gs = GroupService(db)
                caller_groups = await gs.get_waker_groups(caller)
                if not caller_groups:
                    return {
                        "group_id": group_id,
                        "members": [],
                        "message": f"{caller} 尚未加入任何群组，暂无同事可查询。",
                    }
                target_ids = [g.id for g in caller_groups]
                if any(g.id == group_id for g in caller_groups):
                    target_ids = [group_id]
            else:
                target_ids = [group_id]

            names = await _group_member_names(db, target_ids)
            result = await db.execute(
                select(Waker)
                .where(
                    Waker.name.in_(list(names)),
                    Waker.enabled == True,  # noqa: E712
                )
                .order_by(Waker.name)
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
        sync_timeout: float = 240.0,
        poll_interval: float = 2.0,
    ) -> dict:
        """将任务委派给组内员工.

        Returns:
            sync 成功: {ticket_id, status:"done", result, run_id, thread_id}
            sync 超时: {ticket_id, status:"running", message}
        """
        # 0/1/2. 校验 + 建 TASK(kind="delegate")（同一短 session）
        async with self.session_factory() as db:
            # 禁止自派：自身应直接完成的子任务不需要委派（防 Leader 把活派给自己）
            if target_agent == caller:
                return {
                    "error": (
                        f"Cannot delegate to yourself ({caller}): "
                        "请直接完成该子任务，或改派给群内其他成员。"
                    )
                }
            cross_group_err = await _check_same_group(db, caller, target_agent)
            if cross_group_err is not None:
                return {"error": cross_group_err}

            result = await db.execute(select(Waker).where(Waker.name == target_agent))
            waker = result.scalars().first()
            if waker is None:
                return {"error": f"Target agent not found: {target_agent}"}
            if not waker.enabled:
                return {"error": f"Target agent is disabled: {target_agent}"}

            task_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            db.add(
                Task(
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
            )
            await db.commit()

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
            await self._update_task(
                task_id, status="failed", result_summary=f"Failed to start DeerFlow run: {exc}"
            )
            await self._audit(
                "delegate.failed", caller, task_id, {"target_agent": target_agent, "error": str(exc)}
            )
            raise

        # 4. 更新 TASK → running
        await self._update_task(
            task_id, status="running", thread_id=thread_id, run_id=run_id, idempotency_key=task_id
        )

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
                    # 优先从成员 thread state 提取真实回复正文（run 响应常不含 output）；
                    # 澄清（ask_clarification）属会话交互，委派结果仅取正文文本。
                    summary: str | None = None
                    try:
                        state = await self.df.get_thread_state(thread_id)
                        summary, _ = _extract_reply_from_state(state, run_id)
                    except Exception:
                        logger.warning(
                            "delegate result: thread state fallback failed", exc_info=True
                        )
                    if not summary:
                        summary = _extract_result(run_info)
                    await self._update_task(task_id, status="done", result_summary=summary)
                    await self._audit(
                        "delegate.done", caller, task_id, {"target_agent": target_agent}
                    )
                    return {
                        "ticket_id": task_id,
                        "status": "done",
                        "result": summary,
                        "run_id": run_id,
                        "thread_id": thread_id,
                    }
                if mapped in ("failed", "cancelled"):
                    summary = f"Run ended with status: {status}"
                    await self._update_task(task_id, status=mapped, result_summary=summary)
                    await self._audit(
                        "delegate.failed",
                        caller,
                        task_id,
                        {"target_agent": target_agent, "run_status": status},
                    )
                    return {
                        "ticket_id": task_id,
                        "status": mapped,
                        "message": summary,
                        "run_id": run_id,
                        "thread_id": thread_id,
                    }

            # 超时 → 返回 ticket，TASK 保持 running
            await self._audit(
                "delegate.timeout",
                caller,
                task_id,
                {"target_agent": target_agent, "timeout": sync_timeout},
            )
            return {
                "ticket_id": task_id,
                "status": "running",
                "message": "后台继续，看板可查",
                "run_id": run_id,
                "thread_id": thread_id,
            }

        # 6. async 模式: 立即返回 ticket
        await self._audit("delegate.async", caller, task_id, {"target_agent": target_agent})
        return {
            "ticket_id": task_id,
            "status": "running",
            "message": "后台继续，看板可查",
            "run_id": run_id,
            "thread_id": thread_id,
        }

    async def _update_task(self, task_id: str, **fields: object) -> None:
        """在独立 session 中更新任务字段（任务可能已在别的 session 中被修改）."""
        async with self.session_factory() as db:
            task = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalars().first()
            if task is None:
                return
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = datetime.now(UTC)
            await db.commit()

    async def _audit(self, action: str, actor: str, target: str, detail: dict | None) -> None:
        """在独立 session 中写审计日志."""
        async with self.session_factory() as db:
            await log_audit(db, action=action, actor=actor, target=target, detail=detail)


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
