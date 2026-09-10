"""AsyncDelegateService 单元测试."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.deerflow.client import DeerFlowClient
from app.models.delegation import DelegationLedger
from app.models.task import Task
from app.models.waker import Waker
from app.services.async_delegate import AsyncDelegateService
from app.services.delegation_guard import DelegationBlockedError, DelegationGuard
from app.services.wake_engine import WakeDeliveryError, WakeEngine


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
    client = MagicMock(spec=DeerFlowClient)
    client.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    client.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    client.get_run = AsyncMock(return_value={"status": "running"})
    client.cancel_run = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_wake_engine():
    """构建 mock WakeEngine."""
    engine = MagicMock(spec=WakeEngine)
    engine.wake = AsyncMock(return_value="wake-run-id")
    engine.build_wake_input = MagicMock(return_value={"messages": []})
    return engine


@pytest.fixture
def delegation_guard():
    return DelegationGuard()


@pytest.fixture
def async_delegate_service(test_session_factory, mock_deerflow, mock_wake_engine, delegation_guard):
    return AsyncDelegateService(
        db_session_factory=test_session_factory,
        deerflow_client=mock_deerflow,
        wake_engine=mock_wake_engine,
        delegation_guard=delegation_guard,
    )


async def _create_waker(session_factory, name: str, enabled: bool = True):
    """辅助函数：创建 Waker."""
    async with session_factory() as session:
        session.add(Waker(name=name, deer_user="", description=f"Test {name}", soul_summary="...", enabled=enabled))
        await session.commit()


async def _create_parent_task(session_factory, task_id: str, executor: str, thread_id: str = "parent-thread"):
    """辅助函数：创建父任务."""
    async with session_factory() as session:
        task = Task(
            id=task_id,
            kind="async_delegate",
            executor=executor,
            status="running",
            input_text="parent task",
            thread_id=thread_id,
            run_id="parent-run",
            created_by="system",
        )
        session.add(task)
        await session.commit()


async def _create_parent_ledger(
    session_factory,
    ticket_id: str,
    source_waker: str,
    target_waker: str,
    depth: int,
    path: list[str],
    source_task_id: str | None = None,
):
    """辅助函数：创建父 ledger."""
    async with session_factory() as session:
        ledger = DelegationLedger(
            ticket_id=ticket_id,
            source_task_id=source_task_id,
            source_waker=source_waker,
            target_waker=target_waker,
            depth=depth,
            path_json=json.dumps(path),
            status="running",
        )
        session.add(ledger)
        await session.commit()


# ------------------------------------------------------------------
# submit 测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_creates_ticket_and_task(
    async_delegate_service, test_session_factory, mock_deerflow
):
    """submit 返回 ticket_id，TASK 创建."""
    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="分析市场数据",
        group_id="default",
        source_task_id=None,
    )

    assert ticket_id is not None
    assert len(ticket_id) > 0

    # 验证 TASK 创建
    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == ticket_id))
        task = result.scalars().first()
        assert task is not None
        assert task.kind == "async_delegate"
        assert task.ticket_id == ticket_id
        assert task.executor == "bob"
        assert task.status == "running"
        assert task.input_text == "分析市场数据"
        assert task.thread_id is not None
        assert task.run_id is not None

    # 验证 DeerFlow 调用
    mock_deerflow.create_thread.assert_called_once()
    mock_deerflow.create_run.assert_called_once()


@pytest.mark.asyncio
async def test_submit_records_ledger(
    async_delegate_service, test_session_factory
):
    """delegation_ledger 记录正确."""
    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="test",
        group_id="default",
        source_task_id=None,
    )

    # 验证 ledger 记录
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == ticket_id)
        )
        ledger = result.scalars().first()
        assert ledger is not None
        assert ledger.source_waker == "alice"
        assert ledger.target_waker == "bob"
        assert ledger.depth == 1
        assert ledger.status == "running"
        path = json.loads(ledger.path_json)
        assert "alice" in path


@pytest.mark.asyncio
async def test_submit_with_parent_inherits_depth(
    async_delegate_service, test_session_factory
):
    """有父任务时，depth = parent.depth + 1."""
    # 创建父 ledger
    await _create_parent_ledger(
        test_session_factory,
        ticket_id="parent-ticket",
        source_waker="X",
        target_waker="alice",
        depth=2,
        path=["X", "Y"],
    )

    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="test",
        group_id="default",
        source_task_id="parent-ticket",
    )

    # 验证子 ledger depth = 2 + 1 = 3
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == ticket_id)
        )
        ledger = result.scalars().first()
        assert ledger is not None
        assert ledger.depth == 3
        assert ledger.source_task_id == "parent-ticket"
        path = json.loads(ledger.path_json)
        assert "alice" in path


@pytest.mark.asyncio
async def test_submit_blocked_by_depth(
    async_delegate_service, test_session_factory
):
    """深度 >3 被拒."""
    # 创建深度为 3 的 ledger（已达上限）
    await _create_parent_ledger(
        test_session_factory,
        ticket_id="parent-ticket",
        source_waker="X",
        target_waker="alice",
        depth=3,
        path=["A", "B", "X"],
    )

    with pytest.raises(DelegationBlockedError) as exc_info:
        await async_delegate_service.submit(
            source_waker="alice",
            target_waker="bob",
            instruction="test",
            group_id="default",
            source_task_id="parent-ticket",
        )
    assert "Depth limit" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_submit_blocked_by_loop(
    async_delegate_service, test_session_factory
):
    """环路检测拒绝."""
    await _create_parent_ledger(
        test_session_factory,
        ticket_id="parent-ticket",
        source_waker="A",
        target_waker="B",
        depth=2,
        path=["A", "B"],
    )

    with pytest.raises(DelegationBlockedError) as exc_info:
        await async_delegate_service.submit(
            source_waker="B",
            target_waker="A",  # A 已在 path 中
            instruction="test",
            group_id="default",
            source_task_id="parent-ticket",
        )
    assert "Loop detected" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_submit_blocked_by_fanout(
    async_delegate_service, test_session_factory
):
    """扇出 >5 被拒."""
    # 创建 5 个已存在的子委派
    async with test_session_factory() as session:
        for i in range(5):
            ledger = DelegationLedger(
                ticket_id=f"child-{i}",
                source_task_id="parent-ticket",
                source_waker="alice",
                target_waker=f"target-{i}",
                depth=1,
                path_json=json.dumps(["alice"]),
                status="running",
            )
            session.add(ledger)
        await session.commit()

    with pytest.raises(DelegationBlockedError) as exc_info:
        await async_delegate_service.submit(
            source_waker="alice",
            target_waker="target-5",
            instruction="test",
            group_id="default",
            source_task_id="parent-ticket",
        )
    assert "Fan-out limit" in str(exc_info.value.reason)


# ------------------------------------------------------------------
# get_status 测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status(async_delegate_service, test_session_factory):
    """查询 ticket 状态."""
    # 先提交一个委派
    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="test",
        group_id="default",
        source_task_id=None,
    )

    status = await async_delegate_service.get_status(ticket_id)
    assert status["ticket_id"] == ticket_id
    assert status["status"] == "running"
    assert status["target_waker"] == "bob"


@pytest.mark.asyncio
async def test_get_status_not_found(async_delegate_service):
    """查询不存在的 ticket."""
    status = await async_delegate_service.get_status("nonexistent-ticket")
    assert status["status"] == "not_found"


# ------------------------------------------------------------------
# cancel 测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel(async_delegate_service, test_session_factory, mock_deerflow):
    """取消委派."""
    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="test",
        group_id="default",
        source_task_id=None,
    )

    result = await async_delegate_service.cancel(ticket_id)
    assert result is True

    # 验证 TASK 状态
    async with test_session_factory() as session:
        task_result = await session.execute(select(Task).where(Task.id == ticket_id))
        task = task_result.scalars().first()
        assert task.status == "cancelled"

    # 验证 ledger 状态
    async with test_session_factory() as session:
        ledger_result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == ticket_id)
        )
        ledger = ledger_result.scalars().first()
        assert ledger.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_not_found(async_delegate_service):
    """取消不存在的 ticket."""
    result = await async_delegate_service.cancel("nonexistent-ticket")
    assert result is False


# ------------------------------------------------------------------
# on_run_completed 测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_run_completed_triggers_wake(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """run 完成触发唤醒."""
    # 创建父任务
    await _create_parent_task(test_session_factory, "parent-task-id", "alice", "parent-thread")

    # 创建子任务（async_delegate）
    async with test_session_factory() as session:
        child_task = Task(
            id="child-task-id",
            kind="async_delegate",
            ticket_id="child-ticket",
            parent_task_id="parent-task-id",
            executor="bob",
            status="done",
            input_text="child task",
            result_summary="分析完成",
            thread_id="child-thread",
            run_id="child-run",
            created_by="alice",
        )
        session.add(child_task)
        # 创建 ledger
        ledger = DelegationLedger(
            ticket_id="child-ticket",
            source_task_id="parent-task-id",
            source_waker="alice",
            target_waker="bob",
            depth=1,
            path_json=json.dumps(["alice"]),
            status="running",
        )
        session.add(ledger)
        await session.commit()

    # 调用 on_run_completed
    await async_delegate_service.on_run_completed(child_task)

    # 验证 wake_engine.wake 被调用
    mock_wake_engine.wake.assert_called_once()
    call_kwargs = mock_wake_engine.wake.call_args.kwargs
    assert call_kwargs["source_thread_id"] == "parent-thread"
    assert call_kwargs["source_agent_name"] == "alice"
    assert "分析完成" in call_kwargs["result_summary"]

    # 验证 ledger 状态更新
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == "child-ticket")
        )
        ledger = result.scalars().first()
        assert ledger.status == "completed"


@pytest.mark.asyncio
async def test_wake_delivery_failure(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """唤醒投递失败 → TASK failed."""
    # 设置 wake_engine 抛出异常
    mock_wake_engine.wake = AsyncMock(side_effect=WakeDeliveryError("delivery failed"))

    # 创建父任务
    await _create_parent_task(test_session_factory, "parent-task-id", "alice", "parent-thread")

    # 创建子任务
    async with test_session_factory() as session:
        child_task = Task(
            id="child-task-id",
            kind="async_delegate",
            ticket_id="child-ticket",
            parent_task_id="parent-task-id",
            executor="bob",
            status="done",
            input_text="child task",
            result_summary="完成",
            thread_id="child-thread",
            run_id="child-run",
            created_by="alice",
        )
        session.add(child_task)
        ledger = DelegationLedger(
            ticket_id="child-ticket",
            source_task_id="parent-task-id",
            source_waker="alice",
            target_waker="bob",
            depth=1,
            path_json=json.dumps(["alice"]),
            status="running",
        )
        session.add(ledger)
        await session.commit()

    # 调用 on_run_completed
    await async_delegate_service.on_run_completed(child_task)

    # 验证 TASK 状态变为 failed
    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == "child-task-id"))
        task = result.scalars().first()
        assert task.status == "failed"
        assert "Wake delivery failed" in task.result_summary

    # 验证 ledger 状态
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == "child-ticket")
        )
        ledger = result.scalars().first()
        assert ledger.status == "failed"


@pytest.mark.asyncio
async def test_on_run_completed_no_parent(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """无父任务时，不投递唤醒."""
    async with test_session_factory() as session:
        task = Task(
            id="orphan-task",
            kind="async_delegate",
            ticket_id="orphan-ticket",
            parent_task_id=None,
            executor="bob",
            status="done",
            input_text="orphan task",
            result_summary="完成",
            thread_id="orphan-thread",
            run_id="orphan-run",
            created_by="alice",
        )
        session.add(task)
        ledger = DelegationLedger(
            ticket_id="orphan-ticket",
            source_task_id=None,
            source_waker="alice",
            target_waker="bob",
            depth=1,
            path_json=json.dumps(["alice"]),
            status="running",
        )
        session.add(ledger)
        await session.commit()

    await async_delegate_service.on_run_completed(task)

    # wake_engine 不应被调用
    mock_wake_engine.wake.assert_not_called()

    # ledger 应该更新为 completed
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == "orphan-ticket")
        )
        ledger = result.scalars().first()
        assert ledger.status == "completed"


@pytest.mark.asyncio
async def test_on_run_completed_failed_task(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """失败的 run 不投递唤醒."""
    await _create_parent_task(test_session_factory, "parent-task-id", "alice", "parent-thread")

    async with test_session_factory() as session:
        child_task = Task(
            id="child-task-id",
            kind="async_delegate",
            ticket_id="child-ticket",
            parent_task_id="parent-task-id",
            executor="bob",
            status="failed",
            input_text="child task",
            result_summary="执行失败",
            thread_id="child-thread",
            run_id="child-run",
            created_by="alice",
        )
        session.add(child_task)
        ledger = DelegationLedger(
            ticket_id="child-ticket",
            source_task_id="parent-task-id",
            source_waker="alice",
            target_waker="bob",
            depth=1,
            path_json=json.dumps(["alice"]),
            status="running",
        )
        session.add(ledger)
        await session.commit()

    await async_delegate_service.on_run_completed(child_task)

    # wake_engine 不应被调用
    mock_wake_engine.wake.assert_not_called()

    # ledger 应该更新为 failed
    async with test_session_factory() as session:
        result = await session.execute(
            select(DelegationLedger).where(DelegationLedger.ticket_id == "child-ticket")
        )
        ledger = result.scalars().first()
        assert ledger.status == "failed"


# ------------------------------------------------------------------
# 成员汇报写群（异步委派完成/失败 → 发起会话中发声）
# ------------------------------------------------------------------


async def _create_group_conversation(session_factory, conv_id: str, group_id: str = "default"):
    """辅助函数：创建群会话."""
    from app.models.conversation import Conversation

    async with session_factory() as session:
        session.add(
            Conversation(
                id=conv_id,
                scope="group",
                waker_id=None,
                group_id=group_id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _group_messages(session_factory, conv_id: str):
    from app.models.conversation import ConversationMessage

    async with session_factory() as session:
        result = await session.execute(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conv_id
            )
        )
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_submit_stores_conversation_id(async_delegate_service, test_session_factory):
    """submit 携带 conversation_id → 任务存储发起会话 ID."""
    ticket_id = await async_delegate_service.submit(
        source_waker="alice",
        target_waker="bob",
        instruction="写评审意见",
        group_id="default",
        conversation_id="conv-report-1",
    )

    async with test_session_factory() as session:
        result = await session.execute(select(Task).where(Task.id == ticket_id))
        task = result.scalars().first()
        assert task.conversation_id == "conv-report-1"


@pytest.mark.asyncio
async def test_on_run_completed_writes_group_report(
    async_delegate_service, test_session_factory
):
    """任务完成 → 以【成员汇报】写回发起会话（成员在群里发声）."""
    await _create_group_conversation(test_session_factory, "conv-report-1")

    child_task = Task(
        id="child-task-id",
        kind="async_delegate",
        ticket_id="child-ticket",
        parent_task_id=None,
        group_id="default",
        conversation_id="conv-report-1",
        executor="bob",
        status="done",
        input_text="撰写技术落地章节",
        result_summary="已完成章节初稿，含 3 个架构图",
        created_by="alice",
    )
    async with test_session_factory() as session:
        session.add(child_task)
        await session.commit()

    await async_delegate_service.on_run_completed(child_task)

    msgs = await _group_messages(test_session_factory, "conv-report-1")
    assert len(msgs) == 1
    assert msgs[0].role == "waker"
    assert msgs[0].waker_id == "bob"
    assert "【成员汇报 · bob】" in (msgs[0].content_json or "")
    assert "撰写技术落地章节" in (msgs[0].content_json or "")
    assert "已完成章节初稿" in (msgs[0].content_json or "")
    assert '"kind": "report"' in (msgs[0].content_json or "")


@pytest.mark.asyncio
async def test_on_run_completed_failed_writes_group_report(
    async_delegate_service, test_session_factory
):
    """任务失败 → 写「未能完成」汇报."""
    await _create_group_conversation(test_session_factory, "conv-report-2")

    child_task = Task(
        id="child-task-id",
        kind="async_delegate",
        ticket_id="child-ticket",
        parent_task_id=None,
        group_id="default",
        conversation_id="conv-report-2",
        executor="bob",
        status="failed",
        input_text="写评审意见",
        result_summary="模型服务超时",
        created_by="alice",
    )
    async with test_session_factory() as session:
        session.add(child_task)
        await session.commit()

    await async_delegate_service.on_run_completed(child_task)

    msgs = await _group_messages(test_session_factory, "conv-report-2")
    assert len(msgs) == 1
    assert "【成员汇报 · bob】" in (msgs[0].content_json or "")
    assert "未能完成：模型服务超时" in (msgs[0].content_json or "")


@pytest.mark.asyncio
async def test_on_run_completed_without_conversation_writes_nothing(
    async_delegate_service, test_session_factory
):
    """无 conversation_id → 不写群汇报（静默，保持原行为）."""
    await _create_group_conversation(test_session_factory, "conv-report-3")

    child_task = Task(
        id="child-task-id",
        kind="async_delegate",
        ticket_id="child-ticket",
        parent_task_id=None,
        group_id="default",
        conversation_id=None,
        executor="bob",
        status="done",
        input_text="任务",
        result_summary="完成",
        created_by="alice",
    )
    async with test_session_factory() as session:
        session.add(child_task)
        await session.commit()

    await async_delegate_service.on_run_completed(child_task)

    msgs = await _group_messages(test_session_factory, "conv-report-3")
    assert msgs == []
