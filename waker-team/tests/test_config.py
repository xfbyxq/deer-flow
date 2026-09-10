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
