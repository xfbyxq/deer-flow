"""人工确认服务测试 — approve / reject / 超时升级."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.engine.flow_engine import FlowEngine
from app.engine.review_service import ReviewService, ReviewServiceError
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
    engine.on_node_completed = AsyncMock()
    engine.on_node_failed = AsyncMock()
    return engine


@pytest.fixture
def review_service(mock_flow_engine):
    """构建 ReviewService."""
    return ReviewService(flow_engine=mock_flow_engine)


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


async def _create_waiting_review_node(
    session: AsyncSession,
    flow_run_id: str,
    node_key: str = "review1",
    timeout_hours: int = 48,
) -> NodeRun:
    """创建 waiting_review 状态的 NodeRun."""
    node_run = NodeRun(
        id=str(uuid.uuid4()),
        flow_run_id=flow_run_id,
        node_id=node_key,
        node_key=node_key,
        node_type="human_review",
        status="waiting_review",
        started_at=datetime.now(timezone.utc),
        input_json=json.dumps({"timeout_hours": timeout_hours, "checklist": []}),
    )
    session.add(node_run)
    await session.commit()
    await session.refresh(node_run)
    return node_run


# ------------------------------------------------------------------
# 1. approve 成功
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_success(review_service, mock_flow_engine, test_session_factory):
    """approve 成功 — 状态变为 completed."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        node_run = await _create_waiting_review_node(session, flow_run.id)

        result = await review_service.approve(
            flow_run_id=flow_run.id,
            node_key=node_run.node_key,
            db=session,
            comment="LGTM",
        )

        assert result.status == "completed"
        assert result.completed_at is not None
        outcome = json.loads(result.outcome_json)
        assert outcome["action"] == "approve"
        assert outcome["comment"] == "LGTM"

        # FlowEngine 收到回调
        mock_flow_engine.on_node_completed.assert_awaited_once()


# ------------------------------------------------------------------
# 2. reject 成功
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_success(review_service, mock_flow_engine, test_session_factory):
    """reject 成功 — 状态变为 failed."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        node_run = await _create_waiting_review_node(session, flow_run.id)

        result = await review_service.reject(
            flow_run_id=flow_run.id,
            node_key=node_run.node_key,
            db=session,
            comment="Needs more work",
        )

        assert result.status == "failed"
        assert result.completed_at is not None
        outcome = json.loads(result.outcome_json)
        assert outcome["action"] == "reject"
        assert outcome["comment"] == "Needs more work"

        # FlowEngine 收到失败回调
        mock_flow_engine.on_node_failed.assert_awaited_once()


# ------------------------------------------------------------------
# 3. reject 无 comment 报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reject_without_comment(review_service, test_session_factory):
    """reject 无 comment → ReviewServiceError."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        node_run = await _create_waiting_review_node(session, flow_run.id)

        with pytest.raises(ReviewServiceError, match="comment is required"):
            await review_service.reject(
                flow_run_id=flow_run.id,
                node_key=node_run.node_key,
                db=session,
                comment="",
            )


# ------------------------------------------------------------------
# 4. 非 waiting_review 状态拒绝操作
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_non_waiting_review(review_service, test_session_factory):
    """approve 非 waiting_review 状态 → ReviewServiceError."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)

        # 创建 pending 状态的节点
        node_run = NodeRun(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run.id,
            node_id="node1",
            node_key="node1",
            node_type="waker_task",
            status="pending",
        )
        session.add(node_run)
        await session.commit()

        with pytest.raises(ReviewServiceError, match="not waiting review"):
            await review_service.approve(
                flow_run_id=flow_run.id,
                node_key="node1",
                db=session,
            )


# ------------------------------------------------------------------
# 5. 超时检测 — 未超时不标记
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_timeouts_not_escalated(review_service, test_session_factory):
    """未超时节点不被标记."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        node_run = await _create_waiting_review_node(
            session, flow_run.id, timeout_hours=48
        )
        # started_at 是 now，不会超时
        escalated = await review_service.check_timeouts(db=session)

        assert len(escalated) == 0

        # 确认节点状态未变
        await session.refresh(node_run)
        assert node_run.status == "waiting_review"


# ------------------------------------------------------------------
# 6. 超时检测 — 已超时标记 escalated
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_timeouts_escalated(review_service, test_session_factory):
    """已超时节点被标记为 escalated."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)

        # 创建 started_at 在 49 小时前的节点（timeout_hours=48）
        node_run = NodeRun(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run.id,
            node_id="review_timeout",
            node_key="review_timeout",
            node_type="human_review",
            status="waiting_review",
            started_at=datetime.now(timezone.utc) - timedelta(hours=49),
            input_json=json.dumps({"timeout_hours": 48, "checklist": []}),
        )
        session.add(node_run)
        await session.commit()

        escalated = await review_service.check_timeouts(db=session)

        assert len(escalated) == 1
        assert escalated[0].node_key == "review_timeout"
        assert escalated[0].status == "escalated"


# ------------------------------------------------------------------
# 7. approve 后 FlowEngine 收到回调
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_calls_flow_engine(review_service, mock_flow_engine, test_session_factory):
    """approve 后 FlowEngine.on_node_completed 被调用."""
    async with test_session_factory() as session:
        flow_run = await _create_flow_run(session)
        node_run = await _create_waiting_review_node(session, flow_run.id, node_key="review_x")

        await review_service.approve(
            flow_run_id=flow_run.id,
            node_key="review_x",
            db=session,
            comment="ok",
        )

        mock_flow_engine.on_node_completed.assert_awaited_once_with(
            flow_run.id, "review_x", {"action": "approve", "comment": "ok"}, session
        )
