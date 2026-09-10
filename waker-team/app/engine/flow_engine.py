"""WakerFlow 引擎 — DAG 推进 + 节点执行 + 生命周期管理."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.deerflow.client import DeerFlowClient
from app.engine.dag import DAGScheduler
from app.engine.flow_plan_parser import parse_leader_plan_output, validate_subtasks
from app.engine.node_executors import NodeExecutorError, get_executor
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.services.audit_service import log_audit

logger = logging.getLogger(__name__)


class FlowEngineError(Exception):
    """Flow 引擎错误."""


class FlowEngine:
    """WakerFlow 引擎 — 管理 Flow 运行生命周期."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        deerflow_client: DeerFlowClient,
        task_service_factory,
    ) -> None:
        """初始化引擎.

        Parameters
        ----------
        db_session_factory:
            异步 session 工厂。
        deerflow_client:
            DeerFlow API 客户端。
        task_service_factory:
            接受 AsyncSession 返回 TaskService 的工厂函数。
        """
        self._db_factory = db_session_factory
        self._client = deerflow_client
        self._task_service_factory = task_service_factory
        # flow_run_id -> asyncio.Event（用于等待回调唤醒）
        self._wait_events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(
        self,
        flow_def_id: str,
        db: AsyncSession,
        created_by: str = "manual",
        trigger_type: str = "manual",
    ) -> FlowRun:
        """启动 Flow 运行.

        1. 加载 FlowDef，解析 definition_json
        2. 创建 FlowRun (status=running)
        3. 创建 DAGScheduler
        4. 为所有节点创建 NODE_RUN (status=pending)
        5. 开始执行循环
        """
        # 1. 加载 FlowDef
        result = await db.execute(select(FlowDef).where(FlowDef.id == flow_def_id))
        flow_def = result.scalars().first()
        if flow_def is None:
            raise FlowEngineError(f"Flow definition not found: {flow_def_id}")

        def_json = flow_def.definition_json
        if isinstance(def_json, str):
            def_json = json.loads(def_json)

        nodes = def_json.get("nodes") or []
        # 注意：前端/API 保存时可能把 settings 序列化为 null，不能用 .get("settings", {})（key 存在时返回 None）
        settings = def_json.get("settings") or {}
        max_concurrent = settings.get("max_concurrent_nodes", 5)

        # 2. 创建 FlowRun
        now = datetime.now(timezone.utc)
        flow_run = FlowRun(
            id=str(uuid.uuid4()),
            flow_def_id=flow_def.id,
            group_id=flow_def.group_id,
            status="running",
            started_at=now,
            created_by=created_by,
            trigger_type=trigger_type,
        )
        db.add(flow_run)
        await db.commit()

        # 3. 为所有节点创建 NODE_RUN
        for node_def in nodes:
            node_run = NodeRun(
                id=str(uuid.uuid4()),
                flow_run_id=flow_run.id,
                node_id=node_def["key"],
                node_key=node_def["key"],
                node_type=node_def["type"],
                status="pending",
                input_json=json.dumps(node_def),
            )
            db.add(node_run)
        await db.commit()

        await log_audit(
            db,
            action="flow_run.start",
            actor=created_by,
            target=flow_run.id,
            detail={"flow_def_id": flow_def_id, "trigger_type": trigger_type},
        )

        # 4. 开始执行（非阻塞）
        asyncio.create_task(self._run_loop(flow_run.id, max_concurrent))

        return flow_run

    async def pause(self, flow_run_id: str, db: AsyncSession) -> FlowRun:
        """暂停 Flow 运行."""
        result = await db.execute(select(FlowRun).where(FlowRun.id == flow_run_id))
        flow_run = result.scalars().first()
        if flow_run is None:
            raise FlowEngineError(f"Flow run not found: {flow_run_id}")
        if flow_run.status != "running":
            raise FlowEngineError(f"Flow run is not running: status={flow_run.status}")

        flow_run.status = "paused"
        flow_run.paused_at = datetime.now(timezone.utc)
        await db.commit()

        await log_audit(db, action="flow_run.pause", actor="manual", target=flow_run_id, detail=None)
        return flow_run

    async def resume(self, flow_run_id: str, db: AsyncSession) -> FlowRun:
        """恢复 Flow 运行."""
        result = await db.execute(select(FlowRun).where(FlowRun.id == flow_run_id))
        flow_run = result.scalars().first()
        if flow_run is None:
            raise FlowEngineError(f"Flow run not found: {flow_run_id}")
        if flow_run.status != "paused":
            raise FlowEngineError(f"Flow run is not paused: status={flow_run.status}")

        flow_run.status = "running"
        flow_run.paused_at = None
        await db.commit()

        # 重新加载 FlowDef 获取 max_concurrent
        def_result = await db.execute(select(FlowDef).where(FlowDef.id == flow_run.flow_def_id))
        flow_def = def_result.scalars().first()
        max_concurrent = 5
        if flow_def and flow_def.definition_json:
            def_json = flow_def.definition_json
            if isinstance(def_json, str):
                def_json = json.loads(def_json)
            settings = def_json.get("settings") or {}
            max_concurrent = settings.get("max_concurrent_nodes", 5)

        await log_audit(db, action="flow_run.resume", actor="manual", target=flow_run_id, detail=None)

        # 继续执行循环
        asyncio.create_task(self._run_loop(flow_run_id, max_concurrent))
        return flow_run

    async def cancel(self, flow_run_id: str, db: AsyncSession) -> FlowRun:
        """取消 Flow 运行."""
        result = await db.execute(select(FlowRun).where(FlowRun.id == flow_run_id))
        flow_run = result.scalars().first()
        if flow_run is None:
            raise FlowEngineError(f"Flow run not found: {flow_run_id}")
        if flow_run.status in ("completed", "cancelled"):
            raise FlowEngineError(f"Flow run already terminal: status={flow_run.status}")

        flow_run.status = "cancelled"
        flow_run.completed_at = datetime.now(timezone.utc)

        # 将所有 pending/running 的 node_run 标记为 cancelled
        nr_result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.status.in_(["pending", "running", "waiting_review"]),
            )
        )
        for nr in nr_result.scalars().all():
            nr.status = "cancelled"
            nr.completed_at = datetime.now(timezone.utc)

        await db.commit()
        await log_audit(db, action="flow_run.cancel", actor="manual", target=flow_run_id, detail=None)

        # 唤醒等待的执行循环
        event = self._wait_events.get(flow_run_id)
        if event:
            event.set()

        return flow_run

    # ------------------------------------------------------------------
    # 崩溃恢复入口
    # ------------------------------------------------------------------

    async def resume_from_recovery(self, flow_run_id: str) -> None:
        """崩溃恢复后重新启动执行循环.

        由 FlowRecovery 调用，在修复节点状态后恢复 DAG 推进。
        """
        async with self._db_factory() as db:
            result = await db.execute(
                select(FlowRun).where(FlowRun.id == flow_run_id)
            )
            flow_run = result.scalars().first()
            if flow_run is None or flow_run.status != "running":
                return

            # 加载 FlowDef 获取 max_concurrent
            def_result = await db.execute(
                select(FlowDef).where(FlowDef.id == flow_run.flow_def_id)
            )
            flow_def = def_result.scalars().first()
            max_concurrent = 5
            if flow_def and flow_def.definition_json:
                def_json = flow_def.definition_json
                if isinstance(def_json, str):
                    def_json = json.loads(def_json)
                settings = def_json.get("settings") or {}
                max_concurrent = settings.get("max_concurrent_nodes", 5)

        logger.info(
            "FlowEngine: resuming flow %s from recovery (max_concurrent=%d)",
            flow_run_id,
            max_concurrent,
        )
        asyncio.create_task(self._run_loop(flow_run_id, max_concurrent))

    # ------------------------------------------------------------------
    # 回调入口
    # ------------------------------------------------------------------

    async def on_node_completed(
        self, flow_run_id: str, node_key: str, outcome: dict, db: AsyncSession
    ) -> None:
        """SyncEngine 回调：节点完成.

        1. 更新 NODE_RUN.outcome_json
        2. 唤醒执行循环继续推进 DAG
        """
        # 更新 node_run
        result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
            )
        )
        node_run = result.scalars().first()
        if node_run is None:
            logger.warning("on_node_completed: node_run not found for %s/%s", flow_run_id, node_key)
            return

        node_run.outcome_json = json.dumps(outcome)
        node_run.status = "completed"
        node_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        # 唤醒执行循环
        event = self._wait_events.get(flow_run_id)
        if event:
            event.set()

    async def on_node_failed(
        self, flow_run_id: str, node_key: str, error_message: str, db: AsyncSession
    ) -> None:
        """SyncEngine 回调：节点失败."""
        result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
            )
        )
        node_run = result.scalars().first()
        if node_run is None:
            logger.warning("on_node_failed: node_run not found for %s/%s", flow_run_id, node_key)
            return

        node_run.status = "failed"
        node_run.error_message = error_message
        node_run.completed_at = datetime.now(timezone.utc)
        await db.commit()

        # 唤醒执行循环
        event = self._wait_events.get(flow_run_id)
        if event:
            event.set()

    async def on_review_completed(
        self, flow_run_id: str, node_key: str, outcome: dict, db: AsyncSession
    ) -> None:
        """人工确认完成回调 — 由 ReviewService 调用.

        与 on_node_completed 相同，但语义上区分人工确认操作。
        """
        await self.on_node_completed(flow_run_id, node_key, outcome, db)

    async def on_rerun_requested(
        self, flow_run_id: str, node_key: str, new_node_run: NodeRun, db: AsyncSession
    ) -> None:
        """重跑请求回调 — 由 RerunService 调用.

        唤醒执行循环以重新执行该节点。
        """
        # 唤醒执行循环，让它重新调度该节点
        event = self._wait_events.get(flow_run_id)
        if event:
            event.set()

    async def on_plan_completed(
        self, flow_run_id: str, node_key: str, subtasks: list[dict], db: AsyncSession
    ) -> None:
        """leader_plan 完成回调：实例化扇出子任务.

        1. 为每个子任务创建 NODE_RUN (parent_node_run_id=plan_node_run.id)
        2. 唤醒执行循环
        """
        result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
            )
        )
        plan_node_run = result.scalars().first()
        if plan_node_run is None:
            logger.warning("on_plan_completed: node_run not found for %s/%s", flow_run_id, node_key)
            return

        # 为每个子任务创建 NODE_RUN
        for subtask in subtasks:
            child_node_run = NodeRun(
                id=str(uuid.uuid4()),
                flow_run_id=flow_run_id,
                node_id=f"{node_key}_sub_{subtask.get('waker', 'unknown')}",
                node_key=f"{node_key}_sub_{subtask.get('waker', 'unknown')}",
                node_type="waker_task",
                status="pending",
                parent_node_run_id=plan_node_run.id,
                input_json=json.dumps(subtask),
            )
            db.add(child_node_run)

        await db.commit()

        # 唤醒执行循环
        event = self._wait_events.get(flow_run_id)
        if event:
            event.set()

    # ------------------------------------------------------------------
    # 内部执行循环
    # ------------------------------------------------------------------

    async def _run_loop(self, flow_run_id: str, max_concurrent: int) -> None:
        """主执行循环.

        1. 获取就绪节点
        2. 受 max_concurrent_nodes 限制并行执行
        3. 等待完成回调
        4. 检查是否全部完成
        """
        async with self._db_factory() as db:
            # 加载 FlowRun
            result = await db.execute(select(FlowRun).where(FlowRun.id == flow_run_id))
            flow_run = result.scalars().first()
            if flow_run is None or flow_run.status not in ("running",):
                return

            # 加载 FlowDef 获取节点定义
            def_result = await db.execute(select(FlowDef).where(FlowDef.id == flow_run.flow_def_id))
            flow_def = def_result.scalars().first()
            if flow_def is None:
                return

            def_json = flow_def.definition_json
            if isinstance(def_json, str):
                def_json = json.loads(def_json)

            # 加载已有的 node_runs 恢复状态
            nr_result = await db.execute(
                select(NodeRun).where(NodeRun.flow_run_id == flow_run_id)
            )
            existing_runs = {nr.node_key: nr for nr in nr_result.scalars().all()}

            # 构建 DAG 节点列表（包含动态子节点）
            all_nodes = list(def_json.get("nodes") or [])
            # 添加动态子节点（leader_plan 扇出的）
            for nr in existing_runs.values():
                if nr.parent_node_run_id is not None and nr.node_key not in {n["key"] for n in all_nodes}:
                    node_def = json.loads(nr.input_json) if nr.input_json else {}
                    all_nodes.append({
                        "key": nr.node_key,
                        "type": nr.node_type,
                        "waker": node_def.get("waker", ""),
                        "instruction": node_def.get("instruction", ""),
                        "depends_on": [],  # 动态子节点无依赖（由 plan 节点管理）
                    })

            scheduler = DAGScheduler(all_nodes)

            # 恢复已完成/运行中状态
            for nr in existing_runs.values():
                if nr.status == "completed":
                    scheduler.mark_completed(nr.node_key)
                elif nr.status == "running":
                    scheduler.mark_running(nr.node_key)
                elif nr.status in ("cancelled", "skipped"):
                    scheduler.mark_skipped(nr.node_key)

            # 创建等待事件
            event = asyncio.Event()
            self._wait_events[flow_run_id] = event

            try:
                while not scheduler.is_all_done():
                    # 检查 flow_run 是否被暂停/取消
                    await db.refresh(flow_run)
                    if flow_run.status != "running":
                        break

                    ready_nodes = scheduler.get_ready_nodes(max_concurrent)
                    if not ready_nodes:
                        # 没有就绪节点但有运行中的节点 → 等待回调
                        if len(scheduler._running) > 0:
                            event.clear()
                            try:
                                await asyncio.wait_for(event.wait(), timeout=30.0)
                            except asyncio.TimeoutError:
                                continue
                            continue
                        else:
                            # 没有运行中节点也没有就绪节点 → 卡住
                            logger.warning("Flow run %s is stuck: no ready nodes, no running nodes", flow_run_id)
                            flow_run.status = "failed"
                            flow_run.failure_reason = "No ready nodes and no running nodes"
                            flow_run.completed_at = datetime.now(timezone.utc)
                            await db.commit()
                            break

                    # 执行就绪节点
                    for node_def in ready_nodes:
                        scheduler.mark_running(node_def["key"])
                        node_run = existing_runs.get(node_def["key"])
                        if node_run is None:
                            # 动态创建的节点，创建 NodeRun 记录
                            node_run = NodeRun(
                                id=str(uuid.uuid4()),
                                flow_run_id=flow_run_id,
                                node_id=node_def["key"],
                                node_key=node_def["key"],
                                node_type=node_def["type"],
                                status="running",
                                input_json=json.dumps(node_def),
                            )
                            db.add(node_run)
                            existing_runs[node_def["key"]] = node_run
                            await db.commit()
                        else:
                            node_run.status = "running"
                            node_run.started_at = datetime.now(timezone.utc)
                            await db.commit()

                        # 异步执行节点
                        asyncio.create_task(
                            self._execute_node(node_def, flow_run, node_run, scheduler)
                        )

                    # 等待一轮完成
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue

                # 全部完成
                if scheduler.is_all_done():
                    flow_run.status = "completed"
                    flow_run.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await log_audit(
                        db,
                        action="flow_run.complete",
                        actor="flow_engine",
                        target=flow_run_id,
                        detail=None,
                    )

            finally:
                self._wait_events.pop(flow_run_id, None)

    async def _execute_node(
        self, node_def: dict, flow_run: FlowRun, node_run: NodeRun, scheduler: DAGScheduler
    ) -> None:
        """执行单个节点并处理结果."""
        async with self._db_factory() as db:
            node_type = node_def["type"]
            node_key = node_def["key"]

            try:
                # 构建执行上下文
                task_service = self._task_service_factory(db)
                context = {
                    "task_service": task_service,
                    "deerflow_client": self._client,
                    "output_cache": {},
                }

                # 加载 output_cache
                await db.refresh(flow_run)
                if flow_run.output_cache_json:
                    context["output_cache"] = json.loads(flow_run.output_cache_json)

                executor = get_executor(node_type)
                outcome = await executor.execute(node_def, flow_run, node_run, db, context)

                # 对于同步完成的节点（condition, notify, human_review），直接标记
                if outcome.get("status") == "completed" or node_type in ("condition", "notify"):
                    scheduler.mark_completed(node_key)
                    # 更新 output_cache
                    output_cache = context.get("output_cache", {})
                    output_cache[node_key] = outcome
                    flow_run.output_cache_json = json.dumps(output_cache)
                    await db.commit()

                elif outcome.get("status") == "waiting_review":
                    # human_review 节点等待人工操作，不推进
                    pass

                elif outcome.get("status") == "running":
                    # waker_task / leader_plan：等待 SyncEngine 回调
                    # 存储 node_key 到 task 的映射，便于回调查找
                    pass

                # 唤醒执行循环
                event = self._wait_events.get(flow_run.id)
                if event:
                    event.set()

            except NodeExecutorError as exc:
                logger.error("Node %s execution failed: %s", node_key, exc)
                node_run.status = "failed"
                node_run.error_message = str(exc)
                node_run.completed_at = datetime.now(timezone.utc)
                await db.commit()
                scheduler.mark_completed(node_key)  # 标记完成（跳过）以推进 DAG

                event = self._wait_events.get(flow_run.id)
                if event:
                    event.set()

            except Exception as exc:
                logger.exception("Unexpected error executing node %s", node_key)
                node_run.status = "failed"
                node_run.error_message = str(exc)[:500]
                node_run.completed_at = datetime.now(timezone.utc)
                await db.commit()
                scheduler.mark_completed(node_key)

                event = self._wait_events.get(flow_run.id)
                if event:
                    event.set()
