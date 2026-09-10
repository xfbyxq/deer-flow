"""群内消息 MCP 工具（post_group_message）测试.

覆盖：成员校验（同群/跨群/非群会话）、消息写入（meta.kind=leader_post）。
"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.mcp.collab import CollabMCPService
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group, GroupMember
from app.models.waker import Waker


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
def service(test_session_factory):
    return CollabMCPService(test_session_factory)


async def _seed(session_factory, *, with_member: bool = True, direct: bool = False):
    async with session_factory() as db:
        db.add(Waker(name="leader-lu", deer_user="", description="", soul_summary=""))
        db.add(Waker(name="member-li", deer_user="", description="", soul_summary=""))
        db.add(Group(id="g-1", name="协作群", leader_waker_id="leader-lu"))
        if with_member:
            db.add(GroupMember(group_id="g-1", waker_id="leader-lu", role="leader"))
        now = datetime.now(UTC)
        if direct:
            db.add(
                Conversation(
                    id="conv-direct",
                    scope="direct",
                    waker_id="leader-lu",
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            db.add(
                Conversation(
                    id="conv-g1",
                    scope="group",
                    group_id="g-1",
                    created_at=now,
                    updated_at=now,
                )
            )
        await db.commit()


@pytest.mark.asyncio
async def test_post_group_message_writes_leader_post(service, test_session_factory):
    """同群成员发布成功：写入 role=waker 消息，meta 标记 leader_post/partial."""
    await _seed(test_session_factory)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-g1",
        content="任务清单：@member-li 查资料 A，今天完成。",
        mentions=["member-li"],
    )

    assert result["ok"] is True
    assert result["message_id"]
    async with test_session_factory() as db:
        msgs = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == "conv-g1"
                )
            )
        ).scalars().all()
    assert len(msgs) == 1
    assert msgs[0].role == "waker"
    assert msgs[0].waker_id == "leader-lu"
    parsed = json.loads(msgs[0].content_json or "{}")
    assert parsed["text"] == "任务清单：@member-li 查资料 A，今天完成。"
    assert parsed["meta"]["kind"] == "leader_post"
    assert parsed["meta"]["partial"] is True
    assert parsed["meta"]["mentions"] == ["member-li"]


@pytest.mark.asyncio
async def test_post_group_message_rejects_non_member(service, test_session_factory):
    """非群成员（未加入群）发布被拒."""
    await _seed(test_session_factory, with_member=False)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-g1",
        content="你好",
    )

    assert "error" in result
    assert "not a member" in result["error"]


@pytest.mark.asyncio
async def test_post_group_message_rejects_direct_conversation(service, test_session_factory):
    """直聊会话不支持群发消息."""
    await _seed(test_session_factory, direct=True)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-direct",
        content="你好",
    )

    assert result["error"] == "Not a group conversation"


@pytest.mark.asyncio
async def test_post_group_message_rejects_unknown_conversation(service, test_session_factory):
    """会话不存在 → 报错."""
    await _seed(test_session_factory)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-nope",
        content="你好",
    )

    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_post_group_message_rejects_empty_content(service, test_session_factory):
    """空内容 → 报错."""
    await _seed(test_session_factory)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-g1",
        content="   ",
    )

    assert result["error"] == "content is required"
