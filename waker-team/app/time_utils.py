"""统一的时间序列化工具.

SQLite 存储的 datetime 为 naive（值为 UTC），直接 ``isoformat()`` 会丢失时区信息，
前端（浏览器）会按本地时区解析，导致东八区用户看到的时间偏早 8 小时。
本模块统一在序列化时补上 UTC 时区后缀（``+00:00``），保证前后端时间语义一致。
"""

from datetime import UTC, datetime


def to_iso_utc(value: datetime | None) -> str | None:
    """将 datetime 序列化为带 UTC 时区的 ISO 8601 字符串.

    - None 原样返回 None
    - naive datetime 视为 UTC，补上 tzinfo
    - aware datetime 统一转换为 UTC
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()
