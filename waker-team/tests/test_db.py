import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, create_engine, create_session_factory
from app.models.audit import AuditLog
from app.models.task import Task
from app.models.waker import Waker


@pytest.fixture
async def db_engine(tmp_path):
    """独立临时数据库文件的引擎.

    必须显式传入 tmp_path 下的文件 URL：默认 settings.database_url 指向
    真实业务库 waker_team.db，绝不能用于测试（否则 teardown 会清空真实数据）。
    """
    db_file = tmp_path / "test_db.sqlite3"
    engine = create_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    session_factory = create_session_factory(db_engine)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_create_engine_refuses_default_url_in_pytest(db_engine):
    """防呆：pytest 环境下无参 create_engine() 必须报错.

    它指向真实业务库 waker_team.db，历史上曾因测试 drop_all 清空生产数据。
    """
    with pytest.raises(RuntimeError, match="Refusing to open the default database"):
        create_engine()


@pytest.mark.asyncio
async def test_create_all_is_idempotent(db_engine):
    """create_all 可重入，不报错。"""
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_waker_crud(db_session: AsyncSession):
    waker = Waker(name="test-agent", description="Test agent", deer_user="test-user")
    db_session.add(waker)
    await db_session.commit()

    result = await db_session.execute(select(Waker).where(Waker.name == "test-agent"))
    fetched = result.scalar_one()
    assert fetched.name == "test-agent"
    assert fetched.description == "Test agent"
    assert fetched.enabled is True

    fetched.description = "Updated"
    await db_session.commit()

    result = await db_session.execute(select(Waker).where(Waker.name == "test-agent"))
    updated = result.scalar_one()
    assert updated.description == "Updated"


@pytest.mark.asyncio
async def test_task_crud(db_session: AsyncSession):
    task = Task(
        id="task-001",
        kind="manual",
        executor="test-agent",
        status="pending",
        input_text="Do something",
    )
    db_session.add(task)
    await db_session.commit()

    result = await db_session.execute(select(Task).where(Task.id == "task-001"))
    fetched = result.scalar_one()
    assert fetched.kind == "manual"
    assert fetched.status == "pending"
    assert fetched.executor == "test-agent"

    fetched.status = "done"
    fetched.result_summary = "Completed"
    await db_session.commit()

    result = await db_session.execute(select(Task).where(Task.id == "task-001"))
    updated = result.scalar_one()
    assert updated.status == "done"
    assert updated.result_summary == "Completed"


@pytest.mark.asyncio
async def test_audit_log_write(db_session: AsyncSession):
    log = AuditLog(
        action="waker.create",
        actor="system",
        target="test-agent",
        detail='{"reason": "initial setup"}',
    )
    db_session.add(log)
    await db_session.commit()

    result = await db_session.execute(select(AuditLog).where(AuditLog.action == "waker.create"))
    fetched = result.scalar_one()
    assert fetched.actor == "system"
    assert fetched.target == "test-agent"
    assert "initial setup" in fetched.detail
