import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings


def test_settings_defaults():
    settings = Settings()
    assert settings.deerflow_base_url == "http://127.0.0.1:2026"
    assert settings.mcp_host == "0.0.0.0"
    assert settings.mcp_port == 8765
    assert settings.database_url == "sqlite+aiosqlite:///./waker_team.db"


def test_settings_custom_values():
    settings = Settings(
        deerflow_base_url="http://custom:9999",
        service_email="custom@example.com",
        service_password="secret",
    )
    assert settings.deerflow_base_url == "http://custom:9999"
    assert settings.service_email == "custom@example.com"
    assert settings.service_password == "secret"


def test_database_url_format():
    settings = Settings(database_url="sqlite+aiosqlite:///./custom.db")
    assert settings.database_url.startswith("sqlite+aiosqlite://")
    assert "custom.db" in settings.database_url


# ------------------------------------------------------------------
# C8: CORS 白名单（禁止通配 + 凭据组合）
# ------------------------------------------------------------------


def test_cors_origins_default_excludes_wildcard():
    """默认白名单不含 "*"——allow_origins=["*"] + allow_credentials=True
    等效对任意来源开放带凭据跨域。"""
    settings = Settings(_env_file=None)
    assert "*" not in settings.cors_origins
    assert settings.cors_origins  # 非空，否则前端无法跨域访问
    # 默认值覆盖实际前端来源：Vite dev(5173) + nginx 统一入口(2026)
    assert "http://localhost:5173" in settings.cors_origins
    assert "http://localhost:2026" in settings.cors_origins


def test_cors_origins_override():
    settings = Settings(_env_file=None, cors_origins=["https://team.example.com"])
    assert settings.cors_origins == ["https://team.example.com"]


def test_cors_origins_default_is_not_shared_between_instances():
    """默认值为可变列表：两个实例不得共享同一对象（避免运行期串改）."""
    a = Settings(_env_file=None)
    b = Settings(_env_file=None)
    a.cors_origins.append("http://mutated.example")
    assert "http://mutated.example" not in b.cors_origins


@pytest.mark.asyncio
async def test_create_app_cors_uses_configured_origins(monkeypatch):
    """main.create_app 的 CORSMiddleware 只放行配置来源，且保留 allow_credentials."""
    import app.config as config_module
    from app.main import create_app

    monkeypatch.setattr(
        config_module,
        "_settings",
        Settings(_env_file=None, cors_origins=["http://localhost:5173"]),
    )
    application = create_app()

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        allowed = await ac.get("/api/health", headers={"Origin": "http://localhost:5173"})
        assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert allowed.headers.get("access-control-allow-credentials") == "true"

        denied = await ac.get("/api/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in denied.headers


# ------------------------------------------------------------------
# W3: recursion_limit 可配置（不再硬编码注入所有 run）
# ------------------------------------------------------------------


def test_recursion_limit_default():
    """默认 1000——与 DeerFlow Web UI 一致，保持既有部署行为不变."""
    settings = Settings(_env_file=None)
    assert settings.recursion_limit == 1000


def test_recursion_limit_override():
    settings = Settings(_env_file=None, recursion_limit=200)
    assert settings.recursion_limit == 200


# ------------------------------------------------------------------
# S18: 自动 wake run 护栏（并发上限 + 速率上限 + 有界等待）
# ------------------------------------------------------------------


def test_wake_guardrail_defaults():
    settings = Settings(_env_file=None)
    assert settings.wake_max_concurrency > 0
    assert settings.wake_max_per_minute > 0
    assert settings.wake_throttle_wait_timeout_seconds > 0


def test_wake_guardrail_override():
    settings = Settings(
        _env_file=None,
        wake_max_concurrency=2,
        wake_max_per_minute=5,
        wake_throttle_wait_timeout_seconds=1.5,
    )
    assert settings.wake_max_concurrency == 2
    assert settings.wake_max_per_minute == 5
    assert settings.wake_throttle_wait_timeout_seconds == 1.5


# ------------------------------------------------------------------
# CF10：CORS 默认白名单扩展 + 容错解析（避免 import 期崩溃）
# ------------------------------------------------------------------


def test_cors_origins_default_covers_all_dev_ports():
    """CF10：默认白名单覆盖 5173/4173/3000/2026，各含 localhost 与 127.0.0.1."""
    settings = Settings(_env_file=None)
    for port in (5173, 4173, 3000, 2026):
        assert f"http://localhost:{port}" in settings.cors_origins
        assert f"http://127.0.0.1:{port}" in settings.cors_origins
    # 仍然禁止通配
    assert "*" not in settings.cors_origins


def test_cors_origins_env_json_array_override():
    """CF10：环境变量以 JSON 数组字符串覆盖（pydantic-settings 对 list[str] 的要求）."""
    settings = Settings(
        _env_file=None, cors_origins='["https://team.example.com", "https://a.example"]'
    )
    assert settings.cors_origins == ["https://team.example.com", "https://a.example"]


def test_cors_origins_bare_url_is_tolerated():
    """CF10：运维误写裸 URL（非 JSON 数组）被容错包装为单元素列表，而非崩溃."""
    settings = Settings(_env_file=None, cors_origins="https://only.example.com")
    assert settings.cors_origins == ["https://only.example.com"]


def test_cors_origins_empty_string_falls_back_to_default():
    """CF10：.env 中 CORS_ORIGINS= 留空 → 回退默认白名单（不崩溃、不置空）."""
    settings = Settings(_env_file=None, cors_origins="")
    assert settings.cors_origins  # 非空
    assert "http://localhost:5173" in settings.cors_origins


def test_cors_origins_invalid_json_raises_friendly_error():
    """CF10：非法 JSON 数组 → 带修复指引的清晰错误（而非晦涩堆栈）."""
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None, cors_origins='["https://x",]')  # 尾逗号非法 JSON
    assert "CORS_ORIGINS" in str(exc_info.value)


def test_cors_origins_non_origin_string_raises_friendly_error():
    """CF10：既非 JSON 数组也非 http(s) origin → 清晰报错."""
    with pytest.raises(Exception) as exc_info:
        Settings(_env_file=None, cors_origins="not-an-origin")
    assert "CORS_ORIGINS" in str(exc_info.value)


# ------------------------------------------------------------------
# CF2：SyncEngine 瞬时错误重试预算可配
# ------------------------------------------------------------------


def test_sync_max_transient_failures_default():
    """CF2：默认约 30 轮（≈ 5min @10s interval）."""
    settings = Settings(_env_file=None)
    assert settings.sync_max_transient_failures == 30


def test_sync_max_transient_failures_override():
    settings = Settings(_env_file=None, sync_max_transient_failures=5)
    assert settings.sync_max_transient_failures == 5
