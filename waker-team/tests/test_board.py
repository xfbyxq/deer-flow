"""看板 API 测试."""

import pytest
from datetime import UTC, datetime
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock

from app.database import Base
from app.main import create_app
from app.models.task import Task


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
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    """构建测试 FastAPI app."""
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    # Mock sync engine
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


async def _create_tasks(session_factory, tasks: list[dict]):
    """辅助函数：批量创建 Tasks."""
    async with session_factory() as session:
        for t in tasks:
            session.add(Task(
                id=t["id"],
                kind=t.get("kind", "manual"),
                group_id=t.get("group_id", "default"),
                executor=t["executor"],
                status=t["status"],
                input_text=t.get("input_text", "test"),
                created_by=t.get("created_by", "system"),
                created_at=t.get("created_at", datetime.now(UTC)),
                updated_at=t.get("updated_at", datetime.now(UTC)),
            ))
        await session.commit()


# ------------------------------------------------------------------
# 看板聚合查询
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_counts(client, test_session_factory):
    """查 counts 聚合."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "pending"},
        {"id": "t2", "executor": "alice", "status": "running"},
        {"id": "t3", "executor": "bob", "status": "done"},
        {"id": "t4", "executor": "bob", "status": "failed"},
        {"id": "t5", "executor": "alice", "status": "cancelled"},
    ])

    resp = await client.get("/api/board")
    assert resp.status_code == 200
    body = resp.json()

    assert "counts" in body
    assert body["counts"]["pending"] == 1
    assert body["counts"]["running"] == 1
    assert body["counts"]["done"] == 1
    assert body["counts"]["failed"] == 1
    assert body["counts"]["cancelled"] == 1
    assert body["total"] == 5
    assert len(body["items"]) == 5


@pytest.mark.asyncio
async def test_board_filter_by_status(client, test_session_factory):
    """按 status 筛选."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "pending"},
        {"id": "t2", "executor": "bob", "status": "running"},
        {"id": "t3", "executor": "alice", "status": "pending"},
    ])

    resp = await client.get("/api/board?status=pending")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    assert all(item["status"] == "pending" for item in body["items"])


@pytest.mark.asyncio
async def test_board_filter_by_waker(client, test_session_factory):
    """按 waker 筛选."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "running"},
        {"id": "t2", "executor": "bob", "status": "running"},
        {"id": "t3", "executor": "alice", "status": "done"},
    ])

    resp = await client.get("/api/board?waker=alice")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    assert all(item["executor"] == "alice" for item in body["items"])


@pytest.mark.asyncio
async def test_board_filter_by_kind(client, test_session_factory):
    """按 kind 筛选."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "running", "kind": "manual"},
        {"id": "t2", "executor": "bob", "status": "running", "kind": "delegate"},
        {"id": "t3", "executor": "alice", "status": "done", "kind": "manual"},
    ])

    resp = await client.get("/api/board?kind=manual")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 2
    assert all(item["kind"] == "manual" for item in body["items"])


@pytest.mark.asyncio
async def test_board_pagination(client, test_session_factory):
    """分页 limit/offset."""
    tasks = [{"id": f"t{i}", "executor": "alice", "status": "running"} for i in range(10)]
    await _create_tasks(test_session_factory, tasks)

    # 第一页
    resp = await client.get("/api/board?limit=3&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["total"] == 10

    # 第二页
    resp = await client.get("/api/board?limit=3&offset=3")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3

    # 最后一页
    resp = await client.get("/api/board?limit=3&offset=9")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1


@pytest.mark.asyncio
async def test_board_empty(client, test_session_factory):
    """空看板."""
    resp = await client.get("/api/board")
    assert resp.status_code == 200
    body = resp.json()

    assert body["counts"]["pending"] == 0
    assert body["counts"]["running"] == 0
    assert body["counts"]["done"] == 0
    assert body["counts"]["failed"] == 0
    assert body["counts"]["cancelled"] == 0
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_board_combined_filters(client, test_session_factory):
    """组合筛选条件."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "running", "kind": "manual"},
        {"id": "t2", "executor": "alice", "status": "running", "kind": "delegate"},
        {"id": "t3", "executor": "bob", "status": "running", "kind": "manual"},
        {"id": "t4", "executor": "alice", "status": "done", "kind": "manual"},
    ])

    resp = await client.get("/api/board?waker=alice&status=running&kind=manual")
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_board_counts_not_affected_by_filters(client, test_session_factory):
    """counts 不受筛选条件影响（全局统计）."""
    await _create_tasks(test_session_factory, [
        {"id": "t1", "executor": "alice", "status": "pending"},
        {"id": "t2", "executor": "bob", "status": "running"},
        {"id": "t3", "executor": "alice", "status": "done"},
    ])

    resp = await client.get("/api/board?status=pending")
    assert resp.status_code == 200
    body = resp.json()

    # items 只包含 pending
    assert body["total"] == 1
    # counts 是全局的
    assert body["counts"]["pending"] == 1
    assert body["counts"]["running"] == 1
    assert body["counts"]["done"] == 1
