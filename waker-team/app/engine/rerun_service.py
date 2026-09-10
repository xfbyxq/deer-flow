"""单节点重跑服务 — 重新执行已完成/失败的节点."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import NodeRun

logger = logging.getLogger(__name__)


class RerunServiceError(Exception):
    """单节点重跑服务错误."""


class RerunService:
    """单节点重跑服务."""

    def __init__(self, flow_engine) -> None:
        """初始化.

        Parameters
        ----------
        flow_engine:
            FlowEngine 实例，用于回调节点执行事件。
        """
        self._flow_engine = flow_engine

    async def rerun_node(
        self,
        flow_run_id: str,
        node_key: str,
        db: AsyncSession,
    ) -> NodeRun:
        """重跑指定节点.

        原 NODE_RUN 保留（归档，不改状态）。
        新建 NODE_RUN（同 node_key，新 id，status=pending，retry_count=原+1）。

        Parameters
        ----------
        flow_run_id:
            Flow 运行 ID。
        node_key:
            节点 key。
        db:
            数据库 session。

        Returns
        -------
        NodeRun
            新创建的 NodeRun。

        Raises
        ------
        RerunServiceError
            节点不存在或状态不允许重跑。
        """
        # 查找最新的同 node_key 的 NodeRun（按 retry_count 降序）
        result = await db.execute(
            select(NodeRun)
            .where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
            )
            .order_by(NodeRun.retry_count.desc())
        )
        latest_node_run = result.scalars().first()
        if latest_node_run is None:
            raise RerunServiceError(
                f"Node run not found: flow_run={flow_run_id}, node={node_key}"
            )

        # 只有 done(completed)/failed 可重跑
        if latest_node_run.status not in ("completed", "failed"):
            raise RerunServiceError(
                f"Cannot rerun node in status '{latest_node_run.status}'; "
                "only completed/failed nodes can be rerun"
            )

        # 新建 NODE_RUN
        new_node_run = NodeRun(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run_id,
            node_id=latest_node_run.node_id,
            node_key=node_key,
            node_type=latest_node_run.node_type,
            status="pending",
            input_json=latest_node_run.input_json,
            retry_count=latest_node_run.retry_count + 1,
            parent_node_run_id=latest_node_run.parent_node_run_id,
        )
        db.add(new_node_run)
        await db.commit()
        await db.refresh(new_node_run)

        # 通知 FlowEngine 重新执行该节点
        await self._flow_engine.on_rerun_requested(flow_run_id, node_key, new_node_run, db)

        logger.info(
            "Node rerun requested: flow_run=%s node=%s (retry_count=%d, new_id=%s)",
            flow_run_id,
            node_key,
            new_node_run.retry_count,
            new_node_run.id,
        )
        return new_node_run

    async def cancel_running_node(
        self,
        flow_run_id: str,
        node_key: str,
        db: AsyncSession,
    ) -> NodeRun:
        """取消正在运行的节点（重跑前需先取消）.

        Parameters
        ----------
        flow_run_id:
            Flow 运行 ID。
        node_key:
            节点 key。
        db:
            数据库 session。

        Returns
        -------
        NodeRun
            更新后的 NodeRun。

        Raises
        ------
        RerunServiceError
            节点不存在或状态不是 running。
        """
        result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
                NodeRun.status == "running",
            )
        )
        node_run = result.scalars().first()
        if node_run is None:
            raise RerunServiceError(
                f"Running node not found: flow_run={flow_run_id}, node={node_key}"
            )

        node_run.status = "cancelled"
        node_run.completed_at = datetime.now(timezone.utc)
        node_run.error_message = "Cancelled for rerun"
        await db.commit()

        logger.info(
            "Running node cancelled for rerun: flow_run=%s node=%s",
            flow_run_id,
            node_key,
        )
        return node_run
