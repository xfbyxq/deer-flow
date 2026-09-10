"""Flow 时间线服务测试 — 覆盖 7 种时间线场景."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.models.task import Task
from app.services.flow_timeline_service import FlowTimelineService


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
def timeline_service():
    """构建 FlowTimelineService."""
    return FlowTimelineService()


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


async def _create_flow_run(
    session: AsyncSession,
    flow_def: FlowDef,
    status: str = "running",
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    failure_reason: str | None = None,
) -> FlowRun:
    """创建测试用 FlowRun."""
    flow_run = FlowRun(
        id=str(uuid.uuid4()),
        flow_def_id=flow_def.id,
        group_id=flow_def.group_id,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
        completed_at=completed_at,
        failure_reason=failure_reason,
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
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error_message: str | None = None,
    retry_count: int = 0,
    task_id: str | None = None,
    input_json: str | None = None,
    outcome_json: str | None = None,
    parent_node_run_id: str | None = None,
) -> NodeRun:
    """创建 NodeRun."""
    node_run = NodeRun(
        id=str(uuid.uuid4()),
        flow_run_id=flow_run.id,
        node_id=node_key,
        node_key=node_key,
        node_type=node_type,
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        error_message=error_message,
        retry_count=retry_count,
        task_id=task_id,
        input_json=input_json or json.dumps({"key": node_key, "type": node_type}),
        outcome_json=outcome_json,
        parent_node_run_id=parent_node_run_id,
    )
    session.add(node_run)
    await session.commit()
    await session.refresh(node_run)
    return node_run


async def _create_task(
    session: AsyncSession,
    thread_id: str | None = None,
    run_id: str | None = None,
    result_summary: str | None = None,
    status: str = "done",
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
        result_summary=result_summary,
        idempotency_key=str(uuid.uuid4()),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task


# ------------------------------------------------------------------
# 1. 空 Flow 的时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_flow_timeline(timeline_service, test_session_factory):
    """空 Flow（无节点）的时间线."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {"nodes": []})
        flow_run = await _create_flow_run(session, flow_def, status="completed")

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert timeline is not None
        assert timeline["flow_run"]["id"] == flow_run.id
        assert timeline["flow_run"]["status"] == "completed"
        assert timeline["nodes"] == []
        assert timeline["current_node"] is None


# ------------------------------------------------------------------
# 2. 单节点 Flow 的时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_node_timeline(timeline_service, test_session_factory):
    """单节点 Flow 的时间线."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [{"key": "step1", "type": "condition", "depends_on": []}],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(
            session, flow_def, status="completed",
            started_at=now, completed_at=now + timedelta(seconds=5),
        )
        await _create_node_run(
            session, flow_run, "step1", "condition",
            status="completed",
            started_at=now, completed_at=now + timedelta(seconds=2),
            outcome_json=json.dumps({"result": True}),
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 1
        node = timeline["nodes"][0]
        assert node["node_key"] == "step1"
        assert node["type"] == "condition"
        assert node["status"] == "completed"
        assert node["duration_seconds"] == 2.0
        assert node["output"] == {"result": True}
        assert node["error_message"] is None
        assert node["retry_count"] == 0
        assert timeline["total_duration_seconds"] == 5.0
        assert timeline["current_node"] is None


# ------------------------------------------------------------------
# 3. 多节点串行 Flow 的时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serial_flow_timeline(timeline_service, test_session_factory):
    """多节点串行 Flow 的时间线 — 节点按 started_at 排序."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "step1", "type": "condition", "depends_on": []},
                {"key": "step2", "type": "notify", "depends_on": ["step1"]},
                {"key": "step3", "type": "human_review", "depends_on": ["step2"]},
            ],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(session, flow_def, status="running", started_at=now)

        await _create_node_run(
            session, flow_run, "step1", "condition",
            status="completed",
            started_at=now, completed_at=now + timedelta(seconds=1),
        )
        await _create_node_run(
            session, flow_run, "step2", "notify",
            status="completed",
            started_at=now + timedelta(seconds=1), completed_at=now + timedelta(seconds=3),
        )
        await _create_node_run(
            session, flow_run, "step3", "human_review",
            status="waiting_review",
            started_at=now + timedelta(seconds=3),
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 3
        # 验证排序
        assert timeline["nodes"][0]["node_key"] == "step1"
        assert timeline["nodes"][1]["node_key"] == "step2"
        assert timeline["nodes"][2]["node_key"] == "step3"
        # 当前节点
        assert timeline["current_node"] == "step3"
        # step3 无 completed_at，无 duration
        assert timeline["nodes"][2]["completed_at"] is None
        assert timeline["nodes"][2]["duration_seconds"] is None


# ------------------------------------------------------------------
# 4. leader_plan 扇出 Flow 的时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leader_plan_fanout_timeline(timeline_service, test_session_factory):
    """leader_plan 扇出子任务的时间线."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "plan", "type": "leader_plan", "depends_on": []},
            ],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(session, flow_def, status="running", started_at=now)

        # plan 节点
        plan_node = await _create_node_run(
            session, flow_run, "plan", "leader_plan",
            status="completed",
            started_at=now, completed_at=now + timedelta(seconds=5),
            outcome_json=json.dumps({"subtasks": 3}),
        )

        # 扇出的子节点（parent_node_run_id 指向 plan）
        for i, waker in enumerate(["alice", "bob", "charlie"]):
            await _create_node_run(
                session, flow_run, f"plan_sub_{waker}", "waker_task",
                status="completed" if i < 2 else "running",
                started_at=now + timedelta(seconds=5 + i),
                completed_at=now + timedelta(seconds=10 + i) if i < 2 else None,
                parent_node_run_id=plan_node.id,
                input_json=json.dumps({"waker": waker, "instruction": f"task for {waker}"}),
            )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 4  # plan + 3 sub nodes
        # plan 节点
        assert timeline["nodes"][0]["node_key"] == "plan"
        assert timeline["nodes"][0]["type"] == "leader_plan"
        # 子节点 input 包含 waker
        sub_nodes = [n for n in timeline["nodes"] if n["node_key"].startswith("plan_sub_")]
        assert len(sub_nodes) == 3
        wakers = {n["input"]["waker"] for n in sub_nodes}
        assert wakers == {"alice", "bob", "charlie"}
        # 当前运行节点
        assert timeline["current_node"] == "plan_sub_charlie"


# ------------------------------------------------------------------
# 5. 包含 human_review 的 Flow 时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_human_review_timeline(timeline_service, test_session_factory):
    """包含 human_review 的 Flow 时间线."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [
                {"key": "task1", "type": "waker_task", "depends_on": []},
                {"key": "review1", "type": "human_review", "depends_on": ["task1"]},
            ],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(session, flow_def, status="running", started_at=now)

        await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="completed",
            started_at=now, completed_at=now + timedelta(seconds=10),
        )
        await _create_node_run(
            session, flow_run, "review1", "human_review",
            status="waiting_review",
            started_at=now + timedelta(seconds=10),
            input_json=json.dumps({"checklist": ["verify data"], "timeout_hours": 48}),
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 2
        review_node = timeline["nodes"][1]
        assert review_node["node_key"] == "review1"
        assert review_node["type"] == "human_review"
        assert review_node["status"] == "waiting_review"
        assert review_node["input"]["checklist"] == ["verify data"]
        assert timeline["current_node"] == "review1"


# ------------------------------------------------------------------
# 6. 失败节点的时间线（含 error_message）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_node_timeline(timeline_service, test_session_factory):
    """失败节点的时间线 — 包含 error_message."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [{"key": "task1", "type": "waker_task", "depends_on": []}],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(
            session, flow_def, status="failed",
            started_at=now, completed_at=now + timedelta(seconds=15),
            failure_reason="Node task1 failed",
        )
        await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="failed",
            started_at=now, completed_at=now + timedelta(seconds=15),
            error_message="DeerFlow API returned error: timeout",
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert timeline["flow_run"]["status"] == "failed"
        assert timeline["flow_run"]["failure_reason"] == "Node task1 failed"
        assert len(timeline["nodes"]) == 1
        node = timeline["nodes"][0]
        assert node["status"] == "failed"
        assert node["error_message"] == "DeerFlow API returned error: timeout"
        assert node["duration_seconds"] == 15.0


# ------------------------------------------------------------------
# 7. 重跑节点的时间线（retry_count > 0）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rerun_node_timeline(timeline_service, test_session_factory):
    """重跑节点的时间线 — retry_count > 0."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [{"key": "task1", "type": "waker_task", "depends_on": []}],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(session, flow_def, status="running", started_at=now)

        # 原始节点（已失败）
        await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="failed",
            started_at=now, completed_at=now + timedelta(seconds=10),
            error_message="first attempt failed",
            retry_count=0,
        )
        # 重跑节点
        await _create_node_run(
            session, flow_run, "task1_retry_1", "waker_task",
            status="completed",
            started_at=now + timedelta(seconds=15),
            completed_at=now + timedelta(seconds=25),
            retry_count=1,
            outcome_json=json.dumps({"status": "done"}),
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 2
        # 原始节点
        assert timeline["nodes"][0]["retry_count"] == 0
        assert timeline["nodes"][0]["error_message"] == "first attempt failed"
        # 重跑节点
        assert timeline["nodes"][1]["retry_count"] == 1
        assert timeline["nodes"][1]["status"] == "completed"
        assert timeline["nodes"][1]["output"] == {"status": "done"}


# ------------------------------------------------------------------
# 8. 节点关联 Task 详情的时间线
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_with_task_detail_timeline(timeline_service, test_session_factory):
    """节点关联 Task 详情 — thread_id, run_id, result_summary."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [{"key": "task1", "type": "waker_task", "depends_on": []}],
        })
        now = datetime.now(timezone.utc)
        flow_run = await _create_flow_run(session, flow_def, status="running", started_at=now)

        # 创建关联 Task
        task = await _create_task(
            session,
            thread_id="thread-abc-123",
            run_id="run-xyz-456",
            result_summary="Task completed with 5 items processed",
        )

        await _create_node_run(
            session, flow_run, "task1", "waker_task",
            status="completed",
            started_at=now, completed_at=now + timedelta(seconds=10),
            task_id=task.id,
            outcome_json=json.dumps({"status": "done"}),
        )

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 1
        node = timeline["nodes"][0]
        assert node["task"] is not None
        assert node["task"]["thread_id"] == "thread-abc-123"
        assert node["task"]["run_id"] == "run-xyz-456"
        assert node["task"]["result_summary"] == "Task completed with 5 items processed"


# ------------------------------------------------------------------
# 9. 不存在的 Flow 返回 None
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonexistent_flow_timeline(timeline_service, test_session_factory):
    """不存在的 Flow → None."""
    async with test_session_factory() as session:
        timeline = await timeline_service.get_timeline("nonexistent-id", session)
        assert timeline is None


# ------------------------------------------------------------------
# 10. 无 Task 关联的节点 task 字段为 None
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_without_task_detail(timeline_service, test_session_factory):
    """无 Task 关联的节点 — task 字段为 None."""
    async with test_session_factory() as session:
        flow_def = await _create_flow_def(session, {
            "nodes": [{"key": "cond1", "type": "condition", "depends_on": []}],
        })
        flow_run = await _create_flow_run(session, flow_def)
        await _create_node_run(session, flow_run, "cond1", "condition", status="pending")

        timeline = await timeline_service.get_timeline(flow_run.id, session)

        assert len(timeline["nodes"]) == 1
        assert timeline["nodes"][0]["task"] is None
