"""同步引擎测试."""

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.task import Task
from app.services.sync_engine import SyncEngine


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
    client.get_run = AsyncMock()
    return client


async def _create_running_task(session_factory, task_id: str, thread_id: str = "thread-1", run_id: str = "run-1"):
    """辅助函数：创建 running 状态的 Task."""
    async with session_factory() as session:
        session.add(Task(
            id=task_id, kind="manual", executor="alice", status="running",
            input_text="test task", thread_id=thread_id, run_id=run_id, created_by="system",
            created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
        ))
        await session.commit()


# ------------------------------------------------------------------
# 状态同步测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_success(test_session_factory, mock_deerflow):
    """run 状态 success → TASK done."""
    await _create_running_task(test_session_factory, "task-1")
    mock_deerflow.get_run.return_value = {
        "status": "success",
        "messages": [{"content": "任务完成，结果已生成"}],
    }

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    # 验证 TASK 状态
    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-1"))
        task = result.scalars().first()
        assert task.status == "done"
        assert task.result_summary == "任务完成，结果已生成"


@pytest.mark.asyncio
async def test_sync_error(test_session_factory, mock_deerflow):
    """run 状态 error → TASK failed."""
    await _create_running_task(test_session_factory, "task-2")
    mock_deerflow.get_run.return_value = {"status": "error"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-2"))
        task = result.scalars().first()
        assert task.status == "failed"


@pytest.mark.asyncio
async def test_sync_timeout(test_session_factory, mock_deerflow):
    """run 状态 timeout → TASK failed."""
    await _create_running_task(test_session_factory, "task-3")
    mock_deerflow.get_run.return_value = {"status": "timeout"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-3"))
        task = result.scalars().first()
        assert task.status == "failed"


@pytest.mark.asyncio
async def test_sync_interrupted(test_session_factory, mock_deerflow):
    """run 状态 interrupted → TASK cancelled."""
    await _create_running_task(test_session_factory, "task-4")
    mock_deerflow.get_run.return_value = {"status": "interrupted"}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-4"))
        task = result.scalars().first()
        assert task.status == "cancelled"


@pytest.mark.asyncio
async def test_sync_query_failure(test_session_factory, mock_deerflow):
    """查询失败 → TASK failed (sync_error)."""
    await _create_running_task(test_session_factory, "task-5")
    mock_deerflow.get_run.side_effect = Exception("Connection error")

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "task-5"))
        task = result.scalars().first()
        assert task.status == "failed"
        assert "Sync error" in task.result_summary


@pytest.mark.asyncio
async def test_sync_multiple_tasks(test_session_factory, mock_deerflow):
    """多个 running 任务同时同步."""
    await _create_running_task(test_session_factory, "task-a", thread_id="t-a", run_id="r-a")
    await _create_running_task(test_session_factory, "task-b", thread_id="t-b", run_id="r-b")

    # task-a success, task-b error
    async def get_run_side_effect(thread_id, run_id):
        if thread_id == "t-a":
            return {"status": "success", "messages": [{"content": "done"}]}
        return {"status": "error"}

    mock_deerflow.get_run.side_effect = get_run_side_effect

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    async with test_session_factory() as session:
        result_a = await session.execute(select(Task).where(Task.id == "task-a"))
        task_a = result_a.scalars().first()
        assert task_a.status == "done"

        result_b = await session.execute(select(Task).where(Task.id == "task-b"))
        task_b = result_b.scalars().first()
        assert task_b.status == "failed"


# ------------------------------------------------------------------
# 启停测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_start_stop(test_session_factory, mock_deerflow):
    """测试引擎启停."""
    engine = SyncEngine(test_session_factory, mock_deerflow, interval=0.1)

    await engine.start()
    assert engine._running is True
    assert engine._task is not None

    await engine.stop()
    assert engine._running is False


@pytest.mark.asyncio
async def test_sync_running_only(test_session_factory, mock_deerflow):
    """只同步 running 状态的任务，其他状态跳过."""
    # 创建不同状态的任务
    async with test_session_factory() as session:
        session.add(Task(
            id="task-running", kind="manual", executor="alice", status="running",
            input_text="running", thread_id="t1", run_id="r1", created_by="system",
        ))
        session.add(Task(
            id="task-done", kind="manual", executor="alice", status="done",
            input_text="done", thread_id="t2", run_id="r2", created_by="system",
        ))
        session.add(Task(
            id="task-failed", kind="manual", executor="alice", status="failed",
            input_text="failed", thread_id="t3", run_id="r3", created_by="system",
        ))
        await session.commit()

    mock_deerflow.get_run.return_value = {"status": "success", "messages": []}

    engine = SyncEngine(test_session_factory, mock_deerflow, interval=1.0)
    await engine._sync_once()

    # 只调用了 running 任务的 get_run
    assert mock_deerflow.get_run.call_count == 1
    mock_deerflow.get_run.assert_called_with("t1", "r1")
