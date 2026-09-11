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
  以 ``conversation_messages(role="waker", waker_id=回复者)`` 写回；
- 群会话协作事件实时落群：等待期轮询 thread state 增量解析委派调用与
  结果，派活（dispatch）与成员汇报（report）在发生时立即写入会话，
  不再等 run 终态后批量补写；过程消息以 ``meta.partial=True`` 标记。
"""

import asyncio
import json
import logging
import uuid
from collections import OrderedDict
from datetime import UTC, datetime

from sqlalchemy import String, and_, cast, func, or_, select

from app.deerflow.client import DeerFlowClient, build_run_configuration
from app.models.conversation import Conversation, ConversationMessage
from app.models.group import Group, GroupMember
from app.models.waker import Waker
from app.services.run_progress import (
    coerce_content_text as _coerce_content_text,
    extract_progress as _extract_progress,
    strip_think as _strip_think,
)

logger = logging.getLogger(__name__)

_TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}


def build_group_protocol(group_id: str, conversation_id: str, members: list[str]) -> str:
    """构建群会话协作规程（system 消息，动态注入群上下文）。

    分级分流：简单任务直接回答（不打扰成员）；复杂且可协作的任务才走
    「分析 → 发布清单 → 派活 → 汇总」流程。注入 group_id / conversation_id /
    成员名单，使 Leader 能正确调用群发消息与委派工具（不带本文本时，
    Leader 无渠道获知当前群上下文）。用 system 角色注入不会污染标题生成与
    用户消息语义（TitleMiddleware 只看 human）。
    """
    members_line = "\n".join(members) if members else "- （暂无成员信息）"
    return (
        "【群组协作规程】你是本群（Group）的 Leader，负责判断任务类型并组织协作。\n"
        "\n"
        f"当前群组 ID：{group_id}\n"
        f"当前群会话 ID：{conversation_id}\n"
        f"群成员：\n{members_line}\n"
        "\n"
        "收到用户消息后，先判断任务类型，再选择路径：\n"
        "\n"
        "A. 简单任务——直接回答，不拆解、不委派：\n"
        "  适用于：闲聊问候、简单问答、你一两步内就能独立完成的事、不需要其他成员专长或外部协作的内容。\n"
        "  做法：直接给出高质量回答，不要调用 query_group 查成员，也不要委派。\n"
        "\n"
        "B. 复杂任务——走协作流程（分析 → 发布清单 → 派活 → 汇总）：\n"
        "  仅当任务明显需要多步骤执行、多来源调研、或不同专长的成员分工协作才能更好完成时：\n"
        "  1. 分析：拆解任务，判断哪些子任务适合交给群成员、哪些你直接完成；\n"
        "  2. 发布清单：调用 waker-team_post_group_message（conversation_id 用上面的"
        "当前群会话 ID）在群里发布任务目标与任务清单，逐项 @对应成员并写清交付要求；\n"
        "  3. 派活：先调用 waker-team_query_group（group_id 用上面的当前群组 ID）查看群内成员，"
        "再委派给最合适的其他成员：短任务（预计 1 分钟内完成）用 waker-team_delegate_to_agent "
        "同步委派（group_id 用上面的当前群组 ID；可传 sync_timeout 参数（秒，例如 240））；"
        "较长任务（写文档/深度调研等）用 waker-team_delegate_submit 异步委派（group_id 用上面的"
        "当前群组 ID，并务必传 conversation_id 用上面的当前群会话 ID——成员完成后会在群里"
        "发布【成员汇报】，用户可见成员参与）；不要委派给自己；你擅长的部分直接完成或在汇总中说明；"
        "每个子任务只派一次；指令要具体、可独立完成；\n"
        "  4. 汇总：成员返回结果后，整合所有信息给出最终结论回复用户，"
        "并在结论中说明各成员的分工与贡献。\n"
        "  成员的派活与执行汇报会自动展示在群聊中；若成员超时仍在后台执行，请在结论中注明。\n"
        "\n"
        "判断原则：宁可直接回答，也不要为简单问题打扰成员；确属复杂任务时才走协作流程。"
    )


class ChatReplyService:
    """将会话消息接入 DeerFlow 并回写回复."""

    def __init__(
        self,
        db_session_factory,
        deerflow_client: DeerFlowClient,
        poll_interval: float = 3.0,
        reply_timeout: float = 1800.0,
    ) -> None:
        """reply_timeout 默认 30 分钟：调研型任务（web 调研 + 长 thinking）
        真实耗时可达 10 分钟以上，避免过早放弃导致回复丢失。"""
        self._db_factory = db_session_factory
        self._df = deerflow_client
        self._poll_interval = poll_interval
        self._reply_timeout = reply_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        # 停止请求专用锁：与 _locks（回复任务串行）分离——停止不能被长等待阻塞，
        # 仅用于并发停止请求的去重（双击/重试产生的重复调用）。
        self._stop_locks: dict[str, asyncio.Lock] = {}
        # 进行中的回复 run（conversation_id → {thread_id, run_id, target, started_at}），
        # 供 get_progress 提供「思考/工具步骤」进度快照；仅内存态，进程重启后降级为无进度。
        self._active_runs: dict[str, dict] = {}
        # 用户主动停止的会话集合：等待循环据此静默退出，避免误写「失败」提示。
        self._stop_requested: set[str] = set()
        # 投递游标（conversation_id → 最后一条已投递 user 消息的 (created_at, id)）：
        # 多澄清聚合场景下，据此只投递「尚未消费」的 user 回答，避免重复投递。
        # CF13：created_at 微秒精度但非唯一 → 用 (created_at, id) 二元组做复合游标，
        # 过滤时用 (created_at > c_ts) OR (created_at == c_ts AND id > c_id)，与投递
        # 排序 (created_at.asc, id.asc) 同序，杜绝同 created_at 消息漏投。
        # 仅内存态：进程重启后回退到「最近一条成功 waker 回复」作为基线（见 _reply_once_locked）。
        # OrderedDict + 上限裁剪：避免随历史会话单调增长（LRU 淘汰最久未投递的会话）。
        self._dispatch_cursors: "OrderedDict[str, tuple[datetime, str]]" = OrderedDict()
        self._dispatch_cursors_max = 500

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

    def _stop_lock_for(self, conversation_id: str) -> asyncio.Lock:
        lock = self._stop_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._stop_locks[conversation_id] = lock
        return lock

    def _record_dispatch_cursor(
        self, conversation_id: str, created_at: datetime, message_id: str
    ) -> None:
        """记录复合投递游标 (created_at, id)，并按上限 LRU 裁剪（CF13）.

        游标字典只写不删会随历史会话单调增长；改用 OrderedDict，写入时
        move_to_end 标记为最近使用，超过上限时从头部（最久未投递）淘汰。
        """
        self._dispatch_cursors[conversation_id] = (created_at, message_id)
        self._dispatch_cursors.move_to_end(conversation_id)
        while len(self._dispatch_cursors) > self._dispatch_cursors_max:
            self._dispatch_cursors.popitem(last=False)

    async def stop_reply(self, conversation_id: str) -> dict:
        """用户主动停止进行中的回复（取消 DeerFlow run）.

        幂等：无进行中回复时返回 ``{"stopped": False}``；重复停止请求串行化，
        仅首次写「已停止」提示（避免双击产生重复消息）。
        停止成功后在会话写入系统提示（前端据此立即恢复发送态），
        等待循环检测到停止标记后静默退出（不再写失败提示）。
        """
        async with self._stop_lock_for(conversation_id):
            active = self._active_runs.get(conversation_id)
            if active is None:
                return {"stopped": False}
            # 先注册停止意图：即使 run 终止较慢，等待循环下一轮也会立即退出
            already_stopping = conversation_id in self._stop_requested
            self._stop_requested.add(conversation_id)
            try:
                await self._df.cancel_run(active["thread_id"], active["run_id"], wait=True)
            except Exception:
                # run 已到终态（409）/不存在（404）等：撤销标记，按正常流程收敛；
                # 若已有其他停止请求在处理，保留其标记供等待循环静默退出。
                logger.warning(
                    "cancel_run failed (conversation=%s run=%s)",
                    conversation_id,
                    active["run_id"],
                    exc_info=True,
                )
                if not already_stopping:
                    self._stop_requested.discard(conversation_id)
                return {"stopped": False}
            if not already_stopping:
                await self._write_message(conversation_id, "system", None, "⏹ 已停止本次回复。")
            logger.info(
                "Reply stopped by user: conversation=%s run=%s",
                conversation_id,
                active["run_id"],
            )
            return {"stopped": True}

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

            # 收集「自上次投递游标以来尚未消费的 user 回答」——多澄清聚合场景下，
            # defer_reply 的回答只入库不调度，最终触发时需按时间升序聚合为单条投递，
            # 否则之前的回答模型永远看不到。
            cursor = self._dispatch_cursors.get(conversation_id)
            if cursor is None:
                # 内存游标缺失（首次/进程重启）：CF7 以「最近一条代表成功投递/
                # 完整回复的 waker 消息」为基线。必须排除过程消息与失败提示：
                # - meta.partial=true（群内 leader_post / dispatch / report / 成员汇报）；
                # - meta.kind="error_notice"（“回复生成失败”提示，且均为 system 角色）。
                # 否则这些消息的 created_at 会盖过此前**未成功投递**的用户回答，
                # 导致回答被 created_at > cursor 永久排除、重启后永不投递（用户消息丢失）。
                content_col = cast(ConversationMessage.content_json, String)
                partial_flag = func.json_extract(content_col, "$.meta.partial")
                kind_flag = func.json_extract(content_col, "$.meta.kind")
                baseline = (
                    await db.execute(
                        select(ConversationMessage.created_at, ConversationMessage.id)
                        .where(
                            ConversationMessage.conversation_id == conversation_id,
                            ConversationMessage.role == "waker",
                            partial_flag.is_(None),
                            or_(kind_flag.is_(None), kind_flag != "error_notice"),
                        )
                        .order_by(
                            ConversationMessage.created_at.desc(),
                            ConversationMessage.id.desc(),
                        )
                        .limit(1)
                    )
                ).first()
                if baseline is not None:
                    cursor = (baseline[0], baseline[1])
            pending_query = select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "user",
            )
            if cursor is not None:
                # CF13：复合游标——(created_at > c_ts) OR (created_at == c_ts AND id > c_id)，
                # 与下方投递排序 (created_at.asc, id.asc) 同序，同 created_at 不漏投。
                c_ts, c_id = cursor
                pending_query = pending_query.where(
                    or_(
                        ConversationMessage.created_at > c_ts,
                        and_(
                            ConversationMessage.created_at == c_ts,
                            ConversationMessage.id > c_id,
                        ),
                    )
                )
            pending = (
                await db.execute(
                    pending_query.order_by(
                        ConversationMessage.created_at.asc(),
                        ConversationMessage.id.asc(),
                    )
                )
            ).scalars().all()
            # 按时间升序聚合全部待投递回答文本
            parts = [t for t in (_extract_text(m.content_json) for m in pending) if t]
            if not parts:
                return
            text = "\n".join(parts)
            user_msg_id = pending[-1].id
            dispatch_ts = pending[-1].created_at

            # 群会话：查询成员名单（用于动态协作规程注入）
            group_member_lines: list[str] = []
            if conv.group_id:
                group_member_lines = await self._load_group_member_lines(
                    db, conv.group_id, target
                )

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
        # 群会话：注入「群组协作规程」（system 消息，动态携带群/会话 ID 与成员名单）——
        # Leader 先分析规划、在群里发布任务清单、委派成员执行、再汇总结论；
        # system 角色不影响标题/用户消息语义。
        run_messages: list[dict] = []
        if conv.group_id:
            protocol = build_group_protocol(
                conv.group_id, conversation_id, group_member_lines
            )
            run_messages.append({"role": "system", "content": protocol})
        run_messages.append({"role": "user", "content": text})
        run_body = {
            "input": {"messages": run_messages},
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
            # 失败反馈：避免用户静默等待；标记 meta.kind=error_notice，
            # 使冷启动基线（CF7）不把该失败提示当作已消费边界。
            await self._write_message(
                conversation_id,
                "system",
                None,
                "⚠️ 回复生成失败（服务连接异常），请稍后重试。",
                meta={"kind": "error_notice"},
            )
            return
        if not run_id:
            logger.error("Reply run returned no run_id (conversation=%s)", conversation_id)
            await self._write_message(
                conversation_id,
                "system",
                None,
                "⚠️ 回复生成失败（服务连接异常），请稍后重试。",
                meta={"kind": "error_notice"},
            )
            return

        # 投递成功：记录复合游标 (created_at, id)（下次只投递此后的新回答，避免重复投递）。
        self._record_dispatch_cursor(conversation_id, dispatch_ts, user_msg_id)

        # 4. 注册进度快照（前端展示「思考/工具步骤」）并等待 run 终态
        self._active_runs[conversation_id] = {
            "thread_id": thread_id,
            "run_id": run_id,
            "target": target,
            "started_at": datetime.now(UTC),
        }
        try:
            # 等待 run 终态；群会话在等待期增量解析委派事件并实时写入群消息
            # （派活/成员汇报在发生时落群，不再终态后批量补写）。
            reply_text, clarifications, thread_title = await self._wait_for_reply(
                thread_id,
                run_id,
                conversation_id=conversation_id,
                leader=target,
                is_group=bool(conv.group_id),
            )
            # 用户主动停止：stop_reply 已写「已停止」提示，静默收尾（不写失败提示）；
            # 若 run 恰在停止前成功且已提取回复/澄清，则按正常路径写回。
            if conversation_id in self._stop_requested and not reply_text and not clarifications:
                logger.info(
                    "Reply stopped by user; skip failure notice (conversation=%s)",
                    conversation_id,
                )
                return
            if reply_text or clarifications:
                # 5. 写回会话（role=waker）；CONTRACT-CLARIFICATIONS：
                # meta.clarifications = 本 run 内全部澄清卡片（按提问先后顺序）；
                # meta.clarification = 首张（向下兼容旧前端）；无澄清时
                # clarifications=[]、clarification=None（用条件表达式，空列表不求值 [0]）。
                meta = {
                    "clarifications": clarifications,
                    "clarification": clarifications[0] if clarifications else None,
                }
                await self._write_message(
                    conversation_id, "waker", target, reply_text or "", meta=meta
                )
                logger.info(
                    "Reply written: conversation=%s waker=%s run=%s clarifications=%d",
                    conversation_id,
                    target,
                    run_id,
                    len(clarifications),
                )
            else:
                # 失败/超时兜底：写入系统提示，避免用户静默等待；
                # 标记 meta.kind=error_notice，冷启动基线（CF7）据此排除。
                await self._write_message(
                    conversation_id,
                    "system",
                    None,
                    "⚠️ 回复生成失败（模型服务暂不可用或超时），请稍后重试。",
                    meta={"kind": "error_notice"},
                )
                logger.warning(
                    "Reply failure notice written: conversation=%s run=%s", conversation_id, run_id
                )

            # 6. 写回标题（仅当会话尚无标题；DeerFlow TitleMiddleware 首轮生成）
            if thread_title:
                await self._apply_title(conversation_id, thread_title)
        finally:
            self._active_runs.pop(conversation_id, None)
            self._stop_requested.discard(conversation_id)

    # ------------------------------------------------------------------
    # 进度查询（思考/工具步骤快照）
    # ------------------------------------------------------------------

    async def get_progress(self, conversation_id: str) -> dict:
        """返回会话进行中回复的进度快照（无进行中回复时 active=False）."""
        active = self._active_runs.get(conversation_id)
        if not active:
            return {"active": False}
        elapsed = int((datetime.now(UTC) - active["started_at"]).total_seconds())
        base = {
            "active": True,
            "run_id": active["run_id"],
            "target": active["target"],
            "elapsed": elapsed,
        }
        try:
            state = await self._df.get_thread_state(active["thread_id"])
        except Exception:
            logger.warning(
                "get_thread_state failed for progress (conversation=%s)",
                conversation_id,
                exc_info=True,
            )
            return {**base, "steps": [], "current": {"kind": "unknown", "detail": "正在执行…"}}
        steps, current = _extract_progress(state, active["run_id"])
        return {**base, "steps": steps, "current": current}

    async def _apply_title(self, conversation_id: str, title: str) -> None:
        """将会话标题写入本地表（仅当当前为空，不覆盖已有标题）."""
        async with self._db_factory() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == conversation_id))
            ).scalars().first()
            if conv is None or conv.title:
                return
            conv.title = title
            conv.updated_at = datetime.now(UTC)
            await db.commit()
            logger.info("Conversation title written: conversation=%s title=%r", conversation_id, title)

    async def list_active_runs(self, group_id: str | None = None) -> list[dict]:
        """列出进行中的 Leader 回复 run（供群活动状态聚合）.

        Returns
        -------
        [{"conversation_id", "conversation_title", "target", "run_id", "elapsed"}]
        """
        if not self._active_runs:
            return []
        async with self._db_factory() as db:
            query = select(Conversation).where(
                Conversation.id.in_(list(self._active_runs.keys()))
            )
            if group_id is not None:
                query = query.where(Conversation.group_id == group_id)
            convs = (await db.execute(query)).scalars().all()
        now = datetime.now(UTC)
        items: list[dict] = []
        for conv in convs:
            active = self._active_runs.get(conv.id)
            if active is None:
                continue
            items.append(
                {
                    "conversation_id": conv.id,
                    "conversation_title": conv.title,
                    "target": active["target"],
                    "run_id": active["run_id"],
                    "elapsed": int((now - active["started_at"]).total_seconds()),
                }
            )
        return items

    async def _load_group_member_lines(
        self, db, group_id: str, leader: str
    ) -> list[str]:
        """加载群成员名单行（"名字（角色）"），供动态协作规程注入."""
        rows = (
            await db.execute(
                select(GroupMember.waker_id, GroupMember.role, Waker.role)
                .join(Waker, Waker.name == GroupMember.waker_id, isouter=True)
                .where(GroupMember.group_id == group_id)
            )
        ).all()
        lines: list[str] = []
        for waker_id, member_role, waker_role in rows:
            tag = "Leader" if waker_id == leader else (waker_role or member_role or "成员")
            lines.append(f"- {waker_id}（{tag}）")
        return lines

    async def _flush_group_delegation_events(
        self,
        conversation_id: str,
        leader: str,
        state: dict,
        seen: dict,
        run_started_at: datetime,
        post_cache: dict[str, bool],
    ) -> None:
        """把 thread state 中新增的委派事件实时写入群消息（增量、去重）.

        - dispatch（派活）：清单优先——Leader 已通过 post_group_message 发布过
          清单则跳过兜底派活卡；否则写 @成员 派活消息；
        - report（成员汇报）：同步委派结果返回时立即写群（成员在群里发声）。
        """
        events = _diff_delegation_events(state, seen)
        for ev in events:
            if ev["event"] == "dispatch":
                if "has_post" not in post_cache:
                    post_cache["has_post"] = await self._has_leader_post_since(
                        conversation_id, run_started_at
                    )
                if post_cache["has_post"]:
                    continue
                await self._write_dispatch_message(conversation_id, leader, ev)
            elif ev["event"] == "report":
                await self._write_report_message(conversation_id, ev)

    async def _write_dispatch_message(
        self, conversation_id: str, leader: str, ev: dict
    ) -> None:
        """兜底派活消息：Leader 未发布清单时，把委派动作以 @成员 形式写入群."""
        target = ev.get("target") or "成员"
        instruction = _clip(ev.get("instruction") or "", 200)
        text = (
            f"【派活】@{target} 请完成：{instruction}"
            if instruction
            else f"【派活】@{target} 请开始执行。"
        )
        await self._write_message(
            conversation_id,
            "waker",
            leader,
            text,
            meta={
                "kind": "dispatch",
                "target": target,
                "mode": ev.get("mode"),
                "partial": True,
            },
        )

    async def _write_report_message(self, conversation_id: str, ev: dict) -> None:
        """成员汇报消息：同步委派结果返回时立即写群（不再终态批量补写）."""
        target = ev.get("target") or "member"
        instruction = _clip(ev.get("instruction") or "", 80)
        status = ev.get("status")
        if status == "done":
            result = _clip(ev.get("result") or "", 500) or "（无结果摘要）"
            text = f"【成员汇报 · {target}】\n任务：{instruction}\n结果：{result}"
        elif status == "running":
            text = f"【成员汇报 · {target}】\n任务：{instruction}\n状态：执行中（后台继续）"
        else:
            err = ev.get("error") or status or "未知原因"
            text = f"【成员汇报 · {target}】\n任务：{instruction}\n状态：未能完成：{_clip(str(err), 200)}"
        await self._write_message(
            conversation_id,
            "waker",
            target,
            text,
            meta={"kind": "report", "target": target, "status": status, "partial": True},
        )

    async def _has_leader_post_since(
        self, conversation_id: str, since: datetime
    ) -> bool:
        """本 run 期间 Leader 是否已通过 post_group_message 发布过群内消息.

        有清单则派活卡兜底跳过：清单正文已 @成员 并说明分工，避免重复噪音。
        """
        async with self._db_factory() as db:
            rows = (
                await db.execute(
                    select(ConversationMessage).where(
                        ConversationMessage.conversation_id == conversation_id,
                        ConversationMessage.role == "waker",
                        ConversationMessage.created_at >= since,
                    )
                )
            ).scalars().all()
        for m in rows:
            try:
                parsed = json.loads(m.content_json or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            meta = parsed.get("meta") if isinstance(parsed, dict) else None
            if isinstance(meta, dict) and meta.get("kind") == "leader_post":
                return True
        return False

    async def _write_message(
        self,
        conversation_id: str,
        role: str,
        waker_id: str | None,
        text: str,
        *,
        at: datetime | None = None,
        meta: dict | None = None,
    ) -> None:
        """写入一条会话消息并刷新会话 updated_at.

        at 可选指定时间戳（批量排序）；meta 为结构化标记（如
        ``{"kind": "dispatch", "partial": true}``——前端用它区分过程消息，
        避免误判回复已结束）。
        """
        payload: dict = {"text": text}
        if meta:
            payload["meta"] = meta
        async with self._db_factory() as db:
            db.add(
                ConversationMessage(
                    conversation_id=conversation_id,
                    role=role,
                    waker_id=waker_id,
                    content_json=json.dumps(payload, ensure_ascii=False),
                    created_at=at or datetime.now(UTC),
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

    async def _wait_for_reply(
        self,
        thread_id: str,
        run_id: str,
        *,
        conversation_id: str | None = None,
        leader: str | None = None,
        is_group: bool = False,
    ) -> tuple[str | None, list[dict], str | None]:
        """轮询 run 直至终态，成功后从 thread state 提取回复/澄清与会话标题.

        返回 ``(reply_text, clarifications, thread_title)``（clarifications 为列表）：
        澄清场景见 ``_extract_reply_from_state``。

        群会话（is_group=True）在等待期增量解析委派调用与结果，把派活/成员
        汇报在发生时立即写入会话（meta.partial=True），不再等终态批量补写。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._reply_timeout
        seen = {"dispatches": 0, "reports": 0}
        post_cache: dict[str, bool] = {}
        run_started_at = datetime.now(UTC)
        state: dict | None = None
        while loop.time() < deadline:
            await asyncio.sleep(self._poll_interval)
            # 用户主动停止：立即结束等待（「已停止」提示由 stop_reply 负责写入）
            if conversation_id and conversation_id in self._stop_requested:
                logger.info(
                    "Reply wait aborted by user stop (conversation=%s run=%s)",
                    conversation_id,
                    run_id,
                )
                return None, [], None
            try:
                run = await self._df.get_run(thread_id, run_id)
            except Exception:
                logger.warning("get_run failed (thread=%s run=%s)", thread_id, run_id, exc_info=True)
                continue
            # 群会话：每轮尝试增量事件实时落群（派活/汇报在发生时写入）
            if is_group and conversation_id and leader:
                try:
                    state = await self._df.get_thread_state(thread_id)
                except Exception:
                    logger.warning(
                        "group events: get_thread_state failed (thread=%s)",
                        thread_id,
                        exc_info=True,
                    )
                else:
                    await self._flush_group_delegation_events(
                        conversation_id, leader, state, seen, run_started_at, post_cache
                    )
            status = run.get("status")
            if status not in _TERMINAL_RUN_STATUSES:
                continue
            if status != "success":
                logger.warning("Reply run terminal status=%s (run=%s)", status, run_id)
                return None, [], None
            try:
                if state is None:
                    state = await self._df.get_thread_state(thread_id)
                title = _extract_title(state)
                text, clarifications = _extract_reply_from_state(state, run_id)
                return text, clarifications, title
            except Exception:
                logger.exception("Failed to extract reply (thread=%s run=%s)", thread_id, run_id)
                return None, [], None
        logger.warning("Reply run timed out waiting (thread=%s run=%s)", thread_id, run_id)
        return None, [], None


def _extract_reply_from_state(state: dict, run_id: str) -> tuple[str | None, list[dict]]:
    """从 thread state 中提取本次 run 的回复文本与**全部**澄清请求。

    回复文本优先匹配 ``additional_kwargs.run_id == run_id`` 的 AI 正文；
    回退取最后一条内容非空的 AI 消息（跳过 hide_from_ui 的中间注入）。

    澄清提取：本 run 以 ``ask_clarification`` 结束时（该工具消息晚于所有
    AI 正文——模型调用后 run 立即 END），收集**该 run 内全部** ask_clarification
    的结构化 ``human_input`` payload（DeerFlow ClarificationMiddleware 写入
    ToolMessage.artifact），使前端可同时渲染多张交互卡片（选项按钮/表单）；
    若某条澄清 payload 缺失（历史数据/异常），回退把其格式化文本并入回复
    正文，保证澄清不丢失。

    run 边界（CF6）：「只取晚于最后一条 AI 正文的澄清」在**无可见 AI 正文**
    （模型直接发工具调用、ai_idx 为 None）时也必须成立——此时退化为按 run 区间
    边界过滤（``_run_region_start``：最后一条 human 输入或带不同 run_id 的 AI 之后），
    只取 thread 末尾属于本 run 的连续澄清段，绝不返回 thread 全生命周期历史澄清。

    返回 ``(reply_text, clarifications)``，clarifications 为结构化 payload 列表：
    - 普通完成：``(ai_text, [])``
    - 单/多澄清结束（有引导语正文）：``(ai_text, [payload, ...])``——正文与
      结构化澄清分离，前端先展示正文再依次渲染卡片；
    - 澄清结束（无正文且 payload 缺失）：``(clarification_text, [])``；
    - 均无内容：``(None, [])``（进入失败/超时兜底）。
    """
    messages = ((state.get("values") or {}).get("messages")) or []

    candidates: list[tuple[bool, str, int]] = []  # (run 匹配, 文本, 消息索引)
    # 收集全部澄清（保持出现顺序）：(索引, 文本, payload)
    clarifications_seen: list[tuple[int, str | None, dict | None]] = []
    for i, m in enumerate(messages):
        mtype = m.get("type")
        if mtype == "ai":
            extra = m.get("additional_kwargs") or {}
            if extra.get("hide_from_ui"):
                continue
            text = _strip_think(_coerce_content_text(m.get("content")))
            if not text:
                continue
            candidates.append((extra.get("run_id") == run_id, text, i))
        elif mtype == "tool" and m.get("name") == "ask_clarification":
            extra = m.get("additional_kwargs") or {}
            if extra.get("hide_from_ui"):
                continue
            text = _strip_think(_coerce_content_text(m.get("content")))
            artifact = m.get("artifact")
            payload: dict | None = None
            if isinstance(artifact, dict):
                candidate = artifact.get("human_input")
                if isinstance(candidate, dict) and candidate.get("kind") == "human_input_request":
                    payload = candidate
            if text or payload:
                clarifications_seen.append((i, text, payload))

    # AI 正文选取：优先 run 匹配的最后一条，回退最后一条非空正文。
    # CF6：ai_idx 用 None（而非 -1）作「无可用正文」哨兵——LangGraph 常态是模型
    # 直接发 ask_clarification 工具调用而不带正文，此时 candidates 为空、ai_idx 保持
    # None。绝不能用 -1：list 语义下 `c[0] > -1` 对所有历史澄清恒成立，会返回 thread
    # 全生命周期历史澄清（语义反转），导致前端渲染过期卡片、用户可重复回答已答澄清。
    ai_text: str | None = None
    ai_idx: int | None = None
    for matched, text, idx in reversed(candidates):
        if matched:
            ai_text, ai_idx = text, idx
            break
    if ai_text is None and candidates:
        ai_text, ai_idx = candidates[-1][1], candidates[-1][2]

    # 本 run 是否以澄清结束：只取属于本 run 区间的澄清，跳过历史轮。
    # 区间下界 cutoff 取两者较大值：
    # - run 区间起点前一位（_run_region_start：最后一条「属于更早 run 的边界」之后），
    #   在 ai_idx 为 None（无可见正文）时提供 run 边界，保证「只取本 run 澄清」成立；
    # - 最后一条 AI 正文下标 ai_idx（有可见正文时，澄清晚于引导正文）。
    region_start = _run_region_start(messages, run_id)
    cutoff = region_start - 1
    if ai_idx is not None:
        cutoff = max(cutoff, ai_idx)
    run_clarifications = [c for c in clarifications_seen if c[0] > cutoff]
    if not run_clarifications:
        if clarifications_seen and ai_idx is None:
            logger.info(
                "No run-scoped clarification for run=%s (region_start=%d, %d historical "
                "clarification(s) skipped)",
                run_id,
                region_start,
                len(clarifications_seen),
            )
        return ai_text, []

    payloads: list[dict] = []
    reply_text = ai_text
    for _idx, text, payload in run_clarifications:
        if payload is not None:
            payloads.append(payload)
        elif text:
            # payload 缺失：回退把澄清文本并入正文，保证不丢失
            reply_text = f"{reply_text}\n\n{text}" if reply_text else text
    return reply_text, payloads


def _run_region_start(messages: list, run_id: str) -> int:
    """返回当前 run 区间的起始下标（CF6：无可见 AI 正文时的 run 边界兜底）。

    边界消息 =「属于更早 run」的分隔点：
    - 非隐藏的 ``human`` 输入（每一轮 run 由一条用户消息触发，故最后一条 human
      即当前 run 的起点）；或
    - 带有**不同** ``run_id`` 的 ``ai`` 消息（run 标签可用时更精确）。

    取全列表中最后一条边界之后作为区间起点；找不到边界时返回 0（整个 state
    视为当前 run）。据此过滤澄清可确保「无可见正文」分支下也只返回本 run 的
    末尾连续澄清段，绝不回吐历史轮澄清。
    """
    start = 0
    for i, m in enumerate(messages):
        extra = m.get("additional_kwargs") or {}
        if extra.get("hide_from_ui"):
            continue
        mtype = m.get("type")
        if mtype == "human":
            start = i + 1
        elif mtype == "ai":
            m_run = extra.get("run_id")
            if m_run is not None and m_run != run_id:
                start = i + 1
    return start


def _extract_title(state: dict) -> str | None:
    """从 thread state 提取 DeerFlow TitleMiddleware 自动生成的标题。"""
    title = (state.get("values") or {}).get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return None


def _scan_delegation_events(state: dict) -> tuple[list[dict], list[dict]]:
    """扫描当前轮全部委派调用（dispatch）与同步委派结果（report，按序配对）.

    Returns:
        (dispatches, reports)
        dispatches: [{"target", "instruction", "mode": "sync"|"async"}]（保持出现顺序）
        reports:    [{"target", "instruction", "status", "result", "error"}]，
                    仅包含已有结果的同步调用——结果未返回（工具执行中）的调用
                    不生成 report，等后续轮询配对后再写汇报。
    """
    messages = ((state.get("values") or {}).get("messages")) or []

    # 取最近一条可见 human 消息之后的全部消息（当前轮）
    tail: list[dict] = []
    for m in reversed(messages):
        if m.get("type") == "human" and not (m.get("additional_kwargs") or {}).get(
            "hide_from_ui"
        ):
            break
        tail.append(m)
    tail.reverse()

    dispatches: list[dict] = []
    sync_indexes: list[int] = []  # dispatches 中 sync 项的下标（按出现顺序）
    results_raw: list[str] = []
    for m in tail:
        mtype = m.get("type")
        if mtype == "ai":
            for tc in m.get("tool_calls") or []:
                name = tc.get("name") or ""
                args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
                if name.endswith("delegate_to_agent"):
                    sync_indexes.append(len(dispatches))
                    dispatches.append(
                        {
                            "target": args.get("target_agent") or args.get("target"),
                            "instruction": args.get("instruction") or "",
                            "mode": "sync",
                        }
                    )
                elif name.endswith("delegate_submit"):
                    dispatches.append(
                        {
                            "target": args.get("target"),
                            "instruction": args.get("instruction") or "",
                            "mode": "async",
                        }
                    )
        elif mtype == "tool":
            name = m.get("name") or ""
            if name.endswith("delegate_to_agent"):
                results_raw.append(_coerce_content_text(m.get("content")))

    reports: list[dict] = []
    for i, d_idx in enumerate(sync_indexes):
        if i >= len(results_raw):
            break  # 结果尚未返回（工具执行中），待后续轮询配对后再生成汇报
        d = dispatches[d_idx]
        raw = results_raw[i]
        parsed: dict = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = {"result": raw}
        if not isinstance(parsed, dict):
            parsed = {"result": str(parsed)}
        status = parsed.get("status")
        if status is None:
            status = "error" if parsed.get("error") else "unknown"
        reports.append(
            {
                "target": d["target"] or parsed.get("target_agent"),
                "instruction": d["instruction"],
                "status": status,
                "result": parsed.get("result"),
                "error": parsed.get("error"),
            }
        )
    return dispatches, reports


def _extract_delegations(state: dict) -> list[dict]:
    """从 thread state 当前轮提取 Leader 的 delegate_to_agent 调用与结果（按序配对）.

    Returns: [{"target", "instruction", "status", "result", "error"}]
    """
    return _scan_delegation_events(state)[1]


def _diff_delegation_events(state: dict, seen: dict) -> list[dict]:
    """增量解析：返回自 seen 计数以来的新委派事件，并推进计数（幂等可重入）.

    seen: {"dispatches": int, "reports": int}（调用方持有并跨轮传递）
    事件: {"event": "dispatch", "target", "instruction", "mode"}
          {"event": "report", "target", "instruction", "status", "result", "error"}
    """
    dispatches, reports = _scan_delegation_events(state)
    events: list[dict] = []
    for d in dispatches[seen.get("dispatches", 0):]:
        events.append({"event": "dispatch", **d})
    seen["dispatches"] = len(dispatches)
    for r in reports[seen.get("reports", 0):]:
        events.append({"event": "report", **r})
    seen["reports"] = len(reports)
    return events


def _clip(text: str, limit: int) -> str:
    """截断文本（超长添加省略号）."""
    s = str(text).strip()
    return s if len(s) <= limit else s[:limit] + "…"


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
