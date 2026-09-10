"""对话回复引擎：把会话中的用户消息接入 DeerFlow agent run，并把回复写回会话.

设计要点：
- 直聊会话：回复者 = 会话的 ``waker_id``；
- 群组会话：回复者 = 群组 Leader（未指定 Leader 的群消息不触发回复）；
- 同一会话的消息串行处理（DeerFlow 同一 thread 不允许并发 run），
  通过 per-conversation asyncio.Lock 保证顺序；
- 会话 thread 持久化到 ``conversations.thread_id``，多轮对话共享同一
  DeerFlow thread（agent 能看到完整历史），符合"对话在 DeerFlow，伴生
  服务只存引用"的架构约定；
- run 到达终态后从 thread state 中提取本次 run 的 AI 回复，
  以 ``conversation_messages(role="waker", waker_id=回复者)`` 写回。
"""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group

logger = logging.getLogger(__name__)

_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


class ChatReplyService:
    """将会话消息接入 DeerFlow 并回写回复."""

    def __init__(
        self,
        db_session_factory,
        deerflow_client: DeerFlowClient,
        poll_interval: float = 3.0,
        reply_timeout: float = 600.0,
    ) -> None:
        self._db_factory = db_session_factory
        self._df = deerflow_client
        self._poll_interval = poll_interval
        self._reply_timeout = reply_timeout
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 触发入口
    # ------------------------------------------------------------------

    def schedule_reply(self, conversation_id: str) -> None:
        """非阻塞触发一次"用户消息→回复"（API 层调用）."""
        asyncio.create_task(self.reply_once(conversation_id))

    async def reply_once(self, conversation_id: str) -> None:
        """处理会话最新一条用户消息的回复；同会话内串行执行."""
        try:
            async with self._lock_for(conversation_id):
                await self._reply_once_locked(conversation_id)
        except Exception:
            logger.exception("Chat reply failed for conversation %s", conversation_id)

    def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    async def _reply_once_locked(self, conversation_id: str) -> None:
        # 1. 解析会话、回复者与待回复消息
        async with self._db_factory() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalars().first()
            if conv is None:
                return

            target = conv.waker_id
            if not target and conv.group_id:
                group = (
                    await db.execute(select(Group).where(Group.id == conv.group_id))
                ).scalars().first()
                target = group.leader_waker_id if group else None
            if not target:
                logger.info(
                    "Conversation %s has no reply target (waker/leader missing); skip",
                    conversation_id,
                )
                return

            last_user_msg = (
                await db.execute(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.conversation_id == conversation_id,
                        ConversationMessage.role == "user",
                    )
                    .order_by(ConversationMessage.created_at.desc())
                )
            ).scalars().first()
            if last_user_msg is None:
                return
            text = _extract_text(last_user_msg.content_json)
            if not text:
                return
            user_msg_id = last_user_msg.id

            # 2. 确保会话绑定 DeerFlow thread
            thread_id = conv.thread_id
            if not thread_id:
                thread_id = str(
                    uuid.uuid5(uuid.NAMESPACE_DNS, f"conversation-{conversation_id}")
                )
                try:
                    await self._df.create_thread(thread_id=thread_id)
                except Exception:
                    # 线程可能已存在（创建请求幂等失败可忽略），继续尝试发起 run
                    logger.warning(
                        "create_thread failed for %s (maybe exists), continue",
                        thread_id,
                    )
                conv.thread_id = thread_id
                conv.updated_at = datetime.now(UTC)
                await db.commit()

        # 3. 向 DeerFlow 发起对话式 run（agent_name=回复者，携带 waker_identity 凭据）
        run_body = {
            "input": {"messages": [{"role": "user", "content": text}]},
            "config": build_run_configuration(target),
        }
        try:
            run_resp = await self._df.create_run(
                thread_id,
                body=run_body,
                idempotency_key=f"reply-{user_msg_id}",
            )
            run_id = run_resp.get("run_id")
        except Exception:
            logger.exception(
                "Failed to start reply run (conversation=%s, waker=%s)",
                conversation_id,
                target,
            )
            return
        if not run_id:
            logger.error("Reply run returned no run_id (conversation=%s)", conversation_id)
            return

        # 4. 等待 run 终态并提取回复
        reply_text = await self._wait_for_reply(thread_id, run_id)
        if reply_text:
            # 5. 写回会话（role=waker）
            await self._write_message(conversation_id, "waker", target, reply_text)
            logger.info(
                "Reply written: conversation=%s waker=%s run=%s",
                conversation_id,
                target,
                run_id,
            )
        else:
            # 失败/超时兜底：写入系统提示，避免用户静默等待
            await self._write_message(
                conversation_id,
                "system",
                None,
                "⚠️ 回复生成失败（模型服务暂不可用或超时），请稍后重试。",
            )
            logger.warning(
                "Reply failure notice written: conversation=%s run=%s", conversation_id, run_id
            )

    async def _write_message(
        self, conversation_id: str, role: str, waker_id: str | None, text: str
    ) -> None:
        """写入一条会话消息并刷新会话 updated_at."""
        async with self._db_factory() as db:
            db.add(
                ConversationMessage(
                    conversation_id=conversation_id,
                    role=role,
                    waker_id=waker_id,
                    content_json=json.dumps({"text": text}, ensure_ascii=False),
                    created_at=datetime.now(UTC),
                )
            )
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalars().first()
            if conv is not None:
                conv.updated_at = datetime.now(UTC)
            await db.commit()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _wait_for_reply(self, thread_id: str, run_id: str) -> str | None:
        """轮询 run 直至终态，成功后从 thread state 提取本次回复文本."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._reply_timeout
        while loop.time() < deadline:
            await asyncio.sleep(self._poll_interval)
            try:
                run = await self._df.get_run(thread_id, run_id)
            except Exception:
                logger.warning("get_run failed (thread=%s run=%s)", thread_id, run_id, exc_info=True)
                continue
            status = run.get("status")
            if status not in _TERMINAL_RUN_STATUSES:
                continue
            if status != "success":
                logger.warning("Reply run terminal status=%s (run=%s)", status, run_id)
                return None
            try:
                return await self._extract_reply(thread_id, run_id)
            except Exception:
                logger.exception("Failed to extract reply (thread=%s run=%s)", thread_id, run_id)
                return None
        logger.warning("Reply run timed out waiting (thread=%s run=%s)", thread_id, run_id)
        return None

    async def _extract_reply(self, thread_id: str, run_id: str) -> str | None:
        """从 thread state 中提取本次 run 的 AI 回复。

        优先匹配 ``additional_kwargs.run_id == run_id`` 的 AI 消息；
        回退取最后一条内容非空的 AI 消息（跳过 hide_from_ui 的中间注入）。
        """
        state = await self._df.get_thread_state(thread_id)
        messages = ((state.get("values") or {}).get("messages")) or []

        candidates: list[tuple[bool, str]] = []  # (run 匹配, 文本)
        for m in messages:
            if m.get("type") != "ai":
                continue
            extra = m.get("additional_kwargs") or {}
            if extra.get("hide_from_ui"):
                continue
            text = _strip_think(_coerce_content_text(m.get("content")))
            if not text:
                continue
            candidates.append((extra.get("run_id") == run_id, text))

        for matched, text in reversed(candidates):
            if matched:
                return text
        if candidates:
            return candidates[-1][1]
        return None


def _strip_think(text: str) -> str | None:
    """剔除模型思考段（<think>…</think>），只保留正式回复。

    - 含闭合 ``</think>``：取后半段（思考内容不得写入对话）
    - 含未闭合 ``<think>``：整条不可用，返回 None
    - 剔除后为空：返回 None
    """
    if "</think>" in text:
        tail = text.split("</think>", 1)[1].strip()
        return tail or None
    if "<think>" in text:
        return None
    return text.strip() or None


def _coerce_content_text(content) -> str:
    """将消息 content（str 或 parts 列表）归一为纯文本."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def _extract_text(content_json: str | None) -> str:
    """从消息 content_json（{'text': ...} / 列表 / 纯文本）提取用户输入文本."""
    if not content_json:
        return ""
    try:
        parsed = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return content_json
    if isinstance(parsed, dict):
        return _coerce_content_text(parsed.get("text", ""))
    if isinstance(parsed, list):
        return _coerce_content_text(parsed)
    return str(parsed)
