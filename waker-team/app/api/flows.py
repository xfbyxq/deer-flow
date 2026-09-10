"""Flow 定义 REST 路由."""

import json

from fastapi import APIRouter, HTTPException, Request

from app.api.flow_runs import flow_run_to_response
from app.api.schemas import (
    FlowDefCreate,
    FlowDefResponse,
    FlowDefUpdate,
)
from app.services.flow_def_service import FlowDefService, FlowValidationError
from app.time_utils import to_iso_utc

router = APIRouter(prefix="/flows", tags=["flows"])


def _flow_to_response(flow) -> dict:
    """将 FlowDef ORM 对象转为响应 dict."""
    def_json_raw = flow.definition_json
    if isinstance(def_json_raw, str):
        try:
            def_json = json.loads(def_json_raw)
        except (json.JSONDecodeError, TypeError):
            def_json = {}
    elif isinstance(def_json_raw, dict):
        def_json = def_json_raw
    else:
        def_json = {}

    return {
        "id": flow.id,
        "name": flow.name,
        "group_id": flow.group_id,
        "description": flow.description,
        "version": flow.version,
        "definition_json": def_json,
        "status": flow.status,
        "created_at": to_iso_utc(flow.created_at),
        "updated_at": to_iso_utc(flow.updated_at),
        "created_by": flow.created_by,
    }


@router.get("", response_model=list[FlowDefResponse])
async def list_flows(request: Request, group_id: str | None = None):
    """列表 Flow 定义（可选 ?group_id=xxx）."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = FlowDefService(session)
        flows = await service.list(group_id=group_id)
        return [_flow_to_response(f) for f in flows]


@router.post("", response_model=FlowDefResponse, status_code=201)
async def create_flow(data: FlowDefCreate, request: Request):
    """创建 Flow 定义."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = FlowDefService(session)
        try:
            flow = await service.create(data.model_dump())
            return _flow_to_response(flow)
        except FlowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{flow_id}", response_model=FlowDefResponse)
async def get_flow(flow_id: str, request: Request):
    """获取 Flow 定义详情."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = FlowDefService(session)
        flow = await service.get(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="Flow definition not found")
        return _flow_to_response(flow)


@router.put("/{flow_id}", response_model=FlowDefResponse)
async def update_flow(flow_id: str, data: FlowDefUpdate, request: Request):
    """更新 Flow 定义."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = FlowDefService(session)
        try:
            flow = await service.update(flow_id, data.model_dump(exclude_unset=True))
            if flow is None:
                raise HTTPException(status_code=404, detail="Flow definition not found")
            return _flow_to_response(flow)
        except FlowValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{flow_id}/run", status_code=201)
async def start_flow_run(flow_id: str, request: Request):
    """启动 Flow 运行（创建 FlowRun 并调度首个节点）."""
    flow_engine = getattr(request.app.state, "flow_engine", None)
    if flow_engine is None:
        raise HTTPException(status_code=503, detail="Flow engine not initialized")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        try:
            flow_run = await flow_engine.start(
                flow_def_id=flow_id,
                db=session,
                created_by="manual",
                trigger_type="manual",
            )
            return flow_run_to_response(flow_run)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{flow_id}", status_code=204)
async def delete_flow(flow_id: str, request: Request):
    """删除 Flow 定义."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = FlowDefService(session)
        flow = await service.get(flow_id)
        if flow is None:
            raise HTTPException(status_code=404, detail="Flow definition not found")
        await service.delete(flow_id)
        return None
