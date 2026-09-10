"""群内消息 MCP 工具业务逻辑：Leader 发布任务清单/进度播报.

Leader 在群会话 run 中通过 ``post_group_message`` 把「任务目标 + 任务清单 +
@成员分工」以自然语言实时发到群里（前端会话消息轮询即可见），不再把规划
藏在后台 run 里、也无需等服务结束才看到。
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.conversation import Conversation
from app.models.group import GroupMember
from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)


class CollabMCPService:
    """群内消息工具（post_group_message）业务逻辑，与 MCP server 解耦以便测试."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def post_group_message(
        self,
        caller: str,
        conversation_id: str,
        content: str,
        mentions: list[str] | None = None,
    ) -> dict:
        """在群会话中发布一条消息（role=waker, waker_id=caller）.

        校验：会话存在、是群会话、caller 属于该群。

        Returns:
            {"ok": True, "message_id": "..."} 或 {"error": "拒绝原因"}
        """
        text = (content or "").strip()
        if not text:
            return {"error": "content is required"}
        if not caller:
            return {"error": "Forbidden: missing caller identity"}

        async with self._session_factory() as db:
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.id == conversation_id)
                )
            ).scalars().first()
            if conv is None:
                return {"error": f"Conversation not found: {conversation_id}"}
            if not conv.group_id:
                return {"error": "Not a group conversation"}

            member = (
                await db.execute(
                    select(GroupMember).where(
                        GroupMember.group_id == conv.group_id,
                        GroupMember.waker_id == caller,
                    )
                )
            ).scalars().first()
            if member is None:
                return {"error": f"Forbidden: {caller} is not a member of this group"}

            service = ConversationService(db)
            msg = await service.create_message(
                conversation_id=conversation_id,
                role="waker",
                waker_id=caller,
                content_json={
                    "text": text,
                    "meta": {
                        "kind": "leader_post",
                        "mentions": mentions or [],
                        "partial": True,
                    },
                },
            )
            logger.info(
                "Group message posted: conversation=%s caller=%s", conversation_id, caller
            )
            return {"ok": True, "message_id": msg.id}
