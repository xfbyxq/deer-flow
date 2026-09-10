import os
import sqlite3
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _set_sqlite_pragma(dbapi_conn: sqlite3.Connection, connection_record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def create_engine(database_url: str | None = None):
    """创建异步引擎.

    Parameters
    ----------
    database_url:
        显式指定数据库 URL；为 None 时使用全局配置（settings.database_url）。

    Raises
    ------
    RuntimeError:
        在 pytest 环境中禁止使用默认 URL。默认 URL 指向真实业务库
        waker_team.db，测试直接操作（尤其 drop_all）会清空生产数据。
    """
    if database_url is None and os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            "Refusing to open the default database during pytest: the default "
            "database_url points at the real waker_team.db. Pass an explicit "
            "database_url (e.g. a tmp_path file) instead."
        )
    settings = get_settings()
    engine = create_async_engine(database_url or settings.database_url, echo=False)
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)
    return engine


def create_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
