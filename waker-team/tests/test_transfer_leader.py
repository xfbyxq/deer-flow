"""Transfer leader 测试."""

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


class TestTransferLeader:
    async def test_transfer_leader_success(self, client):
        """成功转移群主."""
        # 建群
        resp = await client.post("/api/groups", json={"name": "TransferGroup"})
        group_id = resp.json()["id"]

        # 添加成员
        await client.post(f"/api/groups/{group_id}/members", json={"waker_id": "alice", "role": "member"})

        # 转移群主给 alice
        resp = await client.post(
            f"/api/groups/{group_id}/transfer-leader",
            json={"target_waker_id": "alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["leader_waker_id"] == "alice"

    async def test_transfer_leader_not_member(self, client):
        """目标不是群成员应返回 422."""
        resp = await client.post("/api/groups", json={"name": "TransferGroup2"})
        group_id = resp.json()["id"]

        resp = await client.post(
            f"/api/groups/{group_id}/transfer-leader",
            json={"target_waker_id": "bob"},
        )
        assert resp.status_code == 422
        assert "not a member" in resp.json()["detail"]

    async def test_transfer_leader_group_not_found(self, client):
        """群组不存在应返回 404."""
        resp = await client.post(
            "/api/groups/nonexistent/transfer-leader",
            json={"target_waker_id": "alice"},
        )
        assert resp.status_code == 404

    async def test_transfer_leader_from_service(self, test_session_factory):
        """直接测试 service 层."""
        from datetime import UTC, datetime

        from app.models.group import Group
        from app.services.group_service import GroupService

        async with test_session_factory() as session:
            group = Group(
                id="g1", name="SvcGroup",
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            )
            session.add(group)
            await session.commit()

            service = GroupService(session)
            await service.add_member("g1", "alice", "member")

            result = await service.transfer_leader("g1", "alice")
            assert result is not None
            assert result.leader_waker_id == "alice"

    async def test_transfer_leader_non_member_raises(self, test_session_factory):
        """非成员转移应抛 ValueError."""
        from datetime import UTC, datetime

        from app.models.group import Group
        from app.services.group_service import GroupService

        async with test_session_factory() as session:
            group = Group(
                id="g2", name="SvcGroup2",
                created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
            )
            session.add(group)
            await session.commit()

            service = GroupService(session)
            with pytest.raises(ValueError, match="not a member"):
                await service.transfer_leader("g2", "nonexistent")
