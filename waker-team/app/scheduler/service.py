"""调度服务：asyncio.Task 循环 + croniter 解析 + 触发入队."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.schedule import ScheduleDef, ScheduleRun
from app.scheduler.cron_utils import get_next_run

logger = logging.getLogger(__name__)


class SchedulerService:
    """调度服务：定时检查并触发到期调度."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        flow_engine=None,
        task_service_factory=None,
        interval: float = 30.0,
    ) -> None:
        self._db_factory = db_session_factory
        self._flow_engine = flow_engine
        self._task_service_factory = task_service_factory
        self._task: asyncio.Task | None = None
        self._running = False
        self._interval = interval

    async def start(self) -> None:
        """启动调度循环."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("SchedulerService started (interval=%.1fs)", self._interval)

    async def stop(self) -> None:
        """停止调度."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SchedulerService stopped")

    async def _loop(self) -> None:
        """主循环."""
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("SchedulerService tick error")
            await asyncio.sleep(self._interval)

    async def _tick(self) -> None:
        """单次检查：扫描所有 active 调度，触发到期的."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(ScheduleDef).where(ScheduleDef.status == "active")
            )
            schedules = result.scalars().all()
            now = datetime.now(UTC)

            for sched in schedules:
                # SQLite 返回 naive datetime，需要比较时统一处理
                next_run_at = sched.next_run_at
                if next_run_at is not None and next_run_at.tzinfo is None:
                    next_run_at = next_run_at.replace(tzinfo=UTC)

                if next_run_at is None or next_run_at > now:
                    continue

                # 检查 max_run_count
                if sched.max_run_count is not None and sched.run_count >= sched.max_run_count:
                    sched.status = "archived"
                    continue

                # 检查 end_date
                end_date = sched.end_date
                if end_date is not None and end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=UTC)
                if end_date is not None and now > end_date:
                    sched.status = "archived"
                    continue

                # 触发
                await self._trigger(sched, db, trigger_type="cron")

            await db.commit()

    async def _trigger(self, sched: ScheduleDef, db: AsyncSession, trigger_type: str = "cron") -> ScheduleRun:
        """触发一次调度，创建 ScheduleRun 记录."""
        now = datetime.now(UTC)

        # 创建执行记录
        run = ScheduleRun(
            schedule_def_id=sched.id,
            trigger_type=trigger_type,
            triggered_at=now,
            status="pending",
        )
        db.add(run)

        # 根据 target_type 尝试触发
        try:
            if sched.target_type == "task" and self._task_service_factory is not None:
                # 解析 target_input
                target_input = {}
                if sched.target_input:
                    try:
                        target_input = json.loads(sched.target_input)
                    except (json.JSONDecodeError, TypeError):
                        pass

                # 通过工厂获取与当前 session 绑定的 TaskService 并创建任务
                input_text = target_input.get("input_text", f"Scheduled trigger for {sched.name}")
                executor = sched.target_id
                task_service = self._task_service_factory(db)
                task = await task_service.create_task(
                    executor=executor,
                    input_text=input_text,
                    group_id=sched.group_id or "default",
                    created_by="scheduler",
                    kind="schedule",
                )
                run.task_id = task["id"]
                run.status = "running"

            elif sched.target_type == "flow" and self._flow_engine is not None:
                flow_run = await self._flow_engine.start(
                    flow_def_id=sched.target_id,
                    db=db,
                    created_by="scheduler",
                    trigger_type="schedule",
                )
                run.flow_run_id = flow_run.id
                run.status = "running"
            else:
                # 无对应服务，标记为 pending
                run.status = "pending"

        except Exception as e:
            run.status = "failed"
            run.error_message = str(e)
            logger.exception("Failed to trigger schedule %s", sched.id)

        # 更新调度状态
        sched.last_run_at = now
        sched.run_count += 1
        sched.next_run_at = get_next_run(sched.cron_expression, now)
        sched.updated_at = now

        return run

    async def manual_trigger(self, schedule_id: str, db: AsyncSession) -> ScheduleRun:
        """手动触发一次调度."""
        result = await db.execute(
            select(ScheduleDef).where(ScheduleDef.id == schedule_id)
        )
        sched = result.scalars().first()
        if sched is None:
            raise ValueError(f"Schedule {schedule_id} not found")
        if sched.status == "archived":
            raise ValueError(f"Schedule {schedule_id} is archived")

        return await self._trigger(sched, db, trigger_type="manual")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create(self, db: AsyncSession, data: dict) -> ScheduleDef:
        """创建调度定义."""
        now = datetime.now(UTC)
        target_input_json = None
        if data.get("target_input") is not None:
            target_input_json = json.dumps(data["target_input"])

        sched = ScheduleDef(
            name=data["name"],
            description=data.get("description"),
            group_id=data["group_id"],
            cron_expression=data["cron_expression"],
            target_type=data["target_type"],
            target_id=data["target_id"],
            target_input=target_input_json,
            max_run_count=data.get("max_run_count"),
            end_date=datetime.fromisoformat(data["end_date"]) if data.get("end_date") else None,
            status="active",
            next_run_at=get_next_run(data["cron_expression"], now),
            created_at=now,
            updated_at=now,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched

    async def list(self, db: AsyncSession, group_id: str | None = None) -> list[ScheduleDef]:
        """列表查询."""
        stmt = select(ScheduleDef).order_by(ScheduleDef.created_at.desc())
        if group_id is not None:
            stmt = stmt.where(ScheduleDef.group_id == group_id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, db: AsyncSession, schedule_id: str) -> ScheduleDef | None:
        """获取单个."""
        result = await db.execute(
            select(ScheduleDef).where(ScheduleDef.id == schedule_id)
        )
        return result.scalars().first()

    async def update(self, db: AsyncSession, schedule_id: str, data: dict) -> ScheduleDef | None:
        """更新调度定义."""
        sched = await self.get(db, schedule_id)
        if sched is None:
            return None

        if data.get("name") is not None:
            sched.name = data["name"]
        if data.get("description") is not None:
            sched.description = data["description"]
        if data.get("cron_expression") is not None:
            sched.cron_expression = data["cron_expression"]
            sched.next_run_at = get_next_run(data["cron_expression"])
        if data.get("max_run_count") is not None:
            sched.max_run_count = data["max_run_count"]
        if data.get("end_date") is not None:
            sched.end_date = datetime.fromisoformat(data["end_date"])

        sched.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(sched)
        return sched

    async def delete(self, db: AsyncSession, schedule_id: str) -> bool:
        """删除调度定义（连同执行历史，避免外键约束阻止删除）."""
        sched = await self.get(db, schedule_id)
        if sched is None:
            return False
        await db.execute(delete(ScheduleRun).where(ScheduleRun.schedule_def_id == schedule_id))
        await db.delete(sched)
        await db.commit()
        return True

    async def pause(self, db: AsyncSession, schedule_id: str) -> ScheduleDef | None:
        """暂停调度."""
        sched = await self.get(db, schedule_id)
        if sched is None:
            return None
        sched.status = "paused"
        sched.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(sched)
        return sched

    async def resume(self, db: AsyncSession, schedule_id: str) -> ScheduleDef | None:
        """恢复调度."""
        sched = await self.get(db, schedule_id)
        if sched is None:
            return None
        sched.status = "active"
        sched.next_run_at = get_next_run(sched.cron_expression)
        sched.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(sched)
        return sched

    async def list_runs(self, db: AsyncSession, schedule_id: str) -> list[ScheduleRun]:
        """列出执行历史."""
        result = await db.execute(
            select(ScheduleRun)
            .where(ScheduleRun.schedule_def_id == schedule_id)
            .order_by(ScheduleRun.triggered_at.desc())
        )
        return list(result.scalars().all())
