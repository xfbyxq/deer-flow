"""Waker 管理 API 测试（mock DeerFlowClient）."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

from app.database import Base
from app.main import create_app
from app.models import AuditLog, Task, Waker


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
    client = MagicMock()
    client.list_agents = AsyncMock(return_value=[])
    client.get_agent = AsyncMock(return_value={})
    client.create_agent = AsyncMock(return_value={})
    client.update_agent = AsyncMock(return_value={})
    client.delete_agent = AsyncMock(return_value=None)
    client.list_models = AsyncMock(return_value=[{"id": "qwen3-flash", "name": "Qwen3 Flash"}])
    client.list_skills = AsyncMock(return_value=[{"name": "web-search", "description": "Web search"}])
    return client


@pytest.fixture
async def app(test_session_factory, mock_deerflow):
    """构建测试 FastAPI app."""
    application = create_app()

    # 覆盖 app.state
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
# 创建 Waker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_waker(client, mock_deerflow, test_session_factory):
    """创建 Waker：mock create_agent 返回 → 断言本地台账建立 + SOUL 含模板内容."""
    mock_deerflow.create_agent.return_value = {
        "name": "alice",
        "description": "Research assistant",
        "soul": "## 团队协作规则\n\n你是 Waker Team 的一员",
    }

    resp = await client.post(
        "/api/wakers",
        json={
            "name": "alice",
            "description": "Research assistant",
            "soul": "我擅长数据分析。",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "alice"
    assert body["enabled"] is True

    # 验证 SOUL 模板注入
    call_args = mock_deerflow.create_agent.call_args
    soul_sent = call_args[0][0]["soul"]
    assert "团队协作规则" in soul_sent
    assert "alice" in soul_sent
    assert "我擅长数据分析" in soul_sent

    # 验证本地台账已建立
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Waker).where(Waker.name == "alice"))
        waker = result.scalars().first()
        assert waker is not None
        assert waker.enabled is True

    # 验证审计日志
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(AuditLog).where(AuditLog.action == "waker.create"))
        audit = result.scalars().first()
        assert audit is not None
        assert audit.target == "alice"


# ------------------------------------------------------------------
# 列表 Wakers
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_wakers(client, mock_deerflow, test_session_factory):
    """列表：mock list_agents → 断言合并结果."""
    mock_deerflow.list_agents.return_value = [
        {"name": "alice", "description": "Research", "soul": "..."},
        {"name": "bob", "description": "Writer", "soul": "..."},
    ]

    # 预建本地台账
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="Research", soul_summary="...", enabled=True))
        session.add(Waker(name="bob", deer_user="", description="Writer", soul_summary="...", enabled=False))
        await session.commit()

    resp = await client.get("/api/wakers")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    names = {w["name"] for w in body}
    assert names == {"alice", "bob"}

    # alice enabled=True, bob enabled=False
    alice = next(w for w in body if w["name"] == "alice")
    bob = next(w for w in body if w["name"] == "bob")
    assert alice["enabled"] is True
    assert bob["enabled"] is False


# ------------------------------------------------------------------
# 详情 Waker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_waker(client, mock_deerflow, test_session_factory):
    """详情：DeerFlow agent 数据 + 本地台账合并."""
    mock_deerflow.get_agent.return_value = {
        "name": "alice",
        "description": "Research assistant",
        "soul": "...",
    }
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="user1", description="Research", soul_summary="...", enabled=True))
        await session.commit()

    resp = await client.get("/api/wakers/alice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "alice"
    assert body["deer_user"] == "user1"
    assert body["enabled"] is True


# ------------------------------------------------------------------
# 名称校验
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_waker_invalid_name(client):
    """非法名（含空格/特殊字符）→ 422."""
    resp = await client.post(
        "/api/wakers",
        json={"name": "invalid name!", "description": "test"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_waker_name_with_underscore(client, mock_deerflow, test_session_factory):
    """下划线现在允许 → 201."""
    mock_deerflow.create_agent.return_value = {
        "name": "valid_name",
        "description": "test",
        "soul": "",
    }
    resp = await client.post(
        "/api/wakers",
        json={"name": "valid_name", "description": "test"},
    )
    assert resp.status_code == 201


# ------------------------------------------------------------------
# 编辑 Waker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_waker(client, mock_deerflow, test_session_factory):
    """编辑：mock update_agent → 断言调用正确."""
    mock_deerflow.get_agent.return_value = {
        "name": "alice",
        "description": "Updated description",
        "soul": "...",
    }
    mock_deerflow.update_agent.return_value = {
        "name": "alice",
        "description": "Updated description",
        "soul": "...",
    }
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="Old", soul_summary="...", enabled=True))
        await session.commit()

    resp = await client.put(
        "/api/wakers/alice",
        json={"description": "Updated description"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "Updated description"

    # 验证 DeerFlow 调用
    mock_deerflow.update_agent.assert_called_once()
    call_args = mock_deerflow.update_agent.call_args
    assert call_args[0][0] == "alice"
    assert call_args[0][1]["description"] == "Updated description"


# ------------------------------------------------------------------
# 启停 Waker
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_waker(client, mock_deerflow, test_session_factory):
    """toggle → 断言本地 enabled 变更."""
    mock_deerflow.get_agent.return_value = {
        "name": "alice",
        "description": "Research",
        "soul": "...",
    }
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="Research", soul_summary="...", enabled=True))
        await session.commit()

    # 禁用
    resp = await client.patch("/api/wakers/alice/toggle", json={"enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False

    # 验证本地台账已更新
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Waker).where(Waker.name == "alice"))
        waker = result.scalars().first()
        assert waker.enabled is False


# ------------------------------------------------------------------
# 删除拦截（有 running 任务）
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_waker_with_running_task(client, mock_deerflow, test_session_factory):
    """有 running TASK → 409."""
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="Research", soul_summary="...", enabled=True))
        session.add(Task(id="task-1", kind="manual", executor="alice", status="running", input_text="test", created_by="system"))
        await session.commit()

    resp = await client.delete("/api/wakers/alice")
    assert resp.status_code == 409


# ------------------------------------------------------------------
# 删除成功
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_waker_success(client, mock_deerflow, test_session_factory):
    """无 running TASK → 调 DeerFlow DELETE + 删本地台账."""
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="Research", soul_summary="...", enabled=True))
        await session.commit()

    resp = await client.delete("/api/wakers/alice")
    assert resp.status_code == 204

    # 验证 DeerFlow 调用
    mock_deerflow.delete_agent.assert_called_once_with("alice")

    # 验证本地台账已删除
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Waker).where(Waker.name == "alice"))
        waker = result.scalars().first()
        assert waker is None


# ------------------------------------------------------------------
# 枚举数据
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enum_data(client, mock_deerflow):
    """mock list_models/list_skills → 断言返回格式."""
    resp = await client.get("/api/wakers/enum")
    assert resp.status_code == 200
    body = resp.json()
    assert "models" in body
    assert "skills" in body
    assert "tool_groups" in body
    assert len(body["models"]) == 1
    assert body["models"][0]["id"] == "qwen3-flash"
    assert len(body["skills"]) == 1
    assert "web" in body["tool_groups"]


# ------------------------------------------------------------------
# DeerFlow 错误映射
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_waker_not_found(client, mock_deerflow):
    """DeerFlow AgentNotFoundError → HTTP 404."""
    from app.deerflow.errors import AgentNotFoundError

    mock_deerflow.get_agent.side_effect = AgentNotFoundError("Agent not found: alice")

    resp = await client.get("/api/wakers/alice")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_waker_conflict(client, mock_deerflow, test_session_factory):
    """DeerFlow AgentConflictError：台账已存在 → HTTP 409（真正的重复创建）."""
    from app.deerflow.errors import AgentConflictError

    mock_deerflow.create_agent.side_effect = AgentConflictError("Agent already exists: alice")

    # 先创建本地台账（模拟已存在的员工），再触发同名创建
    async with test_session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="existing", soul_summary=""))
        await session.commit()

    resp = await client.post(
        "/api/wakers",
        json={"name": "alice", "description": "test"},
    )
    assert resp.status_code == 409


async def test_create_waker_recovers_when_agent_leftover(client, mock_deerflow):
    """DeerFlow agent 残留（台账缺失）→ 201 自愈补台账，而非 409 卡死."""
    from app.deerflow.errors import AgentConflictError

    mock_deerflow.create_agent.side_effect = AgentConflictError("Agent already exists: leftover")

    resp = await client.post(
        "/api/wakers",
        json={"name": "leftover", "description": "从残留恢复"},
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "leftover"


@pytest.mark.asyncio
async def test_get_waker_deerflow_unavailable(client, mock_deerflow):
    """DeerFlow DeerFlowUnavailableError → HTTP 502."""
    from app.deerflow.errors import DeerFlowUnavailableError

    mock_deerflow.get_agent.side_effect = DeerFlowUnavailableError("Gateway unreachable")

    resp = await client.get("/api/wakers/alice")
    assert resp.status_code == 502


# ------------------------------------------------------------------
# 员工模板
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_waker_templates(client):
    """模板列表：返回内置模板，id 唯一、字段齐全、建议名符合员工名正则."""
    import re

    resp = await client.get("/api/wakers/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 6

    ids = [t["id"] for t in body]
    assert len(ids) == len(set(ids))

    required = {
        "id",
        "title",
        "description",
        "suggested_name",
        "role",
        "soul",
        "tool_groups",
        "skills",
        "max_concurrent_tasks",
    }
    for t in body:
        assert required <= set(t.keys())
        assert t["title"]
        assert t["description"]
        assert t["role"]
        assert t["soul"]
        assert re.fullmatch(r"[A-Za-z0-9-]+", t["suggested_name"])
        assert isinstance(t["tool_groups"], list)
        assert isinstance(t["skills"], list)
        assert t["max_concurrent_tasks"] >= 1
        if t.get("mcp_connectors") is not None:
            assert isinstance(t["mcp_connectors"], dict)


@pytest.mark.asyncio
async def test_create_waker_from_template(client, mock_deerflow, test_session_factory):
    """按模板字段创建员工：SOUL 拼接协作规则 + 模板内容，台账落 role/并发/MCP."""
    tmpl = (await client.get("/api/wakers/templates")).json()[0]
    mock_deerflow.create_agent.return_value = {
        "name": tmpl["suggested_name"],
        "description": tmpl["description"],
        "soul": "...",
    }

    resp = await client.post(
        "/api/wakers",
        json={
            "name": tmpl["suggested_name"],
            "description": tmpl["description"],
            "soul": tmpl["soul"],
            "model": tmpl["model"],
            "tool_groups": tmpl["tool_groups"],
            "skills": tmpl["skills"],
            "role": tmpl["role"],
            "max_concurrent_tasks": tmpl["max_concurrent_tasks"],
            "mcp_connectors": tmpl["mcp_connectors"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == tmpl["role"]
    assert body["max_concurrent_tasks"] == tmpl["max_concurrent_tasks"]

    # DeerFlow 收到的 SOUL = 协作规则模板 + 模板 soul
    soul_sent = mock_deerflow.create_agent.call_args[0][0]["soul"]
    assert "团队协作规则" in soul_sent
    assert tmpl["soul"].split("\n")[0] in soul_sent

    # 本地台账
    async with test_session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(Waker).where(Waker.name == tmpl["suggested_name"]))
        waker = result.scalars().first()
        assert waker is not None
        assert waker.role == tmpl["role"]
        assert waker.max_concurrent_tasks == tmpl["max_concurrent_tasks"]
        if tmpl["mcp_connectors"]:
            assert waker.mcp_connectors is not None
