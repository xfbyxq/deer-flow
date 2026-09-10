"""Waker 管理业务逻辑：CRUD + SOUL 模板注入."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import WakerCreateRequest, WakerUpdateRequest
from app.deerflow.client import DeerFlowClient
from app.deerflow.errors import AgentConflictError, AgentNotFoundError, AuthenticationError, DeerFlowError, DeerFlowUnavailableError
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group, GroupMember
from app.models.task import Task
from app.models.waker import Waker
from app.services.audit_service import log_audit
from app.time_utils import to_iso_utc

logger = logging.getLogger(__name__)

# DeerFlow 不可用时应捕获并降级的异常类型（不含业务逻辑错误如 409/404）
_DF_UNAVAILABLE_ERRORS = (DeerFlowUnavailableError, AuthenticationError)

# SOUL 协作规则模板路径
_SOUL_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "soul_collaboration.md"

# 员工模板定义路径（静态预定义）
_TEMPLATES_PATH = Path(__file__).parent.parent / "templates" / "waker_templates.json"


def _read_soul_template() -> str:
    """读取 SOUL 协作规则模板."""
    return _SOUL_TEMPLATE_PATH.read_text(encoding="utf-8")


def _load_waker_templates() -> list[dict]:
    """读取员工模板定义（静态 JSON）."""
    import json as _json

    return _json.loads(_TEMPLATES_PATH.read_text(encoding="utf-8"))


def _build_soul(name: str, description: str, custom_soul: str) -> str:
    """拼接最终 SOUL 内容：模板 + 用户自定义."""
    template = _read_soul_template()
    rendered = template.format(name=name, description=description)
    if custom_soul.strip():
        return rendered.rstrip() + "\n\n" + custom_soul.strip()
    return rendered.rstrip()


def _merge_agent_with_waker(agent: dict, waker: Waker | None) -> dict:
    """合并 DeerFlow agent 数据与本地 Waker 台账."""
    import json as _json

    result = dict(agent)
    if waker is not None:
        result["enabled"] = waker.enabled
        result["deer_user"] = waker.deer_user
        result["home_thread_id"] = waker.home_thread_id
        result["presence"] = waker.presence or "offline"
        result["role"] = waker.role
        result["max_concurrent_tasks"] = waker.max_concurrent_tasks or 3
        # mcp_connectors 从 JSON 字符串解析为 dict
        if waker.mcp_connectors:
            try:
                result["mcp_connectors"] = _json.loads(waker.mcp_connectors)
            except (ValueError, TypeError):
                result["mcp_connectors"] = None
        else:
            result["mcp_connectors"] = None
        result["created_at"] = to_iso_utc(waker.created_at)
        result["updated_at"] = to_iso_utc(waker.updated_at)
    else:
        result.setdefault("enabled", True)
        result.setdefault("deer_user", "")
        result.setdefault("home_thread_id", None)
        result.setdefault("presence", "offline")
        result.setdefault("role", None)
        result.setdefault("max_concurrent_tasks", 3)
        result.setdefault("mcp_connectors", None)
        result.setdefault("created_at", None)
        result.setdefault("updated_at", None)
    return result


def _local_waker_to_dict(waker: Waker) -> dict:
    """将本地 Waker 台账转换为与合并格式兼容的 dict（无 DeerFlow 数据）."""
    import json as _json

    return {
        "name": waker.name,
        "description": waker.description or "",
        "enabled": waker.enabled,
        "deer_user": waker.deer_user,
        "home_thread_id": waker.home_thread_id,
        "presence": waker.presence or "offline",
        "online": False,
        "role": waker.role,
        "max_concurrent_tasks": waker.max_concurrent_tasks or 3,
        "mcp_connectors": _json.loads(waker.mcp_connectors) if waker.mcp_connectors else None,
        "model": None,
        "soul": "",
        "tool_groups": None,
        "skills": None,
        "allowed_subagents": None,
        "model_settings": None,
        "thinking_enabled": None,
        "reasoning_effort": None,
        "created_at": to_iso_utc(waker.created_at),
        "updated_at": to_iso_utc(waker.updated_at),
    }


class WakerService:
    """Waker（数字员工）管理服务."""

    def __init__(self, db_session: AsyncSession, deerflow_client: DeerFlowClient) -> None:
        self.db = db_session
        self.df = deerflow_client

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    async def list_wakers(self) -> list[dict]:
        """列表：合并 DeerFlow agents_api 数据 + 本地 WAKER 台账.

        当 DeerFlow 不可用时，仅返回本地台账数据（online=False）。
        """
        # 批量查本地台账
        result = await self.db.execute(select(Waker))
        wakers_by_name: dict[str, Waker] = {w.name: w for w in result.scalars().all()}

        try:
            agents = await self.df.list_agents()
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning(
                "DeerFlow unavailable, returning local wakers only (%d records)",
                len(wakers_by_name),
                exc_info=True,
            )
            return [_local_waker_to_dict(w) for w in wakers_by_name.values()]

        merged = []
        for agent in agents:
            name = agent.get("name", "")
            waker = wakers_by_name.get(name)
            merged.append(_merge_agent_with_waker(agent, waker))
        return merged

    # ------------------------------------------------------------------
    # Get
    # ------------------------------------------------------------------

    async def get_waker(self, name: str) -> dict:
        """详情：DeerFlow agent 数据 + 本地台账合并.

        当 DeerFlow 不可用时，仅返回本地台账数据。
        若本地台账也不存在，则抛出 DeerFlowUnavailableError（502）而非 AgentNotFoundError（404），
        因为此时无法判断 waker 是否真的不存在（DeerFlow 可能是权威数据源）。
        """
        waker = await self._get_local_waker(name)
        try:
            agent = await self.df.get_agent(name)
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning("DeerFlow unavailable, returning local data for waker '%s'", name, exc_info=True)
            if waker is not None:
                return _local_waker_to_dict(waker)
            # DeerFlow 不可用且本地无台账 → 无法判断是否存在，返回 502
            raise DeerFlowUnavailableError(
                f"Cannot verify waker '{name}': DeerFlow is unavailable and no local record exists"
            )
        return _merge_agent_with_waker(agent, waker)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_waker(self, data: WakerCreateRequest, actor: str = "system") -> dict:
        """创建 Waker:
        1. 读取 SOUL 协作规则模板并拼接
        2. 调 DeerFlow create_agent
        3. 本地 WAKER 表建台账
        4. 写审计日志
        """
        final_soul = _build_soul(data.name, data.description, data.soul)

        # 构造 DeerFlow agent 创建请求体
        agent_data: dict[str, Any] = {
            "name": data.name,
            "description": data.description,
            "soul": final_soul,
        }
        if data.model is not None:
            agent_data["model"] = data.model
        if data.tool_groups is not None:
            agent_data["tool_groups"] = data.tool_groups
        if data.skills is not None:
            agent_data["skills"] = data.skills
        if data.allowed_subagents is not None:
            agent_data["allowed_subagents"] = data.allowed_subagents
        if data.model_settings is not None:
            agent_data["model_settings"] = data.model_settings
        if data.thinking_enabled is not None:
            agent_data["thinking_enabled"] = data.thinking_enabled
        if data.reasoning_effort is not None:
            agent_data["reasoning_effort"] = data.reasoning_effort

        try:
            agent_resp = await self.df.create_agent(agent_data)
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning(
                "DeerFlow unavailable, saving waker '%s' locally only (DeerFlow sync skipped)",
                data.name,
                exc_info=True,
            )
            agent_resp = None
        except AgentConflictError:
            # DeerFlow 侧 agent 已存在。
            # - 若本地台账也存在：真正的重复创建 → 保持 409 语义
            # - 若本地台账缺失（如上次删除半途失败的不一致状态）：补写台账自愈
            if await self._get_local_waker(data.name) is not None:
                raise
            logger.warning(
                "Agent '%s' already exists in DeerFlow but local ledger is missing; "
                "restoring local ledger",
                data.name,
            )
            agent_resp = None

        # 本地台账
        import json as _json

        soul_summary = final_soul.split("\n")[0][:200]
        existing = await self._get_local_waker(data.name)
        if existing is not None:
            # 台账已存在（agent 被重建等自愈场景）：更新描述与摘要后直接返回
            existing.description = data.description
            existing.soul_summary = soul_summary
            existing.updated_at = datetime.now(UTC)
            await self.db.commit()
            if agent_resp is not None:
                return _merge_agent_with_waker(agent_resp, existing)
            return _local_waker_to_dict(existing)
        waker = Waker(
            name=data.name,
            deer_user="",
            description=data.description,
            soul_summary=soul_summary,
            enabled=True,
            presence="offline",
            role=data.role,
            max_concurrent_tasks=data.max_concurrent_tasks or 3,
            mcp_connectors=_json.dumps(data.mcp_connectors) if data.mcp_connectors else None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.db.add(waker)
        await self.db.commit()

        # 审计日志
        await log_audit(
            self.db,
            action="waker.create",
            actor=actor,
            target=data.name,
            detail={"description": data.description, "model": data.model},
        )

        if agent_resp is not None:
            return _merge_agent_with_waker(agent_resp, waker)
        return _local_waker_to_dict(waker)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_waker(self, name: str, data: WakerUpdateRequest, actor: str = "system") -> dict:
        """编辑 Waker:
        1. 调 DeerFlow update_agent
        2. 更新本地台账
        3. 写审计日志
        """
        update_data: dict[str, Any] = {}
        if data.description is not None:
            update_data["description"] = data.description
        if data.soul is not None:
            # 重新拼接 SOUL：先获取当前 agent 拿 name/description
            desc = data.description or ""
            if data.description is None:
                try:
                    agent = await self.df.get_agent(name)
                    desc = agent.get("description", "")
                except _DF_UNAVAILABLE_ERRORS:
                    # DeerFlow 不可用时回退到本地台账描述
                    local = await self._get_local_waker(name)
                    desc = local.description if local else ""
            update_data["soul"] = _build_soul(name, desc, data.soul)
        if data.model is not None:
            update_data["model"] = data.model
        if data.tool_groups is not None:
            update_data["tool_groups"] = data.tool_groups
        if data.skills is not None:
            update_data["skills"] = data.skills
        if data.allowed_subagents is not None:
            update_data["allowed_subagents"] = data.allowed_subagents
        if data.model_settings is not None:
            update_data["model_settings"] = data.model_settings
        if data.thinking_enabled is not None:
            update_data["thinking_enabled"] = data.thinking_enabled
        if data.reasoning_effort is not None:
            update_data["reasoning_effort"] = data.reasoning_effort

        try:
            agent_resp = await self.df.update_agent(name, update_data)
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning(
                "DeerFlow unavailable, saved waker '%s' locally only (DeerFlow sync skipped)",
                name,
                exc_info=True,
            )
            agent_resp = None

        # 更新本地台账
        import json as _json

        waker = await self._get_local_waker(name)
        if waker is None:
            # 台账不存在则创建
            waker = Waker(
                name=name,
                deer_user="",
                description=data.description or "",
                soul_summary="",
                enabled=True,
                presence=data.presence or "offline",
                role=data.role,
                max_concurrent_tasks=data.max_concurrent_tasks or 3,
                mcp_connectors=_json.dumps(data.mcp_connectors) if data.mcp_connectors else None,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.db.add(waker)
        else:
            if data.description is not None:
                waker.description = data.description
            if data.soul is not None:
                waker.soul_summary = update_data["soul"].split("\n")[0][:200]
            if data.presence is not None:
                waker.presence = data.presence
            if data.role is not None:
                waker.role = data.role
            if data.max_concurrent_tasks is not None:
                waker.max_concurrent_tasks = data.max_concurrent_tasks
            if data.mcp_connectors is not None:
                waker.mcp_connectors = _json.dumps(data.mcp_connectors)
            waker.updated_at = datetime.now(UTC)
        await self.db.commit()

        # 审计日志
        await log_audit(
            self.db,
            action="waker.update",
            actor=actor,
            target=name,
            detail={k: v for k, v in update_data.items() if k != "soul"},
        )

        if agent_resp is not None:
            return _merge_agent_with_waker(agent_resp, waker)
        return _local_waker_to_dict(waker)

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    async def toggle_waker(self, name: str, enabled: bool, actor: str = "system") -> dict:
        """启停：仅本地 enabled 字段切换."""
        waker = await self._get_local_waker(name)
        try:
            agent = await self.df.get_agent(name)
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning("DeerFlow unavailable, toggling waker '%s' locally only", name, exc_info=True)
            agent = None
        if waker is None:
            desc = agent.get("description", "") if agent else ""
            waker = Waker(
                name=name,
                deer_user="",
                description=desc,
                soul_summary="",
                enabled=enabled,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.db.add(waker)
        else:
            waker.enabled = enabled
            waker.updated_at = datetime.now(UTC)
        await self.db.commit()

        await log_audit(
            self.db,
            action="waker.toggle",
            actor=actor,
            target=name,
            detail={"enabled": enabled},
        )

        if agent is not None:
            return _merge_agent_with_waker(agent, waker)
        return _local_waker_to_dict(waker)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_waker(self, name: str, actor: str = "system") -> None:
        """删除 Waker:
        1. 检查 TASK 表有无该 executor 的 running 任务 → 有则 409
        2. 调 DeerFlow delete_agent
        3. 删本地 WAKER 台账
        4. 写审计日志
        """
        # 检查是否有 running 任务
        running_check = await self.db.execute(
            select(Task).where(Task.executor == name, Task.status == "running")
        )
        running_tasks = running_check.scalars().first()
        if running_tasks is not None:
            from app.deerflow.errors import TaskConflictError

            raise TaskConflictError(
                f"Cannot delete waker '{name}': has running task '{running_tasks.id}'"
            )

        # 调 DeerFlow 删除（不可用时仅删本地）
        try:
            await self.df.delete_agent(name)
        except _DF_UNAVAILABLE_ERRORS:
            logger.warning(
                "DeerFlow unavailable, deleting waker '%s' locally only (DeerFlow sync skipped)",
                name,
                exc_info=True,
            )

        # 删本地台账（先清理外键关联：群成员关系 / 直聊会话与消息）
        members = (
            (await self.db.execute(select(GroupMember).where(GroupMember.waker_id == name)))
            .scalars()
            .all()
        )
        for member in members:
            await self.db.delete(member)
        convs = (
            (await self.db.execute(select(Conversation).where(Conversation.waker_id == name)))
            .scalars()
            .all()
        )
        for conv in convs:
            msgs = (
                (
                    await self.db.execute(
                        select(ConversationMessage).where(
                            ConversationMessage.conversation_id == conv.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for msg in msgs:
                await self.db.delete(msg)
            await self.db.delete(conv)
        # 其余引用 wakers.name 的外键：群聊中该 waker 发出的消息、群主引用
        msgs_by_waker = (
            (
                await self.db.execute(
                    select(ConversationMessage).where(ConversationMessage.waker_id == name)
                )
            )
            .scalars()
            .all()
        )
        for msg in msgs_by_waker:
            await self.db.delete(msg)
        await self.db.execute(
            update(Group).where(Group.leader_waker_id == name).values(leader_waker_id=None)
        )
        waker = await self._get_local_waker(name)
        if waker is not None:
            await self.db.delete(waker)
            await self.db.commit()

        # 审计日志
        await log_audit(
            self.db,
            action="waker.delete",
            actor=actor,
            target=name,
            detail=None,
        )

    # ------------------------------------------------------------------
    # Enum data
    # ------------------------------------------------------------------

    async def get_enum_data(self) -> dict:
        """枚举数据源：models / skills / tool_groups."""
        try:
            models = await self.df.list_models()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to fetch models from DeerFlow", exc_info=True)
            models = []

        try:
            skills = await self.df.list_skills()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to fetch skills from DeerFlow", exc_info=True)
            skills = []

        tool_groups = ["web", "file:read", "file:write", "browser", "knowledge", "sandbox"]

        return {
            "models": models,
            "skills": skills,
            "tool_groups": tool_groups,
        }

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    async def get_waker_templates(self) -> list[dict]:
        """员工模板列表（静态预定义，新建员工时选择后预填表单）."""
        return _load_waker_templates()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_local_waker(self, name: str) -> Waker | None:
        """查询本地 Waker 台账."""
        result = await self.db.execute(select(Waker).where(Waker.name == name))
        return result.scalars().first()
