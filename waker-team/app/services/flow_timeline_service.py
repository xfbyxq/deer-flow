"""Flow 实例时间线聚合服务 — 将 FLOW_RUN + NODE_RUN + Task 聚合为时间线格式."""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import FlowRun, NodeRun
from app.models.task import Task
from app.time_utils import to_iso_utc

logger = logging.getLogger(__name__)


class FlowTimelineService:
    """Flow 实例时间线聚合服务."""

    async def get_timeline(self, flow_run_id: str, db: AsyncSession) -> dict | None:
        """获取 Flow 运行时间线.

        Parameters
        ----------
        flow_run_id:
            Flow 运行 ID。
        db:
            异步数据库 session。

        Returns
        -------
        dict | None
            时间线数据，Flow 不存在时返回 None。
        """
        # 1. 加载 FLOW_RUN
        result = await db.execute(select(FlowRun).where(FlowRun.id == flow_run_id))
        flow_run = result.scalars().first()
        if flow_run is None:
            return None

        # 2. 加载所有 NODE_RUN（按 started_at 排序，None 排最前）
        nr_result = await db.execute(
            select(NodeRun)
            .where(NodeRun.flow_run_id == flow_run_id)
            .order_by(NodeRun.started_at.nulls_first())
        )
        node_runs = nr_result.scalars().all()

        # 3. 批量加载关联的 Task（如有）
        task_ids = [nr.task_id for nr in node_runs if nr.task_id]
        task_map: dict[str, Task] = {}
        if task_ids:
            t_result = await db.execute(select(Task).where(Task.id.in_(task_ids)))
            for t in t_result.scalars().all():
                task_map[t.id] = t

        # 4. 聚合为时间线格式
        nodes = []
        current_node = None
        for nr in node_runs:
            # 计算 duration
            duration = None
            if nr.started_at and nr.completed_at:
                duration = (nr.completed_at - nr.started_at).total_seconds()

            # 解析 input
            input_data = None
            if nr.input_json:
                try:
                    input_data = json.loads(nr.input_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            # 解析 output（outcome_json）
            output_data = None
            if nr.outcome_json:
                try:
                    output_data = json.loads(nr.outcome_json)
                except (json.JSONDecodeError, TypeError):
                    pass

            # 关联 Task 详情
            task_detail = None
            task = task_map.get(nr.task_id) if nr.task_id else None
            if task:
                task_detail = {
                    "thread_id": task.thread_id,
                    "run_id": task.run_id,
                    "result_summary": task.result_summary,
                }

            # 正在 running 或 waiting_review 的节点为 current_node
            if nr.status in ("running", "waiting_review"):
                current_node = nr.node_key

            nodes.append({
                "node_key": nr.node_key,
                "type": nr.node_type,
                "status": nr.status,
                "started_at": to_iso_utc(nr.started_at),
                "completed_at": to_iso_utc(nr.completed_at),
                "duration_seconds": duration,
                "input": input_data,
                "output": output_data,
                "error_message": nr.error_message,
                "retry_count": nr.retry_count,
                "task": task_detail,
            })

        # 5. 计算总耗时
        total_duration = None
        if flow_run.started_at and flow_run.completed_at:
            total_duration = (flow_run.completed_at - flow_run.started_at).total_seconds()

        # 6. 构建 flow_run 摘要
        flow_run_dict = {
            "id": flow_run.id,
            "flow_def_id": flow_run.flow_def_id,
            "group_id": flow_run.group_id,
            "status": flow_run.status,
            "started_at": to_iso_utc(flow_run.started_at),
            "completed_at": to_iso_utc(flow_run.completed_at),
            "created_by": flow_run.created_by,
            "trigger_type": flow_run.trigger_type,
            "failure_reason": flow_run.failure_reason,
        }

        return {
            "flow_run": flow_run_dict,
            "nodes": nodes,
            "total_duration_seconds": total_duration,
            "current_node": current_node,
        }
