from fastapi import APIRouter, Request
from sqlalchemy import text

from app.deerflow.errors import DeerFlowError

router = APIRouter()


@router.get("/health")
async def health_check(request: Request):
    """健康检查：DeerFlow 连通性 + 数据库连通性."""
    status = {"status": "ok", "deerflow": "ok", "db": "ok"}

    # DeerFlow 连通性探测
    deerflow_client = getattr(request.app.state, "deerflow", None)
    if deerflow_client is not None:
        try:
            await deerflow_client.list_agents()
        except DeerFlowError as exc:
            status["deerflow"] = f"error: {exc}"
            status["status"] = "degraded"
        except Exception as exc:  # noqa: BLE001
            status["deerflow"] = f"error: {exc}"
            status["status"] = "degraded"
    else:
        status["deerflow"] = "not_configured"
        status["status"] = "degraded"

    # DB 连通性 + 表完整性探测
    db_session_factory = getattr(request.app.state, "db_session_factory", None)
    if db_session_factory is not None:
        try:
            async with db_session_factory() as session:
                result = await session.execute(
                    text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='wakers'")
                )
                table_count = result.scalar()
                if table_count and table_count > 0:
                    status["db"] = "ok"
                else:
                    status["db"] = "degraded: tables missing"
                    status["status"] = "degraded"
        except Exception as exc:  # noqa: BLE001
            status["db"] = f"error: {exc}"
            status["status"] = "degraded"
    else:
        status["db"] = "not_configured"
        status["status"] = "degraded"

    return status
