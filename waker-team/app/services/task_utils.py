"""Task 序列化工具 — 统一 _task_to_dict 单一来源."""

from app.models.task import Task
from app.time_utils import to_iso_utc


def task_to_dict(task: Task) -> dict:
    """将 Task ORM 对象转换为 API 响应 dict.

    缺失字段给默认值，保证向前兼容。
    """
    return {
        "id": task.id,
        "kind": getattr(task, "kind", "") or "",
        "ticket_id": getattr(task, "ticket_id", None) or getattr(task, "id", ""),
        "parent_task_id": getattr(task, "parent_task_id", None),
        "group_id": getattr(task, "group_id", "default") or "default",
        "executor": task.executor,
        "status": task.status,
        "input_text": task.input_text,
        "result_summary": task.result_summary,
        "thread_id": task.thread_id,
        "run_id": task.run_id,
        "idempotency_key": task.idempotency_key,
        "created_by": task.created_by,
        "created_at": to_iso_utc(task.created_at),
        "updated_at": to_iso_utc(task.updated_at),
    }
