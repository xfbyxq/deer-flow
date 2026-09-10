"""Flow 运行管理 REST 路由."""

import json

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.engine.rerun_service import RerunServiceError
from app.models.flow import FlowRun, NodeRun
from app.time_utils import to_iso_utc

router = APIRouter(prefix="/flow-runs", tags=["flow-runs"])


def flow_run_to_response(run: FlowRun) -> dict:
    """将 FlowRun ORM 对象转为响应 dict."""
    output_cache = {}
    if run.output_cache_json:
        try:
            output_cache = json.loads(run.output_cache_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "id": run.id,
        "flow_def_id": run.flow_def_id,
        "group_id": run.group_id,
        "status": run.status,
        "started_at": to_iso_utc(run.started_at),
        "completed_at": to_iso_utc(run.completed_at),
        "current_node_key": run.current_node_key,
        "output_cache": output_cache,
        "created_by": run.created_by,
        "trigger_type": run.trigger_type,
        "paused_at": to_iso_utc(run.paused_at),
        "failure_reason": run.failure_reason,
    }


@router.get("")
async def list_flow_runs(
    request: Request,
    flow_id: str | None = Query(None),
    group_id: str | None = Query(None),
    status: str | None = Query(None),
):
    """列表 Flow 运行（支持筛选）."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        stmt = select(FlowRun).order_by(FlowRun.started_at.desc())
        if flow_id:
            stmt = stmt.where(FlowRun.flow_def_id == flow_id)
        if group_id:
            stmt = stmt.where(FlowRun.group_id == group_id)
        if status:
            stmt = stmt.where(FlowRun.status == status)

        result = await session.execute(stmt)
        runs = result.scalars().all()
        return [flow_run_to_response(r) for r in runs]


@router.get("/{run_id}")
async def get_flow_run(run_id: str, request: Request):
    """获取 Flow 运行详情."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        result = await session.execute(select(FlowRun).where(FlowRun.id == run_id))
        flow_run = result.scalars().first()
        if flow_run is None:
            raise HTTPException(status_code=404, detail="Flow run not found")

        resp = flow_run_to_response(flow_run)

        # 附加 node_runs
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == run_id).order_by(NodeRun.started_at)
        )
        node_runs = []
        for nr in nr_result.scalars().all():
            outcome = {}
            if nr.outcome_json:
                try:
                    outcome = json.loads(nr.outcome_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            node_runs.append({
                "id": nr.id,
                "node_key": nr.node_key,
                "node_type": nr.node_type,
                "status": nr.status,
                "task_id": nr.task_id,
                "started_at": to_iso_utc(nr.started_at),
                "completed_at": to_iso_utc(nr.completed_at),
                "outcome": outcome,
                "error_message": nr.error_message,
                "parent_node_run_id": nr.parent_node_run_id,
            })
        resp["node_runs"] = node_runs
        return resp


@router.post("/{run_id}/pause")
async def pause_flow_run(run_id: str, request: Request):
    """暂停 Flow 运行."""
    flow_engine = getattr(request.app.state, "flow_engine", None)
    if flow_engine is None:
        raise HTTPException(status_code=503, detail="Flow engine not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            flow_run = await flow_engine.pause(run_id, session)
            return flow_run_to_response(flow_run)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{run_id}/resume")
async def resume_flow_run(run_id: str, request: Request):
    """恢复 Flow 运行."""
    flow_engine = getattr(request.app.state, "flow_engine", None)
    if flow_engine is None:
        raise HTTPException(status_code=503, detail="Flow engine not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            flow_run = await flow_engine.resume(run_id, session)
            return flow_run_to_response(flow_run)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{run_id}/cancel")
async def cancel_flow_run(run_id: str, request: Request):
    """取消 Flow 运行."""
    flow_engine = getattr(request.app.state, "flow_engine", None)
    if flow_engine is None:
        raise HTTPException(status_code=503, detail="Flow engine not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            flow_run = await flow_engine.cancel(run_id, session)
            return flow_run_to_response(flow_run)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{run_id}/nodes/{node_key}/rerun", status_code=201)
async def rerun_node(run_id: str, node_key: str, request: Request):
    """单节点重跑."""
    rerun_service = getattr(request.app.state, "rerun_service", None)
    if rerun_service is None:
        raise HTTPException(status_code=503, detail="Rerun service not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            new_node_run = await rerun_service.rerun_node(run_id, node_key, session)
            return {
                "id": new_node_run.id,
                "node_key": new_node_run.node_key,
                "status": new_node_run.status,
                "retry_count": new_node_run.retry_count,
            }
        except RerunServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
