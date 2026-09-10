"""Group skills 管理测试."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app


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
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.list_agents = AsyncMock(return_value=[])
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestGroupSkillsAPI:
    async def test_add_skill(self, client):
        # 建群
        resp = await client.post("/api/groups", json={"name": "SkillGroup"})
        group_id = resp.json()["id"]

        # 加技能
        resp = await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "web_search"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["skill_name"] == "web_search"
        assert data["group_id"] == group_id

    async def test_list_skills(self, client):
        resp = await client.post("/api/groups", json={"name": "SkillGroup2"})
        group_id = resp.json()["id"]

        await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "skill_a"})
        await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "skill_b"})

        resp = await client.get(f"/api/groups/{group_id}/skills")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_remove_skill(self, client):
        resp = await client.post("/api/groups", json={"name": "SkillGroup3"})
        group_id = resp.json()["id"]

        await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "to_remove"})
        resp = await client.delete(f"/api/groups/{group_id}/skills/to_remove")
        assert resp.status_code == 204

        # 确认已删除
        resp = await client.get(f"/api/groups/{group_id}/skills")
        assert len(resp.json()) == 0

    async def test_remove_nonexistent_skill(self, client):
        resp = await client.post("/api/groups", json={"name": "SkillGroup4"})
        group_id = resp.json()["id"]

        resp = await client.delete(f"/api/groups/{group_id}/skills/nonexistent")
        assert resp.status_code == 404

    async def test_add_skill_group_not_found(self, client):
        resp = await client.post("/api/groups/nonexistent/skills", json={"skill_name": "x"})
        assert resp.status_code == 404

    async def test_duplicate_skill(self, client):
        """重复添加同一技能应幂等返回已有记录."""
        resp = await client.post("/api/groups", json={"name": "SkillGroup5"})
        group_id = resp.json()["id"]

        resp1 = await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "dup_skill"})
        resp2 = await client.post(f"/api/groups/{group_id}/skills", json={"skill_name": "dup_skill"})
        assert resp2.status_code == 201
        assert resp1.json()["id"] == resp2.json()["id"]

    async def test_group_with_description_and_sop(self, client):
        """测试新建群组带 description 和 sop_id."""
        resp = await client.post(
            "/api/groups",
            json={"name": "DescGroup", "description": "A test group", "sop_id": "sop-001"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] == "A test group"
        assert data["sop_id"] == "sop-001"

        # 更新
        resp = await client.put(
            f"/api/groups/{data['id']}",
            json={"description": "Updated desc"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated desc"
        assert resp.json()["sop_id"] == "sop-001"  # 保持不变
