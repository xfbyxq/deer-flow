"""Flow 引擎核心测试 — mock DeerFlow API."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.engine.flow_engine import FlowEngine, FlowEngineError
from app.engine.node_executors import (
    ConditionExecutor,
    HumanReviewExecutor,
    NotifyExecutor,
)
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.models.group import Group
from app.models.waker import Waker


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
def mock_deerflow():
    """构建 mock DeerFlowClient."""
    client = MagicMock()
    client.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    client.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    client.get_run = AsyncMock(return_value={"status": "success", "messages": []})
    client.cancel_run = AsyncMock(return_value=MagicMock())
    return client


@pytest.fixture
def mock_task_service():
    """构建 mock TaskService."""
    service = AsyncMock()
    service.create_task = AsyncMock(return_value={
        "id": str(uuid.uuid4()),
        "kind": "flow_node",
        "executor": "alice",
        "status": "running",
        "input_text": "test",
        "thread_id": "test-thread",
        "run_id": "test-run",
    })
    return service


@pytest.fixture
def flow_engine(test_session_factory, mock_deerflow, mock_task_service):
    """构建 FlowEngine."""
    return FlowEngine(
        db_session_factory=test_session_factory,
        deerflow_client=mock_deerflow,
        task_service_factory=lambda session: mock_task_service,
    )


# ------------------------------------------------------------------
# 测试数据
# ------------------------------------------------------------------

SERIAL_FLOW_DEF = {
    "version": 1,
    "nodes": [
        {"key": "step1", "type": "condition", "expression": "true", "branches": {"true": "step2"}, "depends_on": []},
        {"key": "step2", "type": "notify", "channel": "slack", "depends_on": ["step1"]},
    ],
}


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------


async def _create_flow_def(session: AsyncSession, definition: dict, name: str = "test-flow") -> FlowDef:
    """创建测试用 FlowDef."""
    flow_def = FlowDef(
        id=str(uuid.uuid4()),
        name=name,
        group_id="test-group",
        definition_json=json.dumps(definition),
        status="active",
        version=1,
    )
    session.add(flow_def)
    await session.commit()
    await session.refresh(flow_def)
    return flow_def


# ------------------------------------------------------------------
# 1. 串行 Flow 创建并启动
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_flow_run(flow_engine, test_session_factory):
    """串行 Flow 创建并启动."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)

        flow_run = await flow_engine.start(
            flow_def_id=flow_def.id,
            db=session,
            created_by="test",
            trigger_type="manual",
        )

        assert flow_run is not None
        assert flow_run.status == "running"
        assert flow_run.flow_def_id == flow_def.id

        # 检查 node_runs 已创建
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id)
        )
        node_runs = nr_result.scalars().all()
        assert len(node_runs) == 2


@pytest.mark.asyncio
async def test_start_flow_run_with_null_settings(flow_engine, test_session_factory):
    """回归：definition_json 中 settings 为 null 时启动不得 500.

    历史 bug：保存层会把缺省 settings 序列化为 null，而
    `def_json.get("settings", {})` 在 key 存在且值为 null 时返回 None，
    随后 `settings.get(...)` 抛 "'NoneType' object has no attribute 'get'"。
    """
    definition = {
        "version": 1,
        "nodes": [
            {"key": "step1", "type": "waker_task", "waker": "alice", "instruction": "do", "depends_on": []},
        ],
        "settings": None,
    }
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, definition)
        flow_run = await flow_engine.start(
            flow_def_id=flow_def.id,
            db=session,
            created_by="test",
            trigger_type="manual",
        )
        assert flow_run.status == "running"


# ------------------------------------------------------------------
# 2. condition 节点求值
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condition_executor_true():
    """condition 节点求值 — true 字面量."""
    executor = ConditionExecutor()
    result = executor._safe_evaluate("true", {})
    assert result is True


@pytest.mark.asyncio
async def test_condition_executor_false():
    """condition 节点求值 — false 字面量."""
    executor = ConditionExecutor()
    result = executor._safe_evaluate("false", {})
    assert result is False


@pytest.mark.asyncio
async def test_condition_executor_comparison():
    """condition 节点求值 — 比较表达式."""
    executor = ConditionExecutor()
    context = {"output_cache": {"step1": {"status": "done"}}}
    result = executor._safe_evaluate("step1.status == done", context)
    assert result is True


# ------------------------------------------------------------------
# 3. notify 节点记录
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_executor(test_session_factory):
    """notify 节点记录到审计日志."""
    executor = NotifyExecutor()

    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = FlowRun(
            id=str(uuid.uuid4()),
            flow_def_id=flow_def.id,
            status="running",
        )
        session.add(flow_run)
        await session.commit()

        node_run = NodeRun(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run.id,
            node_id="notify1",
            node_key="notify1",
            node_type="notify",
            status="pending",
        )
        session.add(node_run)
        await session.commit()

        node = {"key": "notify1", "type": "notify", "channel": "slack", "payload": {"msg": "hello"}}
        outcome = await executor.execute(node, flow_run, node_run, session, {})

        assert outcome["status"] == "sent"
        assert node_run.status == "completed"


# ------------------------------------------------------------------
# 4. human_review 节点设置 waiting_review
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_human_review_executor(test_session_factory):
    """human_review 节点设置 waiting_review."""
    executor = HumanReviewExecutor()

    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = FlowRun(
            id=str(uuid.uuid4()),
            flow_def_id=flow_def.id,
            status="running",
        )
        session.add(flow_run)
        await session.commit()

        node_run = NodeRun(
            id=str(uuid.uuid4()),
            flow_run_id=flow_run.id,
            node_id="review1",
            node_key="review1",
            node_type="human_review",
            status="pending",
        )
        session.add(node_run)
        await session.commit()

        node = {"key": "review1", "type": "human_review", "checklist": ["item1"], "timeout_hours": 24}
        outcome = await executor.execute(node, flow_run, node_run, session, {})

        assert outcome["status"] == "waiting_review"
        assert node_run.status == "waiting_review"


# ------------------------------------------------------------------
# 5. Flow 暂停/恢复/取消
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_flow(flow_engine, test_session_factory):
    """Flow 暂停."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = await flow_engine.start(flow_def.id, session)

        paused_run = await flow_engine.pause(flow_run.id, session)
        assert paused_run.status == "paused"


@pytest.mark.asyncio
async def test_cancel_flow(flow_engine, test_session_factory):
    """Flow 取消."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = await flow_engine.start(flow_def.id, session)

        cancelled_run = await flow_engine.cancel(flow_run.id, session)
        assert cancelled_run.status == "cancelled"

        # 检查所有 node_runs 也被取消
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id)
        )
        for nr in nr_result.scalars().all():
            assert nr.status == "cancelled"


# ------------------------------------------------------------------
# 6. on_node_completed 回调
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_node_completed(flow_engine, test_session_factory):
    """on_node_completed 更新 node_run 状态."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = await flow_engine.start(flow_def.id, session)

        # 模拟节点完成回调
        await flow_engine.on_node_completed(
            flow_run.id, "step1", {"status": "done"}, session
        )

        # 检查 node_run 已更新
        nr_result = await session.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run.id,
                NodeRun.node_key == "step1",
            )
        )
        node_run = nr_result.scalars().first()
        assert node_run.status == "completed"
        assert node_run.outcome_json is not None


# ------------------------------------------------------------------
# 7. on_node_failed 回调
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_node_failed(flow_engine, test_session_factory):
    """on_node_failed 更新 node_run 状态."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = await flow_engine.start(flow_def.id, session)

        await flow_engine.on_node_failed(
            flow_run.id, "step1", "Something went wrong", session
        )

        nr_result = await session.execute(
            select(NodeRun).where(
                NodeRun.flow_run_id == flow_run.id,
                NodeRun.node_key == "step1",
            )
        )
        node_run = nr_result.scalars().first()
        assert node_run.status == "failed"
        assert node_run.error_message == "Something went wrong"


# ------------------------------------------------------------------
# 8. 不存在的 FlowDef 启动失败
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_nonexistent_flow_def(flow_engine, test_session_factory):
    """启动不存在的 FlowDef → FlowEngineError."""
    async with test_session_factory() as session:
        with pytest.raises(FlowEngineError, match="not found"):
            await flow_engine.start("nonexistent-id", session)


# ------------------------------------------------------------------
# 9. 暂停非 running 的 Flow 失败
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_non_running_flow(flow_engine, test_session_factory):
    """暂停非 running 的 Flow → FlowEngineError."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = await flow_engine.start(flow_def.id, session)

        # 先取消
        await flow_engine.cancel(flow_run.id, session)

        # 尝试暂停已取消的 flow → 报错
        with pytest.raises(FlowEngineError, match="not running"):
            await flow_engine.pause(flow_run.id, session)
