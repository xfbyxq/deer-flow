"""回归测试：2026-09-10 排查修复.

覆盖三类修复：
1. 时间序列化统一带 UTC 时区（to_iso_utc）——修复前端时间偏移 8 小时
2. Flow 启动运行路由迁移到 POST /api/flows/{id}/run——修复编辑器"运行"404
3. 调度触发接线（task_service_factory / flow_engine）——修复触发只留 pending
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.deerflow.errors import AgentConflictError
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage
from app.models.flow import FlowDef, FlowRun, NodeRun
from app.models.group import Group, GroupMember
from app.models.schedule import ScheduleDef, ScheduleRun
from app.models.waker import Waker
from app.scheduler.service import SchedulerService
from app.time_utils import to_iso_utc


# ------------------------------------------------------------------
# Fixtures（风格对齐 tests/test_tasks.py）
# ------------------------------------------------------------------


@pytest.fixture
async def test_db_engine():
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
    client = MagicMock()
    client.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    client.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    client.get_run = AsyncMock(return_value={"status": "running"})
    client.cancel_run = AsyncMock(return_value=None)
    client.list_agents = AsyncMock(return_value=[])
    client.delete_agent = AsyncMock(return_value=None)
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    # 注入 scheduler（schedule API 依赖 app.state.scheduler）
    application.state.scheduler = SchedulerService(test_session_factory)
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_waker(session_factory, name: str, enabled: bool = True):
    async with session_factory() as session:
        session.add(Waker(name=name, deer_user="", description=f"Test {name}", soul_summary="...", enabled=enabled))
        await session.commit()


# ------------------------------------------------------------------
# 1. to_iso_utc 单元测试
# ------------------------------------------------------------------


class TestToIsoUtc:
    def test_naive_datetime_treated_as_utc(self):
        """naive datetime（SQLite 读回值）补 UTC 时区后缀，而非直接原样输出."""
        result = to_iso_utc(datetime(2026, 9, 10, 7, 35, 39, 550753))
        assert result == "2026-09-10T07:35:39.550753+00:00"

    def test_aware_datetime_converted(self):
        """aware datetime 统一转换为 UTC."""
        from datetime import timezone, timedelta as td

        aware = datetime(2026, 9, 10, 15, 35, tzinfo=timezone(td(hours=8)))
        assert to_iso_utc(aware) == "2026-09-10T07:35:00+00:00"

    def test_none_passthrough(self):
        assert to_iso_utc(None) is None


# ------------------------------------------------------------------
# 2. API 时间契约：所有时间字段必须带时区后缀
# ------------------------------------------------------------------


class TestApiTimeContract:
    @pytest.mark.asyncio
    async def test_task_timestamps_carry_timezone(self, app, client, test_session_factory):
        """任务 API 返回的 created_at/updated_at 必须带时区（前端据此本地化）."""
        await _create_waker(test_session_factory, "time-worker")
        resp = await client.post(
            "/api/tasks",
            json={"executor": "time-worker", "input_text": "time contract"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["created_at"].endswith("+00:00")
        assert body["updated_at"].endswith("+00:00")

        list_resp = await client.get("/api/tasks")
        assert list_resp.status_code == 200
        for item in list_resp.json()["items"]:
            assert item["created_at"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_waker_timestamps_carry_timezone(self, app, client, test_session_factory, mock_deerflow):
        """Waker API 返回的 created_at 必须带时区."""
        await _create_waker(test_session_factory, "tz-waker")
        # 合并路径：DeerFlow 返回同名 agent，本地台账提供 created_at
        mock_deerflow.list_agents.return_value = [{"name": "tz-waker"}]
        resp = await client.get("/api/wakers")
        assert resp.status_code == 200
        wakers = resp.json()
        target = next(w for w in wakers if w["name"] == "tz-waker")
        assert target["created_at"].endswith("+00:00")


# ------------------------------------------------------------------
# 3. Flow 启动运行路由
# ------------------------------------------------------------------


def _fake_flow_run():
    return SimpleNamespace(
        id="run-1",
        flow_def_id="flow-1",
        group_id="default",
        status="running",
        started_at=datetime(2026, 9, 10, 7, 0, 0),
        completed_at=None,
        current_node_key=None,
        output_cache_json=None,
        created_by="manual",
        trigger_type="manual",
        paused_at=None,
        failure_reason=None,
    )


class TestFlowRunStartRoute:
    @pytest.mark.asyncio
    async def test_start_flow_run_via_flows_path(self, app, client, test_session_factory):
        """POST /api/flows/{id}/run 启动运行（前后端约定路径）."""
        async with test_session_factory() as session:
            session.add(
                FlowDef(
                    id="flow-1",
                    name="regression-flow",
                    group_id="default",
                    definition_json=json.dumps({"version": 1, "nodes": []}),
                    status="draft",
                    version=1,
                )
            )
            await session.commit()

        engine = MagicMock()
        engine.start = AsyncMock(return_value=_fake_flow_run())
        app.state.flow_engine = engine

        resp = await client.post("/api/flows/flow-1/run")
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "run-1"
        assert body["status"] == "running"
        # 时间序列化契约
        assert body["started_at"].endswith("+00:00")
        engine.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_old_flow_runs_start_path_removed(self, app, client):
        """旧路径 POST /api/flow-runs/{flow_id}/run 不再存在（迁移后应为 404）."""
        app.state.flow_engine = MagicMock()
        resp = await client.post("/api/flow-runs/flow-1/run")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_start_flow_run_without_engine_returns_503(self, app, client):
        """Flow 引擎未初始化时返回 503."""
        app.state.flow_engine = None
        resp = await client.post("/api/flows/flow-1/run")
        assert resp.status_code == 503


# ------------------------------------------------------------------
# 4. 调度触发接线
# ------------------------------------------------------------------


class TestSchedulerWiring:
    @pytest.mark.asyncio
    async def test_task_schedule_dispatches_via_factory(self, test_session_factory):
        """task 类型调度触发时必须真正派发任务（task_id 落库 + status=running）."""
        now = datetime.now(UTC)
        async with test_session_factory() as db:
            db.add(
                ScheduleDef(
                    name="dispatch-sched",
                    group_id="g1",
                    cron_expression="* * * * *",
                    target_type="task",
                    target_id="worker-1",
                    status="active",
                    next_run_at=now - timedelta(hours=1),
                    run_count=0,
                )
            )
            await db.commit()

        fake_task_service = MagicMock()
        fake_task_service.create_task = AsyncMock(return_value={"id": "task-123"})

        service = SchedulerService(
            test_session_factory,
            task_service_factory=lambda db: fake_task_service,
        )
        await service._tick()

        fake_task_service.create_task.assert_awaited_once()
        kwargs = fake_task_service.create_task.await_args.kwargs
        assert kwargs["executor"] == "worker-1"
        assert kwargs["group_id"] == "g1"
        assert kwargs["kind"] == "schedule"

        async with test_session_factory() as db:
            run = (await db.execute(select(ScheduleRun))).scalars().first()
            assert run is not None
            assert run.task_id == "task-123"
            assert run.status == "running"

    @pytest.mark.asyncio
    async def test_flow_schedule_starts_flow_run(self, test_session_factory):
        """flow 类型调度触发时必须真正启动 FlowRun（flow_run_id 落库 + status=running）."""
        now = datetime.now(UTC)
        async with test_session_factory() as db:
            db.add(
                ScheduleDef(
                    name="flow-sched",
                    group_id="g1",
                    cron_expression="* * * * *",
                    target_type="flow",
                    target_id="flow-1",
                    status="active",
                    next_run_at=now - timedelta(hours=1),
                    run_count=0,
                )
            )
            await db.commit()

        flow_engine = MagicMock()
        flow_engine.start = AsyncMock(return_value=SimpleNamespace(id="run-9"))

        service = SchedulerService(test_session_factory, flow_engine=flow_engine)
        await service._tick()

        flow_engine.start.assert_awaited_once()
        async with test_session_factory() as db:
            run = (await db.execute(select(ScheduleRun))).scalars().first()
            assert run is not None
            assert run.flow_run_id == "run-9"
            assert run.status == "running"

    @pytest.mark.asyncio
    async def test_task_schedule_without_factory_stays_pending(self, test_session_factory):
        """未注入依赖时保持历史行为：记录 pending，不派发."""
        now = datetime.now(UTC)
        async with test_session_factory() as db:
            db.add(
                ScheduleDef(
                    name="noop-sched",
                    group_id="g1",
                    cron_expression="* * * * *",
                    target_type="task",
                    target_id="worker-1",
                    status="active",
                    next_run_at=now - timedelta(hours=1),
                    run_count=0,
                )
            )
            await db.commit()

        service = SchedulerService(test_session_factory)
        await service._tick()

        async with test_session_factory() as db:
            run = (await db.execute(select(ScheduleRun))).scalars().first()
            assert run is not None
            assert run.status == "pending"
            assert run.task_id is None


# ------------------------------------------------------------------
# 5. 删除接口的外键清理（历史上: 有运行记录的实体删除会 500）
# ------------------------------------------------------------------


class TestDeleteForeignKeyCleanup:
    @pytest.mark.asyncio
    async def test_delete_schedule_with_runs(self, app, client, test_session_factory):
        """删除带执行历史的调度 → 204（先清理 schedule_runs）."""
        async with test_session_factory() as db:
            db.add(
                ScheduleDef(
                    id="sched-del-1",
                    name="del-sched",
                    group_id="g1",
                    cron_expression="0 9 * * *",
                    target_type="task",
                    target_id="worker-1",
                    status="active",
                    run_count=1,
                )
            )
            db.add(ScheduleRun(id="srun-1", schedule_def_id="sched-del-1", status="running"))
            await db.commit()

        resp = await client.delete("/api/schedules/sched-del-1")
        assert resp.status_code == 204
        async with test_session_factory() as db:
            assert (await db.execute(select(ScheduleRun))).scalars().first() is None

    @pytest.mark.asyncio
    async def test_delete_flow_with_runs(self, app, client, test_session_factory):
        """删除带运行记录的 Flow → 204（先清理 node_runs/flow_runs）."""
        async with test_session_factory() as db:
            db.add(
                FlowDef(
                    id="flow-del-1",
                    name="del-flow",
                    group_id="default",
                    definition_json=json.dumps({"version": 1, "nodes": []}),
                    status="draft",
                    version=1,
                )
            )
            db.add(FlowRun(id="frun-1", flow_def_id="flow-del-1", status="running"))
            db.add(NodeRun(id="nrun-1", flow_run_id="frun-1", node_id="n1", node_type="waker_task", status="pending"))
            await db.commit()

        resp = await client.delete("/api/flows/flow-del-1")
        assert resp.status_code == 204
        async with test_session_factory() as db:
            assert (await db.execute(select(FlowRun))).scalars().first() is None
            assert (await db.execute(select(NodeRun))).scalars().first() is None

    @pytest.mark.asyncio
    async def test_delete_group_with_conversation_and_flow(self, app, client, test_session_factory):
        """删除带会话/Flow 关联的群组 → 204（解除引用不丢数据）."""
        async with test_session_factory() as db:
            db.add(Group(id="g-del-1", name="del-group"))
            db.add(Conversation(id="conv-1", scope="group", group_id="g-del-1"))
            db.add(
                FlowDef(
                    id="flow-g-1",
                    name="g-flow",
                    group_id="g-del-1",
                    definition_json=json.dumps({"version": 1, "nodes": []}),
                    status="draft",
                    version=1,
                )
            )
            await db.commit()

        resp = await client.delete("/api/groups/g-del-1")
        assert resp.status_code == 204
        async with test_session_factory() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == "conv-1"))
            ).scalars().first()
            flow = (
                await db.execute(select(FlowDef).where(FlowDef.id == "flow-g-1"))
            ).scalars().first()
            # 数据保留，仅解除群关联
            assert conv is not None and conv.group_id is None
            assert flow is not None and flow.group_id is None

    @pytest.mark.asyncio
    async def test_delete_waker_with_membership_and_conversation(
        self, app, client, test_session_factory
    ):
        """删除群成员/有会话的 Waker → 204（先清理成员关系与会话消息）."""
        await _create_waker(test_session_factory, "del-waker")
        async with test_session_factory() as db:
            db.add(Group(id="g-del-2", name="del-group-2"))
            db.add(GroupMember(group_id="g-del-2", waker_id="del-waker", role="member"))
            db.add(Conversation(id="conv-2", scope="direct", waker_id="del-waker"))
            db.add(
                ConversationMessage(
                    id="cmsg-1",
                    conversation_id="conv-2",
                    role="user",
                    content_json="{\"text\": \"hi\"}",
                )
            )
            await db.commit()

        resp = await client.delete("/api/wakers/del-waker")
        assert resp.status_code == 204
        async with test_session_factory() as db:
            assert (
                await db.execute(select(Conversation).where(Conversation.id == "conv-2"))
            ).scalars().first() is None
            assert (
                await db.execute(
                    select(ConversationMessage).where(ConversationMessage.id == "cmsg-1")
                )
            ).scalars().first() is None

    @pytest.mark.asyncio
    async def test_delete_waker_as_group_leader_and_group_sender(
        self, app, client, test_session_factory
    ):
        """删除「群主 + 群消息发送者」→ 204（历史 bug：leader_waker_id / 消息 waker_id 未清理致 FK 失败）."""
        await _create_waker(test_session_factory, "del-leader")
        async with test_session_factory() as db:
            db.add(Group(id="g-del-3", name="del-group-3", leader_waker_id="del-leader"))
            db.add(Conversation(id="gconv-1", scope="group", group_id="g-del-3"))
            db.add(
                ConversationMessage(
                    id="gmsg-1",
                    conversation_id="gconv-1",
                    role="waker",
                    waker_id="del-leader",
                    content_json="{\"text\": \"hello\"}",
                )
            )
            await db.commit()

        resp = await client.delete("/api/wakers/del-leader")
        assert resp.status_code == 204
        async with test_session_factory() as db:
            assert (
                await db.execute(select(ConversationMessage).where(ConversationMessage.id == "gmsg-1"))
            ).scalars().first() is None
            group = (
                await db.execute(select(Group).where(Group.id == "g-del-3"))
            ).scalars().first()
            assert group is not None and group.leader_waker_id is None


# ------------------------------------------------------------------
# 6. 幂等创建：DeerFlow agent 已存在时补写台账而非 409
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# 7. run 身份凭据：waker-team 发起的所有 run 必须携带 waker_identity
# ------------------------------------------------------------------


class TestRunConfigurationIdentity:
    """回归：run 请求必须携带 config.context.secrets.waker_identity.

    共享 MCP server（waker-team）的 headers_from_context 为 fail-closed，
    缺失该凭据时所有工具调用被拒（表现为 agent 无法获取同事列表）。
    """

    def test_build_run_configuration_shape(self):
        from app.deerflow.client import build_run_configuration

        cfg = build_run_configuration("alice")
        assert cfg["configurable"]["agent_name"] == "alice"
        assert cfg["context"]["secrets"]["waker_identity"] == "alice"

    @pytest.mark.asyncio
    async def test_task_service_run_body_carries_identity(
        self, test_session_factory, mock_deerflow
    ):
        from app.services.task_service import TaskService

        await _create_waker(test_session_factory, "id-worker")
        async with test_session_factory() as db:
            service = TaskService(db, mock_deerflow)
            await service.create_task(executor="id-worker", input_text="check identity")

        body = mock_deerflow.create_run.await_args.kwargs["body"]
        assert body["config"]["configurable"]["agent_name"] == "id-worker"
        assert body["config"]["context"]["secrets"]["waker_identity"] == "id-worker"


# ------------------------------------------------------------------
# 8. 幂等创建：DeerFlow agent 已存在时补写台账而非 409
# ------------------------------------------------------------------


class TestCreateWakerIdempotent:
    @pytest.mark.asyncio
    async def test_create_waker_when_agent_already_exists(
        self, app, client, mock_deerflow, test_session_factory
    ):
        """DeerFlow 409（agent 残留）时仍能创建成功并补写本地台账（幂等修复路径）."""
        mock_deerflow.create_agent = AsyncMock(
            side_effect=AgentConflictError("Agent already exists")
        )
        resp = await client.post(
            "/api/wakers",
            json={
                "name": "recovered-waker",
                "description": "从 agent 残留中恢复",
                "soul": "你是恢复助手。",
            },
        )
        assert resp.status_code == 201
        async with test_session_factory() as db:
            waker = (
                await db.execute(select(Waker).where(Waker.name == "recovered-waker"))
            ).scalars().first()
            assert waker is not None
            assert waker.description == "从 agent 残留中恢复"

        # 再次创建（同名）：台账+agent 均存在 → 真正的重复，保持 409 语义
        resp2 = await client.post(
            "/api/wakers",
            json={
                "name": "recovered-waker",
                "description": "再次更新描述",
                "soul": "你是恢复助手。",
            },
        )
        assert resp2.status_code == 409
