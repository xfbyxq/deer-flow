"""MCP 工具测试 — MCPService 业务逻辑 + register_mcp 脚本 + ASGI lifespan."""

import asyncio
import json
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

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
def mock_df():
    """Mock DeerFlowClient."""
    df = MagicMock()
    df.create_thread = AsyncMock(return_value={"thread_id": "test-thread"})
    df.create_run = AsyncMock(return_value={"run_id": "test-run-id"})
    df.get_run = AsyncMock(return_value={"status": "success", "output": "done"})
    df.close = AsyncMock()
    return df


@pytest.fixture
def service(mock_db, mock_df):
    """MCPService with mock dependencies."""
    return MCPService(mock_db, mock_df)


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
# register_mcp.py
# ---------------------------------------------------------------------------


class TestRegisterMCP:
    def test_register(self, tmp_path):
        """注册: 写入 extensions_config.json."""
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        # 导入并测试 register 函数
        scripts_dir = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from register_mcp import register

            with patch("register_mcp.EXTENSIONS_CONFIG", config_file):
                register(port=9999)

            config = json.loads(config_file.read_text(encoding="utf-8"))
            assert "waker-team" in config["mcpServers"]
            server = config["mcpServers"]["waker-team"]
            assert server["enabled"] is True
            assert server["type"] == "http"
            assert server["url"] == "http://host.docker.internal:9999/mcp"
            assert server["headers_from_context"]["on_missing"] == "deny"
            assert "X-Waker-Caller" in server["headers_from_context"]["headers"]
        finally:
            sys.path.pop(0)

    def test_unregister(self, tmp_path):
        """注销: 从 extensions_config.json 移除."""
        config_file = tmp_path / "extensions_config.json"
        initial = {
            "mcpServers": {
                "waker-team": {"url": "http://localhost:8765/mcp"},
                "other": {"url": "http://other"},
            }
        }
        config_file.write_text(json.dumps(initial), encoding="utf-8")

        scripts_dir = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from register_mcp import unregister

            with patch("register_mcp.EXTENSIONS_CONFIG", config_file):
                unregister()

            config = json.loads(config_file.read_text(encoding="utf-8"))
            assert "waker-team" not in config["mcpServers"]
            assert "other" in config["mcpServers"]  # 其他 server 不受影响
        finally:
            sys.path.pop(0)

    def test_unregister_not_registered(self, tmp_path):
        """注销未注册的 server: 不报错."""
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        scripts_dir = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from register_mcp import unregister

            with patch("register_mcp.EXTENSIONS_CONFIG", config_file):
                unregister()  # 不应抛异常

            config = json.loads(config_file.read_text(encoding="utf-8"))
            assert config["mcpServers"] == {}
        finally:
            sys.path.pop(0)

    def test_register_creates_mcpServers_key(self, tmp_path):
        """注册时若 mcpServers 键不存在则自动创建."""
        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(json.dumps({}), encoding="utf-8")

        scripts_dir = Path(__file__).parent.parent / "scripts"
        sys.path.insert(0, str(scripts_dir))
        try:
            from register_mcp import register

            with patch("register_mcp.EXTENSIONS_CONFIG", config_file):
                register(port=8765)

            config = json.loads(config_file.read_text(encoding="utf-8"))
            assert "mcpServers" in config
            assert "waker-team" in config["mcpServers"]
        finally:
            sys.path.pop(0)


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
    async def test_cross_group_delegate_blocked(self, mock_db, mock_df):
        """两个 waker 在不同组 → delegate 被拒."""
        svc = MCPService(mock_db, mock_df)

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
