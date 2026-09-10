from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deerflow_base_url: str = "http://127.0.0.1:2026"
    service_email: str = ""
    service_password: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    database_url: str = "sqlite+aiosqlite:///./waker_team.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
