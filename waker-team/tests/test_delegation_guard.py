"""DelegationGuard 单元测试."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.delegation import DelegationLedger
from app.models.group import Group, GroupMember
from app.models.task import Task
from app.models.waker import Waker
from app.services.delegation_guard import DelegationBlockedError, DelegationGuard


@pytest.fixture
async def db_session():
    """创建测试用内存数据库."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def guard():
    return DelegationGuard()


async def _create_ledger(
    session: AsyncSession,
    ticket_id: str,
    source_waker: str,
    target_waker: str,
    depth: int,
    path: list[str],
    status: str = "running",
    group_id: str | None = None,
    source_task_id: str | None = None,
) -> DelegationLedger:
    """辅助函数：创建 ledger 记录."""
    ledger = DelegationLedger(
        ticket_id=ticket_id,
        source_task_id=source_task_id,
        source_waker=source_waker,
        target_waker=target_waker,
        group_id=group_id,
        depth=depth,
        path_json=json.dumps(path),
        status=status,
    )
    session.add(ledger)
    await session.commit()
    return ledger


# ------------------------------------------------------------------
# 深度限制测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_limit(db_session, guard):
    """深度上限 3：当前深度=3 时拒绝."""
    # 创建深度为 3 的 ledger（已达上限）
    await _create_ledger(
        db_session,
        ticket_id="parent-ticket",
        source_waker="A",
        target_waker="B",
        depth=3,
        path=["X", "Y", "A"],
    )

    # 尝试从 parent-ticket 继续委派，应该被拒绝
    with pytest.raises(DelegationBlockedError) as exc_info:
        await guard.check_before_submit(
            db=db_session,
            source_waker="B",
            target_waker="C",
            group_id=None,
            source_task_id="parent-ticket",
        )
    assert "Depth limit" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_depth_allows_up_to_limit(db_session, guard):
    """深度=2 时允许继续委派."""
    await _create_ledger(
        db_session,
        ticket_id="parent-ticket",
        source_waker="A",
        target_waker="B",
        depth=2,
        path=["X", "A"],
    )

    # 深度=2 < 3，应该通过
    await guard.check_before_submit(
        db=db_session,
        source_waker="B",
        target_waker="C",
        group_id=None,
        source_task_id="parent-ticket",
    )


# ------------------------------------------------------------------
# 环路检测测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_detection(db_session, guard):
    """A→B→C→A 环路检测."""
    await _create_ledger(
        db_session,
        ticket_id="parent-ticket",
        source_waker="C",
        target_waker="A",  # 目标已在链上
        depth=3,
        path=["A", "B", "C"],
    )

    with pytest.raises(DelegationBlockedError) as exc_info:
        await guard.check_before_submit(
            db=db_session,
            source_waker="A",
            target_waker="A",  # 环路：A 已在 path 中
            group_id=None,
            source_task_id="parent-ticket",
        )
    assert "Loop detected" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_no_loop_allows(db_session, guard):
    """无环路时允许通过."""
    await _create_ledger(
        db_session,
        ticket_id="parent-ticket",
        source_waker="A",
        target_waker="B",
        depth=1,
        path=["A"],
    )

    # C 不在 path 中，应该通过
    await guard.check_before_submit(
        db=db_session,
        source_waker="B",
        target_waker="C",
        group_id=None,
        source_task_id="parent-ticket",
    )


# ------------------------------------------------------------------
# 扇出限制测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fanout_limit(db_session, guard):
    """单 run 扇出上限 5."""
    # 创建 5 个已存在的子委派
    for i in range(5):
        await _create_ledger(
            db_session,
            ticket_id=f"child-{i}",
            source_waker="A",
            target_waker=f"target-{i}",
            depth=1,
            path=["A"],
            status="running",
            source_task_id="parent-ticket",
        )

    # 第 6 个应该被拒绝
    with pytest.raises(DelegationBlockedError) as exc_info:
        await guard.check_before_submit(
            db=db_session,
            source_waker="A",
            target_waker="target-5",
            group_id=None,
            source_task_id="parent-ticket",
        )
    assert "Fan-out limit" in str(exc_info.value.reason)


@pytest.mark.asyncio
async def test_fanout_allows_under_limit(db_session, guard):
    """扇出 < 5 时允许."""
    for i in range(3):
        await _create_ledger(
            db_session,
            ticket_id=f"child-{i}",
            source_waker="A",
            target_waker=f"target-{i}",
            depth=1,
            path=["A"],
            status="running",
            source_task_id="parent-ticket",
        )

    # 3 < 5，应该通过
    await guard.check_before_submit(
        db=db_session,
        source_waker="A",
        target_waker="target-3",
        group_id=None,
        source_task_id="parent-ticket",
    )


# ------------------------------------------------------------------
# 组级扇出限制测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_fanout_limit(db_session, guard):
    """组级扇出上限 10."""
    for i in range(10):
        await _create_ledger(
            db_session,
            ticket_id=f"group-child-{i}",
            source_waker="A",
            target_waker=f"target-{i}",
            depth=1,
            path=["A"],
            status="running",
            group_id="group-1",
        )

    # 第 11 个应该被拒绝
    with pytest.raises(DelegationBlockedError) as exc_info:
        await guard.check_before_submit(
            db=db_session,
            source_waker="A",
            target_waker="target-10",
            group_id="group-1",
            source_task_id="some-parent",
        )
    assert "Group fan-out" in str(exc_info.value.reason)


# ------------------------------------------------------------------
# 同组/跨组测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_group_allowed(db_session, guard):
    """同组委派通过（未达上限时）."""
    await _create_ledger(
        db_session,
        ticket_id="parent-ticket",
        source_waker="A",
        target_waker="B",
        depth=1,
        path=["A"],
        status="running",
        group_id="group-1",
    )

    # 同组，只有 1 个进行中，未达上限
    await guard.check_before_submit(
        db=db_session,
        source_waker="B",
        target_waker="C",
        group_id="group-1",
        source_task_id="parent-ticket",
    )


# ------------------------------------------------------------------
# 顶层委派（无 source_task_id）测试
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_top_level_delegation_no_checks(db_session, guard):
    """顶层委派（source_task_id=None）跳过所有检查."""
    # 即使没有任何 ledger 记录，也应该通过
    await guard.check_before_submit(
        db=db_session,
        source_waker="A",
        target_waker="B",
        group_id="group-1",
        source_task_id=None,
    )


# ------------------------------------------------------------------
# build_path 测试
# ------------------------------------------------------------------


def test_build_path_empty_parent(guard):
    """空父路径时，只包含 source."""
    path = guard.build_path(None, "A")
    assert path == ["A"]


def test_build_path_with_parent(guard):
    """有父路径时，追加 source."""
    path = guard.build_path(["X", "Y"], "A")
    assert path == ["X", "Y", "A"]


def test_build_path_does_not_mutate_parent(guard):
    """build_path 不修改父路径."""
    parent = ["X", "Y"]
    _ = guard.build_path(parent, "A")
    assert parent == ["X", "Y"]  # 未被修改
