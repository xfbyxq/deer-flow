"""群内消息 MCP 工具（post_group_message）+ query_group 同事范围测试.

覆盖：成员校验（同群/跨群/非群会话）、消息写入（meta.kind=leader_post）、
CF12（leader 无 GroupMember 行仍属同事）、CF22（成员已确认时短路 Group 查询）。
"""

import json
import re
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.mcp.collab import CollabMCPService
from app.mcp.tasks import MCPService
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
async def test_post_group_message_allows_leader_without_member_row(
    service, test_session_factory
):
    """C7(a)：Leader 未建 GroupMember 行仍视为组内成员——放行.

    默认建群不为 leader 写 group_members 行，但 group_service 已声明
    「leader 视为组内成员」；P0 主流程第一步（Leader 发布清单）不能断。
    """
    await _seed(test_session_factory, with_member=False)

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-g1",
        content="任务清单：@member-li 查资料 A。",
        mentions=["member-li"],
    )

    assert result.get("ok") is True
    assert result.get("message_id")


@pytest.mark.asyncio
async def test_post_group_message_rejects_outsider_waker(service, test_session_factory):
    """C7(b)：既非 GroupMember 也非 leader 的外群 waker → 拒绝."""
    await _seed(test_session_factory, with_member=False)

    result = await service.post_group_message(
        caller="member-li",
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


# ---------------------------------------------------------------------------
# CF22：成员已确认时短路 Group 查询
# ---------------------------------------------------------------------------


@pytest.fixture
def sql_log(test_db_engine):
    """捕获实际下发的 SQL，用于断言 Group 查询是否被短路."""
    statements: list[str] = []

    @event.listens_for(test_db_engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
        statements.append(statement)

    return statements


def _group_selects(statements: list[str]) -> list[str]:
    """仅匹配 groups 表查询（group_members 不包含 'FROM groups'）."""
    return [s for s in statements if re.search(r"FROM\s+groups\b", s, re.IGNORECASE)]


@pytest.mark.asyncio
async def test_post_group_message_skips_group_query_when_member_confirmed(
    service, test_session_factory, sql_log
):
    """CF22：GroupMember 命中即短路，不再查 Group（省一次 DB 往返）."""
    await _seed(test_session_factory)  # with_member=True：leader-lu 有成员行
    sql_log.clear()

    result = await service.post_group_message(
        caller="leader-lu",
        conversation_id="conv-g1",
        content="任务清单：@member-li 查资料 A。",
    )

    assert result.get("ok") is True
    assert _group_selects(sql_log) == []


@pytest.mark.asyncio
async def test_post_group_message_queries_group_only_when_member_missing(
    service, test_session_factory, sql_log
):
    """CF22：member 未命中时才查 Group 判 leader——权限语义不变."""
    await _seed(test_session_factory, with_member=False)

    sql_log.clear()
    allowed = await service.post_group_message(
        caller="leader-lu", conversation_id="conv-g1", content="任务清单"
    )
    assert allowed.get("ok") is True
    assert len(_group_selects(sql_log)) == 1

    sql_log.clear()
    denied = await service.post_group_message(
        caller="member-li", conversation_id="conv-g1", content="你好"
    )
    assert "not a member" in denied["error"]
    assert len(_group_selects(sql_log)) == 1


@pytest.mark.asyncio
async def test_post_group_message_no_attribute_error_when_group_row_missing(
    service, test_session_factory
):
    """CF22：会话指向不存在的 group 行时不抛 AttributeError，而是拒绝."""
    now = datetime.now(UTC)
    async with test_session_factory() as db:
        db.add(Waker(name="ghost-wa", deer_user="", description="", soul_summary=""))
        db.add(
            Conversation(
                id="conv-orphan",
                scope="group",
                group_id="g-missing",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    result = await service.post_group_message(
        caller="ghost-wa", conversation_id="conv-orphan", content="你好"
    )

    assert "not a member" in result["error"]


# ---------------------------------------------------------------------------
# CF12：query_group 成员集合 = GroupMember ∪ Group.leader_waker_id
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_service(test_session_factory):
    """MCPService（真实内存库 + mock DeerFlowClient）."""
    return MCPService(test_session_factory, MagicMock())


async def _seed_scoped_group(
    session_factory,
    *,
    leader_member_row: bool = False,
    leader_enabled: bool = True,
) -> None:
    """建一个典型群：leader（默认无成员行）+ 启用成员 + 停用成员 + 群外员工."""
    async with session_factory() as db:
        db.add(
            Waker(
                name="leader-lu",
                deer_user="",
                description="Leader",
                soul_summary="",
                enabled=leader_enabled,
            )
        )
        db.add(Waker(name="member-li", deer_user="", description="Member", soul_summary=""))
        db.add(Waker(name="outsider-wa", deer_user="", description="Outsider", soul_summary=""))
        db.add(
            Waker(
                name="disabled-bo",
                deer_user="",
                description="Disabled",
                soul_summary="",
                enabled=False,
            )
        )
        db.add(Group(id="g-1", name="协作群", leader_waker_id="leader-lu"))
        db.add(GroupMember(group_id="g-1", waker_id="member-li", role="member"))
        db.add(GroupMember(group_id="g-1", waker_id="disabled-bo", role="member"))
        if leader_member_row:
            db.add(GroupMember(group_id="g-1", waker_id="leader-lu", role="leader"))
        await db.commit()


@pytest.mark.asyncio
async def test_query_group_includes_leader_without_member_row(
    mcp_service, test_session_factory
):
    """CF12：leader 无 GroupMember 行时仍出现在 query_group().members."""
    await _seed_scoped_group(test_session_factory)

    result = await mcp_service.query_group(group_id="g-1")

    assert {m["name"] for m in result["members"]} == {"leader-lu", "member-li"}


@pytest.mark.asyncio
async def test_query_group_leader_visible_to_members(mcp_service, test_session_factory):
    """CF12：成员查同事时能看到 Leader（群协作派活/汇总的前提）."""
    await _seed_scoped_group(test_session_factory)

    result = await mcp_service.query_group(caller="member-li")

    assert {m["name"] for m in result["members"]} == {"leader-lu", "member-li"}


@pytest.mark.asyncio
async def test_query_group_leader_sees_self_and_members(mcp_service, test_session_factory):
    """CF12：leader 自己查询时也在列表中（与 get_waker_groups 不变量一致）."""
    await _seed_scoped_group(test_session_factory)

    result = await mcp_service.query_group(caller="leader-lu")

    assert {m["name"] for m in result["members"]} == {"leader-lu", "member-li"}


@pytest.mark.asyncio
async def test_query_group_disabled_leader_excluded(mcp_service, test_session_factory):
    """CF12：enabled==True 过滤保留——停用的 leader 不进同事列表."""
    await _seed_scoped_group(test_session_factory, leader_enabled=False)

    result = await mcp_service.query_group(group_id="g-1")

    assert {m["name"] for m in result["members"]} == {"member-li"}


@pytest.mark.asyncio
async def test_query_group_leader_not_duplicated_when_member_row_exists(
    mcp_service, test_session_factory
):
    """CF12：leader 同时有 GroupMember 行时并集去重，不重复返回."""
    await _seed_scoped_group(test_session_factory, leader_member_row=True)

    result = await mcp_service.query_group(group_id="g-1")

    names = [m["name"] for m in result["members"]]
    assert names.count("leader-lu") == 1
    assert set(names) == {"leader-lu", "member-li"}


@pytest.mark.asyncio
async def test_query_group_unknown_group_returns_empty(mcp_service, test_session_factory):
    """CF12：不存在的群组 → 空列表（不报错）."""
    await _seed_scoped_group(test_session_factory)

    result = await mcp_service.query_group(group_id="no-such-group")

    assert result["members"] == []


@pytest.mark.asyncio
async def test_query_group_tolerates_null_leader(mcp_service, test_session_factory):
    """CF12：leader_waker_id 为 NULL 的群不报错、不误召其他员工."""
    async with test_session_factory() as db:
        db.add(Waker(name="solo-an", deer_user="", description="", soul_summary=""))
        db.add(Group(id="g-null", name="无Leader群", leader_waker_id=None))
        db.add(GroupMember(group_id="g-null", waker_id="solo-an", role="member"))
        await db.commit()

    result = await mcp_service.query_group(group_id="g-null")

    assert {m["name"] for m in result["members"]} == {"solo-an"}


@pytest.mark.asyncio
async def test_query_group_caller_without_group_gets_empty(
    mcp_service, test_session_factory
):
    """CF12：无群员工查询仍为空 + 提示（第一轮行为不回归）."""
    await _seed_scoped_group(test_session_factory)

    result = await mcp_service.query_group(caller="outsider-wa")

    assert result["members"] == []
    assert "尚未加入任何群组" in result.get("message", "")
