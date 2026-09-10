"""Cron 表达式工具函数."""

from datetime import UTC, datetime

from croniter import croniter


def validate_cron(expression: str) -> bool:
    """校验 cron 表达式是否合法."""
    try:
        croniter(expression)
        return True
    except (ValueError, KeyError):
        return False


def get_next_run(expression: str, base_time: datetime | None = None) -> datetime:
    """获取下次运行时间（UTC）."""
    base = base_time or datetime.now(UTC)
    cron = croniter(expression, base)
    return cron.get_next(datetime)


def should_run(expression: str, last_run_at: datetime, now: datetime | None = None) -> bool:
    """判断是否应该运行（当前时间 >= 上次运行后的下一次触发时间）."""
    next_time = get_next_run(expression, last_run_at)
    return (now or datetime.now(UTC)) >= next_time
