"""会话生命周期增强 + 统一状态映射 测试."""

import asyncio
import time

import httpx
import pytest

import app.deerflow.client as client_module
from app.deerflow.client import DeerFlowClient
from app.deerflow.errors import AuthenticationError, DeerFlowUnavailableError
from app.services.status_mapping import RUN_TO_TASK_STATUS, map_run_to_task_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_handler() -> httpx.MockTransport:
    """返回一个接受任意登录的 MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login/local":
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={})

    return httpx.MockTransport(handler)


async def _make_client_with_transport(
    transport: httpx.MockTransport,
) -> DeerFlowClient:
    client = DeerFlowClient("http://test:2026")
    client._client = httpx.AsyncClient(
        base_url="http://test:2026",
        timeout=60,
        follow_redirects=True,
        transport=transport,
    )
    return client


# ---------------------------------------------------------------------------
# is_authenticated / session_age_days
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_authenticated_before_and_after_login():
    """登录前 is_authenticated=False，登录后=True."""
    client = await _make_client_with_transport(_login_handler())
    assert client.is_authenticated is False

    await client.login("u@test.com", "pw")
    assert client.is_authenticated is True


@pytest.mark.asyncio
async def test_session_age_days_before_login():
    """未登录时 session_age_days 返回 inf."""
    client = DeerFlowClient("http://test:2026")
    assert client.session_age_days == float("inf")


@pytest.mark.asyncio
async def test_session_age_days_after_login():
    """登录后 session_age_days 接近 0."""
    client = await _make_client_with_transport(_login_handler())
    await client.login("u@test.com", "pw")
    assert client.session_age_days < 0.001  # 刚刚登录


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_clears_state():
    """logout 后状态全部清除."""
    client = await _make_client_with_transport(_login_handler())
    await client.login("u@test.com", "pw")
    assert client.is_authenticated is True

    client.logout()
    assert client.is_authenticated is False
    assert client._login_at is None
    assert client._logged_in is False


# ---------------------------------------------------------------------------
# update_credentials
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_credentials():
    """凭据轮换后，下次登录使用新凭据."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login/local":
            # 记录表单字段
            body = dict(httpx.QueryParams(request.content.decode()))
            captured.update(body)
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)

    await client.login("old@test.com", "old_pw")
    client.update_credentials("new@test.com", "new_pw")

    # 触发重新登录（模拟会话过期）
    client._login_at = time.time() - client._refresh_threshold - 1
    # 模拟一次请求，_ensure_logged_in 应触发主动刷新
    # 先直接调用 _ensure_logged_in 验证
    await client._ensure_logged_in()

    assert captured.get("username") == "new@test.com"
    assert captured.get("password") == "new_pw"


# ---------------------------------------------------------------------------
# proactive refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proactive_refresh_when_session_old():
    """会话超过 6 天时，_ensure_logged_in 自动重新登录."""
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"agents": []})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)

    await client.login("u@test.com", "pw")
    assert login_count == 1

    # 模拟会话已存在 6.5 天
    client._login_at = time.time() - (6.5 * 86400)

    # 触发 _ensure_logged_in → 应自动重新登录
    await client._ensure_logged_in()
    assert login_count == 2  # 第二次登录被触发


@pytest.mark.asyncio
async def test_no_proactive_refresh_when_fresh():
    """会话新鲜时不触发主动刷新."""
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)

    await client.login("u@test.com", "pw")
    assert login_count == 1

    # 会话刚建立，不应触发刷新
    await client._ensure_logged_in()
    assert login_count == 1


# ---------------------------------------------------------------------------
# 状态映射一致性
# ---------------------------------------------------------------------------


def test_status_mapping_consistency():
    """所有已知 run status 映射正确."""
    assert map_run_to_task_status("success") == "done"
    assert map_run_to_task_status("error") == "failed"
    assert map_run_to_task_status("timeout") == "failed"
    assert map_run_to_task_status("interrupted") == "cancelled"
    assert map_run_to_task_status("pending") == "pending"
    assert map_run_to_task_status("running") == "running"


def test_unknown_run_status_maps_to_failed():
    """未知 run status 防御式映射为 failed."""
    assert map_run_to_task_status("foobar") == "failed"
    assert map_run_to_task_status("") == "failed"
    assert map_run_to_task_status("completed") == "failed"  # DeerFlow 不用 completed


def test_run_to_task_status_dict_completeness():
    """RUN_TO_TASK_STATUS 包含所有预期键."""
    expected_keys = {"success", "error", "timeout", "interrupted", "pending", "running"}
    assert set(RUN_TO_TASK_STATUS.keys()) == expected_keys


# ---------------------------------------------------------------------------
# G2/G3: 会话自愈（降级启动恢复 / 401 无感恢复 / 冷却节流 / 并发单飞）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_logged_in_self_heals_when_upstream_recovers():
    """G2：降级启动（首次登录连接失败）后，上游恢复时请求自动重登（无需重启进程）."""
    login_count = 0
    upstream_down = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            if upstream_down:
                raise httpx.ConnectError("upstream down")
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={"agents": []})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)

    # 1) 降级启动：登录失败属"上游不可达"（瞬时语义），且凭据已被缓存
    with pytest.raises(DeerFlowUnavailableError):
        await client.login("u@test.com", "pw")
    assert client._email == "u@test.com"
    assert client._password == "pw"
    assert client.is_authenticated is False

    # 2) 上游恢复 → 下一次业务请求自动重登并成功（无需人工干预）
    upstream_down = False
    agents = await client.list_agents()
    assert agents == []
    assert login_count == 2
    assert client.is_authenticated is True


@pytest.mark.asyncio
async def test_401_self_heals_and_retries_once():
    """G3：业务请求收到 401（DeerFlow 重启场景）→ 自动重登并重试一次，用户无感."""
    agents_calls = 0
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal agents_calls, login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            return httpx.Response(200, json={"access_token": "tok"})
        if request.url.path == "/api/agents":
            agents_calls += 1
            if agents_calls == 1:
                return httpx.Response(401, text="Session expired")
            return httpx.Response(200, json={"agents": []})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)
    await client.login("u@test.com", "pw")
    assert login_count == 1

    agents = await client.list_agents()
    assert agents == []
    assert agents_calls == 2  # 401 一次 + 重登后重试一次
    assert login_count == 2  # 自愈重登一次


@pytest.mark.asyncio
async def test_401_with_failed_relogin_raises_authentication_error():
    """G3 兜底：401 后重登也被拒（凭据失效）→ 抛 AuthenticationError（不无限重试）."""
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            if login_count == 1:
                return httpx.Response(200, json={"access_token": "tok"})
            return httpx.Response(401, text="Bad credentials")
        if request.url.path == "/api/agents":
            return httpx.Response(401, text="Session expired")
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)
    await client.login("u@test.com", "pw")

    with pytest.raises(AuthenticationError, match="Session expired"):
        await client.list_agents()


@pytest.mark.asyncio
async def test_relogin_cooldown_throttles_then_recovers():
    """冷却节流：失败后冷却期内跳过上游登录接口；冷却过期 + 凭据恢复后重登成功."""
    login_count = 0
    rejecting = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            if rejecting:
                return httpx.Response(401, text="Bad credentials")
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)
    client._email = "u@test.com"
    client._password = "pw"

    # 1) 首次重登被拒（凭据问题）→ 进入冷却
    with pytest.raises(AuthenticationError, match="Login failed"):
        await client._relogin()
    assert login_count == 1
    assert client._last_login_failure_at is not None

    # 2) 冷却期内再触发 → 跳过上游（登录接口无新调用，防风暴）
    with pytest.raises(DeerFlowUnavailableError, match="cooldown"):
        await client._relogin()
    assert login_count == 1

    # 3) 冷却过期 + 凭据恢复 → 重登成功
    client._last_login_failure_at = (
        time.time() - client_module._RELOGIN_COOLDOWN_SECONDS - 1
    )
    rejecting = False
    await client._relogin()
    assert login_count == 2
    assert client.is_authenticated is True


@pytest.mark.asyncio
async def test_relogin_single_flight_under_concurrency():
    """并发自愈单飞：多个请求同时触发重登 → 登录接口只调用一次."""
    login_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal login_count
        if request.url.path == "/api/v1/auth/login/local":
            login_count += 1
            return httpx.Response(200, json={"access_token": "tok"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    client = await _make_client_with_transport(transport)
    client._email = "u@test.com"
    client._password = "pw"

    await asyncio.gather(*(client._relogin() for _ in range(5)))
    assert login_count == 1
    assert client.is_authenticated is True


@pytest.mark.asyncio
async def test_relogin_without_cached_credentials_raises_not_logged_in():
    """防御：无缓存凭据时 _relogin 拒绝尝试（消息保持 'Not logged in'）."""
    client = await _make_client_with_transport(_login_handler())
    with pytest.raises(AuthenticationError, match="Not logged in"):
        await client._relogin()
