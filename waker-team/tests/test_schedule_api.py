"""调度 API 集成测试."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.schedule import ScheduleDef
from app.scheduler.service import SchedulerService


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
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.list_agents = AsyncMock(return_value=[])
    client.get_agent = AsyncMock(return_value={})
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    """构建测试 FastAPI app."""
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    # 注入 scheduler
    scheduler = SchedulerService(test_session_factory)
    application.state.scheduler = scheduler
    return application


@pytest.fixture
async def client(app):
    """构建测试 HTTP 客户端."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ------------------------------------------------------------------
# 创建调度
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_schedule(client):
    """创建调度（合法 cron）→ 201."""
    resp = await client.post(
        "/api/schedules",
        json={
            "name": "daily-report",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "daily-report"
    assert body["cron_expression"] == "0 9 * * *"
    assert body["status"] == "active"
    assert body["next_run_at"] is not None
    assert body["run_count"] == 0


@pytest.mark.asyncio
async def test_create_schedule_invalid_cron(client):
    """创建调度（非法 cron）→ 422."""
    resp = await client.post(
        "/api/schedules",
        json={
            "name": "bad-cron",
            "group_id": "g1",
            "cron_expression": "not valid",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_schedule_invalid_target_type(client):
    """创建调度（非法 target_type）→ 422."""
    resp = await client.post(
        "/api/schedules",
        json={
            "name": "bad-target",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "invalid",
            "target_id": "worker-1",
        },
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------
# 列表查询
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_schedules(client):
    """列表查询."""
    # 创建两个
    await client.post(
        "/api/schedules",
        json={
            "name": "sched-1",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "w1",
        },
    )
    await client.post(
        "/api/schedules",
        json={
            "name": "sched-2",
            "group_id": "g2",
            "cron_expression": "0 10 * * *",
            "target_type": "task",
            "target_id": "w2",
        },
    )

    # 全部列表
    resp = await client.get("/api/schedules")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2

    # 按 group_id 筛选
    resp = await client.get("/api/schedules?group_id=g1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "sched-1"


# ------------------------------------------------------------------
# 获取详情
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_schedule(client):
    """获取详情."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "detail-test",
            "group_id": "g1",
            "cron_expression": "*/15 * * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.get(f"/api/schedules/{sched_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "detail-test"
    assert body["cron_expression"] == "*/15 * * * *"


@pytest.mark.asyncio
async def test_get_schedule_not_found(client):
    """获取不存在的调度 → 404."""
    resp = await client.get("/api/schedules/nonexistent-id")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 更新调度
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_schedule(client):
    """更新调度."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-update",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/schedules/{sched_id}",
        json={"name": "updated-name", "cron_expression": "0 10 * * *"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "updated-name"
    assert body["cron_expression"] == "0 10 * * *"


@pytest.mark.asyncio
async def test_update_schedule_invalid_cron(client):
    """更新调度（非法 cron）→ 422."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-update-bad",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/schedules/{sched_id}",
        json={"cron_expression": "invalid"},
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------
# 删除调度
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_schedule(client):
    """删除调度."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-delete",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.delete(f"/api/schedules/{sched_id}")
    assert resp.status_code == 204

    # 验证已删除
    resp = await client.get(f"/api/schedules/{sched_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_schedule_not_found(client):
    """删除不存在的调度 → 404."""
    resp = await client.delete("/api/schedules/nonexistent-id")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 暂停/恢复
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_schedule(client):
    """暂停调度."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-pause",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.post(f"/api/schedules/{sched_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


@pytest.mark.asyncio
async def test_resume_schedule(client):
    """恢复调度."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-resume",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    # 先暂停
    await client.post(f"/api/schedules/{sched_id}/pause")

    # 恢复
    resp = await client.post(f"/api/schedules/{sched_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


# ------------------------------------------------------------------
# 手动触发
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_trigger(client):
    """手动触发."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "to-trigger",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    resp = await client.post(f"/api/schedules/{sched_id}/trigger")
    assert resp.status_code == 201
    body = resp.json()
    assert body["trigger_type"] == "manual"
    assert body["status"] in ("pending", "running")


@pytest.mark.asyncio
async def test_manual_trigger_not_found(client):
    """手动触发不存在的调度 → 404."""
    resp = await client.post("/api/schedules/nonexistent-id/trigger")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 执行历史
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs(client):
    """执行历史查询."""
    create_resp = await client.post(
        "/api/schedules",
        json={
            "name": "runs-test",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
        },
    )
    sched_id = create_resp.json()["id"]

    # 手动触发两次
    await client.post(f"/api/schedules/{sched_id}/trigger")
    await client.post(f"/api/schedules/{sched_id}/trigger")

    # 查询历史
    resp = await client.get(f"/api/schedules/{sched_id}/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(r["trigger_type"] == "manual" for r in body)


@pytest.mark.asyncio
async def test_list_runs_not_found(client):
    """查询不存在调度的执行历史 → 404."""
    resp = await client.get("/api/schedules/nonexistent-id/runs")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# max_run_count 到期自动归档（通过 API 验证）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_schedule_with_max_run_count(client):
    """创建带 max_run_count 的调度."""
    resp = await client.post(
        "/api/schedules",
        json={
            "name": "limited-runs",
            "group_id": "g1",
            "cron_expression": "0 9 * * *",
            "target_type": "task",
            "target_id": "worker-1",
            "max_run_count": 5,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["max_run_count"] == 5
