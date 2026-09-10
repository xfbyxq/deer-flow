"""Group CRUD + 成员管理 API 测试."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.group import Group, GroupMember
from app.models.waker import Waker


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
# 创建 Group
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_group(client, test_session_factory):
    """创建群组 → 201 + 返回群组数据."""
    resp = await client.post(
        "/api/groups",
        json={"name": "engineering", "project_id": "proj-1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "engineering"
    assert body["project_id"] == "proj-1"
    assert body["id"] is not None

    # 验证数据库
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Group).where(Group.name == "engineering"))
        group = result.scalars().first()
        assert group is not None
        assert group.project_id == "proj-1"


# ------------------------------------------------------------------
# 列表 Groups
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_groups(client, test_session_factory):
    """列表：预建两个组 → 断言返回."""
    async with test_session_factory() as session:
        session.add(Group(name="alpha", created_at=None, updated_at=None))
        session.add(Group(name="beta", created_at=None, updated_at=None))
        await session.commit()

    resp = await client.get("/api/groups")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    names = {g["name"] for g in body}
    assert names == {"alpha", "beta"}


# ------------------------------------------------------------------
# 详情 Group
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_group(client, test_session_factory):
    """详情：预建组 → 断言返回."""
    async with test_session_factory() as session:
        group = Group(name="gamma", project_id="proj-x")
        session.add(group)
        await session.commit()
        group_id = group.id

    resp = await client.get(f"/api/groups/{group_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "gamma"
    assert body["project_id"] == "proj-x"


# ------------------------------------------------------------------
# 更新 Group
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_group(client, test_session_factory):
    """更新：改名 + 改 project_id."""
    async with test_session_factory() as session:
        group = Group(name="old-name")
        session.add(group)
        await session.commit()
        group_id = group.id

    resp = await client.put(
        f"/api/groups/{group_id}",
        json={"name": "new-name", "project_id": "proj-y"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "new-name"
    assert body["project_id"] == "proj-y"


# ------------------------------------------------------------------
# 删除 Group
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_group(client, test_session_factory):
    """删除：预建组 → 204 + 数据库无记录."""
    async with test_session_factory() as session:
        group = Group(name="to-delete")
        session.add(group)
        await session.commit()
        group_id = group.id

    resp = await client.delete(f"/api/groups/{group_id}")
    assert resp.status_code == 204

    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Group).where(Group.id == group_id))
        assert result.scalars().first() is None


# ------------------------------------------------------------------
# 添加成员
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_member(client, test_session_factory):
    """添加成员 → 201 + 成员数据."""
    # 预建 waker 和 group
    async with test_session_factory() as session:
        session.add(Waker(name="alice", description="test"))
        group = Group(name="team-a")
        session.add(group)
        await session.commit()
        group_id = group.id

    resp = await client.post(
        f"/api/groups/{group_id}/members",
        json={"waker_id": "alice", "role": "member"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["waker_id"] == "alice"
    assert body["role"] == "member"
    assert body["group_id"] == group_id


# ------------------------------------------------------------------
# 移除成员
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_member(client, test_session_factory):
    """移除成员 → 204."""
    async with test_session_factory() as session:
        session.add(Waker(name="bob", description="test"))
        group = Group(name="team-b")
        session.add(group)
        await session.commit()
        group_id = group.id
        session.add(GroupMember(group_id=group_id, waker_id="bob", role="member"))
        await session.commit()

    resp = await client.delete(f"/api/groups/{group_id}/members/bob")
    assert resp.status_code == 204

    # 验证已移除
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(GroupMember).where(
                GroupMember.group_id == group_id,
                GroupMember.waker_id == "bob",
            )
        )
        assert result.scalars().first() is None


# ------------------------------------------------------------------
# 列表成员
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members(client, test_session_factory):
    """列出群组所有成员."""
    async with test_session_factory() as session:
        session.add(Waker(name="alice", description="test"))
        session.add(Waker(name="bob", description="test"))
        group = Group(name="team-c")
        session.add(group)
        await session.commit()
        group_id = group.id
        session.add(GroupMember(group_id=group_id, waker_id="alice", role="leader"))
        session.add(GroupMember(group_id=group_id, waker_id="bob", role="member"))
        await session.commit()

    resp = await client.get(f"/api/groups/{group_id}/members")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    waker_ids = {m["waker_id"] for m in body}
    assert waker_ids == {"alice", "bob"}


# ------------------------------------------------------------------
# 查询 waker 所属组
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_waker_groups(client, test_session_factory):
    """查某 waker 所属的所有组（通过 service 层直接测试）."""
    from app.services.group_service import GroupService

    async with test_session_factory() as session:
        session.add(Waker(name="charlie", description="test"))
        session.add(Waker(name="dave", description="test"))
        await session.commit()

    async with test_session_factory() as session:
        service = GroupService(session)
        g1 = await service.create_group(name="group-1")
        g2 = await service.create_group(name="group-2")
        await service.add_member(g1.id, "charlie", "member")
        await service.add_member(g2.id, "charlie", "member")
        await service.add_member(g1.id, "dave", "member")

        charlie_groups = await service.get_waker_groups("charlie")
        assert len(charlie_groups) == 2

        dave_groups = await service.get_waker_groups("dave")
        assert len(dave_groups) == 1
        assert dave_groups[0].name == "group-1"


@pytest.mark.asyncio
async def test_get_waker_groups_includes_leader_groups(client, test_session_factory):
    """leader 未登记为 member 时，也应返回其担任 leader 的组（同事查询依赖此语义）."""
    from app.services.group_service import GroupService

    async with test_session_factory() as session:
        session.add(Waker(name="leader-lu", description="test"))
        await session.commit()

    async with test_session_factory() as session:
        service = GroupService(session)
        g = await service.create_group(name="leader-group", leader_waker_id="leader-lu")
        groups = await service.get_waker_groups("leader-lu")
        assert [x.id for x in groups] == [g.id]
        # 无关 waker 不受影响
        assert await service.get_waker_groups("nobody") == []


# ------------------------------------------------------------------
# 404 场景
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_not_found(client):
    """获取不存在的群组 → 404."""
    resp = await client.get("/api/groups/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_group_not_found(client):
    """更新不存在的群组 → 404."""
    resp = await client.put(
        "/api/groups/nonexistent-id",
        json={"name": "new-name"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_group_not_found(client):
    """删除不存在的群组 → 404."""
    resp = await client.delete("/api/groups/nonexistent-id")
    assert resp.status_code == 404


# ------------------------------------------------------------------
# 唯一约束
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_group_name(client, test_session_factory):
    """重复组名 → 409."""
    resp = await client.post("/api/groups", json={"name": "unique-group"})
    assert resp.status_code == 201

    resp = await client.post("/api/groups", json={"name": "unique-group"})
    assert resp.status_code == 409
