"""Tests for app.deerflow.client.DeerFlowClient using httpx.MockTransport."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.deerflow.client import DeerFlowClient
from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    AuthenticationError,
    DeerFlowUnavailableError,
    TaskConflictError,
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
# Not-logged-in guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_without_login_raises():
    client = DeerFlowClient("http://test:2026")
    with pytest.raises(AuthenticationError, match="Not logged in"):
        await client.list_agents()
