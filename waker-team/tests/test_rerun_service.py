"""单节点重跑服务测试 — rerun_node / cancel_running_node / 并发上限."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.engine.dag import DAGScheduler
from app.engine.flow_engine import FlowEngine
from app.engine.rerun_service import RerunService, RerunServiceError
from app.models.flow import FlowDef, FlowRun, NodeRun


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


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
def mock_flow_engine():
    """构建 mock FlowEngine."""
    engine = MagicMock(spec=FlowEngine)
    engine.on_rerun_requested = AsyncMock()
    return engine


@pytest.fixture
def rerun_service(mock_flow_engine):
    """构建 RerunService."""
    return RerunService(flow_engine=mock_flow_engine)


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


async def _create_flow_run(session: AsyncSession) -> FlowRun:
    """创建测试用 FlowRun."""
    flow_def = FlowDef(
        id=str(uuid.uuid4()),
        name="test-flow",
        group_id="test-group",
        definition_json=json.dumps({"nodes": [], "settings": {}}),
        status="active",
    )
    session.add(flow_def)
    flow_run = FlowRun(
        id=str(uuid.uuid4()),
        flow_def_id=flow_def.id,
        status="running",
    )
    session.add(flow_run)
    await session.commit()
    await session.refresh(flow_run)
    return flow_run


async def _create_node_run(
    session: AsyncSession,
    flow_run_id: str,
    node_key: str = "node1",
    status: str = "completed",
    retry_count: int = 0,
) -> NodeRun:
    """创建 NodeRun."""
    node_run = NodeRun(
        id=str(uuid.uuid4()),
        flow_run_id=flow_run_id,
        node_id=node_key,
        node_key=node_key,
        node_type="waker_task",
        status=status,
        retry_count=retry_count,
        input_json=json.dumps({"key": node_key, "type": "waker_task"}),
    )
    session.add(node_run)
    await session.commit()
    await session.refresh(node_run)
    return node_run


# ------------------------------------------------------------------
# 1. 重跑 done 状态节点 — 新建 NODE_RUN
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_completed_node(rerun_service, mock_flow_engine, test_session_factory):
    """重跑 completed 节点 — 新建 NodeRun."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        original = await _create_node_run(session, flow_run.id, status="completed")

        new_node = await rerun_service.rerun_node(flow_run.id, original.node_key, session)

        assert new_node.id != original.id
        assert new_node.node_key == original.node_key
        assert new_node.status == "pending"
        assert new_node.retry_count == 1

        # FlowEngine 收到回调
        mock_flow_engine.on_rerun_requested.assert_awaited_once()


# ------------------------------------------------------------------
# 2. 重跑 failed 状态节点 — 新建 NODE_RUN
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_failed_node(rerun_service, test_session_factory):
    """重跑 failed 节点 — 新建 NodeRun."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        original = await _create_node_run(session, flow_run.id, status="failed")

        new_node = await rerun_service.rerun_node(flow_run.id, original.node_key, session)

        assert new_node.status == "pending"
        assert new_node.retry_count == 1


# ------------------------------------------------------------------
# 3. 重跑 running 状态节点 — 拒绝
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_running_node_rejected(rerun_service, test_session_factory):
    """重跑 running 节点 → RerunServiceError."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        original = await _create_node_run(session, flow_run.id, status="running")

        with pytest.raises(RerunServiceError, match="Cannot rerun"):
            await rerun_service.rerun_node(flow_run.id, original.node_key, session)


# ------------------------------------------------------------------
# 4. 原 NODE_RUN 保留不变
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_original_node_preserved(rerun_service, test_session_factory):
    """原 NodeRun 保留不变."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        original = await _create_node_run(session, flow_run.id, status="completed")
        original_id = original.id
        original_status = original.status

        await rerun_service.rerun_node(flow_run.id, original.node_key, session)

        # 重新加载原 NodeRun
        await session.refresh(original)
        assert original.id == original_id
        assert original.status == original_status  # 未改变


# ------------------------------------------------------------------
# 5. 新 NODE_RUN retry_count 递增
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_count_increments(rerun_service, test_session_factory):
    """新 NodeRun retry_count 递增."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)

        # 创建 retry_count=2 的节点
        original = await _create_node_run(
            session, flow_run.id, status="failed", retry_count=2
        )

        new_node = await rerun_service.rerun_node(flow_run.id, original.node_key, session)

        assert new_node.retry_count == 3


# ------------------------------------------------------------------
# 6. 重跑后 FlowEngine 收到回调
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_calls_flow_engine(rerun_service, mock_flow_engine, test_session_factory):
    """重跑后 FlowEngine.on_rerun_requested 被调用."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        original = await _create_node_run(session, flow_run.id, status="completed", retry_count=0)

        new_node = await rerun_service.rerun_node(flow_run.id, original.node_key, session)

        mock_flow_engine.on_rerun_requested.assert_awaited_once_with(
            flow_run.id, original.node_key, new_node, session
        )


# ------------------------------------------------------------------
# 7. 并发上限 — 超过 max_concurrent_nodes 的节点排队
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_queue():
    """DAGScheduler.get_ready_nodes 受 max_concurrent 限制."""
    nodes = [
        {"key": "a", "depends_on": []},
        {"key": "b", "depends_on": []},
        {"key": "c", "depends_on": []},
        {"key": "d", "depends_on": ["a"]},
    ]
    scheduler = DAGScheduler(nodes)

    # max_concurrent=2：第一轮只返回 2 个就绪节点
    ready = scheduler.get_ready_nodes(max_concurrent=2)
    assert len(ready) == 2

    for n in ready:
        scheduler.mark_running(n["key"])

    # 已达上限，不再返回新节点
    ready2 = scheduler.get_ready_nodes(max_concurrent=2)
    assert len(ready2) == 0


# ------------------------------------------------------------------
# 8. 并发上限 — 节点完成后队列释放
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_limit_release():
    """节点完成后释放名额，排队节点可执行."""
    nodes = [
        {"key": "a", "depends_on": []},
        {"key": "b", "depends_on": ["a"]},
    ]
    scheduler = DAGScheduler(nodes)

    # max_concurrent=1：第一轮只返回 1 个节点
    ready = scheduler.get_ready_nodes(max_concurrent=1)
    assert len(ready) == 1
    assert ready[0]["key"] == "a"

    scheduler.mark_running("a")

    # 完成 a 后释放 b
    scheduler.mark_completed("a")

    ready2 = scheduler.get_ready_nodes(max_concurrent=1)
    assert len(ready2) == 1
    assert ready2[0]["key"] == "b"
