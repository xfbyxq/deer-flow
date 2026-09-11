"""AsyncDelegateService 单元测试."""

import asyncio
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.database import Base
from app.deerflow.client import DeerFlowClient
from app.models.conversation import ConversationMessage
from app.models.delegation import DelegationLedger
from app.models.task import Task
from app.models.waker import Waker
from app.services.async_delegate import AsyncDelegateService
from app.services.delegation_guard import DelegationBlockedError, DelegationGuard
from app.services.wake_engine import (
    WakeDeliveryError,
    WakeEngine,
    WakeQueueFullError,
    WakeThrottledError,
)


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
    """构建 mock WakeEngine（CF3：模拟后台 worker 成功投递，触发 on_delivered 回调）."""
    engine = MagicMock(spec=WakeEngine)

    async def _fake_wake(
        source_thread_id="",
        source_agent_name="",
        result_summary="",
        ticket_id="",
        target_waker="",
        *,
        on_delivered=None,
        on_failed=None,
    ):
        # 模拟 worker 成功投递：回调推进 ledger → completed。
        if on_delivered is not None:
            await on_delivered("wake-run-id")
        return "delivery-id"

    engine.wake = AsyncMock(side_effect=_fake_wake)
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
    """真实投递失败（WakeDeliveryError）→ TASK failed（CF3：由 worker on_failed 回调上报）."""

    async def _fail_wake(*args, on_delivered=None, on_failed=None, **kwargs):
        # 模拟 worker 上游投递失败（非背压）。
        if on_failed is not None:
            await on_failed(WakeDeliveryError("delivery failed"))
        return "delivery-id"

    mock_wake_engine.wake = AsyncMock(side_effect=_fail_wake)

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
async def test_wake_throttled_keeps_task_done(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """CF4：本地背压/限速超时（WakeThrottledError）→ 不改 task.status、不覆盖
    result_summary（任务本身已 done 且可能已在群里汇报成功），仅 ledger=failed."""

    async def _throttle_wake(*args, on_delivered=None, on_failed=None, **kwargs):
        # 模拟 worker 限速重试预算耗尽（本地背压，非上游失败）。
        if on_failed is not None:
            await on_failed(WakeThrottledError("throttled"))
        return "delivery-id"

    mock_wake_engine.wake = AsyncMock(side_effect=_throttle_wake)

    await _create_parent_task(
        test_session_factory, "parent-task-id", "alice", "parent-thread"
    )

    async with test_session_factory() as session:
        child_task = Task(
            id="child-task-id",
            kind="async_delegate",
            ticket_id="child-ticket",
            parent_task_id="parent-task-id",
            executor="bob",
            status="done",
            input_text="child task",
            result_summary="真实交付摘要",
            thread_id="child-thread",
            run_id="child-run",
            created_by="alice",
        )
        session.add(child_task)
        session.add(
            DelegationLedger(
                ticket_id="child-ticket",
                source_task_id="parent-task-id",
                source_waker="alice",
                target_waker="bob",
                depth=1,
                path_json=json.dumps(["alice"]),
                status="running",
            )
        )
        await session.commit()

    await async_delegate_service.on_run_completed(child_task)

    # 任务终态不得被背压改写：仍为 done，result_summary 保留真实摘要
    async with test_session_factory() as session:
        task = (
            await session.execute(select(Task).where(Task.id == "child-task-id"))
        ).scalars().first()
        assert task.status == "done"
        assert task.result_summary == "真实交付摘要"

    # ledger 置 failed（结束本轮投递尝试，但不代表任务失败）
    async with test_session_factory() as session:
        ledger = (
            await session.execute(
                select(DelegationLedger).where(
                    DelegationLedger.ticket_id == "child-ticket"
                )
            )
        ).scalars().first()
        assert ledger.status == "failed"


@pytest.mark.asyncio
async def test_wake_queue_full_defers_without_touching_task(
    async_delegate_service, test_session_factory, mock_wake_engine
):
    """CF3：队列满（WakeQueueFullError）→ 入队失败但不改任务终态，ledger 保持 running."""

    async def _full_wake(*args, **kwargs):
        raise WakeQueueFullError("queue full")

    mock_wake_engine.wake = AsyncMock(side_effect=_full_wake)

    await _create_parent_task(
        test_session_factory, "parent-task-id", "alice", "parent-thread"
    )

    async with test_session_factory() as session:
        child_task = Task(
            id="child-task-id",
            kind="async_delegate",
            ticket_id="child-ticket",
            parent_task_id="parent-task-id",
            executor="bob",
            status="done",
            input_text="child task",
            result_summary="真实交付摘要",
            thread_id="child-thread",
            run_id="child-run",
            created_by="alice",
        )
        session.add(child_task)
        session.add(
            DelegationLedger(
                ticket_id="child-ticket",
                source_task_id="parent-task-id",
                source_waker="alice",
                target_waker="bob",
                depth=1,
                path_json=json.dumps(["alice"]),
                status="running",
            )
        )
        await session.commit()

    # 不得抛异常（背压被内部吸收）
    await async_delegate_service.on_run_completed(child_task)

    async with test_session_factory() as session:
        task = (
            await session.execute(select(Task).where(Task.id == "child-task-id"))
        ).scalars().first()
        assert task.status == "done"
        assert task.result_summary == "真实交付摘要"
        ledger = (
            await session.execute(
                select(DelegationLedger).where(
                    DelegationLedger.ticket_id == "child-ticket"
                )
            )
        ).scalars().first()
        # 待投递：保持 running（不被队列满误判为终态）
        assert ledger.status == "running"


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


# ------------------------------------------------------------------
# C2: 群汇报写库失败不得毒化调用方 session
# ------------------------------------------------------------------


class _FailingReportSessionFactory:
    """包装真实 session factory：任何提交【成员汇报】的 session 同时被塞入
    一条违反 NOT NULL 约束的记录，使真实 flush 失败 → session 进入
    pending-rollback 状态（复现生产中 commit 失败后的 session 污染）。

    旧实现复用调用方 session 且 except 只记日志不 rollback，主流程随后的
    execute/commit 会抛 PendingRollbackError → ledger 永停 running、唤醒永不投递。
    """

    def __init__(self, inner, target_conversation_id: str) -> None:
        self._inner = inner
        self._target = target_conversation_id
        self.report_commit_attempts = 0

    def __call__(self):
        session = self._inner()
        original_commit = session.commit

        async def _commit():
            has_report = any(
                isinstance(obj, ConversationMessage)
                and obj.conversation_id == self._target
                for obj in session.new
            )
            if has_report:
                self.report_commit_attempts += 1
                # 制造真实的 DB 失败（NOT NULL 约束），使 session 进入 pending-rollback
                session.add(ConversationMessage(conversation_id=None, role=None))
            await original_commit()

        session.commit = _commit
        return session


@pytest.mark.asyncio
async def test_group_report_commit_failure_does_not_poison_main_flow(
    test_session_factory, mock_deerflow, mock_wake_engine, delegation_guard
):
    """C2 回归：群汇报 commit 失败 → 主流程（ledger 推进 + 唤醒投递）仍须完成."""
    await _create_group_conversation(test_session_factory, "conv-c2")
    await _create_parent_task(
        test_session_factory, "parent-c2", "alice", "parent-thread-c2"
    )

    child_task = Task(
        id="child-c2",
        kind="async_delegate",
        ticket_id="ticket-c2",
        parent_task_id="parent-c2",
        group_id="default",
        conversation_id="conv-c2",
        executor="bob",
        status="done",
        input_text="写评审意见",
        result_summary="已完成初稿",
        thread_id="child-thread",
        run_id="child-run",
        created_by="alice",
    )
    async with test_session_factory() as session:
        session.add(child_task)
        session.add(
            DelegationLedger(
                ticket_id="ticket-c2",
                source_task_id="parent-c2",
                source_waker="alice",
                target_waker="bob",
                depth=1,
                path_json=json.dumps(["alice"]),
                status="running",
            )
        )
        await session.commit()

    factory = _FailingReportSessionFactory(test_session_factory, "conv-c2")
    service = AsyncDelegateService(
        db_session_factory=factory,
        deerflow_client=mock_deerflow,
        wake_engine=mock_wake_engine,
        delegation_guard=delegation_guard,
    )

    # 不得抛异常（旧实现：PendingRollbackError 冒泡到 SyncEngine 回调）
    await service.on_run_completed(child_task)

    # 确实触发了汇报写入失败（否则本用例无意义）
    assert factory.report_commit_attempts >= 1
    # 主流程：唤醒已投递
    mock_wake_engine.wake.assert_awaited_once()
    # 主流程：ledger 已推进到 completed
    async with test_session_factory() as session:
        ledger = (
            await session.execute(
                select(DelegationLedger).where(DelegationLedger.ticket_id == "ticket-c2")
            )
        ).scalars().first()
        assert ledger.status == "completed"
    # 汇报未写入（失败被隔离在独立短 session 内）
    assert await _group_messages(test_session_factory, "conv-c2") == []


@pytest.mark.asyncio
async def test_group_report_uses_independent_session(
    test_session_factory, mock_deerflow, mock_wake_engine, delegation_guard
):
    """C2 结构隔离：群汇报写入使用自开的短 session，不复用调用方 session."""
    await _create_group_conversation(test_session_factory, "conv-c2b")

    task = Task(
        id="task-c2b",
        kind="async_delegate",
        ticket_id="ticket-c2b",
        parent_task_id=None,
        conversation_id="conv-c2b",
        executor="bob",
        status="done",
        input_text="写小节",
        result_summary="完成",
        created_by="alice",
    )
    async with test_session_factory() as session:
        session.add(task)
        await session.commit()

    opened_sessions: list[AsyncSession] = []

    class _TrackingFactory:
        def __init__(self, inner):
            self._inner = inner

        def __call__(self):
            session = self._inner()
            opened_sessions.append(session)
            return session

    service = AsyncDelegateService(
        db_session_factory=_TrackingFactory(test_session_factory),
        deerflow_client=mock_deerflow,
        wake_engine=mock_wake_engine,
        delegation_guard=delegation_guard,
    )

    await service.on_run_completed(task)

    # 主流程 session + 汇报独立 session（至少 2 个）
    assert len(opened_sessions) >= 2
    # 汇报确实写入
    msgs = await _group_messages(test_session_factory, "conv-c2b")
    assert len(msgs) == 1


# ------------------------------------------------------------------
# S18: 自动 wake run 护栏（并发上限 + 速率上限 + 有界等待）
# ------------------------------------------------------------------


class _TrackingWakeClient:
    """记录 create_run 并发峰值与请求体的假 DeerFlow 客户端."""

    def __init__(self, delay: float = 0.02) -> None:
        self._delay = delay
        self.in_flight = 0
        self.peak = 0
        self.calls = 0
        self.bodies: list[dict] = []

    async def create_run(self, *, thread_id, body, idempotency_key=None):
        self.calls += 1
        self.bodies.append(body)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self._delay)
        finally:
            self.in_flight -= 1
        return {"run_id": f"wake-run-{self.calls}"}


async def _wake(engine: WakeEngine, ticket_id: str) -> str:
    """入队一条唤醒请求并**等待后台 worker 完成投递**（CF3）.

    wake() 已改为非阻塞入队，真实 create_run 在 worker 协程内完成。本
    辅助函数通过 on_delivered/on_failed 回调拿到投递结果：成功返回 run_id，
    失败（WakeThrottledError / WakeDeliveryError）则抛出——供测试断言。
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[str] = loop.create_future()

    async def _on_delivered(run_id: str) -> None:
        if not done.done():
            done.set_result(run_id)

    async def _on_failed(exc: Exception) -> None:
        if not done.done():
            done.set_exception(exc)

    await engine.start()
    await engine.wake(
        source_thread_id=f"thread-{ticket_id}",
        source_agent_name="alice",
        result_summary="结果",
        ticket_id=ticket_id,
        target_waker="bob",
        on_delivered=_on_delivered,
        on_failed=_on_failed,
    )
    return await done


@pytest.mark.asyncio
async def test_wake_engine_caps_concurrent_wake_runs():
    """批量任务同时终态 → 在途 wake run 创建数受 worker 池上限约束（CF3）."""
    client = _TrackingWakeClient(delay=0.02)
    engine = WakeEngine(client, max_concurrency=2, max_per_minute=0)
    try:
        run_ids = await asyncio.gather(*[_wake(engine, f"tk-{i}") for i in range(6)])

        assert client.calls == 6
        assert all(run_ids)
        # 并发上限由 worker 池规模（=2）实现：同时在途 create_run <= 2
        assert client.peak <= 2
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_wake_engine_rate_limits_burst():
    """速率护栏：窗口内超额时等待窗口滑出（有界等待，不丢投递）."""
    client = _TrackingWakeClient(delay=0.0)
    engine = WakeEngine(
        client,
        max_concurrency=4,
        max_per_minute=2,
        rate_window_seconds=0.2,
        throttle_wait_timeout_seconds=5.0,
    )
    try:
        started = time.monotonic()
        for i in range(3):
            await _wake(engine, f"tk-rate-{i}")
        elapsed = time.monotonic() - started

        assert client.calls == 3  # 未被丢弃
        assert elapsed >= 0.15  # 第 3 次被限速等待
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_wake_engine_throttle_timeout_raises_delivery_error():
    """有界等待超时且重试预算耗尽 → WakeThrottledError（本地背压，不改任务终态）."""
    client = _TrackingWakeClient(delay=0.0)
    engine = WakeEngine(
        client,
        max_concurrency=2,
        max_per_minute=1,
        rate_window_seconds=30.0,
        throttle_wait_timeout_seconds=0.05,
        # 测试聚焦“超时即上报”路径：关闭重排避免 5×1s 等待。
        throttle_retry_budget=0,
        throttle_retry_delay_seconds=0.0,
    )
    try:
        await _wake(engine, "tk-ok")
        with pytest.raises(WakeDeliveryError, match="throttled"):
            await _wake(engine, "tk-throttled")

        assert client.calls == 1  # 被护栏拦下，未拉起第二个 run
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_wake_engine_guardrail_defaults_from_settings(monkeypatch):
    """护栏阈值可配置：未显式传参时从 settings 读取."""
    import app.config as config_module

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(
            _env_file=None,
            wake_max_concurrency=3,
            wake_max_per_minute=7,
            wake_throttle_wait_timeout_seconds=11.0,
        ),
    )
    engine = WakeEngine(MagicMock())
    assert engine.max_concurrency == 3
    assert engine.max_per_minute == 7
    assert engine.throttle_wait_timeout_seconds == 11.0


@pytest.mark.asyncio
async def test_wake_run_recursion_budget_from_settings(monkeypatch):
    """wake run 的递归预算同样受 settings.recursion_limit 控制（成本护栏）."""
    import app.config as config_module

    monkeypatch.setattr(
        config_module, "_settings", Settings(_env_file=None, recursion_limit=42)
    )
    client = _TrackingWakeClient(delay=0.0)
    engine = WakeEngine(client, max_concurrency=2, max_per_minute=0)
    try:
        await _wake(engine, "tk-budget")

        assert client.bodies[0]["config"]["recursion_limit"] == 42
        assert client.bodies[0]["config"]["configurable"]["agent_name"] == "alice"
    finally:
        await engine.stop()


def test_delegation_depth_bound_is_documented():
    """委派链深度上界仍由 DelegationGuard 约束（wake 后 agent 可再委派）."""
    assert DelegationGuard.MAX_DEPTH == 3
    assert DelegationGuard.MAX_FANOUT_PER_RUN == 5
    assert DelegationGuard.MAX_FANOUT_PER_GROUP == 10
    # wake_engine 模块文档必须显式声明该上界，避免护栏语义丢失
    import app.services.wake_engine as wake_module

    doc = (wake_module.__doc__ or "") + (wake_module.WakeEngine.__doc__ or "")
    assert "MAX_DEPTH" in doc
