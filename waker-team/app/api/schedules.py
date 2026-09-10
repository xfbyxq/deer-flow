"""调度管理 REST 路由."""

import json

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    ScheduleCreate,
    ScheduleResponse,
    ScheduleRunResponse,
    ScheduleUpdate,
)
from app.scheduler.cron_utils import validate_cron
from app.scheduler.service import SchedulerService
from app.time_utils import to_iso_utc

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _schedule_to_response(sched) -> dict:
    """将 ScheduleDef ORM 对象转为响应 dict."""
    target_input = None
    if sched.target_input:
        try:
            target_input = json.loads(sched.target_input)
        except (json.JSONDecodeError, TypeError):
            target_input = None

    return {
        "id": sched.id,
        "name": sched.name,
        "group_id": sched.group_id,
        "description": sched.description,
        "cron_expression": sched.cron_expression,
        "target_type": sched.target_type,
        "target_id": sched.target_id,
        "target_input": target_input,
        "status": sched.status,
        "max_run_count": sched.max_run_count,
        "end_date": to_iso_utc(sched.end_date),
        "last_run_at": to_iso_utc(sched.last_run_at),
        "next_run_at": to_iso_utc(sched.next_run_at),
        "run_count": sched.run_count,
        "executor": sched.target_id if sched.target_type == "task" else None,
        "created_at": to_iso_utc(sched.created_at),
        "updated_at": to_iso_utc(sched.updated_at),
    }


def _run_to_response(run) -> dict:
    """将 ScheduleRun ORM 对象转为响应 dict."""
    return {
        "id": run.id,
        "schedule_def_id": run.schedule_def_id,
        "trigger_type": run.trigger_type,
        "triggered_at": to_iso_utc(run.triggered_at),
        "status": run.status,
        "flow_run_id": run.flow_run_id,
        "task_id": run.task_id,
        "error_message": run.error_message,
        "completed_at": to_iso_utc(run.completed_at),
    }


def _get_scheduler(request: Request) -> SchedulerService:
    """从 app.state 获取 SchedulerService."""
    return request.app.state.scheduler


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(request: Request, group_id: str | None = None):
    """列表调度定义（可选 ?group_id=xxx）."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        schedules = await scheduler.list(session, group_id=group_id)
        return [_schedule_to_response(s) for s in schedules]


@router.post("", response_model=ScheduleResponse, status_code=201)
async def create_schedule(data: ScheduleCreate, request: Request):
    """创建调度定义."""
    # 校验 cron 表达式
    if not validate_cron(data.cron_expression):
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {data.cron_expression}")

    # 校验 target_type
    if data.target_type not in ("flow", "task"):
        raise HTTPException(status_code=422, detail="target_type must be 'flow' or 'task'")

    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        sched = await scheduler.create(session, data.model_dump())
        return _schedule_to_response(sched)


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: str, request: Request):
    """获取调度定义详情."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        sched = await scheduler.get(session, schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _schedule_to_response(sched)


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(schedule_id: str, data: ScheduleUpdate, request: Request):
    """更新调度定义."""
    # 如果更新 cron，先校验
    if data.cron_expression is not None and not validate_cron(data.cron_expression):
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {data.cron_expression}")

    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        sched = await scheduler.update(session, schedule_id, data.model_dump(exclude_unset=True))
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _schedule_to_response(sched)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, request: Request):
    """删除调度定义."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        deleted = await scheduler.delete(session, schedule_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return None


@router.post("/{schedule_id}/pause", response_model=ScheduleResponse)
async def pause_schedule(schedule_id: str, request: Request):
    """暂停调度."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        sched = await scheduler.pause(session, schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _schedule_to_response(sched)


@router.post("/{schedule_id}/resume", response_model=ScheduleResponse)
async def resume_schedule(schedule_id: str, request: Request):
    """恢复调度."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        sched = await scheduler.resume(session, schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return _schedule_to_response(sched)


@router.post("/{schedule_id}/trigger", response_model=ScheduleRunResponse, status_code=201)
async def manual_trigger(schedule_id: str, request: Request):
    """手动触发一次调度."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        try:
            run = await scheduler.manual_trigger(schedule_id, session)
            await session.commit()
            return _run_to_response(run)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{schedule_id}/runs", response_model=list[ScheduleRunResponse])
async def list_runs(schedule_id: str, request: Request):
    """列出执行历史."""
    scheduler = _get_scheduler(request)
    async with request.app.state.db_session_factory() as session:
        # 先检查调度是否存在
        sched = await scheduler.get(session, schedule_id)
        if sched is None:
            raise HTTPException(status_code=404, detail="Schedule not found")
        runs = await scheduler.list_runs(session, schedule_id)
        return [_run_to_response(r) for r in runs]
