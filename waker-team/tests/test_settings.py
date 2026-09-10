"""User settings CRUD 测试."""

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


class TestSettingsAPI:
    async def test_get_default_settings(self, client):
        """首次获取设置返回默认值."""
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "default"
        assert data["notify_task"] is True
        assert data["notify_mention"] is True
        assert data["default_model"] is None

    async def test_create_settings(self, client):
        """创建设置."""
        resp = await client.put(
            "/api/settings",
            json={"default_model": "gpt-4", "density": "compact"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_model"] == "gpt-4"
        assert data["density"] == "compact"
        assert data["notify_task"] is True  # 默认值

    async def test_update_settings(self, client):
        """更新已有设置."""
        # 先创建
        await client.put("/api/settings", json={"default_model": "gpt-4"})
        # 再更新
        resp = await client.put(
            "/api/settings",
            json={"notify_task": False, "density": "comfortable"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notify_task"] is False
        assert data["density"] == "comfortable"
        # default_model 应保持不变
        assert data["default_model"] == "gpt-4"

    async def test_get_after_create(self, client):
        """创建后 GET 返回已创建的值."""
        await client.put("/api/settings", json={"default_model": "claude-3"})
        resp = await client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["default_model"] == "claude-3"
