"""调度器单元测试：cron 工具 + SchedulerService."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.schedule import ScheduleDef, ScheduleRun
from app.scheduler.cron_utils import get_next_run, should_run, validate_cron
from app.scheduler.service import SchedulerService


# ------------------------------------------------------------------
# cron_utils 测试
# ------------------------------------------------------------------


class TestValidateCron:
    """validate_cron 测试."""

    def test_valid_cron_every_minute(self):
        """每分钟."""
        assert validate_cron("* * * * *") is True

    def test_valid_cron_hourly(self):
        """每小时."""
        assert validate_cron("0 * * * *") is True

    def test_valid_cron_daily(self):
        """每天 9:00."""
        assert validate_cron("0 9 * * *") is True

    def test_valid_cron_weekly(self):
        """每周一 8:30."""
        assert validate_cron("30 8 * * 1") is True

    def test_valid_cron_complex(self):
        """复杂表达式."""
        assert validate_cron("*/15 9-17 * * 1-5") is True

    def test_invalid_cron_empty(self):
        """空字符串."""
        assert validate_cron("") is False

    def test_invalid_cron_garbage(self):
        """无效表达式."""
        assert validate_cron("not a cron") is False

    def test_invalid_cron_too_many_fields(self):
        """字段过多."""
        assert validate_cron("1 2 3 4 5 6 7") is False


class TestGetNextRun:
    """get_next_run 测试."""

    def test_next_run_after_now(self):
        """下次运行时间在当前时间之后."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        next_run = get_next_run("0 13 * * *", now)  # 今天 13:00
        assert next_run == datetime(2024, 1, 1, 13, 0, 0)

    def test_next_run_daily(self):
        """每天表达式."""
        now = datetime(2024, 1, 1, 10, 0, 0)
        next_run = get_next_run("0 9 * * *", now)  # 已过 9:00，下次是明天
        assert next_run == datetime(2024, 1, 2, 9, 0, 0)

    def test_next_run_every_15_min(self):
        """每 15 分钟."""
        now = datetime(2024, 1, 1, 12, 7, 0)
        next_run = get_next_run("*/15 * * * *", now)
        assert next_run == datetime(2024, 1, 1, 12, 15, 0)


class TestShouldRun:
    """should_run 测试."""

    def test_should_run_when_past_due(self):
        """已过触发时间，应该运行."""
        last_run = datetime(2024, 1, 1, 10, 0, 0)
        now = datetime(2024, 1, 1, 11, 30, 0)
        # 每天 11:00 触发
        assert should_run("0 11 * * *", last_run, now) is True

    def test_should_not_run_when_not_due(self):
        """未到触发时间，不应运行."""
        last_run = datetime(2024, 1, 1, 10, 0, 0)
        now = datetime(2024, 1, 1, 10, 30, 0)
        # 每天 11:00 触发
        assert should_run("0 11 * * *", last_run, now) is False


# ------------------------------------------------------------------
# SchedulerService 测试
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


class TestSchedulerService:
    """SchedulerService 测试."""

    @pytest.mark.asyncio
    async def test_tick_triggers_due_schedule(self, test_session_factory):
        """_tick 触发到期的调度."""
        # 预建一个到期的调度
        now = datetime.now(UTC)
        past_time = now - timedelta(hours=1)

        async with test_session_factory() as db:
            sched = ScheduleDef(
                name="test-sched",
                group_id="g1",
                cron_expression="* * * * *",  # 每分钟
                target_type="task",
                target_id="worker-1",
                status="active",
                next_run_at=past_time,  # 已到期
                run_count=0,
            )
            db.add(sched)
            await db.commit()

        # 创建 service（无 task_service，只测试触发逻辑）
        service = SchedulerService(test_session_factory)
        await service._tick()

        # 验证：run_count 增加，last_run_at 更新
        async with test_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(select(ScheduleDef))
            updated = result.scalars().first()
            assert updated.run_count == 1
            assert updated.last_run_at is not None
            # next_run_at 从 SQLite 读回是 naive，统一比较
            next_run = updated.next_run_at
            if next_run.tzinfo is None:
                next_run = next_run.replace(tzinfo=UTC)
            assert next_run > past_time

    @pytest.mark.asyncio
    async def test_max_run_count_archives(self, test_session_factory):
        """max_run_count 到期自动归档."""
        now = datetime.now(UTC)
        past_time = now - timedelta(hours=1)

        async with test_session_factory() as db:
            sched = ScheduleDef(
                name="max-run-sched",
                group_id="g1",
                cron_expression="* * * * *",
                target_type="task",
                target_id="worker-1",
                status="active",
                next_run_at=past_time,
                run_count=3,
                max_run_count=3,  # 已达上限
            )
            db.add(sched)
            await db.commit()

        service = SchedulerService(test_session_factory)
        await service._tick()

        # 验证：状态变为 archived
        async with test_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(select(ScheduleDef))
            updated = result.scalars().first()
            assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_end_date_archives(self, test_session_factory):
        """end_date 过期自动归档."""
        now = datetime.now(UTC)
        past_time = now - timedelta(hours=1)
        past_end = now - timedelta(days=1)

        async with test_session_factory() as db:
            sched = ScheduleDef(
                name="end-date-sched",
                group_id="g1",
                cron_expression="* * * * *",
                target_type="task",
                target_id="worker-1",
                status="active",
                next_run_at=past_time,
                run_count=0,
                end_date=past_end,  # 已过期
            )
            db.add(sched)
            await db.commit()

        service = SchedulerService(test_session_factory)
        await service._tick()

        # 验证：状态变为 archived
        async with test_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(select(ScheduleDef))
            updated = result.scalars().first()
            assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_manual_trigger(self, test_session_factory):
        """手动触发创建执行记录."""
        async with test_session_factory() as db:
            sched = ScheduleDef(
                name="manual-sched",
                group_id="g1",
                cron_expression="0 9 * * *",
                target_type="task",
                target_id="worker-1",
                status="active",
                run_count=0,
            )
            db.add(sched)
            await db.commit()
            sched_id = sched.id

        service = SchedulerService(test_session_factory)
        async with test_session_factory() as db:
            run = await service.manual_trigger(sched_id, db)
            await db.commit()
            assert run.schedule_def_id == sched_id
            assert run.trigger_type == "manual"
            assert run.status == "pending"

    @pytest.mark.asyncio
    async def test_manual_trigger_not_found(self, test_session_factory):
        """手动触发不存在的调度抛出异常."""
        service = SchedulerService(test_session_factory)
        async with test_session_factory() as db:
            with pytest.raises(ValueError, match="not found"):
                await service.manual_trigger("nonexistent-id", db)

    @pytest.mark.asyncio
    async def test_crud_operations(self, test_session_factory):
        """CRUD 基本操作."""
        service = SchedulerService(test_session_factory)

        # Create
        async with test_session_factory() as db:
            sched = await service.create(db, {
                "name": "crud-test",
                "group_id": "g1",
                "cron_expression": "0 10 * * *",
                "target_type": "task",
                "target_id": "worker-1",
            })
            assert sched.id is not None
            assert sched.name == "crud-test"
            sched_id = sched.id

        # Read
        async with test_session_factory() as db:
            fetched = await service.get(db, sched_id)
            assert fetched is not None
            assert fetched.name == "crud-test"

        # List
        async with test_session_factory() as db:
            all_scheds = await service.list(db)
            assert len(all_scheds) == 1

        # Update
        async with test_session_factory() as db:
            updated = await service.update(db, sched_id, {"name": "updated-name"})
            assert updated.name == "updated-name"

        # Delete
        async with test_session_factory() as db:
            deleted = await service.delete(db, sched_id)
            assert deleted is True

        async with test_session_factory() as db:
            fetched = await service.get(db, sched_id)
            assert fetched is None

    @pytest.mark.asyncio
    async def test_pause_resume(self, test_session_factory):
        """暂停和恢复."""
        service = SchedulerService(test_session_factory)

        async with test_session_factory() as db:
            sched = await service.create(db, {
                "name": "pause-test",
                "group_id": "g1",
                "cron_expression": "0 10 * * *",
                "target_type": "task",
                "target_id": "worker-1",
            })
            sched_id = sched.id

        # Pause
        async with test_session_factory() as db:
            paused = await service.pause(db, sched_id)
            assert paused.status == "paused"

        # Resume
        async with test_session_factory() as db:
            resumed = await service.resume(db, sched_id)
            assert resumed.status == "active"
            assert resumed.next_run_at is not None

    @pytest.mark.asyncio
    async def test_list_runs(self, test_session_factory):
        """列出执行历史."""
        service = SchedulerService(test_session_factory)

        async with test_session_factory() as db:
            sched = await service.create(db, {
                "name": "runs-test",
                "group_id": "g1",
                "cron_expression": "0 10 * * *",
                "target_type": "task",
                "target_id": "worker-1",
            })
            sched_id = sched.id

        # 手动触发两次
        async with test_session_factory() as db:
            await service.manual_trigger(sched_id, db)
            await db.commit()

        async with test_session_factory() as db:
            await service.manual_trigger(sched_id, db)
            await db.commit()

        # 查询历史
        async with test_session_factory() as db:
            runs = await service.list_runs(db, sched_id)
            assert len(runs) == 2
            assert all(r.trigger_type == "manual" for r in runs)
