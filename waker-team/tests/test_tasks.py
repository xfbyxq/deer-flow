"""任务 API 测试（mock DeerFlowClient）."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock

from app.database import Base
from app.main import create_app
from app.models import AuditLog, Task, Waker


@pytest.fixture
async def test_db_engine():
    """创建测试用内存数据库."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def test_session_factory(test_db_engine):
    return async_sessionmaker(test_db_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def mock_deerflow():
    """构建 mock DeerFlowClient."""
    client = MagicMock()
    client.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    client.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    client.get_run = AsyncMock(return_value={"status": "running"})
    client.cancel_run = AsyncMock(return_value=None)
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    """构建测试 FastAPI app."""
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    # Mock sync engine to avoid background tasks in tests
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync
    return application


@pytest.fixture
async def client(app):
    """构建测试 HTTP 客户端."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_waker(session_factory, name: str, enabled: bool = True):
    """辅助函数：创建 Waker."""
    async with session_factory() as session:
        session.add(Waker(name=name, deer_user="", description=f"Test {name}", soul_summary="...", enabled=enabled))
        await session.commit()


# ------------------------------------------------------------------
# 创建任务
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task(client, mock_deerflow, test_session_factory):
    """创建任务: mock create_thread + create_run → 断言 TASK 建表 + status=running."""
    await _create_waker(test_session_factory, "alice")

    resp = await client.post(
        "/api/tasks",
        json={"executor": "alice", "input_text": "完成数据分析报告"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["executor"] == "alice"
    assert body["status"] == "running"
    assert body["kind"] == "manual"
    assert body["thread_id"] is not None
    assert body["run_id"] is not None
    assert body["input_text"] == "完成数据分析报告"

    # 验证 DeerFlow 调用
    mock_deerflow.create_thread.assert_called_once()
    mock_deerflow.create_run.assert_called_once()

    # 验证 TASK 表
    async with test_session_factory() as session:
        result = await session.execute(select(Task))
        task = result.scalars().first()
        assert task is not None
        assert task.status == "running"
        assert task.executor == "alice"

    # 验证审计日志
    async with test_session_factory() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.action == "task.create"))
        audit = result.scalars().first()
        assert audit is not None


@pytest.mark.asyncio
async def test_create_task_executor_not_found(client, test_session_factory):
    """执行者不存在: → 404."""
    resp = await client.post(
        "/api/tasks",
        json={"executor": "nonexistent", "input_text": "test"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_task_executor_disabled(client, test_session_factory):
    """执行者停用: → 409."""
    await _create_waker(test_session_factory, "bob", enabled=False)

    resp = await client.post(
        "/api/tasks",
        json={"executor": "bob", "input_text": "test"},
    )
    assert resp.status_code == 409


# ------------------------------------------------------------------
# 获取任务
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_task(client, test_session_factory):
    """获取任务详情."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-123", kind="manual", executor="alice", status="running",
            input_text="test task", created_by="system",
        ))
        await session.commit()

    resp = await client.get("/api/tasks/task-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "task-123"
    assert body["status"] == "running"


@pytest.mark.asyncio
async def test_get_task_after_create_via_api(client, mock_deerflow, test_session_factory):
    """Regression: create task via POST then GET by returned UUID id → 200.

    This reproduces the production scenario where a task is created through
    the API (which assigns a UUID id) and then fetched by that id.
    """
    await _create_waker(test_session_factory, "alice")

    # Create task via API (generates UUID id)
    create_resp = await client.post(
        "/api/tasks",
        json={"executor": "alice", "input_text": "test regression"},
    )
    assert create_resp.status_code == 201
    task_id = create_resp.json()["id"]
    assert len(task_id) > 10  # UUID string

    # Fetch by returned id — should succeed (was returning 404 before fix)
    get_resp = await client.get(f"/api/tasks/{task_id}")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["id"] == task_id
    assert body["executor"] == "alice"
    assert body["input_text"] == "test regression"


@pytest.mark.asyncio
async def test_get_task_with_uuid_id(client, test_session_factory):
    """Regression: task with UUID-format id can be fetched by id."""
    import uuid
    task_id = str(uuid.uuid4())
    async with test_session_factory() as session:
        session.add(Task(
            id=task_id, kind="delegate", executor="bob", status="done",
            input_text="uuid task", created_by="system",
        ))
        await session.commit()

    resp = await client.get(f"/api/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id
    assert resp.json()["status"] == "done"


@pytest.mark.asyncio
async def test_get_task_not_found(client):
    """任务不存在: → 404."""
    resp = await client.get("/api/tasks/nonexistent")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 重试任务
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_task(client, mock_deerflow, test_session_factory):
    """重试: mock failed task → 新 run → status=running."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-failed", kind="manual", executor="alice", status="failed",
            input_text="failed task", result_summary="error occurred", created_by="system",
        ))
        await session.commit()

    mock_deerflow.create_run.return_value = {"run_id": "new-run-id"}

    resp = await client.post("/api/tasks/task-failed/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["run_id"] == "new-run-id"
    assert body["result_summary"] is None  # 重试后清除

    # 验证 DeerFlow 调用
    mock_deerflow.create_thread.assert_called_once()
    mock_deerflow.create_run.assert_called_once()


@pytest.mark.asyncio
async def test_retry_task_not_failed(client, test_session_factory):
    """重试非 failed: → 409."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-running", kind="manual", executor="alice", status="running",
            input_text="running task", created_by="system",
        ))
        await session.commit()

    resp = await client.post("/api/tasks/task-running/retry")
    assert resp.status_code == 409


# ------------------------------------------------------------------
# 取消任务
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_task(client, mock_deerflow, test_session_factory):
    """取消: mock cancel_run → status=cancelled."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-cancel", kind="manual", executor="alice", status="running",
            input_text="cancel me", thread_id="thread-1", run_id="run-1", created_by="system",
        ))
        await session.commit()

    resp = await client.post("/api/tasks/task-cancel/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"

    # 验证 DeerFlow 调用
    mock_deerflow.cancel_run.assert_called_once_with("thread-1", "run-1", wait=True)


@pytest.mark.asyncio
async def test_cancel_task_not_running(client, test_session_factory):
    """取消非 running/pending: → 409."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-done", kind="manual", executor="alice", status="done",
            input_text="done task", created_by="system",
        ))
        await session.commit()

    resp = await client.post("/api/tasks/task-done/cancel")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_cancel_pending_task(client, mock_deerflow, test_session_factory):
    """取消 pending 状态的任务: → status=cancelled."""
    async with test_session_factory() as session:
        session.add(Task(
            id="task-pending", kind="manual", executor="alice", status="pending",
            input_text="pending task", created_by="system",
        ))
        await session.commit()

    resp = await client.post("/api/tasks/task-pending/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
