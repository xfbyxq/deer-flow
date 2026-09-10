"""群内活动聚合（GET /groups/{id}/activity）与任务进度（GET /tasks/{id}/progress）测试."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models import Task
from app.models.group import Group


@pytest.fixture
async def test_db_engine():
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
    client = MagicMock()
    client.get_thread_state = AsyncMock(return_value={"values": {"messages": []}})
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_group(session_factory, group_id: str = "g-1"):
    async with session_factory() as db:
        db.add(Group(id=group_id, name=f"群 {group_id}", leader_waker_id="leader-lu"))
        await db.commit()


async def _seed_task(session_factory, task_id: str, **kwargs):
    defaults = dict(
        kind="delegate",
        group_id="g-1",
        executor="member-li",
        status="running",
        input_text="查资料 A",
        created_by="leader-lu",
        created_at=datetime.now(UTC) - timedelta(seconds=30),
        updated_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    async with session_factory() as db:
        db.add(Task(id=task_id, **defaults))
        await db.commit()


# ------------------------------------------------------------------
# Group activity
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_activity_lists_running_member_tasks(client, test_session_factory):
    """成员任务：running → running，pending → queued；manual 人工派活也纳入."""
    await _seed_group(test_session_factory)
    await _seed_task(test_session_factory, "t-1", status="running", kind="manual")
    await _seed_task(test_session_factory, "t-2", status="pending", executor="member-x")

    resp = await client.get("/api/groups/g-1/activity")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    items = body["items"]
    assert len(items) == 2
    assert items[0]["waker"] == "member-li"
    assert items[0]["kind"] == "member_task"
    assert items[0]["status"] == "running"
    assert items[0]["task_id"] == "t-1"
    assert items[0]["title"] == "查资料 A"
    assert items[0]["elapsed"] >= 29
    assert items[1]["waker"] == "member-x"
    assert items[1]["status"] == "queued"


@pytest.mark.asyncio
async def test_group_activity_excludes_terminal_tasks(client, test_session_factory):
    """终态任务（done/failed/cancelled）不计入活动."""
    await _seed_group(test_session_factory)
    await _seed_task(test_session_factory, "t-done", status="done")
    await _seed_task(test_session_factory, "t-failed", status="failed")

    resp = await client.get("/api/groups/g-1/activity")

    body = resp.json()
    assert body["active"] is False
    assert body["items"] == []


@pytest.mark.asyncio
async def test_group_activity_includes_leader_run(app, client, test_session_factory):
    """Leader 进行中回复 run 一并返回（内存态快照）."""
    await _seed_group(test_session_factory)
    mock_chat = MagicMock()
    mock_chat.list_active_runs = AsyncMock(
        return_value=[
            {
                "conversation_id": "conv-1",
                "conversation_title": "多 Agent 设计",
                "target": "leader-lu",
                "run_id": "run-1",
                "elapsed": 42,
            }
        ]
    )
    app.state.chat_reply = mock_chat

    resp = await client.get("/api/groups/g-1/activity")

    body = resp.json()
    assert body["active"] is True
    assert body["items"] == [
        {
            "waker": "leader-lu",
            "kind": "leader_run",
            "status": "running",
            "conversation_id": "conv-1",
            "title": "多 Agent 设计",
            "elapsed": 42,
        }
    ]


@pytest.mark.asyncio
async def test_group_activity_group_not_found(client, test_session_factory):
    """群不存在 → 404."""
    resp = await client.get("/api/groups/g-nope/activity")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Task progress
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_progress_running_task(client, test_session_factory, mock_deerflow):
    """进行中任务：从 thread state 提取步骤/当前动作/最近输出."""
    await _seed_task(test_session_factory, "t-1", thread_id="th-1", run_id="run-1")
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {"type": "human", "content": "查资料 A", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": "web_search", "args": {"query": "3D打印"}}],
                },
            ]
        }
    }

    resp = await client.get("/api/tasks/t-1/progress")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["executor"] == "member-li"
    assert body["status"] == "running"
    assert body["instruction"] == "查资料 A"
    assert body["run_id"] == "run-1"
    assert body["elapsed"] >= 29
    assert body["steps"][0]["name"] == "web_search"
    assert body["current"]["kind"] == "tool"


@pytest.mark.asyncio
async def test_task_progress_pending_task(client, test_session_factory):
    """排队中（pending，无 thread）：starting 占位."""
    await _seed_task(
        test_session_factory, "t-2", status="pending", thread_id=None, run_id=None
    )

    resp = await client.get("/api/tasks/t-2/progress")

    body = resp.json()
    assert body["active"] is True
    assert body["steps"] == []
    assert body["current"]["detail"] == "排队等待执行…"


@pytest.mark.asyncio
async def test_task_progress_terminal_task(client, test_session_factory):
    """终态任务：active=False + result_summary（不再查 thread state）."""
    await _seed_task(test_session_factory, "t-3", status="done", result_summary="资料 A 完成")

    resp = await client.get("/api/tasks/t-3/progress")

    body = resp.json()
    assert body["active"] is False
    assert body["result_summary"] == "资料 A 完成"


@pytest.mark.asyncio
async def test_task_progress_thread_state_failure(client, test_session_factory, mock_deerflow):
    """thread state 查询失败：降级为 unknown 占位（不 500）."""
    await _seed_task(test_session_factory, "t-4", thread_id="th-4", run_id="run-4")
    mock_deerflow.get_thread_state.side_effect = RuntimeError("boom")

    resp = await client.get("/api/tasks/t-4/progress")

    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["current"]["kind"] == "unknown"


@pytest.mark.asyncio
async def test_task_progress_not_found(client, test_session_factory):
    """任务不存在 → 404."""
    resp = await client.get("/api/tasks/t-nope/progress")
    assert resp.status_code == 404
