"""Conversation CRUD + 消息操作测试."""

import json
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.conversation import ConversationMessage
from app.services.conversation_service import ConversationService


def _msg(conv_id: str, role: str, text: str, at: datetime) -> ConversationMessage:
    """构造一条带指定时间戳的会话消息（测试分页排序用）."""
    return ConversationMessage(
        conversation_id=conv_id,
        role=role,
        content_json=json.dumps({"text": text}, ensure_ascii=False),
        created_at=at,
    )


def _messages_query_params(app) -> dict:
    """从 OpenAPI schema 取 GET messages 的 query 参数（前端实际看到的契约）."""
    schema = app.openapi()
    params = schema["paths"]["/api/conversations/{conversation_id}/messages"]["get"][
        "parameters"
    ]
    return {p["name"]: p for p in params if p["in"] == "query"}


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

    async def test_list_messages_returns_most_recent_window_ascending(
        self, test_session_factory
    ):
        """C1：消息超阈值时返回「最近 limit 条」并按时间升序（新消息可见）."""
        from datetime import UTC, datetime, timedelta

        async with test_session_factory() as session:
            service = ConversationService(session)
            conv = await service.create_conversation(scope="group", group_id="g1")
            base = datetime.now(UTC)
            # 直接写入带递增时间戳的 5 条消息（避免同微秒抖动）
            for i in range(5):
                session.add(
                    _msg(conv.id, "user", f"m{i}", base + timedelta(seconds=i))
                )
            await session.commit()

            # limit=2：应返回最近两条（m3、m4），且升序
            msgs = await service.list_messages(conv.id, limit=2)
            assert [json.loads(m.content_json)["text"] for m in msgs] == ["m3", "m4"]

            # offset=1 + limit=2：跳过最新一条后取最近两条（m2、m3），升序
            msgs = await service.list_messages(conv.id, limit=2, offset=1)
            assert [json.loads(m.content_json)["text"] for m in msgs] == ["m2", "m3"]

    async def test_list_messages_default_limit_is_200(self, test_session_factory):
        """C1：默认 limit 提升到 200（默认窗口足够大，新消息不被截断）."""
        import inspect

        from app.services.conversation_service import ConversationService

        sig = inspect.signature(ConversationService.list_messages)
        assert sig.parameters["limit"].default == 200

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

    async def test_list_messages_limit_bounds(self, client):
        """CF11 契约：limit 下限 ge=1（越界 422）；**上限不再 422** 而是 clamp 到 500."""
        resp = await client.post("/api/groups", json={"name": "PageGroup"})
        group_id = resp.json()["id"]
        conv_resp = await client.post(
            f"/api/groups/{group_id}/conversations", json={"scope": "group"}
        )
        conv_id = conv_resp.json()["id"]

        # limit 超上限 500 → **不再 422**（既有 ?limit=1000 客户端不能由静默变报错）
        r = await client.get(f"/api/conversations/{conv_id}/messages?limit=501")
        assert r.status_code == 200
        r = await client.get(f"/api/conversations/{conv_id}/messages?limit=1000")
        assert r.status_code == 200
        # limit=0（< ge=1）→ 422
        r = await client.get(f"/api/conversations/{conv_id}/messages?limit=0")
        assert r.status_code == 422
        # offset 负数 → 422
        r = await client.get(f"/api/conversations/{conv_id}/messages?offset=-1")
        assert r.status_code == 422
        # 合法边界 limit=500 → 200
        r = await client.get(f"/api/conversations/{conv_id}/messages?limit=500")
        assert r.status_code == 200

    async def test_list_messages_limit_over_500_is_clamped(self, client, test_session_factory):
        """CONTRACT-LIMIT：?limit=1000 被 clamp 到 500，返回最近 500 条且时间升序."""
        from datetime import UTC, datetime, timedelta

        from app.models.conversation import Conversation
        from app.models.group import Group

        total = 501
        async with test_session_factory() as session:
            session.add(Group(id="g-clamp", name="ClampGroup"))
            session.add(
                Conversation(id="conv-clamp", scope="group", group_id="g-clamp")
            )
            base = datetime.now(UTC)
            session.add_all(
                [
                    _msg("conv-clamp", "user", f"m{i}", base + timedelta(seconds=i))
                    for i in range(total)
                ]
            )
            await session.commit()

        r = await client.get("/api/conversations/conv-clamp/messages?limit=1000")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 500
        texts = [json.loads(m["content_json"])["text"] for m in body]
        # 倒序取最近 500 条（丢弃最早的 m0）后升序返回
        assert texts[0] == "m1"
        assert texts[-1] == f"m{total - 1}"

    async def test_list_messages_default_limit_is_200_at_api(self, app):
        """CONTRACT-LIMIT：默认 limit=200、下限 1、**无上限校验**（>500 靠 clamp）."""
        params = _messages_query_params(app)

        limit_schema = params["limit"]["schema"]
        assert limit_schema["default"] == 200
        assert limit_schema["minimum"] == 1
        # 上限改用 handler 内 clamp，OpenAPI 不再声明 maximum（否则 >500 会 422）
        assert "maximum" not in limit_schema

    async def test_offset_semantics_documented(self, app):
        """CF19：offset「从最新端跳过」写入 OpenAPI description 与 service docstring."""
        params = _messages_query_params(app)

        assert "最新" in (params["offset"].get("description") or "")
        assert "clamp" in (params["limit"].get("description") or "")
        assert "500" in (params["limit"].get("description") or "")
        assert "最新" in (ConversationService.list_messages.__doc__ or "")

    async def test_list_waker_conversations(self, client):
        resp = await client.get("/api/wakers/alice/conversations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
