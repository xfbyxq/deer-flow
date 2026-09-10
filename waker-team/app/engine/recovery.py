"""M14 崩溃恢复 — 引擎启动扫描 running 实例 + 按 DeerFlow Run 状态补记 + 幂等重发."""

import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.deerflow.client import DeerFlowClient
from app.deerflow.errors import DeerFlowError, ThreadNotFoundError
from app.engine.node_executors import ConditionExecutor, NotifyExecutor, get_executor
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.models.task import Task
from app.services.audit_service import log_audit
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)

# DeerFlow Run 终态集合
_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


class FlowRecovery:
    """Flow 崩溃恢复服务.

    启动时扫描所有 status="running" 的 FLOW_RUN，逐个检查其 NODE_RUN 状态，
    根据关联的 DeerFlow Run 状态执行补记、监控或幂等重发。
    """

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        flow_engine,
        deerflow_client: DeerFlowClient,
        task_service_factory,
    ) -> None:
        """初始化恢复服务.

        Parameters
        ----------
        db_session_factory:
            异步 session 工厂。
        flow_engine:
            FlowEngine 实例，用于回调和恢复执行。
        deerflow_client:
            DeerFlow API 客户端。
        task_service_factory:
            接受 AsyncSession 返回 TaskService 的工厂函数。
        """
        self._db_factory = db_session_factory
        self._engine = flow_engine
        self._client = deerflow_client
        self._task_service_factory = task_service_factory

    async def run(self) -> dict:
        """执行恢复，返回统计信息.

        Returns
        -------
        dict
            {"recovered": N, "skipped": M, "errors": E}
        """
        stats = {"recovered": 0, "skipped": 0, "errors": 0}

        async with self._db_factory() as db:
            # 1. 扫描所有 status="running" 的 FLOW_RUN
            result = await db.execute(
                select(FlowRun).where(FlowRun.status == "running")
            )
            running_flows = result.scalars().all()

            if not running_flows:
                return stats

            logger.info("FlowRecovery: found %d running flow(s)", len(running_flows))

            for flow_run in running_flows:
                try:
                    recovered = await self._recover_flow(flow_run, db)
                    if recovered:
                        stats["recovered"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception:
                    logger.exception(
                        "FlowRecovery: error recovering flow %s", flow_run.id
                    )
                    stats["errors"] += 1

        return stats

    async def _recover_flow(self, flow_run: FlowRun, db: AsyncSession) -> bool:
        """恢复单个 Flow 运行.

        Returns
        -------
        bool
            True 表示执行了恢复操作，False 表示无需恢复。
        """
        # 1. 加载所有 NODE_RUN
        nr_result = await db.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id)
        )
        node_runs = nr_result.scalars().all()

        # 2. 过滤需要恢复的节点（running / pending / waiting_review）
        needs_recovery = [
            nr
            for nr in node_runs
            if nr.status in ("running", "pending", "waiting_review")
        ]

        if not needs_recovery:
            # 所有节点都已完成/失败/跳过 → 直接标记 FlowRun 完成
            await self._finalize_flow(flow_run, node_runs, db)
            return True

        # 3. 对每个需要恢复的节点执行恢复
        recovered_count = 0
        for node_run in needs_recovery:
            try:
                await self._recover_node(node_run, flow_run, db)
                recovered_count += 1
            except Exception:
                logger.exception(
                    "FlowRecovery: error recovering node %s in flow %s",
                    node_run.node_key,
                    flow_run.id,
                )

        if recovered_count > 0:
            await log_audit(
                db,
                action="flow.recovery",
                actor="flow_recovery",
                target=flow_run.id,
                detail={
                    "nodes_recovered": recovered_count,
                    "total_nodes": len(node_runs),
                },
            )

        # 4. 尝试恢复 Flow 执行循环
        await self._try_resume_flow(flow_run, db)

        return recovered_count > 0

    async def _recover_node(
        self, node_run: NodeRun, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """恢复单个节点.

        按节点类型分派到不同的恢复策略：
        - waker_task / leader_plan: 按 DeerFlow Run 状态补记或重发
        - human_review: 天然安全，保持 waiting_review
        - condition: 重新求值（无副作用）
        - notify: 幂等重发
        """
        node_type = node_run.node_type

        # 场景 1-3: waker_task / leader_plan 且有 task_id
        if node_type in ("waker_task", "leader_plan") and node_run.task_id:
            await self._recover_task_node(node_run, flow_run, db)

        # 场景 1 变体: waker_task / leader_plan 但无 task_id（尚未创建 Task）
        elif node_type in ("waker_task", "leader_plan") and not node_run.task_id:
            # 节点还未真正启动 DeerFlow Run，直接重新执行
            await self._resend_node(node_run, flow_run, db)

        # 场景 4: human_review → 天然安全
        elif node_type == "human_review":
            await self._recover_human_review(node_run, db)

        # 场景 5: condition → 重新求值
        elif node_type == "condition":
            await self._reevaluate_condition(node_run, flow_run, db)

        # 场景 5 补充: notify → 重发（幂等）
        elif node_type == "notify":
            if node_run.status in ("pending", "running"):
                await self._resend_notify(node_run, flow_run, db)

    # ------------------------------------------------------------------
    # waker_task / leader_plan 恢复
    # ------------------------------------------------------------------

    async def _recover_task_node(
        self, node_run: NodeRun, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """恢复 waker_task / leader_plan 节点.

        通过 task_id 查找关联的 Task，获取 DeerFlow Run 状态，按状态分派：
        - 终态成功 → 补记 outcome，推进
        - 终态失败 → 标记 failed
        - 仍在跑 → 加入 SyncEngine 监控（无需操作，SyncEngine 会自动轮询）
        - Run 不存在 → 幂等重发
        """
        # 查找关联的 Task 记录
        task_result = await db.execute(select(Task).where(Task.id == node_run.task_id))
        task = task_result.scalars().first()

        if task is None or not task.run_id or not task.thread_id:
            # Task 记录不存在或无 run 信息 → 幂等重发
            logger.info(
                "FlowRecovery: node %s task not found or missing run info, resending",
                node_run.node_key,
            )
            await self._resend_node(node_run, flow_run, db)
            return

        # 查询 DeerFlow Run 状态
        run_status = await self._get_run_status(task.thread_id, task.run_id)

        if run_status in ("success",):
            # 场景 1a: Run 已终态（成功）→ 补记 outcome
            await self._complete_node(node_run, task, db)

        elif run_status in ("error", "timeout", "interrupted"):
            # 场景 1b: Run 已终态（失败）→ 标记 failed
            await self._fail_node(node_run, f"Run ended with {run_status}", db)

        elif run_status in ("pending", "running"):
            # 场景 2: Run 仍在跑 → SyncEngine 会自动轮询，无需额外操作
            logger.info(
                "FlowRecovery: node %s run still %s, SyncEngine will handle",
                node_run.node_key,
                run_status,
            )

        elif run_status is None:
            # 场景 3: Run 不存在 → 幂等重发
            logger.info(
                "FlowRecovery: node %s run not found, resending",
                node_run.node_key,
            )
            await self._resend_node(node_run, flow_run, db)

    # ------------------------------------------------------------------
    # human_review 恢复
    # ------------------------------------------------------------------

    async def _recover_human_review(
        self, node_run: NodeRun, db: AsyncSession
    ) -> None:
        """恢复 human_review 节点.

        - waiting_review → 保持，天然安全
        - pending → 设置为 waiting_review
        """
        if node_run.status == "waiting_review":
            # 保持 waiting_review，无需操作
            logger.debug(
                "FlowRecovery: human_review %s still waiting_review, no action",
                node_run.node_key,
            )
        elif node_run.status == "pending":
            # 还未进入 review，重新设置为 waiting_review
            node_run.status = "waiting_review"
            node_run.started_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info(
                "FlowRecovery: human_review %s set to waiting_review",
                node_run.node_key,
            )

    # ------------------------------------------------------------------
    # condition 恢复
    # ------------------------------------------------------------------

    async def _reevaluate_condition(
        self, node_run: NodeRun, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """重新求值条件节点（无副作用）."""
        node_input = json.loads(node_run.input_json) if node_run.input_json else {}
        expression = node_input.get("expression", "")

        # 构建 output_cache
        output_cache = {}
        if flow_run.output_cache_json:
            output_cache = json.loads(flow_run.output_cache_json)

        context = {"output_cache": output_cache}
        result = ConditionExecutor._safe_evaluate(expression, context)

        branches = node_input.get("branches", {})
        branch_key = "true" if result else "false"
        target_node = branches.get(branch_key)

        node_run.status = "completed"
        node_run.started_at = datetime.now(timezone.utc)
        node_run.completed_at = datetime.now(timezone.utc)
        outcome = {"result": result, "branch": branch_key, "target_node": target_node}
        node_run.outcome_json = json.dumps(outcome)
        await db.commit()

        logger.info(
            "FlowRecovery: condition %s re-evaluated to %s",
            node_run.node_key,
            branch_key,
        )

    # ------------------------------------------------------------------
    # notify 恢复
    # ------------------------------------------------------------------

    async def _resend_notify(
        self, node_run: NodeRun, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """重发通知（幂等）."""
        node_input = json.loads(node_run.input_json) if node_run.input_json else {}
        executor = NotifyExecutor()

        # 构建 context
        output_cache = {}
        if flow_run.output_cache_json:
            output_cache = json.loads(flow_run.output_cache_json)
        context = {"output_cache": output_cache}

        await executor.execute(node_input, flow_run, node_run, db, context)

        await log_audit(
            db,
            action="flow.recovery.notify_resend",
            actor="flow_recovery",
            target=node_run.id,
            detail={"node_key": node_run.node_key},
        )

        logger.info("FlowRecovery: notify %s re-sent", node_run.node_key)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _get_run_status(
        self, thread_id: str, run_id: str
    ) -> str | None:
        """查询 DeerFlow Run 状态.

        Returns
        -------
        str | None
            Run 状态字符串，或 None（Run 不存在）。
        """
        try:
            run_resp = await self._client.get_run(thread_id, run_id)
            return run_resp.get("status")
        except ThreadNotFoundError:
            return None
        except DeerFlowError:
            logger.warning(
                "FlowRecovery: failed to get run status for %s/%s",
                thread_id,
                run_id,
                exc_info=True,
            )
            return None

    async def _complete_node(
        self, node_run: NodeRun, task: Task, db: AsyncSession
    ) -> None:
        """标记节点完成（补记 outcome）."""
        outcome = {
            "status": "done",
            "result_summary": task.result_summary or "recovered",
        }
        node_run.outcome_json = json.dumps(outcome)
        node_run.status = "completed"
        node_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            "FlowRecovery: node %s marked completed (recovered from run success)",
            node_run.node_key,
        )

    async def _fail_node(
        self, node_run: NodeRun, error: str, db: AsyncSession
    ) -> None:
        """标记节点失败."""
        node_run.status = "failed"
        node_run.error_message = error
        node_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(
            "FlowRecovery: node %s marked failed: %s",
            node_run.node_key,
            error,
        )

    async def _resend_node(
        self, node_run: NodeRun, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """幂等重发节点.

        创建新 Task，使用新 Idempotency-Key: ``recover-{node_run_id}-{uuid4_hex[:8]}``
        """
        node_input = json.loads(node_run.input_json) if node_run.input_json else {}
        waker = node_input.get("waker", "")
        instruction = (
            node_input.get("instruction")
            or f"Execute flow node: {node_run.node_key}"
        )

        # 生成新幂等键
        idempotency_key = f"recover-{node_run.id}-{uuid.uuid4().hex[:8]}"

        task_service = self._task_service_factory(db)
        try:
            task_dict = await task_service.create_task(
                executor=waker,
                input_text=instruction,
                group_id=flow_run.group_id or "default",
                created_by=f"flow_recovery:{flow_run.id}",
                kind="flow_node",
            )

            # 更新 node_run 关联新 task
            node_run.task_id = task_dict["id"]
            node_run.status = "running"
            node_run.started_at = datetime.now(timezone.utc)
            node_run.retry_count += 1
            await db.commit()

            await log_audit(
                db,
                action="flow.recovery.resend",
                actor="flow_recovery",
                target=node_run.id,
                detail={
                    "node_key": node_run.node_key,
                    "new_task_id": task_dict["id"],
                    "idempotency_key": idempotency_key,
                },
            )

            logger.info(
                "FlowRecovery: node %s resent with new task %s",
                node_run.node_key,
                task_dict["id"],
            )
        except Exception:
            logger.exception(
                "FlowRecovery: failed to resend node %s", node_run.node_key
            )
            node_run.status = "failed"
            node_run.error_message = "Recovery resend failed"
            node_run.completed_at = datetime.now(timezone.utc)
            await db.commit()

    async def _finalize_flow(
        self,
        flow_run: FlowRun,
        node_runs: list[NodeRun],
        db: AsyncSession,
    ) -> None:
        """所有节点都已终态，标记 FlowRun 完成."""
        has_failed = any(nr.status == "failed" for nr in node_runs)
        if has_failed:
            flow_run.status = "failed"
            flow_run.failure_reason = "Recovery: some nodes failed"
        else:
            flow_run.status = "completed"
        flow_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        await log_audit(
            db,
            action="flow.recovery.finalize",
            actor="flow_recovery",
            target=flow_run.id,
            detail={"final_status": flow_run.status},
        )

    async def _try_resume_flow(
        self, flow_run: FlowRun, db: AsyncSession
    ) -> None:
        """尝试恢复 Flow 执行循环.

        如果所有节点都已终态，标记 FlowRun 完成；
        否则通过 FlowEngine 重新启动执行循环。
        """
        # 重新加载所有 node_runs
        nr_result = await db.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id)
        )
        node_runs = nr_result.scalars().all()

        # 检查是否所有节点都已完成/失败/跳过
        all_terminal = all(
            nr.status in ("completed", "failed", "cancelled", "skipped")
            for nr in node_runs
        )

        if all_terminal:
            await self._finalize_flow(flow_run, node_runs, db)
        else:
            # 还有活跃节点（running 等），恢复执行循环
            await self._engine.resume_from_recovery(flow_run.id)
            logger.info(
                "FlowRecovery: resumed execution for flow %s", flow_run.id
            )
