"""Conversation REST 路由."""

from fastapi import APIRouter, HTTPException, Request

from app.api.schemas import (
    ConversationCreate,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ConversationResponse,
)
from app.services.conversation_service import ConversationService
from app.time_utils import to_iso_utc

router = APIRouter(tags=["conversations"])


def _get_service(request: Request) -> tuple:
    """从 request 构建 ConversationService 实例."""
    session_factory = request.app.state.db_session_factory
    return session_factory


def _conv_to_response(conv) -> dict:
    """将 Conversation ORM 对象转为响应 dict."""
    return {
        "id": conv.id,
        "scope": conv.scope,
        "waker_id": conv.waker_id,
        "group_id": conv.group_id,
        "title": conv.title,
        "status": conv.status,
        "thread_id": conv.thread_id,
        "created_by": conv.created_by,
        "created_at": to_iso_utc(conv.created_at),
        "updated_at": to_iso_utc(conv.updated_at),
    }


def _msg_to_response(msg) -> dict:
    """将 ConversationMessage ORM 对象转为响应 dict."""
    return {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "role": msg.role,
        "waker_id": msg.waker_id,
        "content_json": msg.content_json,
        "created_at": to_iso_utc(msg.created_at),
    }


# ------------------------------------------------------------------
# Waker conversations
# ------------------------------------------------------------------


@router.get("/wakers/{name}/conversations", response_model=list[ConversationResponse])
async def list_waker_conversations(name: str, request: Request):
    """列出某 waker 的所有会话."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        convs = await service.list_waker_conversations(name)
        return [_conv_to_response(c) for c in convs]


@router.post("/wakers/{name}/conversations", response_model=ConversationResponse, status_code=201)
async def create_waker_conversation(name: str, data: ConversationCreate, request: Request):
    """创建与某 waker 的直接会话."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        conv = await service.create_conversation(
            scope="direct",
            waker_id=name,
            title=data.title,
            thread_id=data.thread_id,
        )
        return _conv_to_response(conv)


# ------------------------------------------------------------------
# Group conversations
# ------------------------------------------------------------------


@router.get("/groups/{group_id}/conversations", response_model=list[ConversationResponse])
async def list_group_conversations(group_id: str, request: Request):
    """列出群组的所有会话."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        convs = await service.list_group_conversations(group_id)
        return [_conv_to_response(c) for c in convs]


@router.post("/groups/{group_id}/conversations", response_model=ConversationResponse, status_code=201)
async def create_group_conversation(group_id: str, data: ConversationCreate, request: Request):
    """创建群组会话."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        conv = await service.create_conversation(
            scope="group",
            group_id=group_id,
            waker_id=data.waker_id,
            title=data.title,
            thread_id=data.thread_id,
        )
        return _conv_to_response(conv)


# ------------------------------------------------------------------
# Messages
# ------------------------------------------------------------------


@router.get("/conversations/{conversation_id}/messages", response_model=list[ConversationMessageResponse])
async def list_messages(
    conversation_id: str,
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    """获取会话消息历史."""
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        # 检查会话是否存在
        conv = await service.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        msgs = await service.list_messages(conversation_id, limit=limit, offset=offset)
        return [_msg_to_response(m) for m in msgs]


@router.post("/conversations/{conversation_id}/messages", response_model=ConversationMessageResponse, status_code=201)
async def create_message(
    conversation_id: str,
    data: ConversationMessageCreate,
    request: Request,
):
    """发送消息.

    用户消息（role=user）入库后异步触发对话回复：
    直聊会话由 waker 回复，群组会话由 Leader 回复（无 Leader 则不回复）。
    ``defer_reply=true`` 时仅入库不触发（多澄清问题聚合回答：最后一个回答才触发处理）。
    """
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        # 检查会话是否存在
        conv = await service.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        msg = await service.create_message(
            conversation_id=conversation_id,
            role=data.role,
            waker_id=data.waker_id,
            content_json=data.content_json,
        )
        # 非阻塞触发回复（不阻塞消息发送响应）；defer_reply 时仅入库
        if data.role == "user" and not data.defer_reply:
            chat_reply = getattr(request.app.state, "chat_reply", None)
            if chat_reply is not None:
                chat_reply.schedule_reply(conversation_id)
        return _msg_to_response(msg)


@router.get("/conversations/{conversation_id}/progress")
async def get_conversation_progress(conversation_id: str, request: Request):
    """获取会话进行中回复的进度快照（思考/工具步骤），供前端实时展示.

    无进行中回复时返回 {"active": false}。
    仅内存态：进程重启后降级为无进度（前端回退到默认"正在思考"指示）。
    """
    chat_reply = getattr(request.app.state, "chat_reply", None)
    if chat_reply is None:
        return {"active": False}
    return await chat_reply.get_progress(conversation_id)


@router.post("/conversations/{conversation_id}/stop")
async def stop_conversation_reply(conversation_id: str, request: Request):
    """停止会话进行中的回复（取消 DeerFlow run）.

    幂等：无进行中回复时返回 {"stopped": false}。
    停止成功后写入系统提示「⏹ 已停止本次回复。」，前端据此恢复发送态。
    """
    session_factory = _get_service(request)
    async with session_factory() as session:
        service = ConversationService(session)
        conv = await service.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
    chat_reply = getattr(request.app.state, "chat_reply", None)
    if chat_reply is None:
        return {"stopped": False}
    return await chat_reply.stop_reply(conversation_id)