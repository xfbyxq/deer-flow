"""对话回复引擎测试：会话消息 → DeerFlow run → 回复写回.

覆盖：
- 直聊会话由 waker 回复、群组会话由 Leader 回复、无 Leader 不回复；
- 会话 thread 持久化（复用历史 thread，不重复创建）；
- run 失败/超时 不写回复；
- API 层：用户消息触发回复调度，waker 消息不触发。
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import asyncio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.main import create_app
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group, GroupMember
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
    client.cancel_run = AsyncMock(return_value=MagicMock())
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


async def _get_system_messages(session_factory, conv_id: str):
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == conv_id,
                    ConversationMessage.role == "system",
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
    # 递归预算与主 UI 一致（默认 100 易被搜索重试类场景耗尽导致 run 报错）
    assert kwargs.kwargs["body"]["config"]["recursion_limit"] == 1000
    # 直聊不注入群组协作规程（仅群会话注入）
    assert len(kwargs.kwargs["body"]["input"]["messages"]) == 1

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
# 2.5 群组协作：规程注入 + 成员汇报可视化
# ------------------------------------------------------------------


def test_build_group_protocol_includes_context():
    """build_group_protocol：动态注入群/会话 ID 与成员名单，含群发消息与委派指引."""
    from app.services.chat_reply import build_group_protocol

    text = build_group_protocol(
        "g-1", "conv-1", ["- alice（Leader）", "- bob（质量分析师）"]
    )
    assert "群组协作规程" in text
    assert "g-1" in text
    assert "conv-1" in text
    assert "alice（Leader）" in text
    assert "bob（质量分析师）" in text
    assert "post_group_message" in text  # 发布清单工具指引
    assert "delegate_to_agent" in text
    assert "简单任务" in text  # 分级分流保留


def test_extract_delegations_pairs_calls_and_results():
    """_extract_delegations：delegate 调用与结果按序配对（含成功/错误）."""
    from app.services.chat_reply import _extract_delegations

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "旧轮", "additional_kwargs": {}},
                {
                    "type": "human",
                    "content": "帮我调研",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {"name": "waker-team_query_group", "args": {"group_id": "default"}},
                        {
                            "name": "waker-team_delegate_to_agent",
                            "args": {"target_agent": "bob", "instruction": "查资料 A"},
                        },
                    ],
                },
                {
                    "type": "tool",
                    "name": "waker-team_query_group",
                    "content": '{"members": []}',
                },
                {
                    "type": "tool",
                    "name": "waker-team_delegate_to_agent",
                    "content": json.dumps(
                        {"ticket_id": "t1", "status": "done", "result": "资料 A 完成"},
                        ensure_ascii=False,
                    ),
                },
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "waker-team_delegate_to_agent",
                            "args": {"target_agent": "carl", "instruction": "查资料 B"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "waker-team_delegate_to_agent",
                    "content": json.dumps(
                        {"error": "Target agent is disabled: carl"}, ensure_ascii=False
                    ),
                },
            ]
        }
    }
    ds = _extract_delegations(state)
    assert len(ds) == 2
    assert ds[0] == {
        "target": "bob",
        "instruction": "查资料 A",
        "status": "done",
        "result": "资料 A 完成",
        "error": None,
    }
    assert ds[1]["target"] == "carl"
    assert ds[1]["status"] == "error"
    assert "disabled" in (ds[1]["error"] or "")


def test_diff_delegation_events_incremental_and_idempotent():
    """_diff_delegation_events：增量返回新事件，重复解析幂等（去重）."""
    from app.services.chat_reply import _diff_delegation_events

    def _state(calls, results):
        messages = [
            {"type": "human", "content": "开始", "additional_kwargs": {"run_id": "run-1"}}
        ]
        for c in calls:
            messages.append({"type": "ai", "content": "", "tool_calls": [c]})
        for r in results:
            messages.append(
                {"type": "tool", "name": "waker-team_delegate_to_agent", "content": r}
            )
        return {"values": {"messages": messages}}

    call_a = {
        "name": "waker-team_delegate_to_agent",
        "args": {"target_agent": "bob", "instruction": "查资料 A"},
    }
    result_a = json.dumps(
        {"ticket_id": "t1", "status": "done", "result": "A 完成"}, ensure_ascii=False
    )
    state = _state([call_a], [result_a])

    seen = {"dispatches": 0, "reports": 0}
    events = _diff_delegation_events(state, seen)
    assert [e["event"] for e in events] == ["dispatch", "report"]
    assert events[0]["target"] == "bob"
    assert events[1]["status"] == "done"
    assert events[1]["result"] == "A 完成"

    # 幂等：同一 state 再解析不再产生事件
    assert _diff_delegation_events(state, seen) == []

    # 增长：追加第二个成员调用后，仅返回新增 dispatch
    call_b = {
        "name": "waker-team_delegate_to_agent",
        "args": {"target_agent": "carl", "instruction": "查资料 B"},
    }
    events2 = _diff_delegation_events(_state([call_a, call_b], [result_a]), seen)
    assert [e["event"] for e in events2] == ["dispatch"]
    assert events2[0]["target"] == "carl"


def test_scan_delegation_events_includes_async_submit():
    """delegate_submit（异步）计入 dispatch（mode=async），不产生 report."""
    from app.services.chat_reply import _scan_delegation_events

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "开始", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "waker-team_delegate_submit",
                            "args": {"target": "bob", "instruction": "异步任务"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "waker-team_delegate_submit",
                    "content": json.dumps({"ticket_id": "t9", "status": "submitted"}),
                },
            ]
        }
    }
    dispatches, reports = _scan_delegation_events(state)
    assert len(dispatches) == 1
    assert dispatches[0]["mode"] == "async"
    assert dispatches[0]["target"] == "bob"
    assert reports == []


@pytest.mark.asyncio
async def test_group_reply_injects_protocol_and_member_reports(
    service, test_session_factory, mock_deerflow
):
    """群会话：run 注入动态协作规程；派活/汇报实时落群，先于 Leader 汇总."""
    await _seed_waker(test_session_factory, "leader-lu")
    await _seed_waker(test_session_factory, "member-li")
    async with test_session_factory() as db:
        db.add(Group(id="g-coop", name="协作群", leader_waker_id="leader-lu"))
        db.add(GroupMember(group_id="g-coop", waker_id="leader-lu", role="leader"))
        db.add(GroupMember(group_id="g-coop", waker_id="member-li", role="member"))
        await db.commit()
    await _seed_conversation(test_session_factory, "gconv-coop", group_id="g-coop")
    await _add_user_message(test_session_factory, "gconv-coop", "帮我调研一下")

    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {
                    "type": "human",
                    "content": "帮我调研一下",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "waker-team_delegate_to_agent",
                            "args": {"target_agent": "member-li", "instruction": "查资料 A"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "waker-team_delegate_to_agent",
                    "content": json.dumps(
                        {"ticket_id": "t1", "status": "done", "result": "资料 A 研究完成"},
                        ensure_ascii=False,
                    ),
                },
                {
                    "type": "ai",
                    "content": "汇总：成员完成了资料 A 调研。",
                    "additional_kwargs": {"run_id": "run-1"},
                },
            ]
        }
    }

    await service.reply_once("gconv-coop")

    # run 注入：首条为 system 协作规程（含动态群上下文与分级分流规则），末条为用户消息
    body_messages = mock_deerflow.create_run.await_args.kwargs["body"]["input"]["messages"]
    assert body_messages[0]["role"] == "system"
    assert "群组协作规程" in body_messages[0]["content"]
    assert "简单任务" in body_messages[0]["content"]  # 分级分流：小事直接回答
    assert "g-coop" in body_messages[0]["content"]  # 动态注入群 ID
    assert "gconv-coop" in body_messages[0]["content"]  # 动态注入会话 ID
    assert "leader-lu（Leader）" in body_messages[0]["content"]  # 成员名单
    assert "member-li（member）" in body_messages[0]["content"]
    assert body_messages[-1] == {"role": "user", "content": "帮我调研一下"}

    # 群消息顺序：user → 派活（Leader）→ 成员汇报（成员）→ Leader 汇总（Leader）
    async with test_session_factory() as db:
        msgs = (
            await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == "gconv-coop")
                .order_by(ConversationMessage.created_at.asc())
            )
        ).scalars().all()
    assert [(m.role, m.waker_id) for m in msgs] == [
        ("user", None),
        ("waker", "leader-lu"),
        ("waker", "member-li"),
        ("waker", "leader-lu"),
    ]
    assert "【派活】@member-li" in (msgs[1].content_json or "")
    assert '"partial": true' in (msgs[1].content_json or "")  # 过程消息标记
    assert "【成员汇报 · member-li】" in (msgs[2].content_json or "")
    assert "资料 A 研究完成" in (msgs[2].content_json or "")
    assert "汇总：成员完成了资料 A 调研。" in (msgs[3].content_json or "")
    # Leader 汇总不带 partial 标记（前端据此结束等待）
    assert "partial" not in (msgs[3].content_json or "")


@pytest.mark.asyncio
async def test_flush_group_events_writes_realtime_dispatch_and_report(
    service, test_session_factory
):
    """_flush_group_delegation_events：增量写入派活/汇报消息（带 partial 标记），重复无损."""
    await _seed_waker(test_session_factory, "leader-lu")
    await _seed_waker(test_session_factory, "member-li")
    await _seed_conversation(test_session_factory, "gconv-rt", group_id="g-rt")

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "开始", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "waker-team_delegate_to_agent",
                            "args": {"target_agent": "member-li", "instruction": "查资料 A"},
                        }
                    ],
                },
                {
                    "type": "tool",
                    "name": "waker-team_delegate_to_agent",
                    "content": json.dumps(
                        {"ticket_id": "t1", "status": "done", "result": "资料 A 完成"},
                        ensure_ascii=False,
                    ),
                },
            ]
        }
    }
    seen = {"dispatches": 0, "reports": 0}
    await service._flush_group_delegation_events(
        "gconv-rt", "leader-lu", state, seen, datetime.now(UTC) - timedelta(minutes=1), {}
    )

    async with test_session_factory() as db:
        msgs = (
            await db.execute(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == "gconv-rt")
                .order_by(ConversationMessage.created_at.asc())
            )
        ).scalars().all()
    assert len(msgs) == 2
    assert msgs[0].waker_id == "leader-lu"
    assert "【派活】@member-li" in (msgs[0].content_json or "")
    assert '"kind": "dispatch"' in (msgs[0].content_json or "")
    assert msgs[1].waker_id == "member-li"
    assert "【成员汇报 · member-li】" in (msgs[1].content_json or "")
    assert '"kind": "report"' in (msgs[1].content_json or "")

    # 幂等：同一 state 再次 flush 不重复写入
    await service._flush_group_delegation_events(
        "gconv-rt", "leader-lu", state, seen, datetime.now(UTC) - timedelta(minutes=1), {}
    )
    async with test_session_factory() as db:
        count = len(
            (
                await db.execute(
                    select(ConversationMessage).where(
                        ConversationMessage.conversation_id == "gconv-rt"
                    )
                )
            ).scalars().all()
        )
    assert count == 2


@pytest.mark.asyncio
async def test_flush_group_events_skips_dispatch_when_leader_post_exists(
    service, test_session_factory
):
    """清单优先：Leader 已发布任务清单（leader_post）时跳过兜底派活卡."""
    await _seed_waker(test_session_factory, "leader-lu")
    await _seed_conversation(test_session_factory, "gconv-post", group_id="g-post")
    # 预置一条 leader_post 消息（等价于 run 期间 post_group_message 发布）
    async with test_session_factory() as db:
        db.add(
            ConversationMessage(
                conversation_id="gconv-post",
                role="waker",
                waker_id="leader-lu",
                content_json=json.dumps(
                    {
                        "text": "任务清单：@member-li 查资料 A",
                        "meta": {"kind": "leader_post", "partial": True},
                    },
                    ensure_ascii=False,
                ),
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "开始", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "waker-team_delegate_to_agent",
                            "args": {"target_agent": "member-li", "instruction": "查资料 A"},
                        }
                    ],
                },
            ]
        }
    }
    seen = {"dispatches": 0, "reports": 0}
    await service._flush_group_delegation_events(
        "gconv-post", "leader-lu", state, seen, datetime.now(UTC) - timedelta(minutes=1), {}
    )

    async with test_session_factory() as db:
        msgs = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == "gconv-post"
                )
            )
        ).scalars().all()
    # 只有预置的 leader_post，没有兜底派活卡
    assert len(msgs) == 1
    assert "【派活】" not in (msgs[0].content_json or "")


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


@pytest.mark.asyncio
async def test_create_run_failure_writes_notice(service, test_session_factory, mock_deerflow):
    """create_run 抛异常（如网关重启断连）→ 写系统失败提示（不静默）."""
    from app.deerflow.errors import DeerFlowUnavailableError

    mock_deerflow.create_run = AsyncMock(side_effect=DeerFlowUnavailableError("boom"))
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-connfail", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-connfail", "断连测试")

    await service.reply_once("conv-connfail")

    async with test_session_factory() as db:
        system_msgs = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == "conv-connfail",
                    ConversationMessage.role == "system",
                )
            )
        ).scalars().all()
    assert len(system_msgs) == 1
    assert "回复生成失败" in (system_msgs[0].content_json or "")


# ------------------------------------------------------------------
# 3.7 会话标题：从 thread state 写回（DeerFlow TitleMiddleware 生成）
# ------------------------------------------------------------------


def test_extract_title():
    """_extract_title：仅提取非空字符串标题."""
    from app.services.chat_reply import _extract_title

    assert _extract_title({"values": {"title": "查询团队成员列表"}}) == "查询团队成员列表"
    assert _extract_title({"values": {"title": "  带空格  "}}) == "带空格"
    assert _extract_title({"values": {"title": ""}}) is None
    assert _extract_title({"values": {"title": None}}) is None
    assert _extract_title({"values": {}}) is None
    assert _extract_title({}) is None


@pytest.mark.asyncio
async def test_title_written_from_thread_state(service, test_session_factory, mock_deerflow):
    """回复写回后，thread state 的 title 应写入空的会话标题（修复“未命名会话”）."""
    mock_deerflow.get_thread_state.return_value["values"]["title"] = "查询团队成员列表"

    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-title", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-title", "查一下团队成员")

    await service.reply_once("conv-title")

    async with test_session_factory() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == "conv-title"))
        ).scalars().first()
    assert conv.title == "查询团队成员列表"


@pytest.mark.asyncio
async def test_existing_title_not_overwritten(service, test_session_factory, mock_deerflow):
    """会话已有标题时不被 thread title 覆盖（已有命名优先）."""
    mock_deerflow.get_thread_state.return_value["values"]["title"] = "自动生成的标题"

    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-named", waker_id="alice")
    async with test_session_factory() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == "conv-named"))
        ).scalars().first()
        conv.title = "用户命名"
        await db.commit()
    await _add_user_message(test_session_factory, "conv-named", "你好")

    await service.reply_once("conv-named")

    async with test_session_factory() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == "conv-named"))
        ).scalars().first()
    assert conv.title == "用户命名"


# ------------------------------------------------------------------
# 3.8 回复提取：ask_clarification（结构化澄清 + 文本合并）
# ------------------------------------------------------------------


def _clarification_artifact(question: str = "请选择方向") -> dict:
    return {
        "human_input": {
            "version": 1,
            "kind": "human_input_request",
            "source": "ask_clarification",
            "request_id": "clarification:call-1",
            "tool_call_id": "call-1",
            "clarification_type": "approach_choice",
            "question": question,
            "input_mode": "choice_with_other",
            "options": [
                {"id": "option-1", "label": "方向 A", "value": "方向 A"},
                {"id": "option-2", "label": "方向 B", "value": "方向 B"},
            ],
        }
    }


def test_extract_reply_ask_clarification_fallback():
    """无 AI 正文且无结构化 payload 时，回退取 ask_clarification 工具文本."""
    from app.services.chat_reply import _extract_reply_from_state

    state = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "思考中…\n</think>\n\n",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {"type": "tool", "name": "list_uploaded_files", "content": '{"files": []}'},
                {
                    "type": "tool",
                    "name": "ask_clarification",
                    "content": "请问您想要哪个方向？",
                },
            ]
        }
    }
    assert _extract_reply_from_state(state, "run-1") == ("请问您想要哪个方向？", None)


def test_extract_reply_merges_ai_text_with_clarification():
    """有引导语正文 + 澄清（无 payload）时合并返回，不丢澄清."""
    from app.services.chat_reply import _extract_reply_from_state

    state = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "这是正式回复。",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {"type": "tool", "name": "ask_clarification", "content": "问题"},
            ]
        }
    }
    assert _extract_reply_from_state(state, "run-1") == ("这是正式回复。\n\n问题", None)


def test_extract_reply_returns_structured_clarification_payload():
    """澄清带 artifact.human_input 时返回结构化 payload，正文与澄清分离."""
    from app.services.chat_reply import _extract_reply_from_state

    state = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "好的，请确认以下问题：",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "tool",
                    "name": "ask_clarification",
                    "content": "🔀 请选择方向\n\n  1. 方向 A\n  2. 方向 B",
                    "artifact": _clarification_artifact(),
                },
            ]
        }
    }
    text, clarification = _extract_reply_from_state(state, "run-1")
    assert text == "好的，请确认以下问题："
    assert clarification is not None
    assert clarification["kind"] == "human_input_request"
    assert clarification["question"] == "请选择方向"
    assert clarification["input_mode"] == "choice_with_other"
    assert len(clarification["options"]) == 2


def test_extract_reply_ignores_old_clarification_after_new_run_text():
    """历史澄清在新一轮 run 正文之前时，不误判为本轮澄清."""
    from app.services.chat_reply import _extract_reply_from_state

    state = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": "ask_clarification", "id": "call-1", "args": {}}],
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "tool",
                    "name": "ask_clarification",
                    "content": "🔀 请选择方向",
                    "artifact": _clarification_artifact(),
                },
                {"type": "human", "content": "选方向 A"},
                {
                    "type": "ai",
                    "content": "报告如下……",
                    "additional_kwargs": {"run_id": "run-2"},
                },
            ]
        }
    }
    text, clarification = _extract_reply_from_state(state, "run-2")
    assert text == "报告如下……"
    assert clarification is None


@pytest.mark.asyncio
async def test_reply_from_ask_clarification(service, test_session_factory, mock_deerflow):
    """模型全部输出在思考段 + ask_clarification（无 payload）时，提问文本合并写回."""
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "let me think …</think>\n\n",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "tool",
                    "name": "ask_clarification",
                    "content": "🤔 需要先确认：您想基于哪类依据来定选题？",
                },
            ]
        }
    }
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-ask", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-ask", "帮我规划选题")

    await service.reply_once("conv-ask")

    replies = await _get_reply_messages(test_session_factory, "conv-ask")
    assert len(replies) == 1
    assert "需要先确认" in (replies[0].content_json or "")


@pytest.mark.asyncio
async def test_reply_writes_clarification_meta(service, test_session_factory, mock_deerflow):
    """澄清 run：写回消息携带 meta.clarification（结构化 payload），正文保留."""
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {
                    "type": "ai",
                    "content": "好的，请确认以下问题：",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "tool",
                    "name": "ask_clarification",
                    "content": "🔀 请选择方向",
                    "artifact": _clarification_artifact(),
                },
            ]
        }
    }
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-clarify", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-clarify", "帮我定选题")

    await service.reply_once("conv-clarify")

    replies = await _get_reply_messages(test_session_factory, "conv-clarify")
    assert len(replies) == 1
    stored = json.loads(replies[0].content_json or "{}")
    assert stored["text"] == "好的，请确认以下问题："
    assert stored["meta"]["clarification"]["question"] == "请选择方向"
    assert stored["meta"]["clarification"]["request_id"] == "clarification:call-1"


# ------------------------------------------------------------------
# 3.9 进度快照：思考/工具步骤（等待期实时展示）
# ------------------------------------------------------------------


def test_extract_progress_steps_and_current():
    """_extract_progress：工具链步骤 + 当前动作（进行中的工具）."""
    from app.services.chat_reply import _extract_progress

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "旧消息", "additional_kwargs": {}},
                {"type": "ai", "content": "旧的回复"},
                {
                    "type": "human",
                    "content": "帮我查资料",
                    "additional_kwargs": {"run_id": "run-1"},
                },
                {
                    "type": "ai",
                    "content": "thinking…",
                    "tool_calls": [{"name": "web_search", "args": {"query": "3D打印 展会 2026"}}],
                },
                {"type": "tool", "name": "web_search", "content": "5 条结果：IFA 2026 …"},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": "web_fetch", "args": {"url": "https://x.com/a"}}],
                },
            ]
        }
    }
    steps, current = _extract_progress(state, "run-1")
    assert len(steps) == 2
    assert steps[0] == {
        "name": "web_search",
        "detail": "3D打印 展会 2026",
        "done": True,
        "result": "5 条结果：IFA 2026 …",
    }
    assert steps[1]["name"] == "web_fetch"
    assert steps[1]["done"] is False
    assert current == {"kind": "tool", "name": "web_fetch", "detail": "https://x.com/a"}


def test_extract_progress_current_thinking():
    """最后一条为 tool 结果 → 当前动作=正在整理信息."""
    from app.services.chat_reply import _extract_progress

    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "hi", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": "web_search", "args": {"query": "x"}}],
                },
                {"type": "tool", "name": "web_search", "content": "res"},
            ]
        }
    }
    steps, current = _extract_progress(state, "run-1")
    assert steps[0]["done"] is True
    assert current["kind"] == "thinking"
    assert "整理信息" in current["detail"]


@pytest.mark.asyncio
async def test_get_progress_inactive_and_active(service, mock_deerflow):
    """未有进行中 run → active=False；注册后 → 返回步骤/当前动作/耗时."""
    assert await service.get_progress("conv-x") == {"active": False}

    # 手动注册（等价于 reply_once 进行中）
    service._active_runs["conv-x"] = {
        "thread_id": "t-1",
        "run_id": "run-1",
        "target": "alice",
        "started_at": datetime.now(UTC),
    }
    mock_deerflow.get_thread_state.return_value = {
        "values": {
            "messages": [
                {"type": "human", "content": "hi", "additional_kwargs": {}},
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": "web_search", "args": {"query": "题"}}],
                },
            ]
        }
    }
    prog = await service.get_progress("conv-x")
    assert prog["active"] is True
    assert prog["target"] == "alice"
    assert prog["steps"][0]["name"] == "web_search"
    assert prog["current"]["kind"] == "tool"
    assert prog["elapsed"] >= 0


@pytest.mark.asyncio
async def test_reply_once_clears_active_run(service, test_session_factory, mock_deerflow):
    """回复完成后清除进行中注册（进度回到 active=False）."""
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-prog", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-prog", "你好")

    await service.reply_once("conv-prog")

    assert await service.get_progress("conv-prog") == {"active": False}


# ------------------------------------------------------------------
# 3.10 用户主动停止：取消 run + 写「已停止」提示
# ------------------------------------------------------------------


def _register_active_run(service, conv_id: str, thread_id: str = "t-1", run_id: str = "run-1"):
    service._active_runs[conv_id] = {
        "thread_id": thread_id,
        "run_id": run_id,
        "target": "alice",
        "started_at": datetime.now(UTC),
    }


@pytest.mark.asyncio
async def test_stop_reply_without_active_run(service, mock_deerflow):
    """无进行中回复 → 幂等返回 stopped=False，不调 cancel."""
    result = await service.stop_reply("conv-idle")

    assert result == {"stopped": False}
    mock_deerflow.cancel_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_reply_cancels_and_notifies(service, test_session_factory, mock_deerflow):
    """进行中回复被停止：cancel_run 被调、写「已停止」系统提示、返回 stopped=True."""
    await _seed_conversation(test_session_factory, "conv-stop", waker_id="alice")
    _register_active_run(service, "conv-stop")

    result = await service.stop_reply("conv-stop")

    assert result == {"stopped": True}
    mock_deerflow.cancel_run.assert_awaited_once_with("t-1", "run-1", wait=True)
    systems = await _get_system_messages(test_session_factory, "conv-stop")
    assert len(systems) == 1
    assert "已停止" in (systems[0].content_json or "")


@pytest.mark.asyncio
async def test_stop_reply_cancel_failure_returns_false(
    service, test_session_factory, mock_deerflow
):
    """cancel 失败（run 已终态等）→ 不写提示、清除停止标记、返回 stopped=False."""
    from app.deerflow.errors import TaskConflictError

    mock_deerflow.cancel_run = AsyncMock(side_effect=TaskConflictError("already terminal"))
    await _seed_conversation(test_session_factory, "conv-stopfail", waker_id="alice")
    _register_active_run(service, "conv-stopfail")

    result = await service.stop_reply("conv-stopfail")

    assert result == {"stopped": False}
    assert "conv-stopfail" not in service._stop_requested
    assert await _get_system_messages(test_session_factory, "conv-stopfail") == []


@pytest.mark.asyncio
async def test_repeated_stop_writes_notice_once(service, test_session_factory, mock_deerflow):
    """重复停止（双击/重试）：仅首次写「已停止」提示，不产生重复消息."""
    await _seed_conversation(test_session_factory, "conv-stop2x", waker_id="alice")
    _register_active_run(service, "conv-stop2x")

    first = await service.stop_reply("conv-stop2x")
    assert first == {"stopped": True}

    # 第二次停止：run 已在取消中（_active_runs 尚未清理）→ 不重复写提示
    second = await service.stop_reply("conv-stop2x")
    assert second == {"stopped": True}

    systems = await _get_system_messages(test_session_factory, "conv-stop2x")
    assert len(systems) == 1


@pytest.mark.asyncio
async def test_repeated_stop_failure_keeps_stop_marker(
    service, test_session_factory, mock_deerflow
):
    """第二次停止 cancel 失败时保留首个停止标记（等待循环仍需静默退出）."""
    from app.deerflow.errors import TaskConflictError

    await _seed_conversation(test_session_factory, "conv-stopmark", waker_id="alice")
    _register_active_run(service, "conv-stopmark")

    first = await service.stop_reply("conv-stopmark")
    assert first == {"stopped": True}

    mock_deerflow.cancel_run = AsyncMock(side_effect=TaskConflictError("already cancelled"))
    second = await service.stop_reply("conv-stopmark")
    assert second == {"stopped": False}
    # 标记保留：等待循环据此静默退出（不写失败提示）
    assert "conv-stopmark" in service._stop_requested


@pytest.mark.asyncio
async def test_stopped_wait_exits_without_failure_notice(
    service, test_session_factory, mock_deerflow
):
    """集成：等待中的回复被停止 → 等待循环立即退出，只写「已停止」不写失败提示."""
    mock_deerflow.get_run = AsyncMock(return_value={"status": "running"})
    await _seed_waker(test_session_factory, "alice")
    await _seed_conversation(test_session_factory, "conv-live", waker_id="alice")
    await _add_user_message(test_session_factory, "conv-live", "停一下")

    task = asyncio.create_task(service.reply_once("conv-live"))
    # 等待 run 注册（reply 已进入等待循环）
    for _ in range(200):
        if "conv-live" in service._active_runs:
            break
        await asyncio.sleep(0.01)
    assert "conv-live" in service._active_runs

    result = await service.stop_reply("conv-live")
    assert result == {"stopped": True}

    await asyncio.wait_for(task, timeout=2)

    # 不写 waker 回复；只写一条「已停止」系统提示（非失败提示）
    assert await _get_reply_messages(test_session_factory, "conv-live") == []
    systems = await _get_system_messages(test_session_factory, "conv-live")
    assert len(systems) == 1
    assert "已停止" in (systems[0].content_json or "")
    assert "回复生成失败" not in (systems[0].content_json or "")
    # 停止标记与进行中注册都已清理
    assert "conv-live" not in service._stop_requested
    assert "conv-live" not in service._active_runs


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


@pytest.mark.asyncio
async def test_defer_reply_skips_scheduling(app, client, test_session_factory):
    """defer_reply=true：消息仅入库不触发回复调度（多澄清聚合回答）."""
    await _seed_conversation(test_session_factory, "conv-api-defer", waker_id="alice")
    resp = await client.post(
        "/api/conversations/conv-api-defer/messages",
        json={
            "role": "user",
            "content_json": {"text": "回答澄清一"},
            "defer_reply": True,
        },
    )
    assert resp.status_code == 201
    app.state.chat_reply.schedule_reply.assert_not_called()

    # 最后一个回答不带 defer：正常触发处理
    resp = await client.post(
        "/api/conversations/conv-api-defer/messages",
        json={"role": "user", "content_json": {"text": "回答澄清二"}},
    )
    assert resp.status_code == 201
    app.state.chat_reply.schedule_reply.assert_called_once_with("conv-api-defer")


@pytest.mark.asyncio
async def test_stop_endpoint_delegates_to_service(app, client, test_session_factory):
    """POST /conversations/{id}/stop → 委托 chat_reply.stop_reply（转发其响应）."""
    await _seed_conversation(test_session_factory, "conv-api-stop", waker_id="alice")
    app.state.chat_reply.stop_reply = AsyncMock(return_value={"stopped": True})

    resp = await client.post("/api/conversations/conv-api-stop/stop")

    assert resp.status_code == 200
    assert resp.json() == {"stopped": True}
    app.state.chat_reply.stop_reply.assert_awaited_once_with("conv-api-stop")


@pytest.mark.asyncio
async def test_stop_endpoint_no_active_reply(app, client, test_session_factory):
    """无进行中回复 → 幂等返回 stopped=False."""
    await _seed_conversation(test_session_factory, "conv-api-stop2", waker_id="alice")
    app.state.chat_reply.stop_reply = AsyncMock(return_value={"stopped": False})

    resp = await client.post("/api/conversations/conv-api-stop2/stop")

    assert resp.status_code == 200
    assert resp.json() == {"stopped": False}


@pytest.mark.asyncio
async def test_stop_endpoint_conversation_not_found(client):
    """会话不存在 → 404."""
    resp = await client.post("/api/conversations/conv-missing/stop")

    assert resp.status_code == 404
