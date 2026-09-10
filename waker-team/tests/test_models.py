"""数据模型结构验证测试."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
# 确保所有模型被导入
import app.models  # noqa: F401


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


# ------------------------------------------------------------------
# 所有表已创建
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_tables_created(test_db_engine):
    """验证所有新表（groups, group_members, delegation_ledger, flow_defs, flow_runs, node_runs）存在."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        # 使用 run_sync 在同步上下文中执行 inspect
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())

    expected_tables = {
        "groups",
        "group_members",
        "delegation_ledger",
        "flow_defs",
        "flow_runs",
        "node_runs",
        # P0 已有表
        "wakers",
        "tasks",
        "audit_logs",
    }
    for table in expected_tables:
        assert table in tables, f"Table '{table}' not found in database"


# ------------------------------------------------------------------
# Task 新增字段
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_new_fields(test_db_engine):
    """验证 task 表新增字段（group_id, ticket_id, parent_task_id, kind）."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("tasks")]
        )

    expected_fields = ["group_id", "ticket_id", "parent_task_id", "kind"]
    for field in expected_fields:
        assert field in columns, f"Field '{field}' not found in tasks table"


# ------------------------------------------------------------------
# Group 表字段验证
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_table_columns(test_db_engine):
    """验证 groups 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("groups")]
        )

    expected = ["id", "name", "leader_waker_id", "project_id", "created_at", "updated_at"]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in groups table"


# ------------------------------------------------------------------
# GroupMember 表字段验证
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_member_table_columns(test_db_engine):
    """验证 group_members 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("group_members")]
        )

    expected = ["group_id", "waker_id", "role", "joined_at"]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in group_members table"


# ------------------------------------------------------------------
# DelegationLedger 表字段验证
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegation_ledger_columns(test_db_engine):
    """验证 delegation_ledger 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("delegation_ledger")]
        )

    expected = [
        "id", "ticket_id", "source_task_id", "source_waker",
        "target_waker", "group_id", "depth", "path_json", "status", "created_at",
    ]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in delegation_ledger table"


# ------------------------------------------------------------------
# Flow 表字段验证
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_defs_columns(test_db_engine):
    """验证 flow_defs 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("flow_defs")]
        )

    expected = ["id", "name", "group_id", "definition_json", "status", "created_at", "updated_at"]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in flow_defs table"


@pytest.mark.asyncio
async def test_flow_runs_columns(test_db_engine):
    """验证 flow_runs 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("flow_runs")]
        )

    expected = ["id", "flow_def_id", "group_id", "status", "started_at", "completed_at"]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in flow_runs table"


@pytest.mark.asyncio
async def test_node_runs_columns(test_db_engine):
    """验证 node_runs 表字段."""
    from sqlalchemy import inspect

    async with test_db_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda c: [col["name"] for col in inspect(c).get_columns("node_runs")]
        )

    expected = [
        "id", "flow_run_id", "node_id", "node_type",
        "status", "task_id", "started_at", "completed_at",
    ]
    for field in expected:
        assert field in columns, f"Field '{field}' not found in node_runs table"
