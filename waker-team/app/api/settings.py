"""User settings REST 路由."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.schemas import UserSettingsResponse, UserSettingsUpdate
from app.models.user_setting import UserSetting
from app.time_utils import to_iso_utc

router = APIRouter(prefix="/settings", tags=["settings"])

# 默认 user_id（单用户模式）
_DEFAULT_USER_ID = "default"


def _setting_to_response(setting: UserSetting) -> dict:
    """将 UserSetting ORM 对象转为响应 dict."""
    return {
        "user_id": setting.user_id,
        "default_model": setting.default_model,
        "density": setting.density,
        "notify_task": setting.notify_task,
        "notify_mention": setting.notify_mention,
        "updated_at": to_iso_utc(setting.updated_at),
    }


@router.get("", response_model=UserSettingsResponse)
async def get_settings(request: Request):
    """获取当前用户设置."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(UserSetting).where(UserSetting.user_id == _DEFAULT_USER_ID)
        )
        setting = result.scalars().first()
        if setting is None:
            # 返回默认设置
            return {
                "user_id": _DEFAULT_USER_ID,
                "default_model": None,
                "density": None,
                "notify_task": True,
                "notify_mention": True,
                "updated_at": None,
            }
        return _setting_to_response(setting)


@router.put("", response_model=UserSettingsResponse)
async def update_settings(data: UserSettingsUpdate, request: Request):
    """更新用户设置."""
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        result = await session.execute(
            select(UserSetting).where(UserSetting.user_id == _DEFAULT_USER_ID)
        )
        setting = result.scalars().first()
        if setting is None:
            # 创建新设置
            setting = UserSetting(
                user_id=_DEFAULT_USER_ID,
                default_model=data.default_model,
                density=data.density,
                notify_task=data.notify_task if data.notify_task is not None else True,
                notify_mention=data.notify_mention if data.notify_mention is not None else True,
                updated_at=datetime.now(UTC),
            )
            session.add(setting)
        else:
            if data.default_model is not None:
                setting.default_model = data.default_model
            if data.density is not None:
                setting.density = data.density
            if data.notify_task is not None:
                setting.notify_task = data.notify_task
            if data.notify_mention is not None:
                setting.notify_mention = data.notify_mention
            setting.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(setting)
        return _setting_to_response(setting)
