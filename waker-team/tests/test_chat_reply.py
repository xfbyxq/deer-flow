"""对话回复引擎测试：会话消息 → DeerFlow run → 回复写回.

覆盖：
- 直聊会话由 waker 回复、群组会话由 Leader 回复、无 Leader 不回复；
- 会话 thread 持久化（复用历史 thread，不重复创建）；
- run 失败/超时 不写回复；
- API 层：用户消息触发回复调度，waker 消息不触发。
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group
from app.models.waker import Waker
from app.services.chat_reply import ChatReplyService


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
    client.create_thread = AsyncMock(return_value={"thread_id": "t-new"})
    client.create_run = AsyncMock(return_value={"run_id": "run-1"})
    # 第一次轮询 running，第二次 success（保证经过轮询路径）
    client.get_run = AsyncMock(
        side_effect=[{"status": "running"}, {"status": "success"}]
    )
    client.get_thread_state = AsyncMock(
        return_value={
            "values": {
                "messages": [
                    {
                        "type": "human",
                        "content": "你好",
                        "additional_kwargs": {"run_id": "run-1"},
                    },
                    {
                        "type": "system",
                        "content": "hidden",
                        "additional_kwargs": {"hide_from_ui": True},
                    },
                    {
                        "type": "ai",
                        "content": "我是小张，很高兴为你服务。",
                        "additional_kwargs": {"run_id": "run-1"},
                    },
                ]
            }
        }
    )
    return client


@pytest.fixture
def service(test_session_factory, mock_deerflow):
    return ChatReplyService(
        test_session_factory, mock_deerflow, poll_interval=0.01, reply_timeout=2.0
    )


async def _seed_waker(session_factory, name: str):
    async with session_factory() as db:
        db.add(Waker(name=name, deer_user="", description=f"waker {name}", soul_summary=""))
        await db.commit()


async def _seed_conversation(session_factory, conv_id: str, waker_id=None, group_id=None, thread_id=None):
    async with session_factory() as db:
        db.add(
            Conversation(
                id=conv_id,
                scope="direct" if waker_id else "group",
                waker_id=waker_id,
                group_id=group_id,
                thread_id=thread_id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        await db.commit()


async def _add_user_message(session_factory, conv_id: str, text: str):
    async with session_factory() as db:
        db.add(
            ConversationMessage(
                conversation_id=conv_id,
                role="user",
                waker_id=None,
                content_json=json.dumps({"text": text}, ensure_ascii=False),
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()


async def _get_reply_messages(session_factory, conv_id: str):
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conv_id,
                    ConversationMessage.role == "waker",
                )
            )
        ).scalars().all()
        return list(rows)


# ------------------------------------------------------------------
# 1. 直聊：waker 回复
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_conversation_gets_waker_reply(
    service, test_session_factory, mock_deerflow
):
    """直聊用户消息 → 触发 waker run → 回复写入会话（role=waker）."""
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-1", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-1", "你好，介绍一下你自己")

    await service.reply_once("conv-1")

    # run 以 agent_name=alice 发起，并携带 waker_identity 凭据（否则 MCP 工具被拒）
    mock_deerflow.create_thread.assert_awaited_once()
    kwargs = mock_deerflow.create_run.await_args
    assert kwargs.kwargs["body"]["config"]["configurable"]["agent_name"] == "alice"
    assert (
        kwargs.kwargs["body"]["config"]["context"]["secrets"]["waker_identity"] == "alice"
    )

    replies = await _get_reply_messages(test_session_factory, "conv-1")
    assert len(replies) == 1
    assert replies[0].waker_id == "alice"
    assert "小张" in (replies[0].content_json or "")

    # thread_id 持久化到会话
    async with test_session_factory() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == "conv-1"))
        ).scalars().first()
        assert conv.thread_id


@pytest.mark.asyncio
async def test_existing_thread_not_recreated(service, test_session_factory, mock_deerflow):
    """会话已有 thread 时直接复用（多轮对话共享同一 DeerFlow thread）."""
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(
        test_session_factory, "conv-2", waker_id="alice", thread_id="existing-thread"
    )
    await _add_user_message(test_session_factory, "conv-2", "继续")

    await service.reply_once("conv-2")

    mock_deerflow.create_thread.assert_not_awaited()
    assert mock_deerflow.create_run.await_args.args[0] == "existing-thread"
    assert len(await _get_reply_messages(test_session_factory, "conv-2")) == 1


# ------------------------------------------------------------------
# 2. 群聊：Leader 回复
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_conversation_uses_leader(
    service, test_session_factory, mock_deerflow
):
    """群会话由 Leader 回复."""
    await _seed_waker(test_session_factory, "leader-bob")
    async with test_session_factory() as db:
        db.add(Group(id="g-1", name="测试群", leader_waker_id="leader-bob"))
        await db.commit()
    await _seed_conversation(test_session_factory, "gconv-1", group_id="g-1")
    await _add_user_message(test_session_factory, "gconv-1", "大家好")

    await service.reply_once("gconv-1")

    assert (
        mock_deerflow.create_run.await_args.kwargs["body"]["config"]["configurable"][
            "agent_name"
        ]
        == "leader-bob"
    )
    assert (
        mock_deerflow.create_run.await_args.kwargs["body"]["config"]["context"][
            "secrets"
        ]["waker_identity"]
        == "leader-bob"
    )
    replies = await _get_reply_messages(test_session_factory, "gconv-1")
    assert len(replies) == 1
    assert replies[0].waker_id == "leader-bob"


@pytest.mark.asyncio
async def test_group_without_leader_skips(service, test_session_factory, mock_deerflow):
    """无 Leader 的群 → 不触发 run、不写回复."""
    async with test_session_factory() as db:
        db.add(Group(id="g-2", name="无主群", leader_waker_id=None))
        await db.commit()
    await _seed_conversation(test_session_factory, "gconv-2", group_id="g-2")
    await _add_user_message(test_session_factory, "gconv-2", "有人在吗")

    await service.reply_once("gconv-2")

    mock_deerflow.create_run.assert_not_awaited()
    assert len(await _get_reply_messages(test_session_factory, "gconv-2")) == 0


# ------------------------------------------------------------------
# 3. 失败路径
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_failure_writes_notice(service, test_session_factory, mock_deerflow):
    """run 终态为 error → 不写 waker 回复，但写入系统失败提示（避免静默）."""
    mock_deerflow.get_run = AsyncMock(return_value={"status": "error"})
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-3", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-3", "宕机测试")

    await service.reply_once("conv-3")

    assert len(await _get_reply_messages(test_session_factory, "conv-3")) == 0
    async with test_session_factory() as db:
        system_msgs = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == "conv-3",
                    ConversationMessage.role == "system",
                )
            )
        ).scalars().all()
    assert len(system_msgs) == 1
    assert "回复生成失败" in (system_msgs[0].content_json or "")


@pytest.mark.asyncio
async def test_reply_timeout_writes_notice(test_session_factory, mock_deerflow):
    """run 一直 running → 超时退出，不写 waker 回复但写系统提示."""
    mock_deerflow.get_run = AsyncMock(return_value={"status": "running"})
    service = ChatReplyService(
        test_session_factory, mock_deerflow, poll_interval=0.01, reply_timeout=0.05
    )
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-4", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-4", "超时测试")

    await service.reply_once("conv-4")

    assert len(await _get_reply_messages(test_session_factory, "conv-4")) == 0
    async with test_session_factory() as db:
        system_msgs = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == "conv-4",
                    ConversationMessage.role == "system",
                )
            )
        ).scalars().all()
    assert len(system_msgs) == 1


# ------------------------------------------------------------------
# 3.5 思考段剔除（回复不得混入 <think> 内容）
# ------------------------------------------------------------------


def test_strip_think():
    """_strip_think：剔除思考段，未闭合/剔空则整条不可用."""
    from app.services.chat_reply import _strip_think

    assert _strip_think("<think>推理中…</think>\n\n正式回复。") == "正式回复。"
    assert _strip_think("推理尾</think>\n\n正式回复。") == "正式回复。"
    assert _strip_think("<think>全是思考</think>") is None
    assert _strip_think("<think>未闭合的思考") is None
    assert _strip_think("正常回复") == "正常回复"
    assert _strip_think("   ") is None


@pytest.mark.asyncio
async def test_reply_strips_think_segment(service, test_session_factory, mock_deerflow):
    """集成：thread state 含思考段时，写回的回复只保留正式内容."""
    mock_deerflow.get_thread_state = AsyncMock(
        return_value={
            "values": {
                "messages": [
                    {
                        "type": "ai",
                        "content": "<think>先查工具再回答</think>\n\n成员：ui-test-agent、zww。",
                        "additional_kwargs": {"run_id": "run-1"},
                    }
                ]
            }
        }
    )
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-think", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-think", "查同事")

    await service.reply_once("conv-think")

    replies = await _get_reply_messages(test_session_factory, "conv-think")
    assert len(replies) == 1
    content = replies[0].content_json or ""
    assert "ui-test-agent" in content
    assert "think" not in content.lower()
    assert "先查工具" not in content


# ------------------------------------------------------------------
# 4. API 层：用户消息触发回复调度
# ------------------------------------------------------------------


@pytest.fixture
def app(test_session_factory, mock_deerflow):
    application = create_app()
    application.state.db_session_factory = test_session_factory
    application.state.deerflow = mock_deerflow
    application.state.chat_reply = MagicMock()
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


@pytest.mark.asyncio
async def test_user_message_schedules_reply(app, client, test_session_factory):
    """POST 用户消息 → 触发 schedule_reply."""
    await _seed_conversation(test_session_factory, "conv-api-1", waker_id="alice")
    resp = await client.post(
        "/api/conversations/conv-api-1/messages",
        json={"role": "user", "content_json": {"text": "你好"}},
    )
    assert resp.status_code == 201
    app.state.chat_reply.schedule_reply.assert_called_once_with("conv-api-1")


@pytest.mark.asyncio
async def test_waker_message_does_not_schedule_reply(app, client, test_session_factory):
    """waker 自身消息（如工具回写）不触发回复."""
    await _seed_conversation(test_session_factory, "conv-api-2", waker_id="alice")
    resp = await client.post(
        "/api/conversations/conv-api-2/messages",
        json={"role": "waker", "waker_id": "alice", "content_json": {"text": "回复内容"}},
    )
    assert resp.status_code == 201
    app.state.chat_reply.schedule_reply.assert_not_called()
