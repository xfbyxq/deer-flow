"""任务 REST 路由."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from app.api.schemas import TaskCreateRequest, TaskListResponse, TaskResponse
from app.deerflow.errors import AgentNotFoundError, TaskConflictError, TaskNotFoundError
from app.models.task import Task
from app.services.run_progress import extract_progress, latest_visible_output
from app.services.task_service import TaskService
from app.services.task_utils import task_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _map_deerflow_error(exc: Exception) -> None:
    """将 DeerFlow 异常映射为 HTTP 异常并抛出."""
    # FastAPI HTTPException should propagate as-is
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, TaskNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, TaskConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    request: Request,
    executor: str | None = Query(None, description="按执行者过滤"),
    group_id: str | None = Query(None, description="按群组过滤"),
    thread_id: str | None = Query(None, description="按 thread 过滤"),
    kind: str | None = Query(None, description="按类型过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """任务列表查询."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        base_query = select(Task)
        count_query = select(func.count()).select_from(Task)

        if executor:
            base_query = base_query.where(Task.executor == executor)
            count_query = count_query.where(Task.executor == executor)
        if group_id:
            base_query = base_query.where(Task.group_id == group_id)
            count_query = count_query.where(Task.group_id == group_id)
        if thread_id:
            base_query = base_query.where(Task.thread_id == thread_id)
            count_query = count_query.where(Task.thread_id == thread_id)
        if kind:
            base_query = base_query.where(Task.kind == kind)
            count_query = count_query.where(Task.kind == kind)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        items_query = base_query.order_by(Task.created_at.desc()).offset(offset).limit(limit)
        items_result = await session.execute(items_query)
        items = [task_to_dict(t) for t in items_result.scalars().all()]

        return {"items": items, "total": total}


@router.post("", response_model=TaskResponse, status_code=201)
async def create_task(body: TaskCreateRequest, request: Request):
    """创建任务."""
    session_factory = request.app.state.db_session_factory
    deerflow_client = request.app.state.deerflow
    async with session_factory() as session:
        service = TaskService(session, deerflow_client)
        try:
            return await service.create_task(
                executor=body.executor,
                input_text=body.input_text,
                group_id=body.group_id,
            )
        except Exception as exc:
            _map_deerflow_error(exc)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, request: Request):
    """获取任务详情."""
    session_factory = request.app.state.db_session_factory
    deerflow_client = request.app.state.deerflow
    async with session_factory() as session:
        service = TaskService(session, deerflow_client)
        try:
            return await service.get_task(task_id)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.post("/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str, request: Request):
    """重试任务."""
    session_factory = request.app.state.db_session_factory
    deerflow_client = request.app.state.deerflow
    async with session_factory() as session:
        service = TaskService(session, deerflow_client)
        try:
            return await service.retry_task(task_id)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(task_id: str, request: Request):
    """取消任务."""
    session_factory = request.app.state.db_session_factory
    deerflow_client = request.app.state.deerflow
    async with session_factory() as session:
        service = TaskService(session, deerflow_client)
        try:
            return await service.cancel_task(task_id)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.get("/{task_id}/progress")
async def get_task_progress(task_id: str, request: Request):
    """成员任务进度快照（群协作运行状态条的详情抽屉数据源）.

    - 进行中任务：从 DeerFlow thread state 提取步骤/当前动作/最近输出；
    - 排队中（pending，尚无 thread）：返回 starting 占位；
    - 终态任务：active=False + result_summary。
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        task = (
            await session.execute(select(Task).where(Task.id == task_id))
        ).scalars().first()
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    base = {
        "task_id": task.id,
        "executor": task.executor,
        "status": task.status,
        "instruction": task.input_text,
        "active": False,
    }

    # 终态任务：只给结果摘要
    if task.status not in ("pending", "running"):
        base["result_summary"] = task.result_summary
        return base

    created = task.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    elapsed = int((datetime.now(UTC) - created).total_seconds()) if created else 0

    # 排队中（尚无 run）：占位进度
    if not task.thread_id or not task.run_id:
        return {
            **base,
            "active": True,
            "elapsed": elapsed,
            "steps": [],
            "current": {"kind": "starting", "detail": "排队等待执行…"},
        }

    deerflow_client = request.app.state.deerflow
    try:
        state = await deerflow_client.get_thread_state(task.thread_id)
    except Exception:
        logger.warning(
            "task progress: get_thread_state failed (task=%s thread=%s)",
            task.id,
            task.thread_id,
            exc_info=True,
        )
        return {
            **base,
            "active": True,
            "run_id": task.run_id,
            "elapsed": elapsed,
            "steps": [],
            "current": {"kind": "unknown", "detail": "正在执行…"},
        }

    steps, current = extract_progress(state, task.run_id)
    return {
        **base,
        "active": True,
        "run_id": task.run_id,
        "elapsed": elapsed,
        "steps": steps,
        "current": current,
        "latest_output": latest_visible_output(state),
    }
