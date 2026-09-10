import pytest

from app.config import Settings


@pytest.fixture
def test_settings():
    return Settings(
        deerflow_base_url="http://test:2026",
        service_email="test@test.com",
        service_password="testpass",
        database_url="sqlite+aiosqlite:///./test_waker_team.db",
    )
