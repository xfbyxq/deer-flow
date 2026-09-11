"""Tests for app.deerflow.client.DeerFlowClient using httpx.MockTransport."""

import asyncio
import inspect
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    AuthenticationError,
    DeerFlowError,
    DeerFlowUnavailableError,
    McpServerNotFoundError,
    TaskConflictError,
    TaskNotFoundError,
    ThreadNotFoundError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(responses: dict[str, list[dict]]) -> httpx.MockTransport:
    """Build a MockTransport from a route → list-of-responses map.

    Each entry: {"status": int, "json": dict|None, "headers": dict|None, "cookies": dict|None}
    Responses are consumed in order; last one is reused for subsequent calls.
    """
    call_counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        # Include query string for finer matching
        qs = str(request.url.params)
        key_qs = f"{key}?{qs}" if qs else key

        candidates = responses.get(key_qs) or responses.get(key)
        if candidates is None:
            return httpx.Response(404, text=f"No mock for {key}")

        idx = call_counts.get(key, 0)
        call_counts[key] = idx + 1
        entry = candidates[min(idx, len(candidates) - 1)]

        headers = dict(entry.get("headers", {}))
        resp = httpx.Response(
            entry["status"],
            json=entry.get("json"),
            text=entry.get("text"),
            headers=headers,
        )
        # Inject cookies
        for name, value in entry.get("cookies", {}).items():
            resp.headers.setdefault(
                "set-cookie", f"{name}={value}; Path=/"
            )
            # httpx MockTransport doesn't auto-parse set-cookie; we set them
            # on the client jar manually after login.
        return resp

    return httpx.MockTransport(handler)


def _login_response() -> dict:
    return {
        "status": 200,
        "json": {"access_token": "fake-token"},
        "cookies": {"access_token": "fake-token", "csrf_token": "fake-csrf"},
    }


async def _make_logged_in_client(
    handler: httpx.MockTransport,
) -> DeerFlowClient:
    """Create a client that is already logged in (bypasses login transport)."""
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=handler,
    )
    # Simulate post-login state without going through the transport
    client._client.cookies.set("access_token", "fake-token")
    client._client.cookies.set("csrf_token", "fake-csrf")
    client._logged_in = True
    return client


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success():
    handler = _make_handler({
        "POST /api/v1/auth/login/local": [_login_response()],
    })
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=handler,
    )
    await client.login("test@test.com", "testpass")
    assert client._logged_in is True


@pytest.mark.asyncio
async def test_login_failure_raises():
    handler = _make_handler({
        "POST /api/v1/auth/login/local": [
            {"status": 401, "text": "Invalid credentials"},
        ],
    })
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=handler,
    )
    with pytest.raises(AuthenticationError, match="Login failed"):
        await client.login("bad@test.com", "wrong")


# ---------------------------------------------------------------------------
# CSRF injection tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_header_injected_on_write():
    captured_headers: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"name": "test-agent"})

    transport = httpx.MockTransport(handler)
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=transport,
    )
    client._logged_in = True
    client._client.cookies.set("csrf_token", "my-csrf-value")

    await client.create_agent({"name": "test-agent"})
    assert captured_headers.get("x-csrf-token") == "my-csrf-value"


@pytest.mark.asyncio
async def test_csrf_header_not_on_get():
    captured_headers: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"agents": []})

    transport = httpx.MockTransport(handler)
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=transport,
    )
    client._logged_in = True
    client._client.cookies.set("csrf_token", "my-csrf-value")

    await client.list_agents()
    assert "x-csrf-token" not in captured_headers


# ---------------------------------------------------------------------------
# 401 auto-relogin tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_raises_authentication_error():
    """401 raises AuthenticationError (caller must invoke login again)."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, text="Session expired")

    transport = httpx.MockTransport(handler)
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=transport,
    )
    client._logged_in = True
    client._client.cookies.set("csrf_token", "csrf")

    with pytest.raises(AuthenticationError, match="Session expired"):
        await client.list_agents()


@pytest.mark.asyncio
async def test_request_with_relogin_retries():
    """request_with_relogin re-authenticates and retries on 401."""
    agents_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal agents_call_count
        if request.url.path == "/api/v1/auth/login/local":
            return httpx.Response(200, json={"access_token": "new-token"})
        if request.url.path == "/api/agents":
            agents_call_count += 1
            if agents_call_count == 1:
                return httpx.Response(401, text="expired")
            return httpx.Response(200, json={"agents": ["a1"]})
        return httpx.Response(404, text=f"unexpected: {request.url}")

    transport = httpx.MockTransport(handler)
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=transport,
    )
    client._logged_in = True
    client._client.cookies.set("csrf_token", "csrf")

    result = await client.request_with_relogin(
        "GET", "/api/agents", "test@test.com", "testpass"
    )
    assert result.json() == {"agents": ["a1"]}


# ---------------------------------------------------------------------------
# Connection retry tests（网关重启后旧 keep-alive 连接失效场景）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_error_retried_then_success():
    """连接类错误应重试；后续成功则正常返回（不再直接抛 Connection error）."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, json={"agents": ["a1"]})

    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    client._logged_in = True

    resp = await client._request("GET", "/api/agents")
    assert resp.status_code == 200
    assert call_count == 3


@pytest.mark.asyncio
async def test_connection_error_exhausted_raises_unavailable():
    """连接类错误重试耗尽 → DeerFlowUnavailableError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    client._logged_in = True

    with pytest.raises(DeerFlowUnavailableError):
        await client._request("GET", "/api/agents")


@pytest.mark.asyncio
async def test_connection_error_exhausted_rebuilds_http_client():
    """重试耗尽 → 重建 HTTP 客户端自愈（cookie 保留，新请求用全新连接）."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("all connection attempts failed")

    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    client._client.cookies.set("csrf_token", "my-csrf")
    client._logged_in = True
    stale_client = client._client

    with pytest.raises(DeerFlowUnavailableError):
        await client._request("GET", "/api/agents")

    # 已重建：新对象、登录 cookie 保留
    assert client._client is not stale_client
    assert client._client.cookies.get("csrf_token") == "my-csrf"
    # 给异步关闭任务一个调度窗口（避免事件循环关闭告警）
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_rebuild_skipped_when_already_rebuilt():
    """并发场景：client 已被其他请求重建时，重复调用重建直接跳过."""
    client = DeerFlowClient("http://test:2026")
    current = client._client

    await client._rebuild_http_client(stale=httpx.AsyncClient(base_url="http://test:2026"))

    # stale 不是当前 client → 不重建
    assert client._client is current
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Error mapping tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_404_agent_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(AgentNotFoundError):
        await client.get_agent("nonexistent")


@pytest.mark.asyncio
async def test_404_thread_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(ThreadNotFoundError):
        await client.delete_thread("nonexistent-thread")


@pytest.mark.asyncio
async def test_409_agent_conflict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="agent exists")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(AgentConflictError):
        await client.create_agent({"name": "existing"})


@pytest.mark.asyncio
async def test_409_task_conflict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, text="task conflict")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(TaskConflictError):
        await client.create_run("tid", {"input": {}})


@pytest.mark.asyncio
async def test_422_validation_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="invalid field")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(ValidationError):
        await client.create_agent({"name": ""})


@pytest.mark.asyncio
async def test_500_deerflow_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(DeerFlowUnavailableError, match="Gateway error"):
        await client.list_agents()


# ---------------------------------------------------------------------------
# API method tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_agents():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/agents"
        return httpx.Response(200, json={"agents": [{"name": "a1"}, {"name": "a2"}]})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.list_agents()
    assert result == [{"name": "a1"}, {"name": "a2"}]


@pytest.mark.asyncio
async def test_get_agent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/agents/my-agent"
        return httpx.Response(200, json={"name": "my-agent", "description": "test"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.get_agent("my-agent")
    assert result["name"] == "my-agent"


@pytest.mark.asyncio
async def test_create_agent():
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/agents"
        captured_body.update(json.loads(request.content))
        return httpx.Response(201, json={"name": "new-agent"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.create_agent({"name": "new-agent", "description": "desc"})
    assert result["name"] == "new-agent"
    assert captured_body["name"] == "new-agent"
    assert captured_body["description"] == "desc"


@pytest.mark.asyncio
async def test_update_agent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/api/agents/my-agent"
        body = json.loads(request.content)
        return httpx.Response(200, json={"name": "my-agent", **body})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.update_agent("my-agent", {"description": "updated"})
    assert result["description"] == "updated"


@pytest.mark.asyncio
async def test_delete_agent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/agents/my-agent"
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)
    await client.delete_agent("my-agent")


@pytest.mark.asyncio
async def test_check_agent_name():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/agents/check"
        name = request.url.params.get("name", "")
        return httpx.Response(200, json={"available": name == "free-name", "name": name})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.check_agent_name("free-name")
    assert result["available"] is True


@pytest.mark.asyncio
async def test_list_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"id": "m1"}, {"id": "m2"}]})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.list_models()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_skills():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"skills": [{"name": "s1"}]})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.list_skills()
    assert len(result) == 1


@pytest.mark.asyncio
async def test_create_thread():
    captured_body: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/threads"
        captured_body.update(json.loads(request.content))
        return httpx.Response(200, json={"thread_id": "tid-123"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.create_thread(thread_id="tid-123")
    assert result["thread_id"] == "tid-123"
    assert captured_body["thread_id"] == "tid-123"


@pytest.mark.asyncio
async def test_create_thread_no_id():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "thread_id" not in body
        return httpx.Response(200, json={"thread_id": "auto-id"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.create_thread()
    assert result["thread_id"] == "auto-id"


@pytest.mark.asyncio
async def test_create_run_with_idempotency_key():
    captured_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/api/threads/tid-1/runs" == request.url.path
        captured_headers.update(dict(request.headers))
        return httpx.Response(200, json={"run_id": "rid-1", "status": "pending"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    body = {
        "input": {"messages": [{"role": "user", "content": "hello"}]},
        "config": {"configurable": {"agent_name": "my-agent"}},
    }
    result = await client.create_run("tid-1", body, idempotency_key="idem-123")
    assert result["run_id"] == "rid-1"
    assert captured_headers.get("idempotency-key") == "idem-123"


@pytest.mark.asyncio
async def test_get_run():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/threads/tid-1/runs/rid-1"
        return httpx.Response(200, json={"run_id": "rid-1", "status": "success"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.get_run("tid-1", "rid-1")
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_cancel_run():
    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/threads/tid-1/runs/rid-1/cancel"
        captured_params.update(dict(request.url.params))
        return httpx.Response(202, json={"status": "cancelling"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.cancel_run("tid-1", "rid-1", wait=True)
    assert result.status_code == 202
    assert captured_params["wait"] == "true"


@pytest.mark.asyncio
async def test_cancel_run_wait_false():
    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    result = await client.cancel_run("tid-1", "rid-1", wait=False)
    assert result.status_code == 204
    assert captured_params["wait"] == "false"


# ---------------------------------------------------------------------------
# MCP config API tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mcp_config_returns_servers():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/mcp/config"
        return httpx.Response(200, json={"mcp_servers": {"waker-team": {"type": "http"}}})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    servers = await client.get_mcp_config()
    assert servers == {"waker-team": {"type": "http"}}


@pytest.mark.asyncio
async def test_add_mcp_server_sends_payload_with_csrf():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content)
        captured["csrf"] = request.headers.get("x-csrf-token")
        return httpx.Response(200, json={"mcp_servers": {}})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    await client.add_mcp_server("waker-team", {"type": "http", "url": "http://x/mcp"})

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/mcp/config/servers"
    assert captured["json"] == {
        "mcp_servers": {"waker-team": {"type": "http", "url": "http://x/mcp"}}
    }
    assert captured["csrf"] == "fake-csrf"


@pytest.mark.asyncio
async def test_update_mcp_server_sends_payload():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["json"] = json.loads(request.content)
        return httpx.Response(200, json={"mcp_servers": {}})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    await client.update_mcp_server("waker-team", {"type": "http", "url": "http://x/mcp"})

    assert captured["method"] == "PUT"
    assert captured["path"] == "/api/mcp/config/server"
    assert captured["json"] == {
        "server_name": "waker-team",
        "server": {"type": "http", "url": "http://x/mcp"},
    }


@pytest.mark.asyncio
async def test_delete_mcp_server_not_found_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/mcp/config/servers/waker-team"
        return httpx.Response(404, json={"detail": "MCP server 'waker-team' not found"})

    transport = httpx.MockTransport(handler)
    client = await _make_logged_in_client(transport)

    with pytest.raises(McpServerNotFoundError):
        await client.delete_mcp_server("waker-team")


# ---------------------------------------------------------------------------
# Not-logged-in guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_without_login_raises():
    client = DeerFlowClient("http://test:2026")
    with pytest.raises(AuthenticationError, match="Not logged in"):
        await client.list_agents()


# ---------------------------------------------------------------------------
# W3: recursion_limit 可配置（签名保持稳定）
# ---------------------------------------------------------------------------


def test_build_run_configuration_signature_stable():
    """对外签名必须保持稳定：其他包（task_service/chat_reply/mcp）无需改调用点."""
    params = list(inspect.signature(build_run_configuration).parameters)
    assert params == ["agent_name"]


def test_build_run_configuration_default_recursion_limit(monkeypatch):
    """默认递归预算仍为 1000（保持既有部署行为不变）."""
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", Settings(_env_file=None))
    cfg = build_run_configuration("alice")
    assert cfg["recursion_limit"] == 1000
    assert cfg["configurable"]["agent_name"] == "alice"
    assert cfg["context"]["secrets"]["waker_identity"] == "alice"


def test_build_run_configuration_reads_recursion_limit_from_settings(monkeypatch):
    """recursion_limit 不再硬编码：从 settings 读取."""
    import app.config as config_module

    monkeypatch.setattr(
        config_module, "_settings", Settings(_env_file=None, recursion_limit=250)
    )
    cfg = build_run_configuration("alice")
    assert cfg["recursion_limit"] == 250


# ---------------------------------------------------------------------------
# C9: 上游响应体不得透传到异常消息（仅进服务端日志，且截断）
# ---------------------------------------------------------------------------

_SENSITIVE_BODY = "SENSITIVE-UPSTREAM-DETAIL internal-trace db=diamond password=hunter2"


@pytest.mark.parametrize(
    ("status", "path", "exc_type"),
    [
        (500, "/api/agents", DeerFlowUnavailableError),
        (503, "/api/threads/t1/runs", DeerFlowUnavailableError),
        (404, "/api/agents/alice", AgentNotFoundError),
        (404, "/api/threads/t1", ThreadNotFoundError),
        (404, "/api/mcp/config/servers/x", McpServerNotFoundError),
        (409, "/api/agents", AgentConflictError),
        (409, "/api/threads/t1/runs", TaskConflictError),
        (422, "/api/agents", ValidationError),
        (401, "/api/agents", AuthenticationError),
        (418, "/api/agents", DeerFlowError),
    ],
)
def test_raise_for_status_never_embeds_upstream_body(status, path, exc_type):
    """异常消息只携带安全摘要（状态码/路径），不包含上游 body."""
    resp = httpx.Response(
        status, text=_SENSITIVE_BODY, request=httpx.Request("GET", f"http://test:2026{path}")
    )
    with pytest.raises(exc_type) as exc_info:
        DeerFlowClient._raise_for_status(resp, path)

    message = str(exc_info.value)
    assert _SENSITIVE_BODY not in message
    assert "SENSITIVE-UPSTREAM-DETAIL" not in message
    assert "hunter2" not in message
    assert str(status) in message


def test_raise_for_status_logs_body(caplog):
    """上游 body 仍进服务端日志，保留排障能力."""
    resp = httpx.Response(
        500, text=_SENSITIVE_BODY, request=httpx.Request("GET", "http://test:2026/api/agents")
    )
    with caplog.at_level(logging.WARNING, logger="app.deerflow.client"):
        with pytest.raises(DeerFlowUnavailableError):
            DeerFlowClient._raise_for_status(resp, "/api/agents")
    assert "SENSITIVE-UPSTREAM-DETAIL" in caplog.text


def test_raise_for_status_truncates_logged_body(caplog):
    """日志中的 body 被截断，避免超大响应淹没日志."""
    huge = "X" * 5000
    resp = httpx.Response(
        500, text=huge, request=httpx.Request("GET", "http://test:2026/api/agents")
    )
    with caplog.at_level(logging.WARNING, logger="app.deerflow.client"):
        with pytest.raises(DeerFlowUnavailableError):
            DeerFlowClient._raise_for_status(resp, "/api/agents")
    assert "X" * 1000 in caplog.text
    assert "X" * 1001 not in caplog.text


@pytest.mark.asyncio
async def test_login_failure_message_excludes_upstream_body(caplog):
    """登录失败：异常消息只有状态码，body 仅进日志."""
    handler = _make_handler({
        "POST /api/v1/auth/login/local": [
            {"status": 401, "text": _SENSITIVE_BODY},
        ],
    })
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=handler,
    )
    with caplog.at_level(logging.WARNING, logger="app.deerflow.client"):
        with pytest.raises(AuthenticationError) as exc_info:
            await client.login("bad@test.com", "wrong")

    message = str(exc_info.value)
    assert "Login failed" in message
    assert "401" in message
    assert "SENSITIVE-UPSTREAM-DETAIL" not in message
    # 密码也不得出现在异常消息/日志中
    assert "wrong" not in message
    assert "SENSITIVE-UPSTREAM-DETAIL" in caplog.text


# ---------------------------------------------------------------------------
# C9 / 契约2：api/tasks.py 上游失败 → 502 + 安全 detail
# ---------------------------------------------------------------------------


def test_map_deerflow_error_upstream_returns_502_safe_detail():
    from fastapi import HTTPException

    from app.api.tasks import _map_deerflow_error

    with pytest.raises(HTTPException) as exc_info:
        _map_deerflow_error(
            DeerFlowUnavailableError(
                "DeerFlow Gateway error: status=500 path=/api/threads"
            )
        )
    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "上游服务暂时不可用，请稍后重试"


def test_map_deerflow_error_unexpected_returns_500_safe_detail():
    """非上游的未预期异常也不得回传 str(exc)."""
    from fastapi import HTTPException

    from app.api.tasks import _map_deerflow_error

    with pytest.raises(HTTPException) as exc_info:
        _map_deerflow_error(RuntimeError(_SENSITIVE_BODY))
    assert exc_info.value.status_code == 500
    assert "SENSITIVE-UPSTREAM-DETAIL" not in exc_info.value.detail


@pytest.fixture
async def api_db_engine():
    """API 级测试用内存数据库（与真实 waker_team.db 完全隔离）."""
    import app.models  # noqa: F401 — 确保所有 ORM 模型已注册
    from app.database import Base

    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_task_upstream_failure_returns_502_without_body(api_db_engine, caplog):
    """端到端（契约2）：网关 500 带敏感 body → HTTP 502 + 安全 detail，body 仅进日志."""
    from app.main import create_app
    from app.models import Waker

    session_factory = async_sessionmaker(
        api_db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        session.add(
            Waker(
                name="alice",
                deer_user="",
                description="Test alice",
                soul_summary="...",
                enabled=True,
            )
        )
        await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=_SENSITIVE_BODY)

    real_client = DeerFlowClient("http://test:2026")
    real_client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    real_client._logged_in = True

    application = create_app()
    application.state.db_session_factory = session_factory
    application.state.deerflow = real_client
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync

    with caplog.at_level(logging.WARNING):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/tasks", json={"executor": "alice", "input_text": "hi"}
            )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "上游服务暂时不可用，请稍后重试"
    assert "SENSITIVE-UPSTREAM-DETAIL" not in resp.text
    assert "hunter2" not in resp.text
    # 服务端日志保留完整 body（已截断）供排障
    assert "SENSITIVE-UPSTREAM-DETAIL" in caplog.text


# ---------------------------------------------------------------------------
# CF20：4xx 白名单提取上游可操作原因（reason）
# ---------------------------------------------------------------------------

_UPSTREAM_REASON = "该 waker 已有运行中任务"
# 上游 409 body：detail 是可展示原因，其余字段（栈/主机名/凭据）绝不能外泄
_REASON_BODY = json.dumps(
    {
        "detail": _UPSTREAM_REASON,
        "internal_trace": 'File "/app/deerflow/gateway/runs.py", line 42',
        "host": "http://gateway-internal:8001",
        "password": "hunter2",
    },
    ensure_ascii=False,
)


def _resp(status: int, body: str, path: str = "/api/threads/t1/runs") -> httpx.Response:
    return httpx.Response(
        status, text=body, request=httpx.Request("POST", f"http://test:2026{path}")
    )


def test_raise_for_status_extracts_whitelisted_reason():
    """4xx：白名单提取上游 detail 并入异常消息（可操作原因不再丢失）."""
    with pytest.raises(TaskConflictError) as exc_info:
        DeerFlowClient._raise_for_status(_resp(409, _REASON_BODY), "/api/threads/t1/runs")

    message = str(exc_info.value)
    assert _UPSTREAM_REASON in message
    assert getattr(exc_info.value, "upstream_reason", None) == _UPSTREAM_REASON
    # 仍不整体透传 body：栈/主机名/凭据/其它字段一律不出现
    assert "internal_trace" not in message
    assert "/app/deerflow" not in message
    assert "gateway-internal" not in message
    assert "hunter2" not in message


@pytest.mark.parametrize(
    "body",
    [
        # FastAPI 422：detail 是校验错误列表（非字符串）→ 丢弃
        json.dumps({"detail": [{"loc": ["body", "name"], "msg": "field required"}]}),
        json.dumps({"detail": {"nested": "object"}}),
        json.dumps({"detail": 123}),
        # 含栈帧/内部路径/URL 的字符串 → 丢弃
        json.dumps(
            {"detail": 'Traceback (most recent call last): File "/app/deerflow/x.py", line 3'}
        ),
        json.dumps({"message": "upstream http://gateway-internal:8001/api/x refused"}),
        json.dumps({"detail": "password=hunter2"}),
        json.dumps({"detail": "   "}),
        json.dumps({"other": "无关字段"}),
        json.dumps(["not", "an", "object"]),
        "not json at all",
        "",
    ],
)
def test_reason_whitelist_rejects_unsafe_or_non_string(body):
    """白名单只接受顶层 detail/message 的安全字符串，其余一律不提取."""
    with pytest.raises(TaskConflictError) as exc_info:
        DeerFlowClient._raise_for_status(_resp(409, body), "/api/threads/t1/runs")
    assert getattr(exc_info.value, "upstream_reason", None) is None


def test_reason_truncated_to_200_chars():
    """reason 限长 200 字符，避免上游长文本灌进 detail."""
    body = json.dumps({"detail": "任" * 500}, ensure_ascii=False)
    with pytest.raises(TaskConflictError) as exc_info:
        DeerFlowClient._raise_for_status(_resp(409, body), "/api/threads/t1/runs")
    reason = getattr(exc_info.value, "upstream_reason", None)
    assert reason is not None
    assert len(reason) == 200


def test_reason_collapses_multiline_whitespace():
    """多行/多空白折叠为单行，避免 detail 里出现换行碎片."""
    body = json.dumps({"detail": "已有\n运行中\t任务   请稍后"}, ensure_ascii=False)
    with pytest.raises(TaskConflictError) as exc_info:
        DeerFlowClient._raise_for_status(_resp(409, body), "/api/threads/t1/runs")
    assert exc_info.value.upstream_reason == "已有 运行中 任务 请稍后"


@pytest.mark.parametrize(("status", "exc_type"), [
    (500, DeerFlowUnavailableError),
    (503, DeerFlowUnavailableError),
    (401, AuthenticationError),
])
def test_reason_not_extracted_for_5xx_and_401(status, exc_type):
    """5xx/401 不提取 reason：前者是网关故障，后者是凭据问题，都不该展示给用户."""
    with pytest.raises(exc_type) as exc_info:
        DeerFlowClient._raise_for_status(_resp(status, _REASON_BODY, "/api/agents"), "/api/agents")
    assert getattr(exc_info.value, "upstream_reason", None) is None
    assert _UPSTREAM_REASON not in str(exc_info.value)


# ---------------------------------------------------------------------------
# CF14：_raise_for_status 日志分级（4xx 正常控制流不再洪泛 WARNING）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 404, 409, 422])
def test_expected_4xx_logs_debug_without_body(caplog, status):
    """轮询期高频 4xx：仅 DEBUG 一行，不含上游 body."""
    with caplog.at_level(logging.DEBUG, logger="app.deerflow.client"):
        with pytest.raises(DeerFlowError):
            DeerFlowClient._raise_for_status(
                _resp(status, _SENSITIVE_BODY, "/api/agents"), "/api/agents"
            )
    records = [r for r in caplog.records if r.name == "app.deerflow.client"]
    assert records, "4xx 仍应留一行可排障日志"
    assert all(r.levelno < logging.WARNING for r in records)
    assert "SENSITIVE-UPSTREAM-DETAIL" not in caplog.text


def test_repeated_404_polling_does_not_flood_warning_logs(caplog):
    """回归：50 次 404 轮询 → 0 条 WARNING（此前每次记 1KB body）."""
    with caplog.at_level(logging.DEBUG, logger="app.deerflow.client"):
        for _ in range(50):
            with pytest.raises(ThreadNotFoundError):
                DeerFlowClient._raise_for_status(
                    _resp(404, "not found", "/api/threads/t1/state"),
                    "/api/threads/t1/state",
                )
    warnings = [
        r
        for r in caplog.records
        if r.name == "app.deerflow.client" and r.levelno >= logging.WARNING
    ]
    assert warnings == []


@pytest.mark.parametrize("status", [500, 502, 503, 400, 403, 418, 429])
def test_5xx_and_unexpected_status_log_warning_with_body(caplog, status):
    """5xx 与未预期状态码：WARNING + 截断 body（保留排障能力）."""
    with caplog.at_level(logging.DEBUG, logger="app.deerflow.client"):
        with pytest.raises(DeerFlowError):
            DeerFlowClient._raise_for_status(
                _resp(status, _SENSITIVE_BODY, "/api/agents"), "/api/agents"
            )
    assert any(
        r.levelno == logging.WARNING and r.name == "app.deerflow.client"
        for r in caplog.records
    )
    assert "SENSITIVE-UPSTREAM-DETAIL" in caplog.text


# ---------------------------------------------------------------------------
# CF9：共享错误映射 app/api/errors.py（tasks 与 wakers 语义一致）
# ---------------------------------------------------------------------------

_SHARED_MAPPING_CASES = [
    (ValidationError("Validation error: status=422 path=/api/agents"), 422, "请求参数校验未通过"),
    (AuthenticationError("Authentication failed: status=401 path=/api/agents"), 503, "服务账号鉴权失败"),
    (ThreadNotFoundError("Thread not found: status=404 path=/api/threads/t1"), 404, "关联会话已不存在"),
    (McpServerNotFoundError("MCP server not found: status=404 path=/api/mcp/config/servers/x"), 404, "MCP"),
    (AgentNotFoundError("Agent not found: status=404 path=/api/agents/alice"), 404, "员工不存在"),
    (TaskNotFoundError("Task not found: status=404 path=/api/tasks/t1"), 404, "任务不存在"),
    (AgentConflictError("Agent conflict: status=409 path=/api/agents"), 409, "资源状态冲突"),
    (TaskConflictError("Task conflict: status=409 path=/api/threads/t1/runs"), 409, "资源状态冲突"),
    (DeerFlowUnavailableError("DeerFlow Gateway error: status=500 path=/api/agents"), 502, "上游服务暂时不可用"),
    (DeerFlowError("Unexpected DeerFlow error: status=418 path=/api/agents"), 502, "上游服务暂时不可用"),
]

_LEAK_MARKERS = ("path=", "/api/", "status=", "hunter2", "http://", "/app/")


@pytest.mark.parametrize(("exc", "status", "detail_part"), _SHARED_MAPPING_CASES)
def test_shared_map_deerflow_error_status_and_safe_detail(exc, status, detail_part):
    """共享映射：状态码语义正确 + detail 为安全中文（不泄漏路径/body/主机名）."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    with pytest.raises(HTTPException) as exc_info:
        map_deerflow_error(exc)

    assert exc_info.value.status_code == status
    assert detail_part in exc_info.value.detail
    for leak in _LEAK_MARKERS:
        assert leak not in exc_info.value.detail


@pytest.mark.parametrize(("exc", "status", "_detail_part"), _SHARED_MAPPING_CASES)
def test_tasks_and_wakers_mapping_is_identical(exc, status, _detail_part):
    """CF9：两条路由复用同一映射，(status, detail) 必须完全一致（消除分叉）."""
    from fastapi import HTTPException

    from app.api.tasks import _map_deerflow_error as tasks_map
    from app.api.wakers import _map_deerflow_error as wakers_map

    outcomes = []
    for mapper in (tasks_map, wakers_map):
        with pytest.raises(HTTPException) as exc_info:
            mapper(exc)
        outcomes.append((exc_info.value.status_code, exc_info.value.detail))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == status


def test_authentication_error_maps_to_503_not_401_and_logs_critical(caplog):
    """服务账号鉴权失败 → 503（避免前端误判用户登录态失效而登出）+ CRITICAL 告警."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    with caplog.at_level(logging.DEBUG, logger="app.api.errors"):
        with pytest.raises(HTTPException) as exc_info:
            map_deerflow_error(AuthenticationError("Authentication failed: status=401"))

    assert exc_info.value.status_code == 503
    assert exc_info.value.status_code != 401
    assert any(
        r.levelno == logging.CRITICAL and r.name == "app.api.errors" for r in caplog.records
    )


def test_validation_error_logs_exception_as_service_defect(caplog):
    """ValidationError 属本服务缺陷 → logger.exception（ERROR + 栈）."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    exc = ValidationError("Validation error: status=422 path=/api/agents")
    with caplog.at_level(logging.DEBUG, logger="app.api.errors"):
        with pytest.raises(HTTPException) as exc_info:
            map_deerflow_error(exc)

    assert exc_info.value.status_code == 422
    error_records = [
        r
        for r in caplog.records
        if r.name == "app.api.errors" and r.levelno == logging.ERROR
    ]
    assert error_records
    assert error_records[0].exc_info is not None
    assert error_records[0].exc_info[1] is exc


def test_shared_map_passes_through_http_exception():
    """FastAPI HTTPException 原样透传（路由内主动抛出的 404/409 不被改写）."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    original = HTTPException(status_code=404, detail="Task not found")
    with pytest.raises(HTTPException) as exc_info:
        map_deerflow_error(original)
    assert exc_info.value is original


def test_shared_map_unexpected_error_returns_500_safe_detail():
    """非 DeerFlow 异常 → 500 + 安全 detail（不回传 str(exc)）."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    with pytest.raises(HTTPException) as exc_info:
        map_deerflow_error(RuntimeError(_SENSITIVE_BODY))
    assert exc_info.value.status_code == 500
    assert "SENSITIVE-UPSTREAM-DETAIL" not in exc_info.value.detail


def test_conflict_detail_composes_whitelisted_reason():
    """CF20 + CF9：白名单 reason 组织进 409 detail，前端可直接展示."""
    from fastapi import HTTPException

    from app.api.errors import map_deerflow_error

    with pytest.raises(TaskConflictError) as exc_info:
        DeerFlowClient._raise_for_status(_resp(409, _REASON_BODY), "/api/threads/t1/runs")
    with pytest.raises(HTTPException) as http_exc:
        map_deerflow_error(exc_info.value)

    assert http_exc.value.status_code == 409
    assert http_exc.value.detail == f"资源状态冲突，请刷新后重试：{_UPSTREAM_REASON}"


@pytest.mark.asyncio
async def test_create_task_auth_failure_returns_503(api_db_engine):
    """端到端：网关 401 → 503 + 安全中文 detail（不透传 401 触发前端登出）."""
    from app.main import create_app
    from app.models import Waker

    session_factory = async_sessionmaker(
        api_db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="d", soul_summary="s", enabled=True))
        await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=_SENSITIVE_BODY)

    real_client = DeerFlowClient("http://test:2026")
    real_client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    real_client._logged_in = True

    application = create_app()
    application.state.db_session_factory = session_factory
    application.state.deerflow = real_client
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/tasks", json={"executor": "alice", "input_text": "hi"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == "服务账号鉴权失败，请检查凭据配置"
    assert "SENSITIVE-UPSTREAM-DETAIL" not in resp.text


@pytest.mark.asyncio
async def test_create_task_conflict_detail_carries_upstream_reason(api_db_engine):
    """端到端：网关 409 带 detail → HTTP 409 + 「安全文案：上游原因」."""
    from app.main import create_app
    from app.models import Waker

    session_factory = async_sessionmaker(
        api_db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        session.add(Waker(name="alice", deer_user="", description="d", soul_summary="s", enabled=True))
        await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/threads":
            return httpx.Response(200, json={"thread_id": "t1"})
        return httpx.Response(409, text=_REASON_BODY)

    real_client = DeerFlowClient("http://test:2026")
    real_client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )
    real_client._logged_in = True

    application = create_app()
    application.state.db_session_factory = session_factory
    application.state.deerflow = real_client
    mock_sync = MagicMock()
    mock_sync.start = AsyncMock()
    mock_sync.stop = AsyncMock()
    application.state.sync_engine = mock_sync

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/tasks", json={"executor": "alice", "input_text": "hi"})

    assert resp.status_code == 409
    assert resp.json()["detail"] == f"资源状态冲突，请刷新后重试：{_UPSTREAM_REASON}"
    assert "hunter2" not in resp.text
    assert "gateway-internal" not in resp.text
