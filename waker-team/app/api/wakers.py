"""Waker 管理 REST 路由."""

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    EnumDataResponse,
    WakerCreateRequest,
    WakerResponse,
    WakerTemplateResponse,
    WakerUpdateRequest,
)
from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    DeerFlowUnavailableError,
    TaskConflictError,
    ValidationError,
)
from app.services.waker_service import WakerService

router = APIRouter(prefix="/wakers", tags=["wakers"])


def _get_service(request: Request) -> WakerService:
    """从 request 构建 WakerService 实例（同步 session factory + deerflow client）."""
    deerflow_client = request.app.state.deerflow
    session_factory = request.app.state.db_session_factory
    # 注意：这里返回的 service 需要在路由中 async 使用，
    # session 通过 async with 管理
    return WakerService.__new__(WakerService), session_factory, deerflow_client


def _map_deerflow_error(exc: Exception) -> None:
    """将 DeerFlow 异常映射为 HTTP 异常并抛出."""
    if isinstance(exc, AgentNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AgentConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, TaskConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, DeerFlowUnavailableError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("", response_model=list[WakerResponse])
async def list_wakers(request: Request):
    """列表所有 Wakers."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.list_wakers()
        except Exception as exc:
            _map_deerflow_error(exc)


@router.post("", response_model=WakerResponse, status_code=201)
async def create_waker(data: WakerCreateRequest, request: Request):
    """创建新 Waker."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.create_waker(data)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.get("/enum", response_model=EnumDataResponse)
async def get_enum_data(request: Request):
    """获取枚举数据源（模型/技能/工具组列表）."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.get_enum_data()
        except Exception as exc:
            _map_deerflow_error(exc)


@router.get("/templates", response_model=list[WakerTemplateResponse])
async def list_waker_templates(request: Request):
    """员工模板列表（新建员工时选择模板快速预填，注意需在 /{name} 之前注册）."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.get_waker_templates()
        except Exception as exc:
            _map_deerflow_error(exc)


@router.get("/{name}", response_model=WakerResponse)
async def get_waker(name: str, request: Request):
    """获取单个 Waker 详情."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.get_waker(name)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.put("/{name}", response_model=WakerResponse)
async def update_waker(name: str, data: WakerUpdateRequest, request: Request):
    """更新 Waker."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.update_waker(name, data)
        except Exception as exc:
            _map_deerflow_error(exc)


@router.patch("/{name}/toggle", response_model=WakerResponse)
async def toggle_waker(name: str, body: dict, request: Request):
    """启停 Waker. body: {"enabled": true/false}."""
    enabled = body.get("enabled")
    if enabled is None:
        raise HTTPException(status_code=422, detail="Missing 'enabled' field in body")
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.toggle_waker(name, bool(enabled))
        except Exception as exc:
            _map_deerflow_error(exc)


@router.delete("/{name}", status_code=204)
async def delete_waker(name: str, request: Request):
    """删除 Waker."""
    _, session_factory, deerflow_client = _get_service(request)
    async with session_factory() as session:
        service = WakerService(session, deerflow_client)
        try:
            return await service.delete_waker(name)
        except Exception as exc:
            _map_deerflow_error(exc)
