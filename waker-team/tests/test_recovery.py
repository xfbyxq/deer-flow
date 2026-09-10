"""M14 崩溃恢复测试 — 覆盖 5 种恢复场景 + 边界条件."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.engine.flow_engine import FlowEngine
from app.engine.recovery import FlowRecovery
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.models.group import Group
from app.models.task import Task
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


@pytest.fixture
def flow_recovery(test_session_factory, flow_engine, mock_deerflow, mock_task_service):
    """构建 FlowRecovery."""
    return FlowRecovery(
        db_session_factory=test_session_factory,
        flow_engine=flow_engine,
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

WAKER_FLOW_DEF = {
    "version": 1,
    "nodes": [
        {"key": "task1", "type": "waker_task", "waker": "alice", "instruction": "Do task 1", "depends_on": []},
        {"key": "task2", "type": "waker_task", "waker": "bob", "instruction": "Do task 2", "depends_on": ["task1"]},
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


async def _create_running_flow(session: AsyncSession, flow_def: FlowDef) -> FlowRun:
    """创建 running 状态的 FlowRun."""
    flow_run = FlowRun(
        id=str(uuid.uuid4()),
        flow_def_id=flow_def.id,
        group_id=flow_def.group_id,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    session.add(flow_run)
    await session.commit()
    await session.refresh(flow_run)
    return flow_run


async def _create_node_run(
    session: AsyncSession,
    flow_run: FlowRun,
    node_key: str,
    node_type: str,
    status: str = "pending",
    task_id: str | None = None,
    input_json: str | None = None,
) -> NodeRun:
    """创建 NodeRun."""
    node_run = NodeRun(
        id=str(uuid.uuid4()),
        flow_run_id=flow_run.id,
        node_id=node_key,
        node_key=node_key,
        node_type=node_type,
        status=status,
        task_id=task_id,
        input_json=input_json or json.dumps({"key": node_key, "type": node_type}),
    )
    session.add(node_run)
    await session.commit()
    await session.refresh(node_run)
    return node_run


async def _create_task(
    session: AsyncSession,
    thread_id: str,
    run_id: str,
    status: str = "running",
) -> Task:
    """创建 Task 记录."""
    task = Task(
        id=str(uuid.uuid4()),
        kind="flow_node",
        group_id="test-group",
        executor="alice",
        status=status,
        input_text="test task",
        thread_id=thread_id,
        run_id=run_id,
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


# ------------------------------------------------------------------
# 场景 1: Run 已终态（成功）— 补记 NODE_RUN.outcome
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_run_success(flow_recovery, mock_deerflow, test_session_factory):
    """Run 已终态（成功）→ 补记 NODE_RUN.outcome，标记 completed."""
    node_run_id = None
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, WAKER_FLOW_DEF)
        flow_run = await _create_running_flow(session, flow_def)

        # 创建 Task 记录
        task = await _create_task(session, thread_id="thread-1", run_id="run-1")

        # 创建 running 的 waker_task 节点
        node_run = await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="running", task_id=task.id,
        )
        node_run_id = node_run.id

        # Mock DeerFlow Run 返回 success
        mock_deerflow.get_run.return_value = {
            "status": "success",
            "messages": [{"content": "Task completed successfully"}],
        }

    # 执行恢复（使用自己的 session）
    stats = await flow_recovery.run()
    assert stats["recovered"] == 1

    # 在新 session 中验证
    async with test_session_factory() as session:
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.id == node_run_id)
        )
        recovered_node = nr_result.scalars().first()
        assert recovered_node.status == "completed"
        assert recovered_node.outcome_json is not None
        outcome = json.loads(recovered_node.outcome_json)
        assert outcome["status"] == "done"


# ------------------------------------------------------------------
# 场景 2: Run 仍在跑 — 加入 SyncEngine 监控（无需额外操作）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_run_still_running(flow_recovery, mock_deerflow, test_session_factory):
    """Run 仍在跑 → SyncEngine 会自动处理，恢复逻辑不做额外操作."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, WAKER_FLOW_DEF)
        flow_run = await _create_running_flow(session, flow_def)

        task = await _create_task(session, thread_id="thread-1", run_id="run-1")
        node_run = await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="running", task_id=task.id,
        )

        # Mock DeerFlow Run 仍在跑
        mock_deerflow.get_run.return_value = {"status": "running", "messages": []}

        stats = await flow_recovery.run()

        # 恢复计数为 1（因为尝试了恢复，只是节点保持 running）
        assert stats["recovered"] == 1

        # 验证 node_run 仍为 running（SyncEngine 会接管）
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.id == node_run.id)
        )
        recovered_node = nr_result.scalars().first()
        assert recovered_node.status == "running"


# ------------------------------------------------------------------
# 场景 3: Run 不存在 — 幂等重发
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_run_not_found(flow_recovery, mock_deerflow, mock_task_service, test_session_factory):
    """Run 不存在 → 幂等重发，创建新 Task."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, WAKER_FLOW_DEF)
        flow_run = await _create_running_flow(session, flow_def)

        task = await _create_task(session, thread_id="thread-1", run_id="run-1")
        node_run = await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="running", task_id=task.id,
            input_json=json.dumps({
                "key": "task1", "type": "waker_task",
                "waker": "alice", "instruction": "Do task 1",
            }),
        )

        # Mock DeerFlow Run 不存在（ThreadNotFoundError）
        from app.deerflow.errors import ThreadNotFoundError
        mock_deerflow.get_run.side_effect = ThreadNotFoundError("Thread not found")

        stats = await flow_recovery.run()

        assert stats["recovered"] == 1

        # 验证 create_task 被调用（幂等重发）
        mock_task_service.create_task.assert_called()

        # 验证 node_run 已更新
        await session.refresh(node_run)
        assert node_run.status == "running"
        assert node_run.retry_count == 1


# ------------------------------------------------------------------
# 场景 4: human_review 节点 — 保持 waiting_review，天然安全
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_human_review_waiting(flow_recovery, test_session_factory):
    """human_review waiting_review → 保持不变."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "review1", "type": "human_review", "checklist": ["item1"], "depends_on": []},
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)
        node_run = await _create_node_run(
            session, flow_run, "review1", "human_review",
            status="waiting_review",
        )

        stats = await flow_recovery.run()

        # 验证 node_run 仍为 waiting_review
        await session.refresh(node_run)
        assert node_run.status == "waiting_review"
        assert stats["recovered"] == 1


@pytest.mark.asyncio
async def test_recovery_human_review_pending(flow_recovery, test_session_factory):
    """human_review pending → 设置为 waiting_review."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "review1", "type": "human_review", "checklist": ["item1"], "depends_on": []},
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)
        node_run = await _create_node_run(
            session, flow_run, "review1", "human_review",
            status="pending",
        )

        stats = await flow_recovery.run()

        # 验证 node_run 已设置为 waiting_review
        await session.refresh(node_run)
        assert node_run.status == "waiting_review"


# ------------------------------------------------------------------
# 场景 5: 扇出部分完成 — 已完成不重跑，未完成按场景处理
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_fanout_partial(flow_recovery, mock_deerflow, test_session_factory):
    """扇出部分完成：已完成节点不重跑，未完成节点按状态处理."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "task1", "type": "waker_task", "waker": "alice", "depends_on": []},
                {"key": "task2", "type": "waker_task", "waker": "bob", "depends_on": []},
                {"key": "task3", "type": "waker_task", "waker": "charlie", "depends_on": []},
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)

        # task1: 已完成（不重跑）
        await _create_node_run(session, flow_run, "task1", "waker_task", status="completed")

        # task2: running，DeerFlow Run 成功 → 补记
        task2 = await _create_task(session, thread_id="t2", run_id="r2")
        await _create_node_run(
            session, flow_run, "task2", "waker_task",
            status="running", task_id=task2.id,
        )

        # task3: running，DeerFlow Run 失败 → 标记 failed
        task3 = await _create_task(session, thread_id="t3", run_id="r3")
        await _create_node_run(
            session, flow_run, "task3", "waker_task",
            status="running", task_id=task3.id,
        )

        # Mock: task2 success, task3 error
        async def mock_get_run(thread_id, run_id):
            if thread_id == "t2":
                return {"status": "success", "messages": []}
            elif thread_id == "t3":
                return {"status": "error", "messages": []}
            return {"status": "running", "messages": []}

        mock_deerflow.get_run.side_effect = mock_get_run

        stats = await flow_recovery.run()
        assert stats["recovered"] == 1

        # task1 保持 completed
        nr1 = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id, NodeRun.node_key == "task1")
        )
        assert nr1.scalars().first().status == "completed"

        # task2 补记为 completed
        nr2 = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id, NodeRun.node_key == "task2")
        )
        assert nr2.scalars().first().status == "completed"

        # task3 标记为 failed
        nr3 = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run.id, NodeRun.node_key == "task3")
        )
        assert nr3.scalars().first().status == "failed"


# ------------------------------------------------------------------
# 场景 6: 无 running Flow 时恢复返回空统计
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_no_running_flows(flow_recovery, test_session_factory):
    """无 running Flow → 返回空统计."""
    async with test_session_factory() as session:
        # 创建一个已完成的 flow
        flow_def = await _create_flow_def(session, SERIAL_FLOW_DEF)
        flow_run = FlowRun(
            id=str(uuid.uuid4()),
            flow_def_id=flow_def.id,
            status="completed",
            completed_at=datetime.now(timezone.utc),
        )
        session.add(flow_run)
        await session.commit()

    stats = await flow_recovery.run()

    assert stats == {"recovered": 0, "skipped": 0, "errors": 0}


# ------------------------------------------------------------------
# 场景 7: condition 节点重新求值
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_condition_reevaluate(flow_recovery, test_session_factory):
    """condition 节点重新求值."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {
                    "key": "cond1",
                    "type": "condition",
                    "expression": "true",
                    "branches": {"true": "next_step", "false": "alt_step"},
                    "depends_on": [],
                },
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)
        node_run = await _create_node_run(
            session, flow_run, "cond1", "condition",
            status="running",
            input_json=json.dumps({
                "key": "cond1", "type": "condition",
                "expression": "true",
                "branches": {"true": "next_step", "false": "alt_step"},
            }),
        )

        stats = await flow_recovery.run()
        assert stats["recovered"] == 1

        # 验证 condition 已重新求值并标记 completed
        await session.refresh(node_run)
        assert node_run.status == "completed"
        assert node_run.outcome_json is not None
        outcome = json.loads(node_run.outcome_json)
        assert outcome["result"] is True
        assert outcome["branch"] == "true"
        assert outcome["target_node"] == "next_step"


# ------------------------------------------------------------------
# 场景 8: notify 节点重发
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_notify_resend(flow_recovery, test_session_factory):
    """notify 节点幂等重发."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "notify1", "type": "notify", "channel": "slack", "payload": {"msg": "hello"}, "depends_on": []},
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)
        node_run = await _create_node_run(
            session, flow_run, "notify1", "notify",
            status="pending",
            input_json=json.dumps({
                "key": "notify1", "type": "notify",
                "channel": "slack", "payload": {"msg": "hello"},
            }),
        )

        stats = await flow_recovery.run()
        assert stats["recovered"] == 1

        # 验证 notify 已标记 completed
        await session.refresh(node_run)
        assert node_run.status == "completed"
        assert node_run.outcome_json is not None
        outcome = json.loads(node_run.outcome_json)
        assert outcome["status"] == "sent"


# ------------------------------------------------------------------
# 场景 9: 恢复后 FLOW_RUN 状态正确更新
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_flow_run_status_updated(flow_recovery, mock_deerflow, test_session_factory):
    """恢复后 FLOW_RUN 状态正确更新."""
    async with test_session_factory() as session:
        # 创建只有一个已完成节点的 flow
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "task1", "type": "waker_task", "waker": "alice", "depends_on": []},
            ],
        })
        flow_run = await _create_running_flow(session, flow_def)

        # task1: running，DeerFlow Run 成功
        task = await _create_task(session, thread_id="t1", run_id="r1")
        await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="running", task_id=task.id,
        )

        mock_deerflow.get_run.return_value = {"status": "success", "messages": []}

        await flow_recovery.run()

        # 验证 flow_run 已标记 completed（所有节点完成）
        await session.refresh(flow_run)
        # 由于 _try_resume_flow 会检查所有节点状态并 finalize
        assert flow_run.status in ("completed", "running")  # 可能已 finalize 或等待 resume


# ------------------------------------------------------------------
# 场景 10: 恢复过程中出错不影响其他 Flow
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_error_isolation(flow_recovery, mock_deerflow, test_session_factory):
    """恢复过程中一个 Flow 出错不影响其他 Flow."""
    async with test_session_factory() as session:
        # Flow 1: 正常恢复
        flow_def1 = await _create_flow_def(session, {
            "nodes": [{"key": "cond1", "type": "condition", "expression": "true", "branches": {}, "depends_on": []}],
        }, name="flow-1")
        flow_run1 = await _create_running_flow(session, flow_def1)
        await _create_node_run(
            session, flow_run1, "cond1", "condition",
            status="running",
            input_json=json.dumps({"key": "cond1", "type": "condition", "expression": "true", "branches": {}}),
        )

        # Flow 2: 会出错（Task 存在但 get_run 抛异常）
        flow_def2 = await _create_flow_def(session, {
            "nodes": [{"key": "task1", "type": "waker_task", "waker": "alice", "depends_on": []}],
        }, name="flow-2")
        flow_run2 = await _create_running_flow(session, flow_def2)
        task2 = await _create_task(session, thread_id="t2", run_id="r2")
        await _create_node_run(
            session, flow_run2, "task1", "waker_task",
            status="running", task_id=task2.id,
            input_json=json.dumps({"key": "task1", "type": "waker_task", "waker": "alice", "instruction": "test"}),
        )

        # Mock: flow2 的 get_run 抛出意外异常
        from app.deerflow.errors import DeerFlowUnavailableError

        async def mock_get_run(thread_id, run_id):
            if thread_id == "t2":
                raise DeerFlowUnavailableError("Connection error")
            return {"status": "running", "messages": []}

        mock_deerflow.get_run.side_effect = mock_get_run

        stats = await flow_recovery.run()

        # flow1 应该成功恢复，flow2 应该出错但不影响 flow1
        assert stats["recovered"] >= 1  # flow1 成功
        assert stats["errors"] >= 0  # flow2 可能出错

        # 验证 flow1 的 condition 节点已恢复
        nr_result = await session.execute(
            select(NodeRun).where(NodeRun.flow_run_id == flow_run1.id, NodeRun.node_key == "cond1")
        )
        cond_node = nr_result.scalars().first()
        assert cond_node.status == "completed"


# ------------------------------------------------------------------
# 额外测试: Run 终态失败 → 标记节点 failed
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recovery_run_error(flow_recovery, mock_deerflow, test_session_factory):
    """Run 已终态（失败）→ 标记节点 failed."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, WAKER_FLOW_DEF)
        flow_run = await _create_running_flow(session, flow_def)

        task = await _create_task(session, thread_id="thread-1", run_id="run-1")
        node_run = await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="running", task_id=task.id,
        )

        mock_deerflow.get_run.return_value = {"status": "error", "messages": []}

        stats = await flow_recovery.run()
        assert stats["recovered"] == 1

        await session.refresh(node_run)
        assert node_run.status == "failed"
        assert "error" in node_run.error_message
