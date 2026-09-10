import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import select, text

from app.api.router import create_api_router
from app.config import get_settings
from app.database import Base, create_engine, create_session_factory
from app.deerflow.client import DeerFlowClient
from app.engine.flow_engine import FlowEngine
from app.engine.recovery import FlowRecovery
from app.engine.rerun_service import RerunService
from app.engine.review_service import ReviewService
from app.models.group import Group
import app.models  # noqa: F401 — ensure all ORM models are registered with Base.metadata before create_all
from app.scheduler.service import SchedulerService
from app.services.async_delegate import AsyncDelegateService
from app.services.chat_reply import ChatReplyService
from app.services.delegation_guard import DelegationGuard
from app.services.sync_engine import SyncEngine
from app.services.task_service import TaskService
from app.services.wake_engine import WakeEngine

logger = logging.getLogger(__name__)


def _run_schema_migrations(sync_conn) -> None:
    """为已有表添加新列（幂等，使用 ALTER TABLE IF NOT EXISTS 语义）.

    SQLAlchemy create_all 只创建不存在的表，不会给已有表加列。
    这里手动处理列迁移，确保向前兼容。
    必须在 run_sync 回调中调用（接收 sync connection）。
    """
    cursor = sync_conn.connection.cursor()

    def _get_tables() -> set[str]:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in cursor.fetchall()}

    def _get_columns(table_name: str) -> set[str]:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cursor.fetchall()}

    tables = _get_tables()

    # wakers 表新列
    if "wakers" in tables:
        waker_cols = _get_columns("wakers")
        if "presence" not in waker_cols:
            cursor.execute("ALTER TABLE wakers ADD COLUMN presence VARCHAR DEFAULT 'offline'")
        if "role" not in waker_cols:
            cursor.execute("ALTER TABLE wakers ADD COLUMN role VARCHAR")
        if "max_concurrent_tasks" not in waker_cols:
            cursor.execute("ALTER TABLE wakers ADD COLUMN max_concurrent_tasks INTEGER DEFAULT 3")
        if "mcp_connectors" not in waker_cols:
            cursor.execute("ALTER TABLE wakers ADD COLUMN mcp_connectors TEXT")

    # groups 表新列
    if "groups" in tables:
        group_cols = _get_columns("groups")
        if "description" not in group_cols:
            cursor.execute("ALTER TABLE groups ADD COLUMN description TEXT")
        if "sop_id" not in group_cols:
            cursor.execute("ALTER TABLE groups ADD COLUMN sop_id VARCHAR")

    # tasks 表新列
    if "tasks" in tables:
        task_cols = _get_columns("tasks")
        if "conversation_id" not in task_cols:
            cursor.execute("ALTER TABLE tasks ADD COLUMN conversation_id VARCHAR")

    sync_conn.commit()
    cursor.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = create_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 运行 schema 迁移（为已有表添加新列），在同一 sync 回调中执行
        await conn.run_sync(_run_schema_migrations)

    # Verify tables were actually created (guards against corrupted WAL state after kill -9)
    async with engine.begin() as conn:
        result = await conn.run_sync(
            lambda c: c.execute(
                text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='wakers'")
            ).scalar()
        )
        if result == 0:
            logger.error("Tables not created on first attempt, retrying...")
            # Force drop and recreate
            async with engine.begin() as retry_conn:
                await retry_conn.run_sync(Base.metadata.create_all)
            # Verify again
            async with engine.begin() as verify_conn:
                count = await verify_conn.run_sync(
                    lambda c: c.execute(
                        text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='wakers'")
                    ).scalar()
                )
                if count == 0:
                    raise RuntimeError("Failed to create database tables after retry")
            logger.info("Tables created on retry")

    app.state.db_engine = engine
    app.state.db_session_factory = create_session_factory(engine)

    # Seed default data (确保 FK 依赖的默认记录存在)
    async with app.state.db_session_factory() as session:
        result = await session.execute(select(Group).where(Group.id == "default"))
        if not result.scalar_one_or_none():
            session.add(Group(id="default", name="默认团队"))
            await session.commit()
            logger.info("Seeded default group")

    # 初始化 DeerFlow 客户端并登录
    deerflow_client = DeerFlowClient(settings.deerflow_base_url)
    if settings.service_email and settings.service_password:
        try:
            await deerflow_client.login(settings.service_email, settings.service_password)
            logger.info("DeerFlow client logged in")
        except Exception:
            logger.warning("DeerFlow login failed — service will start without authenticated DeerFlow session")
    app.state.deerflow = deerflow_client

    # 初始化 Flow 引擎
    def _task_service_factory(session):
        return TaskService(session, deerflow_client)

    flow_engine = FlowEngine(
        db_session_factory=app.state.db_session_factory,
        deerflow_client=deerflow_client,
        task_service_factory=_task_service_factory,
    )
    app.state.flow_engine = flow_engine

    # 初始化人工确认服务与单节点重跑服务
    review_service = ReviewService(flow_engine)
    app.state.review_service = review_service

    rerun_service = RerunService(flow_engine)
    app.state.rerun_service = rerun_service

    # 崩溃恢复：扫描 running 实例，按 DeerFlow Run 状态补记 + 幂等重发
    recovery = FlowRecovery(
        db_session_factory=app.state.db_session_factory,
        flow_engine=flow_engine,
        deerflow_client=deerflow_client,
        task_service_factory=_task_service_factory,
    )
    try:
        recovery_result = await recovery.run()
        if recovery_result["recovered"] > 0 or recovery_result["errors"] > 0:
            logger.info("Flow recovery result: %s", recovery_result)
    except Exception:
        logger.exception("Flow recovery failed during startup")

    # 异步委派完成回调依赖：run 终态后更新 ledger + 向源 agent 投递唤醒
    async_delegate_service = AsyncDelegateService(
        db_session_factory=app.state.db_session_factory,
        deerflow_client=deerflow_client,
        wake_engine=WakeEngine(deerflow_client),
        delegation_guard=DelegationGuard(),
    )
    app.state.async_delegate = async_delegate_service

    # 启动同步引擎（注入 flow_engine 回调 + async delegate 完成回调）
    sync_engine = SyncEngine(
        app.state.db_session_factory,
        deerflow_client,
        flow_engine=flow_engine,
        async_delegate_service=async_delegate_service,
    )
    await sync_engine.start()
    app.state.sync_engine = sync_engine

    # 启动调度服务（注入 task/flow 触发依赖，否则触发只会留 pending 记录）
    scheduler = SchedulerService(
        app.state.db_session_factory,
        flow_engine=flow_engine,
        task_service_factory=_task_service_factory,
    )
    await scheduler.start()
    app.state.scheduler = scheduler

    # 对话回复引擎：用户会话消息 → DeerFlow agent run → 回复写回会话
    chat_reply = ChatReplyService(app.state.db_session_factory, deerflow_client)
    app.state.chat_reply = chat_reply

    logger.info("Waker Team service started")
    yield

    # 停止调度服务
    await scheduler.stop()
    # 停止同步引擎
    await sync_engine.stop()
    await deerflow_client.close()
    await engine.dispose()
    logger.info("Waker Team service stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Waker Team", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_api_router())
    return app


app = create_app()
