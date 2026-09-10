"""5 种节点执行器 — waker_task / leader_plan / human_review / condition / notify."""

import json
import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flow import FlowRun, NodeRun
from app.models.task import Task
from app.models.waker import Waker

logger = logging.getLogger(__name__)


class NodeExecutorError(Exception):
    """节点执行失败."""


class NodeExecutor:
    """节点执行器基类."""

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        """执行节点，返回 outcome dict.

        Parameters
        ----------
        node:
            节点定义 dict（来自 FlowDef.definition_json）。
        flow_run:
            当前 FlowRun ORM 对象。
        node_run:
            当前 NodeRun ORM 对象。
        db:
            数据库 session。
        context:
            执行上下文（包含 task_service, deerflow_client 等）。
        """
        raise NotImplementedError


class WakerTaskExecutor(NodeExecutor):
    """waker_task: 创建 DeerFlow Thread+Run，通过 TaskService 管理."""

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        task_service = context["task_service"]
        waker = node.get("waker", "")
        instruction = node.get("instruction") or f"Execute flow node: {node.get('key', '')}"

        # 通过 TaskService 创建任务（kind=flow_node）
        task_dict = await task_service.create_task(
            executor=waker,
            input_text=instruction,
            group_id=flow_run.group_id or "default",
            created_by=f"flow_engine:{flow_run.id}",
            kind="flow_node",
        )

        # 关联 task_id 到 node_run
        node_run.task_id = task_dict["id"]
        node_run.status = "running"
        node_run.started_at = datetime.now(timezone.utc)
        await db.commit()

        # 返回 outcome（实际完成由 SyncEngine 回调驱动）
        return {"status": "running", "task_id": task_dict["id"]}


class LeaderPlanExecutor(NodeExecutor):
    """leader_plan: 创建 Thread+Run，指令要求返回 JSON 子任务数组."""

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        task_service = context["task_service"]
        waker = node.get("waker", "")
        key = node.get("key", "")

        # 构造 planner 指令：要求返回 JSON 子任务数组
        base_instruction = node.get("instruction") or f"Plan tasks for flow node: {key}"
        plan_instruction = (
            f"{base_instruction}\n\n"
            "IMPORTANT: You MUST respond with a JSON array of subtasks. "
            "Each subtask must have 'waker' and 'instruction' fields. "
            "Format: [{\"waker\": \"name\", \"instruction\": \"task description\"}, ...]"
        )

        task_dict = await task_service.create_task(
            executor=waker,
            input_text=plan_instruction,
            group_id=flow_run.group_id or "default",
            created_by=f"flow_engine:{flow_run.id}",
            kind="flow_node",
        )

        node_run.task_id = task_dict["id"]
        node_run.status = "running"
        node_run.started_at = datetime.now(timezone.utc)
        await db.commit()

        return {"status": "running", "task_id": task_dict["id"]}


class HumanReviewExecutor(NodeExecutor):
    """human_review: 设置 waiting_review 状态，等待人工操作."""

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        node_run.status = "waiting_review"
        node_run.started_at = datetime.now(timezone.utc)
        # 存储审核元数据
        review_meta = {
            "checklist": node.get("checklist", []),
            "timeout_hours": node.get("timeout_hours", 48),
        }
        node_run.input_json = json.dumps(review_meta)
        await db.commit()

        return {"status": "waiting_review"}


class ConditionExecutor(NodeExecutor):
    """condition: 安全表达式求值（不使用 eval）.

    支持格式：``node_key.field == value``
    """

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        expression = node.get("expression", "")
        branches = node.get("branches", {})

        # 安全求值
        result = self._safe_evaluate(expression, context)

        branch_key = "true" if result else "false"
        target_node = branches.get(branch_key)

        node_run.status = "completed"
        node_run.started_at = datetime.now(timezone.utc)
        node_run.completed_at = datetime.now(timezone.utc)
        outcome = {"result": result, "branch": branch_key, "target_node": target_node}
        node_run.outcome_json = json.dumps(outcome)
        await db.commit()

        return outcome

    @staticmethod
    def _safe_evaluate(expression: str, context: dict) -> bool:
        """安全求值简单表达式.

        支持格式：
        - ``node_key.field == value``
        - ``node_key.field != value``
        - ``true`` / ``false`` 字面量
        """
        expression = expression.strip()

        # 字面量
        if expression.lower() == "true":
            return True
        if expression.lower() == "false":
            return False

        # 比较运算符
        for op in ("==", "!="):
            if op in expression:
                parts = expression.split(op, 1)
                if len(parts) == 2:
                    left = parts[0].strip()
                    right = parts[1].strip().strip("'\"")

                    # 从上下文查找值
                    left_value = ConditionExecutor._resolve_value(left, context)

                    if op == "==":
                        return str(left_value) == right
                    else:
                        return str(left_value) != right

        # 默认返回 True
        logger.warning("Could not evaluate expression: %s, defaulting to True", expression)
        return True

    @staticmethod
    def _resolve_value(ref: str, context: dict) -> str:
        """从上下文解析引用值.

        格式：``node_key.field`` 或简单字符串。
        """
        output_cache = context.get("output_cache", {})
        if "." in ref:
            parts = ref.split(".", 1)
            node_key = parts[0]
            field = parts[1]
            node_output = output_cache.get(node_key, {})
            if isinstance(node_output, dict):
                return str(node_output.get(field, ""))
        return ref


class NotifyExecutor(NodeExecutor):
    """notify: 发送通知（v1: 只记录到 audit_log）."""

    async def execute(
        self, node: dict, flow_run: FlowRun, node_run: NodeRun, db: AsyncSession, context: dict
    ) -> dict:
        from app.services.audit_service import log_audit

        channel = node.get("channel", "unknown")
        payload = node.get("payload", {})
        key = node.get("key", "")

        # v1: 记录到审计日志
        await log_audit(
            db,
            action="flow.notify",
            actor=f"flow_engine:{flow_run.id}",
            target=key,
            detail={
                "channel": channel,
                "payload": payload,
                "flow_run_id": flow_run.id,
            },
        )

        node_run.status = "completed"
        node_run.started_at = datetime.now(timezone.utc)
        node_run.completed_at = datetime.now(timezone.utc)
        outcome = {"status": "sent", "channel": channel}
        node_run.outcome_json = json.dumps(outcome)
        await db.commit()

        return outcome


# ------------------------------------------------------------------
# Executor 注册表
# ------------------------------------------------------------------

_EXECUTORS: dict[str, NodeExecutor] = {
    "waker_task": WakerTaskExecutor(),
    "leader_plan": LeaderPlanExecutor(),
    "human_review": HumanReviewExecutor(),
    "condition": ConditionExecutor(),
    "notify": NotifyExecutor(),
}


def get_executor(node_type: str) -> NodeExecutor:
    """获取节点类型对应的执行器.

    Raises
    ------
    NodeExecutorError
        未知节点类型时抛出。
    """
    executor = _EXECUTORS.get(node_type)
    if executor is None:
        raise NodeExecutorError(f"Unknown node type: {node_type}")
    return executor
