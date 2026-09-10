import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Group, GroupMember, ChatMessage, Waker, GroupActivityItem, ClarificationRequest } from '../types'
import Avatar from '../components/Avatar'
import MessageBubble from '../components/chat/MessageBubble'
import TypingIndicator from '../components/chat/TypingIndicator'
import ReplyProgress, { type ReplyProgressInfo } from '../components/chat/ReplyProgress'
import Composer from '../components/chat/Composer'
import type { MentionMember } from '../components/chat/Composer'
import RunningWakersBar from '../components/chat/RunningWakersBar'
import WakerActivityDrawer from '../components/chat/WakerActivityDrawer'
import { parseMessageContent } from '../utils/messageContent'
import {
  buildClarificationAnswerDisplay,
  buildClarificationAnswerText,
  computeClarificationState,
  type ClarificationAnswer,
} from '../utils/clarification'

type SideTab = 'tasks' | 'settings'

// Helper to format ISO date to display time
function formatTime(isoString: string | null): string {
  if (!isoString) return ''
  const d = new Date(isoString)
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

// Helper to format ISO date to display date+time
function formatDateTime(isoString: string | null): string {
  if (!isoString) return ''
  const d = new Date(isoString)
  const month = d.getMonth() + 1
  const day = d.getDate()
  return `${month}月${day}日 ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

// Convert backend message to frontend ChatMessage
function toChatMessage(
  msg: { id: string; role: string; waker_id: string | null; content_json: string | null; created_at: string | null },
  wakerLookup: Record<string, { name: string; role: string }>,
): ChatMessage {
  const time = formatTime(msg.created_at)
  const { text, parts, meta } = parseMessageContent(msg.content_json)

  const wakerInfo = msg.waker_id ? wakerLookup[msg.waker_id] : undefined
  return {
    id: msg.id,
    role: msg.role as ChatMessage['role'],
    waker: msg.role === 'waker' && wakerInfo ? wakerInfo : undefined,
    time,
    text,
    parts,
    meta,
  }
}

/** 回复是否已完成：system 消息，或非过程消息（meta.partial）的 waker 消息 */
function isReplyComplete(msg?: ChatMessage): boolean {
  if (!msg) return false
  if (msg.role === 'system') return true
  return msg.role === 'waker' && !msg.meta?.partial
}

export default function GroupChatPage() {
  const { groupId } = useParams<{ groupId: string }>()
  const gid = groupId ?? ''

  // Data state
  const [group, setGroup] = useState<Group | null>(null)
  const [members, setMembers] = useState<GroupMember[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversations, setConversations] = useState<Array<{ id: string; title: string | null; status: string; created_at: string | null }>>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [sideTab, setSideTab] = useState<SideTab>('tasks')
  // 右侧任务/设置面板：默认隐藏，通过头部「任务 / 设置」按钮展开
  const [sidePanelOpen, setSidePanelOpen] = useState(false)
  const [typing, setTyping] = useState(false)
  // 等待回复期间的动作/工具步骤进度（3s 轮询快照）
  const [progress, setProgress] = useState<ReplyProgressInfo | null>(null)
  // 长任务慢速补拉：typing 指示已停但回复可能稍后到达（后端等待上限 30 分钟）
  const [awaitingSlowReply, setAwaitingSlowReply] = useState(false)
  // 群内活动（运行状态条）：成员在跑时驱动消息轮询，让派活/汇报实时可见
  const [activityActive, setActivityActive] = useState(false)
  // 员工运行详情抽屉（点击状态条头像打开）
  const [drawerItem, setDrawerItem] = useState<GroupActivityItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)
  const [wakerLookup, setWakerLookup] = useState<Record<string, { name: string; role: string }>>({})

  // Refs
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  const displayName = group?.name ?? gid

  // Build mention members from real group members + waker lookup
  const mentionMembers: MentionMember[] = members.map(m => {
    const info = wakerLookup[m.waker_id]
    return { name: info?.name ?? m.waker_id, role: info?.role ?? m.role }
  })

  // Auto-scroll
  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Load data
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const [g, ms, convs, wakers] = await Promise.all([
          api.getGroup(gid).catch(() => null),
          api.listGroupMembers(gid).catch(() => [] as GroupMember[]),
          api.listGroupConversations(gid).catch(() => []),
          api.listWakers().catch(() => [] as Waker[]),
        ])
        if (cancelled) return

        setGroup(g)
        setMembers(ms)
        setConversations(convs)

        // Build waker lookup by name (waker_id in members = waker name)
        const lookup: Record<string, { name: string; role: string }> = {}
        for (const w of wakers) {
          lookup[w.name] = { name: w.name, role: w.role ?? '' }
        }
        setWakerLookup(lookup)

        // Load messages from the first conversation if available
        if (convs.length > 0) {
          const firstConv = convs[0]
          setActiveConversationId(firstConv.id)
          try {
            const msgs = await api.getConversationMessages(firstConv.id)
            if (!cancelled) {
              setMessages(msgs.map(m => toChatMessage(m, lookup)))
            }
          } catch {
            // Messages load failed, keep empty
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : '加载失败')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [gid])

  // Auto-scroll on messages/typing change
  useEffect(() => {
    scrollToBottom()
  }, [messages, typing, scrollToBottom])

  // 回复到达后刷新会话列表（标题由后端从 DeerFlow 自动生成写回）
  const refreshConversations = useCallback(async () => {
    try {
      const convs = await api.listGroupConversations(gid)
      setConversations(convs)
    } catch {
      /* 忽略，下次再刷 */
    }
  }, [gid])

  // 新建会话：立即创建并切换过去（发送后自动生成标题）
  const handleNewConversation = useCallback(async () => {
    try {
      const newConv = await api.createGroupConversation(gid, {})
      setConversations(prev => [
        { id: newConv.id, title: newConv.title, status: newConv.status, created_at: newConv.created_at },
        ...prev,
      ])
      setActiveConversationId(newConv.id)
      setMessages([])
      setSendError(null)
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '创建会话失败')
    }
  }, [gid])

  // 等待 Leader 回复期间轮询会话消息（回复由后端 DeerFlow run 异步写回）
  const replyWaitStartRef = useRef<number | null>(null)
  useEffect(() => {
    if (!typing) {
      replyWaitStartRef.current = null
      return
    }
    if (replyWaitStartRef.current === null) replyWaitStartRef.current = Date.now()
    if (!activeConversationId) return
    const timer = setInterval(async () => {
      // 超时保护：5 分钟后停止"正在思考"指示，但切换为慢速补拉
      // （调研型任务可达 10 分钟以上，后端等待上限 30 分钟）
      if (replyWaitStartRef.current && Date.now() - replyWaitStartRef.current > 5 * 60_000) {
        setTyping(false)
        setAwaitingSlowReply(true)
        return
      }
      try {
        const [msgs, prog] = await Promise.all([
          api.getConversationMessages(activeConversationId),
          api.getConversationProgress(activeConversationId).catch(() => null),
        ])
        setProgress(prog?.active ? prog : null)
        setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
        const last = msgs[msgs.length - 1]
        if (isReplyComplete(toChatMessage(last, wakerLookup))) {
          setTyping(false)
          setProgress(null)
          refreshConversations()
        }
      } catch {
        /* 保持轮询，下次重试 */
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [typing, activeConversationId, wakerLookup, refreshConversations])

  // 慢速补拉：长任务（调研类）回复迟到时自动带到页面
  useEffect(() => {
    if (!awaitingSlowReply || !activeConversationId) return
    const startedAt = Date.now()
    const timer = setInterval(async () => {
      if (Date.now() - startedAt > 30 * 60_000) {
        setAwaitingSlowReply(false)
        return
      }
      try {
        const [msgs, prog] = await Promise.all([
          api.getConversationMessages(activeConversationId),
          api.getConversationProgress(activeConversationId).catch(() => null),
        ])
        setProgress(prog?.active ? prog : null)
        setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
        const last = msgs[msgs.length - 1]
        if (isReplyComplete(toChatMessage(last, wakerLookup))) {
          setAwaitingSlowReply(false)
          setProgress(null)
          refreshConversations()
        }
      } catch {
        /* 继续补拉 */
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [awaitingSlowReply, activeConversationId, wakerLookup, refreshConversations])

  // 群内活动期间刷新消息：Leader 派活/成员汇报实时到达（3s 轮询，页面可见时）
  useEffect(() => {
    if (!activityActive || !activeConversationId) return
    const timer = setInterval(async () => {
      if (document.visibilityState === 'hidden') return
      try {
        const msgs = await api.getConversationMessages(activeConversationId)
        setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
      } catch {
        /* 继续轮询 */
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [activityActive, activeConversationId, wakerLookup])

  // 发送用户消息（文本 + 可选结构化 meta），并进入等待回复状态；供普通发送与澄清回答复用。
  // deferReply=true：仅入库不触发 run（多澄清聚合回答，最后一个回答才触发处理）。
  const sendUserMessage = useCallback(async (
    text: string,
    meta?: ChatMessage['meta'],
    options?: { deferReply?: boolean },
  ) => {
    const deferReply = options?.deferReply === true
    setSendError(null)

    // 1. Add user message optimistically
    const now = new Date()
    const timeStr = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`
    const userMsg: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      time: timeStr,
      text,
      ...(meta ? { meta } : {}),
    }
    setMessages(prev => [...prev, userMsg])

    // 2. Ensure a conversation exists (lazy creation)
    let convId = activeConversationId
    try {
      if (!convId) {
        const newConv = await api.createGroupConversation(gid, {})
        convId = newConv.id
        setActiveConversationId(convId)
        setConversations(prev => [{ id: newConv.id, title: newConv.title, status: newConv.status, created_at: newConv.created_at }, ...prev])
      }
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '创建会话失败')
      setTyping(false)
      return
    }

    // 3. Send to backend（后端将发起群 Leader 的 DeerFlow run，回复异步写回）
    try {
      if (!deferReply) {
        setTyping(true)
        setAwaitingSlowReply(false)
        setProgress(null)
      }
      await api.sendConversationMessage(convId, {
        role: 'user',
        content_json: meta ? { text, meta } : { text },
        ...(deferReply ? { defer_reply: true } : {}),
      })

      // 4. Reload messages to get the full conversation including any waker response
      const msgs = await api.getConversationMessages(convId)
      setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
      // 若回复已到达（极快响应）则结束等待，否则保持 typing 由轮询接管
      const last = msgs[msgs.length - 1]
      if (!deferReply && isReplyComplete(toChatMessage(last, wakerLookup))) {
        setTyping(false)
        refreshConversations()
      }
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '发送消息失败')
      setTyping(false)
      // Keep the optimistic message in place
    }
  }, [activeConversationId, wakerLookup, gid, refreshConversations])

  // Send message handler（普通输入）
  const handleSend = useCallback((text: string) => {
    void sendUserMessage(text)
  }, [sendUserMessage])

  // 澄清回答提交：构造与 DeerFlow 主 UI 一致的回答文案并发送。
  // 多澄清聚合：还有其他未答卡片时延迟触发（仅入库），最后一个回答才触发一次处理。
  const clarificationState = useMemo(() => computeClarificationState(messages), [messages])

  // 多澄清待答提示：全部回答后统一处理
  const totalClarifications = useMemo(
    () => messages.filter((m) => m.role === 'waker' && m.meta?.clarification).length,
    [messages],
  )

  const handleClarificationSubmit = useCallback(
    (request: ClarificationRequest, answer: ClarificationAnswer) => {
      const text = buildClarificationAnswerText(request, answer)
      const pendingOthers = clarificationState.openRequestIds.filter(
        (id) => id !== request.request_id,
      ).length
      return sendUserMessage(
        text,
        {
          clarification_response: {
            request_id: request.request_id,
            kind: answer.kind,
            value: buildClarificationAnswerDisplay(request, answer),
          },
        },
        { deferReply: pendingOthers > 0 },
      )
    },
    [sendUserMessage, clarificationState],
  )

  // Select a conversation to load its messages
  const handleSelectConversation = useCallback(async (convId: string) => {
    setActiveConversationId(convId)
    try {
      const msgs = await api.getConversationMessages(convId)
      setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
    } catch {
      setMessages([])
    }
  }, [wakerLookup])

  // 运行中：等待 Leader 回复或长任务慢速补拉期间，发送按钮显示为「停止」态
  const running = typing || awaitingSlowReply

  // 停止当前回复：取消 DeerFlow run（后端写「已停止」系统提示后返回）
  const stoppingRef = useRef(false)
  const handleStop = useCallback(async () => {
    // 防双击：停止请求处理中忽略重复点击
    if (!activeConversationId || stoppingRef.current) return
    stoppingRef.current = true
    try {
      const res = await api.stopConversationReply(activeConversationId)
      if (!res.stopped) {
        // run 可能已结束（回复即将/已写入），保持等待由轮询自然收敛
        return
      }
      setTyping(false)
      setAwaitingSlowReply(false)
      setProgress(null)
      // 重新拉取消息，把「已停止」提示带进来
      const msgs = await api.getConversationMessages(activeConversationId)
      setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '停止失败')
    } finally {
      stoppingRef.current = false
    }
  }, [activeConversationId, wakerLookup])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-sm text-[var(--text-3)]">加载中…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-sm text-red-500">{error}</div>
      </div>
    )
  }

  const leaderKey = members.find(m => m.role === 'leader')?.waker_id
  const leaderName = leaderKey ? wakerLookup[leaderKey]?.name ?? '' : ''
  const memberKeys = members.map(m => m.waker_id)

  return (
    <div className="flex h-full flex-col">
      {/* ── Header bar ── */}
      <header className="flex items-center gap-3 border-b border-[var(--border)] bg-[var(--panel)] px-4 py-2.5">
        {/* Avatar stack */}
        <div className="flex -space-x-2">
          {memberKeys.slice(0, 4).map(key => {
            const w = wakerLookup[key]
            return w ? (
              <div key={key} className="ring-2 ring-white rounded-full">
                <Avatar name={w.name} size="sm" />
              </div>
            ) : null
          })}
        </div>
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-semibold text-[var(--text)]">{displayName}</span>
            <span className="text-[11px] rounded-full bg-[var(--panel-3)] px-1.5 py-0.5 font-medium text-[var(--text-2)]">
              {members.length} 名成员
            </span>
            <span className="inline-flex items-center gap-1 text-[11px] rounded-full bg-emerald-50 px-2 py-0.5 font-semibold text-emerald-600">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-3 h-3"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              群组协作
            </span>
          </div>
          <span className="text-[11.5px] text-[var(--text-3)]">
            {members.length} 名成员{leaderName ? ` · Leader ${leaderName}` : ''}
          </span>
        </div>
        {/* 新建会话 */}
        <button
          type="button"
          onClick={handleNewConversation}
          className="ml-auto flex items-center gap-1 rounded-lg border border-[var(--border)] px-3 py-1.5 text-[12.5px] font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
          title="新建会话"
        >
          ＋ 新建会话
        </button>
        {/* 任务/设置面板开关（面板默认隐藏） */}
        <button
          type="button"
          onClick={() => setSidePanelOpen(v => !v)}
          className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
            sidePanelOpen
              ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)]'
              : 'border-[var(--border)] text-[var(--text-2)] hover:bg-[var(--panel-2)]'
          }`}
          title={sidePanelOpen ? '收起任务/设置面板' : '展开任务/设置面板'}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="15" y1="3" x2="15" y2="21" />
          </svg>
          任务 / 设置
        </button>
      </header>

      {/* ── Body ── */}
      <div className="flex flex-1 min-h-0">
        {/* ── Center area ── */}
        <section className="flex-1 flex flex-col min-w-0 bg-[var(--bg)]">
          {/* No-leader notice: 无 Leader 时消息不会触发回复 */}
          {group && !group.leader_waker_id && (
            <div className="flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-700">
              <span>⚠️</span>
              <span>本群未指定 Leader，发送的消息暂不会触发回复。请前往「群配置」设置负责人。</span>
            </div>
          )}
          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
            {messages.length === 0 && !typing && (
              <div className="flex h-full items-center justify-center">
                <div className="text-center">
                  <div className="mb-2 text-3xl opacity-40">👥</div>
                  <p className="text-[13px] text-[var(--text-3)]">群组对话即将开始</p>
                </div>
              </div>
            )}
            {messages.map(msg => {
              const clarification = msg.meta?.clarification
              return (
                <MessageBubble
                  key={msg.id}
                  message={msg}
                  mode="group"
                  clarificationAnswered={
                    clarification
                      ? clarificationState.answeredIds.has(clarification.request_id)
                      : false
                  }
                  clarificationAnsweredValue={
                    clarification
                      ? clarificationState.answeredValues.get(clarification.request_id) ?? null
                      : null
                  }
                  onClarificationSubmit={handleClarificationSubmit}
                />
              )
            })}
            {typing &&
              (progress ? (
                <ReplyProgress info={progress} />
              ) : (
                <TypingIndicator label="正在思考…" />
              ))}
            {!typing && awaitingSlowReply && progress && <ReplyProgress info={progress} />}
            <div ref={bottomRef} />
          </div>

          {/* Error banner */}
          {sendError && (
            <div className="flex items-center justify-between bg-red-50 px-6 py-2 text-sm text-red-700">
              <span>{sendError}</span>
              <button onClick={() => setSendError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
            </div>
          )}

          {/* 运行状态条：正在运行/排队的 Waker（点击头像查看详情） */}
          <RunningWakersBar
            groupId={gid}
            onOpenWaker={setDrawerItem}
            onActivityChange={setActivityActive}
          />

          {/* 多澄清待答提示：全部回答后统一处理 */}
          {totalClarifications > 1 && clarificationState.openRequestIds.length > 0 && (
            <div className="flex items-center gap-2 border-t border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-700">
              <span>📋</span>
              <span>
                还有 {clarificationState.openRequestIds.length} 个澄清问题待回答，全部回答后将统一处理
              </span>
            </div>
          )}

          {/* Composer with @mention support */}
          <Composer onSend={handleSend} onStop={handleStop} running={running} mentionMembers={mentionMembers} />
        </section>

        {/* ── Right panel（默认隐藏，通过头部「任务 / 设置」按钮展开） ── */}
        {sidePanelOpen && (
        <aside className="w-[288px] shrink-0 border-l border-[var(--border)] flex flex-col bg-[var(--bg)]">
          {/* Tab switcher */}
          <div className="flex border-b border-[var(--border)]">
            <button
              type="button"
              onClick={() => setSideTab('tasks')}
              className={`flex-1 py-2.5 text-[13px] font-medium transition-colors ${
                sideTab === 'tasks'
                  ? 'text-[var(--primary)] border-b-2 border-[var(--primary)]'
                  : 'text-[var(--text-3)] hover:text-[var(--text-2)]'
              }`}
            >
              任务
            </button>
            <button
              type="button"
              onClick={() => setSideTab('settings')}
              className={`flex-1 py-2.5 text-[13px] font-medium transition-colors ${
                sideTab === 'settings'
                  ? 'text-[var(--primary)] border-b-2 border-[var(--primary)]'
                  : 'text-[var(--text-3)] hover:text-[var(--text-2)]'
              }`}
            >
              群设置
            </button>
          </div>

          {/* Panel content */}
          <div className="flex-1 overflow-y-auto">
            {sideTab === 'tasks' ? (
              <div className="py-1">
                <div className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide">
                    {conversations.length} 个群任务
                  </span>
                  <button
                    type="button"
                    onClick={handleNewConversation}
                    className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-[var(--primary)] hover:bg-[var(--primary-soft)] transition-colors"
                    title="新建会话"
                  >
                    ＋ 新建
                  </button>
                </div>
                {conversations.map(conv => (
                  <button
                    key={conv.id}
                    type="button"
                    onClick={() => handleSelectConversation(conv.id)}
                    className={`w-full text-left px-3 py-2.5 hover:bg-[var(--panel-2)] transition-colors border-b border-[var(--border)]/50 ${
                      conv.id === activeConversationId ? 'bg-[var(--panel-2)]' : ''
                    }`}
                  >
                    <div className="text-[13px] font-medium text-[var(--text)] truncate">{conv.title || '未命名会话'}</div>
                    <div className="mt-1">
                      <span className="text-[11px] text-[var(--text-3)]">{formatDateTime(conv.created_at)}</span>
                    </div>
                  </button>
                ))}
                {conversations.length === 0 && (
                  <div className="px-3 py-6 text-center text-[12px] text-[var(--text-3)]">暂无群任务</div>
                )}
              </div>
            ) : (
              <div className="py-2">
                {/* Members list */}
                <div className="px-3 py-1.5 text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide">
                  群成员 · {members.length} 人
                </div>
                {members.map(m => {
                  const w = wakerLookup[m.waker_id]
                  const name = w?.name ?? m.waker_id
                  const role = w?.role ?? m.role
                  return (
                    <div key={m.waker_id} className="flex items-center gap-2.5 px-3 py-2 hover:bg-[var(--panel-2)] transition-colors">
                      <Avatar name={name} size="sm" presence="online" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[13px] font-medium text-[var(--text)] truncate">{name}</span>
                          {m.role === 'leader' && (
                            <span className="text-[10px] rounded bg-[var(--primary)] text-white px-1 py-px font-semibold leading-tight">
                              LEADER
                            </span>
                          )}
                        </div>
                        <span className="text-[11.5px] text-[var(--text-3)]">{role}</span>
                      </div>
                    </div>
                  )
                })}
                {members.length === 0 && (
                  <div className="px-3 py-6 text-center text-[12px] text-[var(--text-3)]">暂无成员信息</div>
                )}
              </div>
            )}
          </div>
        </aside>
        )}
      </div>

      {/* 员工运行详情抽屉（点击状态条头像打开） */}
      <WakerActivityDrawer
        item={drawerItem}
        role={drawerItem ? wakerLookup[drawerItem.waker]?.role : undefined}
        onClose={() => setDrawerItem(null)}
      />
    </div>
  )
}
