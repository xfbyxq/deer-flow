"""Flow 定义 CRUD + JSON schema 校验测试."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.group import Group
from app.services.flow_def_service import FlowDefService, FlowValidationError


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
    return application


@pytest.fixture
async def client(app):
    """构建测试 HTTP 客户端."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ------------------------------------------------------------------
# 测试数据
# ------------------------------------------------------------------

VALID_FLOW_DEF = {
    "version": 1,
    "nodes": [
        {"key": "start", "type": "leader_plan", "waker": "planner", "depends_on": []},
        {"key": "task1", "type": "waker_task", "waker": "alice", "depends_on": ["start"]},
        {"key": "review1", "type": "human_review", "depends_on": ["task1"], "timeout_hours": 48},
        {"key": "notify1", "type": "notify", "channel": "slack", "depends_on": ["review1"]},
    ],
}


# ------------------------------------------------------------------
# 1. 创建 Flow 定义（含完整 JSON schema）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_flow(client, test_session_factory):
    """创建 Flow 定义 → 201 + 返回数据."""
    # 预建 group
    async with test_session_factory() as session:
        session.add(Group(name="engineering"))
        await session.commit()
        from sqlalchemy import select
        from app.models.group import Group as GroupModel
        result = await session.execute(select(GroupModel).where(GroupModel.name == "engineering"))
        group = result.scalars().first()
        group_id = group.id

    resp = await client.post(
        "/api/flows",
        json={
            "name": "deploy-pipeline",
            "group_id": group_id,
            "description": "Production deploy flow",
            "definition_json": VALID_FLOW_DEF,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "deploy-pipeline"
    assert body["group_id"] == group_id
    assert body["description"] == "Production deploy flow"
    assert body["version"] == 1
    assert body["status"] == "draft"
    assert body["id"] is not None
    assert len(body["definition_json"]["nodes"]) == 4


# ------------------------------------------------------------------
# 2. 列表查询（按 group_id 筛选）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_flows(client, test_session_factory):
    """列表：预建两个 Flow → 断言返回 + group_id 筛选."""
    async with test_session_factory() as session:
        session.add(Group(name="g1"))
        session.add(Group(name="g2"))
        await session.commit()
        from sqlalchemy import select
        from app.models.group import Group as GroupModel
        r1 = await session.execute(select(GroupModel).where(GroupModel.name == "g1"))
        r2 = await session.execute(select(GroupModel).where(GroupModel.name == "g2"))
        g1_id = r1.scalars().first().id
        g2_id = r2.scalars().first().id

    # 创建两个 flow
    await client.post(
        "/api/flows",
        json={"name": "flow-a", "group_id": g1_id, "definition_json": VALID_FLOW_DEF},
    )
    await client.post(
        "/api/flows",
        json={"name": "flow-b", "group_id": g2_id, "definition_json": VALID_FLOW_DEF},
    )

    # 全量列表
    resp = await client.get("/api/flows")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # 按 group_id 筛选
    resp = await client.get(f"/api/flows?group_id={g1_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "flow-a"


# ------------------------------------------------------------------
# 3. 获取详情
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flow(client):
    """详情：创建后获取."""
    resp = await client.post(
        "/api/flows",
        json={"name": "detail-flow", "group_id": "g1", "definition_json": VALID_FLOW_DEF},
    )
    flow_id = resp.json()["id"]

    resp = await client.get(f"/api/flows/{flow_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "detail-flow"
    assert body["id"] == flow_id


# ------------------------------------------------------------------
# 4. 更新定义
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_flow(client):
    """更新：改名 + 改描述."""
    resp = await client.post(
        "/api/flows",
        json={"name": "old-name", "group_id": "g1", "definition_json": VALID_FLOW_DEF},
    )
    flow_id = resp.json()["id"]

    new_def = {
        "version": 1,
        "nodes": [
            {"key": "only", "type": "waker_task", "waker": "bob", "depends_on": []},
        ],
    }
    resp = await client.put(
        f"/api/flows/{flow_id}",
        json={"name": "new-name", "description": "updated", "definition_json": new_def},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "new-name"
    assert body["description"] == "updated"
    assert len(body["definition_json"]["nodes"]) == 1


# ------------------------------------------------------------------
# 5. 删除
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_flow(client):
    """删除：创建后删除 → 204 + 404."""
    resp = await client.post(
        "/api/flows",
        json={"name": "to-delete", "group_id": "g1", "definition_json": VALID_FLOW_DEF},
    )
    flow_id = resp.json()["id"]

    resp = await client.delete(f"/api/flows/{flow_id}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/flows/{flow_id}")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 6. JSON schema 校验 — 合法定义通过
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_valid_definition():
    """合法定义不抛异常."""
    FlowDefService.validate_definition(VALID_FLOW_DEF)


# ------------------------------------------------------------------
# 7. JSON schema 校验 — 缺少 nodes 报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_missing_nodes():
    """缺少 nodes → FlowValidationError."""
    with pytest.raises(FlowValidationError, match="non-empty array"):
        FlowDefService.validate_definition({"version": 1})

    with pytest.raises(FlowValidationError, match="non-empty array"):
        FlowDefService.validate_definition({"version": 1, "nodes": []})


# ------------------------------------------------------------------
# 8. JSON schema 校验 — 环形依赖报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_cyclic_dependency():
    """环形依赖 → FlowValidationError."""
    cyclic_def = {
        "version": 1,
        "nodes": [
            {"key": "a", "type": "waker_task", "waker": "x", "depends_on": ["b"]},
            {"key": "b", "type": "waker_task", "waker": "y", "depends_on": ["a"]},
        ],
    }
    with pytest.raises(FlowValidationError, match="[Cc]ircular"):
        FlowDefService.validate_definition(cyclic_def)


# ------------------------------------------------------------------
# 9. JSON schema 校验 — 无效节点类型报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_invalid_node_type():
    """无效节点类型 → FlowValidationError."""
    bad_type_def = {
        "version": 1,
        "nodes": [
            {"key": "bad", "type": "unknown_type", "depends_on": []},
        ],
    }
    with pytest.raises(FlowValidationError, match="invalid type"):
        FlowDefService.validate_definition(bad_type_def)


# ------------------------------------------------------------------
# 10. JSON schema 校验 — depends_on 引用不存在的 key 报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_depends_on_unknown_key():
    """depends_on 引用不存在的 key → FlowValidationError."""
    bad_dep_def = {
        "version": 1,
        "nodes": [
            {"key": "a", "type": "waker_task", "waker": "x", "depends_on": ["nonexistent"]},
        ],
    }
    with pytest.raises(FlowValidationError, match="unknown key"):
        FlowDefService.validate_definition(bad_dep_def)


# ------------------------------------------------------------------
# 11. JSON schema 校验 — waker_task 缺少 waker 报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_waker_task_missing_waker():
    """waker_task 缺少 waker → FlowValidationError."""
    no_waker_def = {
        "version": 1,
        "nodes": [
            {"key": "task1", "type": "waker_task", "depends_on": []},
        ],
    }
    with pytest.raises(FlowValidationError, match="requires 'waker'"):
        FlowDefService.validate_definition(no_waker_def)


# ------------------------------------------------------------------
# 12. JSON schema 校验 — condition 缺少 expression 报错
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_condition_missing_expression():
    """condition 缺少 expression → FlowValidationError."""
    bad_cond_def = {
        "version": 1,
        "nodes": [
            {
                "key": "cond1",
                "type": "condition",
                "depends_on": [],
                "branches": {"yes": "a", "no": "b"},
            },
        ],
    }
    with pytest.raises(FlowValidationError, match="requires 'expression'"):
        FlowDefService.validate_definition(bad_cond_def)
