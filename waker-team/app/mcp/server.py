"""Waker Team MCP server — HTTP Streamable endpoint for DeerFlow integration.

Uses MCP Python SDK 2.x (MCPServer). Runs as a standalone ASGI app via
``streamable_http_app()`` and can be launched with ``uvicorn app.mcp.server:asgi_app``.

Business logic lives in :mod:`app.mcp.tasks` (``MCPService``) for testability.
Async delegate tools live in :mod:`app.mcp.async_tasks` (``AsyncDelegateMCPService``).
"""

import logging

from mcp.server.mcpserver import MCPServer, Context

from app.config import get_settings
from app.database import Base, create_engine, create_session_factory
from app.deerflow.client import DeerFlowClient
from app.mcp.collab import CollabMCPService
from app.mcp.tasks import MCPService
from app.mcp.async_tasks import AsyncDelegateMCPService
from app.services.async_delegate import AsyncDelegateService
from app.services.delegation_guard import DelegationGuard
from app.services.wake_engine import WakeEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global MCP services (initialised at startup)
# ---------------------------------------------------------------------------
_service: MCPService | None = None
_async_service: AsyncDelegateMCPService | None = None
_collab_service: CollabMCPService | None = None


def _get_service() -> MCPService:
    if _service is None:
        raise RuntimeError("MCPService not initialised")
    return _service


def _get_async_service() -> AsyncDelegateMCPService:
    if _async_service is None:
        raise RuntimeError("AsyncDelegateMCPService not initialised")
    return _async_service


def _get_collab_service() -> CollabMCPService:
    if _collab_service is None:
        raise RuntimeError("CollabMCPService not initialised")
    return _collab_service


# ---------------------------------------------------------------------------
# FastMCP → MCPServer (MCP 2.x)
# ---------------------------------------------------------------------------
settings = get_settings()

mcp = MCPServer(
    name="waker-team",
    log_level="INFO",
)


# ---------------------------------------------------------------------------
# Startup / shutdown helpers
# ---------------------------------------------------------------------------

async def init_service(
    db_url: str | None = None,
    deerflow_url: str | None = None,
) -> None:
    """Create DB engine + DeerFlow client and wire them into *MCPService*.

    Parameters allow tests / scripts to override URLs without touching Settings.
    """
    global _service, _async_service, _collab_service
    s = get_settings()
    engine = create_engine() if db_url is None else None
    if engine is not None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = create_session_factory(engine)
    else:
        # Fallback: use settings directly
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
        engine = create_async_engine(db_url or s.database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    df = DeerFlowClient(deerflow_url or s.deerflow_base_url)
    if s.service_email and s.service_password:
        await df.login(s.service_email, s.service_password)
    # 注意：传入 session_factory（每次调用开短 session），不复用长活 session
    _service = MCPService(session_factory, df)

    # Initialise async delegate service
    wake_engine = WakeEngine(df)
    delegation_guard = DelegationGuard()
    async_delegate_svc = AsyncDelegateService(
        db_session_factory=session_factory,
        deerflow_client=df,
        wake_engine=wake_engine,
        delegation_guard=delegation_guard,
    )
    _async_service = AsyncDelegateMCPService(async_delegate_svc, db_session_factory=session_factory)
    _collab_service = CollabMCPService(session_factory)
    logger.info("MCPService and AsyncDelegateMCPService initialised")


def _extract_caller(ctx: Context) -> str | None:
    """Read ``X-Waker-Caller`` from HTTP headers injected by DeerFlow
    ``headers_from_context`` mechanism."""
    try:
        headers = ctx.headers
        if headers is not None:
            return headers.get("x-waker-caller")
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
async def query_group(group_id: str = "default", ctx: Context = None) -> dict:
    """查询你所在团队（Group）的同事列表和状态。

    同事 = 与你同群组的 enabled 员工（不再返回全体员工）；
    若你属于多个群组，可用 group_id 指定聚焦其中一个。

    Args:
        group_id: 团队 ID，默认 "default"

    Returns:
        包含同组 enabled 员工列表的字典，每个员工有 name, description, enabled 字段。
    """
    caller = _extract_caller(ctx) if ctx else None
    return await _get_service().query_group(group_id, caller=caller)


@mcp.tool()
async def delegate_to_agent(
    group_id: str,
    target_agent: str,
    instruction: str,
    accept_criteria: str = "",
    sync: bool = True,
    sync_timeout: float = 240.0,
    ctx: Context = None,
) -> dict:
    """将任务委派给组内另一个员工执行。

    Args:
        group_id: 团队 ID（必须在同一组）
        target_agent: 目标员工名（必须是 enabled 的 Waker）
        instruction: 委派指令（目标员工的输入）
        accept_criteria: 期望交付标准（可选）
        sync: 是否同步等待结果（默认 True）
        sync_timeout: 同步等待上限（秒，默认 240；子任务较复杂时可调高）

    Returns:
        sync 成功: {"ticket_id": "...", "status": "done", "result": "...", ...}
        sync 超时: {"ticket_id": "...", "status": "running", "message": "后台继续，看板可查"}
    """
    caller = _extract_caller(ctx) if ctx else None
    if not caller:
        return {"error": "Forbidden: missing caller identity (X-Waker-Caller header)"}
    result = await _get_service().delegate_to_agent(
        caller=caller,
        group_id=group_id,
        target_agent=target_agent,
        instruction=instruction,
        accept_criteria=accept_criteria,
        sync=sync,
        sync_timeout=sync_timeout,
    )
    return result


@mcp.tool()
async def delegate_submit(
    target: str,
    instruction: str,
    group_id: str = "default",
    conversation_id: str = "",
    source_task_id: str = "",
    ctx: Context = None,
) -> dict:
    """异步委派任务给指定 Waker，立即返回 ticket_id。

    Args:
        target: 目标 Waker 名
        instruction: 委派指令
        group_id: 团队 ID（默认 "default"）
        conversation_id: 发起会话 ID（群会话场景请传入：成员完成后会以【成员汇报】写回群里，让用户看到成员参与）
        source_task_id: 源任务 ID（用于委派链追踪，可选）

    Returns:
        {"ticket_id": "...", "status": "submitted", "message": "..."}
    """
    caller = _extract_caller(ctx) if ctx else None
    if not caller:
        return {"error": "Forbidden: missing caller identity (X-Waker-Caller header)"}
    return await _get_async_service().delegate_submit(
        caller=caller,
        target=target,
        instruction=instruction,
        group_id=group_id,
        source_task_id=source_task_id or None,
        conversation_id=conversation_id or None,
    )


@mcp.tool()
async def delegate_status(ticket_id: str) -> dict:
    """查询异步委派 ticket 的当前状态。

    Args:
        ticket_id: 委派 ticket ID

    Returns:
        {"ticket_id": "...", "status": "...", "target_waker": "...", ...}
    """
    return await _get_async_service().delegate_status(ticket_id)


@mcp.tool()
async def delegate_cancel(ticket_id: str) -> dict:
    """取消一个待执行或执行中的异步委派。

    Args:
        ticket_id: 委派 ticket ID

    Returns:
        {"ticket_id": "...", "status": "cancelled", ...}
    """
    return await _get_async_service().delegate_cancel(ticket_id)


@mcp.tool()
async def post_group_message(
    conversation_id: str,
    content: str,
    mentions: list[str] | None = None,
    ctx: Context = None,
) -> dict:
    """在群会话中发布一条消息（任务清单、@成员分工、进度说明）。

    在群会话 run 中调用：把任务目标与任务清单写出来，@对应成员并说明交付
    要求，让群内所有人实时看到分工与进展（不依赖消息轮询，发布即入群）。

    Args:
        conversation_id: 目标群会话 ID（协作规程中已给出「当前群会话 ID」）
        content: 消息正文（支持 @成员名，例如「@小忻 出一版数据摘要」）
        mentions: 被 @ 的成员名列表（可选，供前端结构化展示）

    Returns:
        {"ok": true, "message_id": "..."} 或 {"error": "拒绝原因"}
    """
    caller = _extract_caller(ctx) if ctx else None
    if not caller:
        return {"error": "Forbidden: missing caller identity (X-Waker-Caller header)"}
    return await _get_collab_service().post_group_message(
        caller=caller,
        conversation_id=conversation_id,
        content=content,
        mentions=mentions,
    )


# ---------------------------------------------------------------------------
# ASGI application (for ``uvicorn app.mcp.server:asgi_app``)
# ---------------------------------------------------------------------------

_inner_asgi = mcp.streamable_http_app(
    host="0.0.0.0",
    streamable_http_path="/mcp",
)


class _ASGIWithLifespan:
    """Wrap the MCP streamable-HTTP ASGI app with startup/shutdown lifecycle.

    ``streamable_http_app()`` returns a Starlette app whose lifespan runs
    ``session_manager.run()`` (which creates the task group).  We must chain
    our ``init_service()`` call with the session manager's run() so both the
    MCPService and the session manager are properly initialised.
    """

    def __init__(self, inner_asgi) -> None:
        self._inner = inner_asgi
        # Extract the session manager from the inner Starlette app
        # so we can manage its lifecycle directly
        self._session_manager = getattr(inner_asgi, '_session_manager', None)
        if self._session_manager is None:
            # Fallback: try to get it from the lowlevel server
            try:
                self._session_manager = mcp._lowlevel_server._session_manager
            except AttributeError:
                logger.warning("Could not find session_manager; MCP tools may not work")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            import contextlib
            @contextlib.asynccontextmanager
            async def combined_lifespan():
                # Start session manager first (creates task group)
                if self._session_manager is not None:
                    async with self._session_manager.run():
                        # Then init our MCPService
                        await init_service()
                        yield
                else:
                    await init_service()
                    yield
            
            async with combined_lifespan():
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                # Wait for shutdown
                message = await receive()
                if message["type"] == "lifespan.shutdown":
                    try:
                        if _service is not None:
                            await _service.df.close()
                    except Exception:  # noqa: BLE001
                        logger.warning("Error during MCP service shutdown", exc_info=True)
                    await send({"type": "lifespan.shutdown.complete"})
        else:
            await self._inner(scope, receive, send)


asgi_app = _ASGIWithLifespan(_inner_asgi)


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

async def _run() -> None:
    """Initialise service then start the streamable-HTTP server."""
    await init_service()
    s = get_settings()
    try:
        await mcp.run(
            transport="streamable-http",
            host=s.mcp_host,
            port=s.mcp_port,
            streamable_http_path="/mcp",
        )
    finally:
        if _service is not None:
            await _service.df.close()


def main() -> None:
    """Entry-point for ``python -m app.mcp.server``."""
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
