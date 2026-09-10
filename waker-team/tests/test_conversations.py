"""Conversation CRUD + 消息操作测试."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.services.conversation_service import ConversationService


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
    client.get_agent = AsyncMock(return_value={})
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


# ------------------------------------------------------------------
# Service-level tests
# ------------------------------------------------------------------


class TestConversationService:
    async def test_create_conversation(self, test_session_factory):
        async with test_session_factory() as session:
            service = ConversationService(session)
            conv = await service.create_conversation(
                scope="direct",
                waker_id="alice",
                title="Test chat",
            )
            assert conv.id is not None
            assert conv.scope == "direct"
            assert conv.waker_id == "alice"
            assert conv.title == "Test chat"
            assert conv.status == "active"

    async def test_list_waker_conversations(self, test_session_factory):
        async with test_session_factory() as session:
            service = ConversationService(session)
            await service.create_conversation(scope="direct", waker_id="alice", title="Chat 1")
            await service.create_conversation(scope="direct", waker_id="bob", title="Chat 2")
            await service.create_conversation(scope="direct", waker_id="alice", title="Chat 3")

            convs = await service.list_waker_conversations("alice")
            assert len(convs) == 2

    async def test_list_waker_conversations_excludes_group_conversations(
        self, test_session_factory
    ):
        """内容隔离：直聊列表不包含所属群组的群会话（即使群会话误带 waker_id）."""
        from datetime import UTC, datetime

        from app.models.group import Group, GroupMember

        async with test_session_factory() as session:
            session.add(
                Group(
                    id="g1",
                    name="Team A",
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            session.add(GroupMember(group_id="g1", waker_id="alice", role="member"))
            await session.commit()

            service = ConversationService(session)
            await service.create_conversation(scope="direct", waker_id="alice", title="直聊")
            await service.create_conversation(scope="group", group_id="g1", title="群会话")
            await service.create_conversation(
                scope="group", group_id="g1", waker_id="alice", title="群会话2"
            )

            convs = await service.list_waker_conversations("alice")
            assert [c.title for c in convs] == ["直聊"]

    async def test_list_group_conversations(self, test_session_factory):
        """测试群组会话列表（需要先建 group）。"""
        from app.models.group import Group
        from datetime import UTC, datetime

        async with test_session_factory() as session:
            # 先建一个 group
            group = Group(id="g1", name="Team A", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            session.add(group)
            await session.commit()

            service = ConversationService(session)
            await service.create_conversation(scope="group", group_id="g1", title="Group chat")
            await service.create_conversation(scope="direct", waker_id="alice", title="Direct chat")

            convs = await service.list_group_conversations("g1")
            assert len(convs) == 1
            assert convs[0].title == "Group chat"

    async def test_create_message(self, test_session_factory):
        async with test_session_factory() as session:
            service = ConversationService(session)
            conv = await service.create_conversation(scope="direct", waker_id="alice")

            msg = await service.create_message(
                conversation_id=conv.id,
                role="user",
                content_json={"type": "text", "text": "Hello"},
            )
            assert msg.id is not None
            assert msg.conversation_id == conv.id
            assert msg.role == "user"
            assert '"Hello"' in msg.content_json

    async def test_list_messages(self, test_session_factory):
        async with test_session_factory() as session:
            service = ConversationService(session)
            conv = await service.create_conversation(scope="direct", waker_id="alice")

            await service.create_message(conv.id, "user", content_json={"text": "msg1"})
            await service.create_message(conv.id, "waker", waker_id="alice", content_json={"text": "msg2"})

            msgs = await service.list_messages(conv.id)
            assert len(msgs) == 2
            assert msgs[0].role == "user"
            assert msgs[1].role == "waker"

    async def test_get_conversation_not_found(self, test_session_factory):
        async with test_session_factory() as session:
            service = ConversationService(session)
            conv = await service.get_conversation("nonexistent")
            assert conv is None


# ------------------------------------------------------------------
# API-level tests
# ------------------------------------------------------------------


class TestConversationAPI:
    async def test_create_group_conversation(self, client):
        # 先建群组
        resp = await client.post("/api/groups", json={"name": "TestGroup"})
        assert resp.status_code == 201
        group_id = resp.json()["id"]

        # 创建群组会话
        resp = await client.post(
            f"/api/groups/{group_id}/conversations",
            json={"scope": "group", "title": "Sprint chat"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Sprint chat"
        assert data["scope"] == "group"
        assert data["group_id"] == group_id

    async def test_list_group_conversations(self, client):
        resp = await client.post("/api/groups", json={"name": "TestGroup2"})
        group_id = resp.json()["id"]

        await client.post(f"/api/groups/{group_id}/conversations", json={"scope": "group"})
        resp = await client.get(f"/api/groups/{group_id}/conversations")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_send_and_list_messages(self, client):
        resp = await client.post("/api/groups", json={"name": "TestGroup3"})
        group_id = resp.json()["id"]

        conv_resp = await client.post(f"/api/groups/{group_id}/conversations", json={"scope": "group"})
        conv_id = conv_resp.json()["id"]

        # 发消息
        msg_resp = await client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"role": "user", "content_json": {"text": "Hello"}},
        )
        assert msg_resp.status_code == 201
        assert msg_resp.json()["role"] == "user"

        # 列消息
        list_resp = await client.get(f"/api/conversations/{conv_id}/messages")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

    async def test_message_conversation_not_found(self, client):
        resp = await client.get("/api/conversations/nonexistent/messages")
        assert resp.status_code == 404

    async def test_list_waker_conversations(self, client):
        resp = await client.get("/api/wakers/alice/conversations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
