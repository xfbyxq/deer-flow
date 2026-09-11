"""MCP 工具测试 — MCPService 业务逻辑 + register_mcp 脚本 + ASGI lifespan."""

import asyncio
import json
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.tasks import MCPService, _check_same_group, _extract_result
from app.models.waker import Waker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Mock AsyncSession."""
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_session_factory(mock_db):
    """返回 mock_db 的 session factory（async context manager）."""

    class _Ctx:
        async def __aenter__(self):
            return mock_db

        async def __aexit__(self, *exc):
            return False

    def _factory():
        return _Ctx()

    return _factory


@pytest.fixture
def mock_df():
    """Mock DeerFlowClient."""
    df = MagicMock()
    df.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    df.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    df.get_run = AsyncMock(return_value={"status": "success", "output": "done"})
    df.close = AsyncMock()
    return df


@pytest.fixture
def service(mock_session_factory, mock_df):
    """MCPService with mock dependencies."""
    return MCPService(mock_session_factory, mock_df)


def _mock_waker(name: str, enabled: bool = True) -> Waker:
    """Build a lightweight Waker-like mock."""
    w = MagicMock()
    w.name = name
    w.description = f"Test {name}"
    w.enabled = enabled
    w.soul_summary = "..."
    w.home_thread_id = None
    return w


def _mock_execute_returning(mock_db, rows):
    """Configure mock_db.execute to return *rows* via .scalars().all()/.first()."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    scalars.first.return_value = rows[0] if rows else None
    result.scalars.return_value = scalars
    mock_db.execute = AsyncMock(return_value=result)


# ---------------------------------------------------------------------------
# query_group
# ---------------------------------------------------------------------------


class TestQueryGroup:
    @pytest.mark.asyncio
    async def test_returns_enabled_wakers(self, service, mock_db):
        wakers = [_mock_waker("alice"), _mock_waker("bob")]
        _mock_execute_returning(mock_db, wakers)

        result = await service.query_group("default")

        assert result["group_id"] == "default"
        assert len(result["members"]) == 2
        assert result["members"][0]["name"] == "alice"
        assert result["members"][0]["enabled"] is True
        assert result["members"][1]["name"] == "bob"

    @pytest.mark.asyncio
    async def test_empty_group(self, service, mock_db):
        _mock_execute_returning(mock_db, [])

        result = await service.query_group()

        assert result["group_id"] == "default"
        assert result["members"] == []


# ---------------------------------------------------------------------------
# delegate_to_agent
# ---------------------------------------------------------------------------


class TestDelegateToAgent:
    @pytest.fixture(autouse=True)
    def _skip_group_check(self):
        """Patch _check_same_group → None for all existing tests (P0 path)."""
        with patch("app.mcp.tasks._check_same_group", new=AsyncMock(return_value=None)):
            yield

    @pytest.mark.asyncio
    async def test_success_sync(self, service, mock_db, mock_df):
        """sync 模式: run 完成 → status=done + 结果."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {
            "status": "success",
            "output": {"messages": [{"content": "Analysis complete"}]},
        }

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="alice",
            instruction="分析数据",
            accept_criteria="准确",
            sync=True,
            sync_timeout=4.0,
            poll_interval=0.01,
        )

        assert result["status"] == "done"
        assert result["ticket_id"] is not None
        assert result["run_id"] == "test-run-id"
        assert result["thread_id"] is not None
        assert "Analysis complete" in result["result"]
        mock_df.create_thread.assert_called_once()
        mock_df.create_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_caller_validation_at_server_layer(self, service, mock_db):
        """caller 校验在 server.py 的 tool wrapper 中完成，service 层不校验.

        这里验证 service 层接受 caller 参数后继续执行业务逻辑。
        实际的 caller=None → error 行为由 server 层的 _extract_caller 处理。
        """
        # service 层不校验 caller，它假设 caller 已被 server 层验证
        # 测试 target 不存在的情况（caller 不影响此路径）
        _mock_execute_returning(mock_db, [])
        result = await service.delegate_to_agent(
            caller="any-caller",
            group_id="default",
            target_agent="nonexistent",
            instruction="test",
        )
        assert "error" in result  # target not found

    @pytest.mark.asyncio
    async def test_target_not_found(self, service, mock_db):
        """目标员工不存在 → error."""
        _mock_execute_returning(mock_db, [])

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="nonexistent",
            instruction="test",
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_target_disabled(self, service, mock_db):
        """目标员工停用 → error."""
        _mock_execute_returning(mock_db, [_mock_waker("alice", enabled=False)])

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="alice",
            instruction="test",
        )

        assert "error" in result
        assert "disabled" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_sync_timeout(self, service, mock_db, mock_df):
        """sync 超时: run 一直 running → 返回 ticket."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {"status": "running"}

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="alice",
            instruction="test",
            sync=True,
            sync_timeout=0.1,
            poll_interval=0.01,
        )

        assert result["status"] == "running"
        assert result["ticket_id"] is not None
        assert "看板可查" in result["message"]

    @pytest.mark.asyncio
    async def test_run_failed(self, service, mock_db, mock_df):
        """run 失败 → status=failed."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {"status": "failed"}

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="alice",
            instruction="test",
            sync=True,
            sync_timeout=4.0,
            poll_interval=0.01,
        )

        assert result["status"] == "failed"
        assert "failed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_async_mode(self, service, mock_db, mock_df):
        """async 模式: 立即返回 ticket."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])

        result = await service.delegate_to_agent(
            caller="bob",
            group_id="default",
            target_agent="alice",
            instruction="test",
            sync=False,
        )

        assert result["status"] == "running"
        assert result["ticket_id"] is not None
        # async 模式不调用 get_run
        mock_df.get_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_run_fails(self, service, mock_db, mock_df):
        """create_run 失败 → TASK status=failed + 异常上抛."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.create_run.side_effect = Exception("DeerFlow down")

        with pytest.raises(Exception, match="DeerFlow down"):
            await service.delegate_to_agent(
                caller="bob",
                group_id="default",
                target_agent="alice",
                instruction="test",
            )

        # TASK 应被标记为 failed
        assert mock_db.commit.called


# ---------------------------------------------------------------------------
# _extract_result helper
# ---------------------------------------------------------------------------


class TestExtractResult:
    def test_with_messages(self):
        run_info = {"output": {"messages": [{"content": "hello"}]}}
        assert _extract_result(run_info) == "hello"

    def test_with_direct_output(self):
        run_info = {"output": "simple result"}
        assert _extract_result(run_info) == "simple result"

    def test_with_none_output(self):
        run_info = {"output": None}
        assert _extract_result(run_info) == "Run completed"

    def test_with_empty_messages(self):
        run_info = {"output": {"messages": []}}
        assert _extract_result(run_info) == "{'messages': []}"


# ---------------------------------------------------------------------------
# register_mcp.py（经 Gateway MCP 配置 API，位置无关）
# ---------------------------------------------------------------------------

GW_URL = "http://test:2026"


@pytest.fixture(scope="module")
def register_mcp_module():
    """导入 scripts/register_mcp.py（脚本会自行把 waker-team 根目录加入 sys.path）."""
    scripts_dir = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import register_mcp

        return register_mcp
    finally:
        sys.path.pop(0)


@pytest.fixture
def mcp_env(monkeypatch):
    """脚本连接参数（环境变量优先于 .env）."""
    monkeypatch.setenv("DEERFLOW_BASE_URL", GW_URL)
    monkeypatch.setenv("SERVICE_EMAIL", "admin@test.com")
    monkeypatch.setenv("SERVICE_PASSWORD", "adminpass")


class TestRegisterMCP:
    """注册=存在则更新/不存在则新增；注销=幂等删除（走 Gateway API）."""

    @staticmethod
    def _mock_login(httpx_mock):
        httpx_mock.add_response(
            method="POST",
            url=f"{GW_URL}/api/v1/auth/login/local",
            json={"access_token": "t"},
            headers={"set-cookie": "csrf_token=test-csrf; Path=/"},
        )

    async def test_register_adds_when_absent(
        self, httpx_mock, mcp_env, register_mcp_module
    ):
        """未注册: POST 新增，payload 与旧版结构一致."""
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="GET", url=f"{GW_URL}/api/mcp/config", json={"mcp_servers": {}}
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{GW_URL}/api/mcp/config/servers",
            json={"mcp_servers": {}},
        )

        await register_mcp_module.register(port=9999)

        post = httpx_mock.get_request(
            method="POST", url=f"{GW_URL}/api/mcp/config/servers"
        )
        server = json.loads(post.content)["mcp_servers"]["waker-team"]
        assert server["enabled"] is True
        assert server["type"] == "http"
        assert server["url"] == "http://host.docker.internal:9999/mcp"
        assert server["headers_from_context"]["headers"]["X-Waker-Caller"] == "waker_identity"
        assert server["headers_from_context"]["on_missing"] == "deny"
        assert post.headers["X-CSRF-Token"] == "test-csrf"

    async def test_register_updates_when_present(
        self, httpx_mock, mcp_env, register_mcp_module
    ):
        """已注册: PUT 更新，不发新增请求."""
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="GET",
            url=f"{GW_URL}/api/mcp/config",
            json={"mcp_servers": {"waker-team": {"url": "http://old"}}},
        )
        httpx_mock.add_response(
            method="PUT", url=f"{GW_URL}/api/mcp/config/server", json={"mcp_servers": {}}
        )

        await register_mcp_module.register(port=8765)

        put = httpx_mock.get_request(method="PUT", url=f"{GW_URL}/api/mcp/config/server")
        payload = json.loads(put.content)
        assert payload["server_name"] == "waker-team"
        assert payload["server"]["url"] == "http://host.docker.internal:8765/mcp"
        assert (
            httpx_mock.get_requests(method="POST", url=f"{GW_URL}/api/mcp/config/servers")
            == []
        )

    async def test_unregister_deletes(self, httpx_mock, mcp_env, register_mcp_module):
        """注销: DELETE 指定 server."""
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="DELETE",
            url=f"{GW_URL}/api/mcp/config/servers/waker-team",
            json={"mcp_servers": {}},
        )

        await register_mcp_module.unregister()

        delete = httpx_mock.get_request(
            method="DELETE", url=f"{GW_URL}/api/mcp/config/servers/waker-team"
        )
        assert delete.headers["X-CSRF-Token"] == "test-csrf"

    async def test_unregister_not_registered_is_idempotent(
        self, httpx_mock, mcp_env, register_mcp_module
    ):
        """注销未注册的 server: 404 容忍，不抛异常."""
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="DELETE",
            url=f"{GW_URL}/api/mcp/config/servers/waker-team",
            status_code=404,
            json={"detail": "MCP server 'waker-team' not found"},
        )

        await register_mcp_module.unregister()  # 不应抛异常

    async def test_register_requires_credentials(
        self, monkeypatch, tmp_path, register_mcp_module
    ):
        """缺少凭据: 提前退出并提示（不依赖 .env）."""
        monkeypatch.delenv("SERVICE_EMAIL", raising=False)
        monkeypatch.delenv("SERVICE_PASSWORD", raising=False)
        monkeypatch.setattr(register_mcp_module, "ENV_FILE", tmp_path / "missing.env")

        with pytest.raises(SystemExit):
            await register_mcp_module.register(port=8765)

    async def test_register_with_admin_email_uses_env_password(
        self, httpx_mock, mcp_env, monkeypatch, register_mcp_module
    ):
        """--email 指定管理员: 密码取 DEERFLOW_ADMIN_PASSWORD 环境变量."""
        monkeypatch.setenv("DEERFLOW_ADMIN_PASSWORD", "admin-secret")
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="GET", url=f"{GW_URL}/api/mcp/config", json={"mcp_servers": {}}
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{GW_URL}/api/mcp/config/servers",
            json={"mcp_servers": {}},
        )

        await register_mcp_module.register(port=8765, email="admin@corp.com")

        login = httpx_mock.get_request(
            method="POST", url=f"{GW_URL}/api/v1/auth/login/local"
        )
        form = parse_qs(login.content.decode())
        assert form["username"] == ["admin@corp.com"]
        assert form["password"] == ["admin-secret"]

    async def test_register_with_admin_email_prompts_password(
        self, httpx_mock, mcp_env, monkeypatch, register_mcp_module
    ):
        """--email 且无环境变量密码: 交互式输入（此处 patch getpass）."""
        monkeypatch.delenv("DEERFLOW_ADMIN_PASSWORD", raising=False)
        monkeypatch.setattr(
            register_mcp_module.getpass, "getpass", lambda prompt: "typed-secret"
        )
        self._mock_login(httpx_mock)
        httpx_mock.add_response(
            method="GET", url=f"{GW_URL}/api/mcp/config", json={"mcp_servers": {}}
        )
        httpx_mock.add_response(
            method="POST",
            url=f"{GW_URL}/api/mcp/config/servers",
            json={"mcp_servers": {}},
        )

        await register_mcp_module.register(port=8765, email="admin@corp.com")

        login = httpx_mock.get_request(
            method="POST", url=f"{GW_URL}/api/v1/auth/login/local"
        )
        form = parse_qs(login.content.decode())
        assert form["password"] == ["typed-secret"]


# ---------------------------------------------------------------------------
# ASGI lifespan regression test
# ---------------------------------------------------------------------------


def _make_tracking_lifespan(events: list):
    """Return a *side_effect* callable for ``session_manager.run()``.

    Each invocation creates a fresh async context manager that appends
    ``'enter'`` / ``'exit'`` to *events*, allowing tests to assert that
    ``init_service`` was called **inside** the session manager's run().
    """
    def _factory():
        @asynccontextmanager
        async def _tracked():
            events.append("enter")
            yield
            events.append("exit")
        return _tracked()
    return _factory


class TestASGILifespan:
    """Verify that the ASGI wrapper initialises MCPService on lifespan startup."""

    @pytest.mark.asyncio
    async def test_lifespan_startup_calls_init_service(self):
        """lifespan.startup should trigger init_service() inside session_manager.run()."""
        from app.mcp import server as srv

        # Save and reset global state
        original_service = srv._service
        srv._service = None

        messages_sent = []
        call_count = 0

        async def mock_receive():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "lifespan.startup"}
            return {"type": "lifespan.shutdown"}

        async def mock_send(message):
            messages_sent.append(message)

        mock_inner = AsyncMock()
        events: list[str] = []

        async def tracking_init_service(*a, **kw):
            events.append("init_service")

        with patch.object(srv, "init_service", new=tracking_init_service):
            wrapper = srv._ASGIWithLifespan(mock_inner)
            mock_sm = MagicMock()
            mock_sm.run = MagicMock(side_effect=_make_tracking_lifespan(events))
            wrapper._session_manager = mock_sm
            await wrapper({"type": "lifespan"}, mock_receive, mock_send)

            # init_service must be called INSIDE session_manager.run()
            assert events == ["enter", "init_service", "exit"]
            assert any(m["type"] == "lifespan.startup.complete" for m in messages_sent)
            # Inner app should NOT be called for lifespan events
            mock_inner.assert_not_called()

        # Restore
        srv._service = original_service

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_calls_cleanup(self):
        """lifespan.shutdown should close the DeerFlow client."""
        from app.mcp import server as srv

        mock_df = MagicMock()
        mock_df.close = AsyncMock()
        mock_service = MagicMock()
        mock_service.df = mock_df

        original_service = srv._service
        srv._service = mock_service

        messages_sent = []
        call_count = 0

        async def mock_receive():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "lifespan.startup"}
            return {"type": "lifespan.shutdown"}

        async def mock_send(message):
            messages_sent.append(message)

        mock_inner = AsyncMock()
        events: list[str] = []

        async def tracking_init_service(*a, **kw):
            events.append("init_service")

        with patch.object(srv, "init_service", new=tracking_init_service):
            wrapper = srv._ASGIWithLifespan(mock_inner)
            mock_sm = MagicMock()
            mock_sm.run = MagicMock(side_effect=_make_tracking_lifespan(events))
            wrapper._session_manager = mock_sm
            await wrapper({"type": "lifespan"}, mock_receive, mock_send)

            mock_df.close.assert_awaited_once()
            assert events == ["enter", "init_service", "exit"]
            assert any(m["type"] == "lifespan.shutdown.complete" for m in messages_sent)

        srv._service = original_service

    @pytest.mark.asyncio
    async def test_non_lifespan_delegates_to_inner(self):
        """HTTP requests should be forwarded to the inner ASGI app."""
        from app.mcp import server as srv

        mock_inner = AsyncMock()
        wrapper = srv._ASGIWithLifespan(mock_inner)

        mock_receive = AsyncMock()
        mock_send = AsyncMock()
        scope = {"type": "http", "path": "/mcp"}
        await wrapper(scope, mock_receive, mock_send)

        mock_inner.assert_awaited_once_with(scope, mock_receive, mock_send)


# ---------------------------------------------------------------------------
# Cross-group safety tests (M9)
# ---------------------------------------------------------------------------


def _mock_group(group_id: str, name: str = ""):
    """Build a lightweight Group-like mock."""
    g = MagicMock()
    g.id = group_id
    g.name = name or f"group-{group_id}"
    return g


class TestCrossGroupDelegate:
    """M9: 跨组委派安全校验."""

    @pytest.mark.asyncio
    async def test_cross_group_delegate_blocked(self, mock_session_factory, mock_df):
        """两个 waker 在不同组 → delegate 被拒."""
        svc = MCPService(mock_session_factory, mock_df)

        async def fake_check(db, caller, target):
            return "blocked: cross_group_delegation — charlie is not in your group"

        with patch("app.mcp.tasks._check_same_group", side_effect=fake_check):
            result = await svc.delegate_to_agent(
                caller="alice",
                group_id="default",
                target_agent="charlie",
                instruction="test",
            )

        assert "error" in result
        assert "cross_group_delegation" in result["error"]
        assert "charlie" in result["error"]

    @pytest.mark.asyncio
    async def test_same_group_delegate_allowed(self, service, mock_db, mock_df):
        """两个 waker 在同组 → delegate 通过."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {"status": "success", "output": "done"}

        with patch("app.mcp.tasks._check_same_group", new=AsyncMock(return_value=None)) as mock_chk:
            result = await service.delegate_to_agent(
                caller="bob",
                group_id="default",
                target_agent="alice",
                instruction="test",
                sync=True,
                sync_timeout=4.0,
                poll_interval=0.01,
            )

        mock_chk.assert_awaited_once_with(mock_db, "bob", "alice")
        assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_delegate_to_self_rejected(self, service):
        """委派给自己 → 明确拒绝（Leader 不应把活派给自己）."""
        result = await service.delegate_to_agent(
            caller="alice", group_id="default", target_agent="alice", instruction="x"
        )
        assert "error" in result
        assert "yourself" in result["error"]

    @pytest.mark.asyncio
    async def test_delegate_result_from_thread_state(self, service, mock_db, mock_df):
        """run 响应无 output 时，result 从成员 thread state 提取真实回复正文."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {"status": "success"}  # 无 output/messages
        mock_df.get_thread_state = AsyncMock(
            return_value={
                "values": {
                    "messages": [
                        {
                            "type": "human",
                            "content": "介绍",
                            "additional_kwargs": {"run_id": "test-run-id"},
                        },
                        {
                            "type": "ai",
                            "content": "我是小Li，负责调研。",
                            "additional_kwargs": {"run_id": "test-run-id"},
                        },
                    ]
                }
            }
        )

        with patch("app.mcp.tasks._check_same_group", new=AsyncMock(return_value=None)):
            result = await service.delegate_to_agent(
                caller="bob",
                group_id="default",
                target_agent="alice",
                instruction="test",
                sync=True,
                sync_timeout=4.0,
                poll_interval=0.01,
            )

        assert result["status"] == "done"
        assert result["result"] == "我是小Li，负责调研。"

    @pytest.mark.asyncio
    async def test_no_group_delegate_allowed(self, service, mock_db, mock_df):
        """waker 不在任何组 → delegate 通过（P0 兼容）."""
        _mock_execute_returning(mock_db, [_mock_waker("alice")])
        mock_df.get_run.return_value = {"status": "success", "output": "done"}

        # _check_same_group returns None when either party has no groups
        with patch("app.mcp.tasks._check_same_group", new=AsyncMock(return_value=None)) as mock_chk:
            result = await service.delegate_to_agent(
                caller="lone-wolf",
                group_id="default",
                target_agent="alice",
                instruction="test",
                sync=True,
                sync_timeout=4.0,
                poll_interval=0.01,
            )

        mock_chk.assert_awaited_once()
        assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_cross_group_async_delegate_blocked(self):
        """async delegate 跨组 → 同样被拒."""
        from app.mcp.async_tasks import AsyncDelegateMCPService

        mock_service = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory = MagicMock(return_value=mock_session)

        async_svc = AsyncDelegateMCPService(mock_service, db_session_factory=mock_factory)

        async def fake_check(db, caller, target):
            return "blocked: cross_group_delegation — charlie is not in your group"

        with patch("app.mcp.async_tasks._check_same_group", side_effect=fake_check):
            result = await async_svc.delegate_submit(
                caller="alice",
                target="charlie",
                instruction="test",
            )

        assert result["status"] == "error"
        assert "cross_group_delegation" in result["error"]
        mock_service.submit.assert_not_called()


# ---------------------------------------------------------------------------
# _check_same_group unit tests
# ---------------------------------------------------------------------------


class TestCheckSameGroup:
    """Unit tests for the _check_same_group helper."""

    @pytest.mark.asyncio
    async def test_both_in_same_group(self, mock_db):
        """Same group → None (allowed)."""
        g1 = _mock_group("g1")
        # First call: caller groups, second call: target groups
        call_count = 0

        async def fake_execute(query):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            scalars = MagicMock()
            if call_count == 1:
                scalars.all.return_value = [g1]
            else:
                scalars.all.return_value = [g1]
            result.scalars.return_value = scalars
            return result

        mock_db.execute = AsyncMock(side_effect=fake_execute)

        with patch("app.mcp.tasks.GroupService") as MockGS:
            instance = MockGS.return_value
            instance.get_waker_groups = AsyncMock(side_effect=[[g1], [g1]])
            err = await _check_same_group(mock_db, "alice", "bob")

        assert err is None

    @pytest.mark.asyncio
    async def test_different_groups_blocked(self, mock_db):
        """Different groups → error message."""
        g1 = _mock_group("g1")
        g2 = _mock_group("g2")

        with patch("app.mcp.tasks.GroupService") as MockGS:
            instance = MockGS.return_value
            instance.get_waker_groups = AsyncMock(side_effect=[[g1], [g2]])
            err = await _check_same_group(mock_db, "alice", "charlie")

        assert err is not None
        assert "cross_group_delegation" in err
        assert "charlie" in err

    @pytest.mark.asyncio
    async def test_caller_not_in_any_group(self, mock_db):
        """Caller not in any group → None (P0 compat)."""
        g1 = _mock_group("g1")

        with patch("app.mcp.tasks.GroupService") as MockGS:
            instance = MockGS.return_value
            instance.get_waker_groups = AsyncMock(side_effect=[[], [g1]])
            err = await _check_same_group(mock_db, "lone", "bob")

        assert err is None

    @pytest.mark.asyncio
    async def test_target_not_in_any_group(self, mock_db):
        """Target not in any group → None (P0 compat)."""
        g1 = _mock_group("g1")

        with patch("app.mcp.tasks.GroupService") as MockGS:
            instance = MockGS.return_value
            instance.get_waker_groups = AsyncMock(side_effect=[[g1], []])
            err = await _check_same_group(mock_db, "alice", "lone")

        assert err is None


# ---------------------------------------------------------------------------
# 回归：session 管理（历史故障：长活 session 毒化导致工具持续报错）
# ---------------------------------------------------------------------------


class TestMCPServiceSessionIsolation:
    """回归：MCPService 每次调用使用独立短 session.

    历史故障：复用长活 session 时，一次 flush 失败（UPDATE 0 rows matched）
    会使 session 进入 pending-rollback 状态，后续所有工具调用持续失败，
    直到进程重启（表现为 query_group 间歇性 "Error executing tool"）。
    """

    @pytest.mark.asyncio
    async def test_query_group_sees_external_writes(self):
        """两次 query_group 之间由其他 session 写入的数据应立即可见（无长活快照隔离）."""
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.database import Base
        from app.models.group import Group, GroupMember

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        svc = MCPService(factory, MagicMock())
        first = await svc.query_group()
        assert first["members"] == []

        # 另一个 session 写入新 waker 并加入 default 组（query_group 按组过滤）
        async with factory() as db:
            db.add(Waker(name="newbie", deer_user="", description="x", soul_summary=""))
            db.add(Group(id="default", name="默认团队"))
            db.add(GroupMember(group_id="default", waker_id="newbie", role="member"))
            await db.commit()

        second = await svc.query_group()
        assert [m["name"] for m in second["members"]] == ["newbie"]
        await engine.dispose()


# ---------------------------------------------------------------------------
# 回归：query_group 按群组成员关系过滤（同组才是同事）
# ---------------------------------------------------------------------------


class TestQueryGroupScoping:
    """同事列表 = 与 caller 同群组的 enabled 员工（不再返回全体员工）."""

    @pytest.fixture
    async def scoped_factory(self):
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from app.database import Base
        from app.models.group import Group, GroupMember

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all(
                [
                    Waker(name="alice", description="a"),
                    Waker(name="bob", description="b"),
                    Waker(name="carl", description="c"),  # 不在任何组
                    Waker(name="dave", description="d", enabled=False),  # 同组但停用
                ]
            )
            db.add(Group(id="g1", name="组1", leader_waker_id="alice"))
            db.add(GroupMember(group_id="g1", waker_id="bob", role="member"))
            db.add(GroupMember(group_id="g1", waker_id="dave", role="member"))
            await db.commit()
        yield factory
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_caller_scoped_to_own_groups(self, scoped_factory):
        """leader（未登记为 member）查询 → 只返回其组的 enabled 成员."""
        svc = MCPService(scoped_factory, MagicMock())
        result = await svc.query_group(caller="alice")
        # dave 停用；carl 不在组；alice 非 group_members 成员（仅 leader）
        assert {m["name"] for m in result["members"]} == {"alice", "bob"}

    @pytest.mark.asyncio
    async def test_caller_without_group_gets_empty(self, scoped_factory):
        """无组 waker 查询 → 空列表 + 提示."""
        svc = MCPService(scoped_factory, MagicMock())
        result = await svc.query_group(caller="carl")
        assert result["members"] == []
        assert "尚未加入任何群组" in result.get("message", "")

    @pytest.mark.asyncio
    async def test_no_caller_filters_by_group_id(self, scoped_factory):
        """无 caller（管理视角）→ 按显式 group_id 过滤."""
        svc = MCPService(scoped_factory, MagicMock())
        result = await svc.query_group(group_id="g1")
        assert {m["name"] for m in result["members"]} == {"alice", "bob"}
        # 不存在的组 → 空
        result2 = await svc.query_group(group_id="no-such-group")
        assert result2["members"] == []

    @pytest.mark.asyncio
    async def test_member_sees_own_group(self, scoped_factory):
        """member 查询 → 返回同组 enabled 成员（含自己）."""
        svc = MCPService(scoped_factory, MagicMock())
        result = await svc.query_group(caller="bob")
        assert {m["name"] for m in result["members"]} == {"alice", "bob"}


class TestInitServiceGlobals:
    """回归：init_service 必须同时初始化 _service 与 _async_service.

    历史故障：漏写 ``global _async_service``，赋值落入局部变量，
    导致 delegate_submit/status/cancel 永远报 "AsyncDelegateMCPService not initialised"。
    """

    @pytest.mark.asyncio
    async def test_init_service_sets_both_services(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        import app.mcp.server as mcp_server

        class _FakeDF:
            def __init__(self, base_url: str) -> None:
                self.base_url = base_url

            async def login(self, email: str, password: str) -> None:  # noqa: ARG002
                return None

        monkeypatch.setattr(mcp_server, "DeerFlowClient", _FakeDF)
        monkeypatch.setattr(
            mcp_server,
            "get_settings",
            lambda: SimpleNamespace(
                deerflow_base_url="http://test",
                service_email="",
                service_password="",
                database_url="sqlite+aiosqlite://",
            ),
        )
        monkeypatch.setattr(mcp_server, "_service", None)
        monkeypatch.setattr(mcp_server, "_async_service", None)

        await mcp_server.init_service(db_url=f"sqlite+aiosqlite:///{tmp_path}/mcp.db")

        assert mcp_server._service is not None
        assert mcp_server._async_service is not None


class TestInitServiceDegradesWhenLoginFails:
    """G1: DeerFlow 未就绪时 MCP Server 降级启动（登录失败不崩溃，会话按需自愈）."""

    @pytest.mark.asyncio
    async def test_init_service_degrades_when_login_fails(self, tmp_path, monkeypatch):
        """login 抛错时 init_service 仍完成装配（服务可启动，重登由 client 自愈）."""
        from types import SimpleNamespace

        import app.mcp.server as mcp_server
        from app.deerflow.errors import AuthenticationError

        class _FailingLoginDF:
            def __init__(self, base_url: str) -> None:
                self.base_url = base_url

            async def login(self, email: str, password: str) -> None:  # noqa: ARG002
                raise AuthenticationError("DeerFlow not ready")

        monkeypatch.setattr(mcp_server, "DeerFlowClient", _FailingLoginDF)
        monkeypatch.setattr(
            mcp_server,
            "get_settings",
            lambda: SimpleNamespace(
                deerflow_base_url="http://test",
                service_email="u@test.com",
                service_password="pw",
                database_url="sqlite+aiosqlite://",
            ),
        )
        monkeypatch.setattr(mcp_server, "_service", None)
        monkeypatch.setattr(mcp_server, "_async_service", None)
        monkeypatch.setattr(mcp_server, "_collab_service", None)

        # 降级启动：login 失败不得中断装配（异常不得向上冒泡）
        await mcp_server.init_service(
            db_url=f"sqlite+aiosqlite:///{tmp_path}/degrade.db"
        )

        assert mcp_server._service is not None
        assert mcp_server._async_service is not None
        assert mcp_server._collab_service is not None
