"""人工确认服务 — approve / reject / 超时升级."""

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import NodeRun

logger = logging.getLogger(__name__)


class ReviewServiceError(Exception):
    """人工确认服务错误."""


class ReviewService:
    """人工确认服务."""

    def __init__(self, flow_engine) -> None:
        """初始化.

        Parameters
        ----------
        flow_engine:
            FlowEngine 实例，用于回调节点完成事件。
        """
        self._flow_engine = flow_engine

    async def approve(
        self,
        flow_run_id: str,
        node_key: str,
        db: AsyncSession,
        comment: str | None = None,
    ) -> NodeRun:
        """通过人工确认.

        Parameters
        ----------
        flow_run_id:
            Flow 运行 ID。
        node_key:
            节点 key。
        db:
            数据库 session。
        comment:
            可选备注。

        Returns
        -------
        NodeRun
            更新后的 NodeRun。

        Raises
        ------
        ReviewServiceError
            节点不存在或状态不正确。
        """
        node_run = await self._find_waiting_review(flow_run_id, node_key, db)

        node_run.status = "completed"
        node_run.completed_at = datetime.now(timezone.utc)
        node_run.outcome_json = json.dumps({"action": "approve", "comment": comment})
        await db.commit()

        # 通知 FlowEngine 推进 DAG
        await self._flow_engine.on_node_completed(
            flow_run_id, node_key, {"action": "approve", "comment": comment}, db
        )

        logger.info("Review approved: flow_run=%s node=%s", flow_run_id, node_key)
        return node_run

    async def reject(
        self,
        flow_run_id: str,
        node_key: str,
        db: AsyncSession,
        comment: str,
    ) -> NodeRun:
        """打回人工确认（comment 必填）.

        Parameters
        ----------
        flow_run_id:
            Flow 运行 ID。
        node_key:
            节点 key。
        db:
            数据库 session。
        comment:
            打回原因（必填）。

        Returns
        -------
        NodeRun
            更新后的 NodeRun。

        Raises
        ------
        ReviewServiceError
            节点不存在、状态不正确或缺少 comment。
        """
        if not comment or not comment.strip():
            raise ReviewServiceError("comment is required for rejection")

        node_run = await self._find_waiting_review(flow_run_id, node_key, db)

        node_run.status = "failed"
        node_run.completed_at = datetime.now(timezone.utc)
        node_run.error_message = f"Rejected: {comment}"
        node_run.outcome_json = json.dumps({"action": "reject", "comment": comment})
        await db.commit()

        # 通知 FlowEngine 节点失败
        await self._flow_engine.on_node_failed(
            flow_run_id, node_key, f"Rejected: {comment}", db
        )

        logger.info("Review rejected: flow_run=%s node=%s", flow_run_id, node_key)
        return node_run

    async def check_timeouts(self, db: AsyncSession) -> list[NodeRun]:
        """检查超时的人工确认节点.

        扫描所有 status=waiting_review 的 NODE_RUN，
        如果 started_at + timeout_hours < now → 标记 escalated。

        Returns
        -------
        list[NodeRun]
            超时节点列表。
        """
        result = await db.execute(
            select(NodeRun).where(NodeRun.status == "waiting_review")
        )
        waiting_nodes = result.scalars().all()
        now = datetime.now(timezone.utc)
        escalated: list[NodeRun] = []

        for node_run in waiting_nodes:
            # 从 input_json 读取 timeout_hours
            timeout_hours = 48  # 默认 48 小时
            if node_run.input_json:
                try:
                    input_data = json.loads(node_run.input_json)
                    timeout_hours = input_data.get("timeout_hours", 48)
                except (json.JSONDecodeError, TypeError):
                    pass

            if node_run.started_at is None:
                continue

            # SQLite 可能返回 naive datetime，统一转为 aware
            started_at = node_run.started_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)

            deadline = started_at + timedelta(hours=timeout_hours)
            if now > deadline:
                node_run.status = "escalated"
                node_run.completed_at = now
                node_run.error_message = f"Review timed out after {timeout_hours}h"
                node_run.outcome_json = json.dumps({"action": "timeout"})
                escalated.append(node_run)

                logger.info(
                    "Review timed out: flow_run=%s node=%s (started=%s, deadline=%s)",
                    node_run.flow_run_id,
                    node_run.node_key,
                    node_run.started_at.isoformat(),
                    deadline.isoformat(),
                )

        if escalated:
            await db.commit()

        return escalated

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    async def _find_waiting_review(
        self, flow_run_id: str, node_key: str, db: AsyncSession
    ) -> NodeRun:
        """查找 waiting_review 状态的 NodeRun.

        Raises
        ------
        ReviewServiceError
            未找到或状态不正确。
        """
        result = await db.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run_id,
                NodeRun.node_key == node_key,
            )
        )
        node_run = result.scalars().first()
        if node_run is None:
            raise ReviewServiceError(
                f"Node run not found: flow_run={flow_run_id}, node={node_key}"
            )
        if node_run.status != "waiting_review":
            raise ReviewServiceError(
                f"Node is not waiting review: status={node_run.status}"
            )
        return node_run
