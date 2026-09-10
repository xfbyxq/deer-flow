"""看板 REST 路由."""

from fastapi import APIRouter, Query, Request

from app.api.schemas import BoardResponse
from app.services.board_service import BoardService

router = APIRouter(prefix="/board", tags=["board"])


@router.get("", response_model=BoardResponse)
async def get_board(
    request: Request,
    status: str | None = Query(None),
    waker: str | None = Query(None),
    kind: str | None = Query(None),
    group_id: str | None = Query(None, description="按群组过滤"),
    days: int = Query(30, ge=1, le=365, description="日期范围（天）"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """看板聚合查询."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        service = BoardService(session)
        return await service.get_board(
            status=status,
            waker=waker,
            kind=kind,
            group_id=group_id,
            days=days,
            limit=limit,
            offset=offset,
        )
