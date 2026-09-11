import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Waker, ChatMessage, AutoTask, ClarificationRequest } from '../types'
import Avatar from '../components/Avatar'
import StatusTag from '../components/StatusTag'
import MessageBubble from '../components/chat/MessageBubble'
import TypingIndicator from '../components/chat/TypingIndicator'
import ReplyProgress, { type ReplyProgressInfo } from '../components/chat/ReplyProgress'
import Composer from '../components/chat/Composer'
import { parseMessageContent } from '../utils/messageContent'
import {
  buildClarificationAnswerDisplay,
  buildClarificationAnswerText,
  computeClarificationState,
  type ClarificationAnswer,
} from '../utils/clarification'

type SideTab = 'tasks' | 'auto'

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
function toChatMessage(msg: { id: string; role: string; waker_id: string | null; content_json: string | null; created_at: string | null }, wakerInfo?: { name: string; role: string }): ChatMessage {
  const time = formatTime(msg.created_at)
  const { text, parts, meta } = parseMessageContent(msg.content_json)

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

export default function DirectChatPage() {
  const { wakerName } = useParams<{ wakerName: string }>()
  const decodedName = decodeURIComponent(wakerName ?? '')

  // Data state
  const [waker, setWaker] = useState<Waker | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversations, setConversations] = useState<Array<{ id: string; title: string | null; status: string; created_at: string | null }>>([])
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null)
  const [autoTasks] = useState<AutoTask[]>([]) // TODO: migrate to real API when available
  const [sideTab, setSideTab] = useState<SideTab>('tasks')
  const [typing, setTyping] = useState(false)
  // 等待回复期间的动作/工具步骤进度（3s 轮询快照）
  const [progress, setProgress] = useState<ReplyProgressInfo | null>(null)
  // 长任务慢速补拉：typing 指示已停但回复可能稍后到达（后端等待上限 30 分钟）
  const [awaitingSlowReply, setAwaitingSlowReply] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)

  // Refs
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Display metadata from real waker data
  const displayName = waker?.name ?? decodedName
  const displayRole = waker?.role ?? ''

  // Auto-scroll to bottom
  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Load waker data and conversations on mount
  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        setError(null)
        const [w, convs] = await Promise.all([
          api.getWaker(decodedName).catch(() => null),
          api.listWakerConversations(decodedName).catch(() => []),
        ])
        if (cancelled) return
        setWaker(w)
        setConversations(convs)

        // Load messages from the first conversation if available
        if (convs.length > 0) {
          const firstConv = convs[0]
          setActiveConversationId(firstConv.id)
          try {
            const msgs = await api.getConversationMessages(firstConv.id)
            if (!cancelled) {
              const wakerInfo = w ? { name: w.name, role: w.role ?? '' } : undefined
              setMessages(msgs.map(m => toChatMessage(m, wakerInfo)))
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
  }, [decodedName])

  // Auto-scroll when messages change
  useEffect(() => {
    scrollToBottom()
  }, [messages, typing, scrollToBottom])

  // 回复到达后刷新会话列表（标题由后端从 DeerFlow 自动生成写回）
  const refreshConversations = useCallback(async () => {
    try {
      const convs = await api.listWakerConversations(decodedName)
      setConversations(convs)
    } catch {
      /* 忽略，下次再刷 */
    }
  }, [decodedName])

  // 新建会话：立即创建并切换过去（发送后自动生成标题）
  const handleNewConversation = useCallback(async () => {
    try {
      const newConv = await api.createWakerConversation(decodedName)
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
  }, [decodedName])

  // 等待 waker 回复期间轮询会话消息（回复由后端 DeerFlow run 异步写回）
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
          // CONTRACT-LIMIT：轮询热路径只拉最新消息（limit=50），首屏/切换会话仍用 200
          api.getConversationMessages(activeConversationId, 50),
          api.getConversationProgress(activeConversationId).catch(() => null),
        ])
        setProgress(prog?.active ? prog : null)
        const wakerInfo = waker ? { name: waker.name, role: waker.role ?? '' } : undefined
        setMessages(msgs.map((m) => toChatMessage(m, wakerInfo)))
        const last = msgs[msgs.length - 1]
        if (last && (last.role === 'waker' || last.role === 'system')) {
          setTyping(false)
          setProgress(null)
          refreshConversations()
        }
      } catch {
        /* 保持轮询，下次重试 */
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [typing, activeConversationId, waker, refreshConversations])

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
          // CONTRACT-LIMIT：轮询热路径只拉最新消息（limit=50），首屏/切换会话仍用 200
          api.getConversationMessages(activeConversationId, 50),
          api.getConversationProgress(activeConversationId).catch(() => null),
        ])
        setProgress(prog?.active ? prog : null)
        const wakerInfo = waker ? { name: waker.name, role: waker.role ?? '' } : undefined
        setMessages(msgs.map((m) => toChatMessage(m, wakerInfo)))
        const last = msgs[msgs.length - 1]
        if (last && (last.role === 'waker' || last.role === 'system')) {
          setAwaitingSlowReply(false)
          setProgress(null)
          refreshConversations()
        }
      } catch {
        /* 继续补拉 */
      }
    }, 15000)
    return () => clearInterval(timer)
  }, [awaitingSlowReply, activeConversationId, waker, refreshConversations])

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
        const newConv = await api.createWakerConversation(decodedName)
        convId = newConv.id
        setActiveConversationId(convId)
        setConversations(prev => [{ id: newConv.id, title: newConv.title, status: newConv.status, created_at: newConv.created_at }, ...prev])
      }
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '创建会话失败')
      setTyping(false)
      return
    }

    // 3. Send to backend（后端将在 DeerFlow 上发起 waker run，回复异步写回）
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
      const wakerInfo = waker ? { name: waker.name, role: waker.role ?? '' } : undefined
      setMessages(msgs.map(m => toChatMessage(m, wakerInfo)))
      // 若回复已到达（极快响应）则结束等待，否则保持 typing 由轮询接管
      const last = msgs[msgs.length - 1]
      if (!deferReply && last && (last.role === 'waker' || last.role === 'system')) {
        setTyping(false)
        refreshConversations()
      }
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '发送消息失败')
      setTyping(false)
      // Keep the optimistic message in place
    }
  }, [activeConversationId, waker, decodedName, refreshConversations])

  // Send message handler（普通输入）
  const handleSend = useCallback((text: string) => {
    void sendUserMessage(text)
  }, [sendUserMessage])

  // 澄清回答提交：构造与 DeerFlow 主 UI 一致的回答文案并发送。
  // 多澄清聚合：还有其他未答卡片时延迟触发（仅入库），最后一个回答才触发一次处理。
  const clarificationState = useMemo(() => computeClarificationState(messages), [messages])

  // 多澄清待答提示：全部回答后统一处理（按卡片数统计，同一条消息可能含多张卡）
  const totalClarifications = useMemo(
    () =>
      messages.reduce((n, m) => {
        if (m.role !== 'waker') return n
        const cards = m.meta?.clarifications ?? (m.meta?.clarification ? [m.meta.clarification] : [])
        return n + cards.length
      }, 0),
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
      const wakerInfo = waker ? { name: waker.name, role: waker.role ?? '' } : undefined
      setMessages(msgs.map(m => toChatMessage(m, wakerInfo)))
    } catch {
      setMessages([])
    }
  }, [waker])

  // 运行中：等待回复或长任务慢速补拉期间，发送按钮显示为「停止」态
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
      const wakerInfo = waker ? { name: waker.name, role: waker.role ?? '' } : undefined
      setMessages(msgs.map(m => toChatMessage(m, wakerInfo)))
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '停止失败')
    } finally {
      stoppingRef.current = false
    }
  }, [activeConversationId, waker])

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

  return (
    <div className="flex h-full flex-col">
      {/* ── Header bar ── */}
      <header className="flex items-center gap-3 border-b border-[var(--border)] bg-[var(--panel)] px-4 py-2.5">
        <Avatar name={displayName} size="sm" presence="online" />
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-semibold text-[var(--text)]">{displayName}</span>
            {displayRole && (
              <span className="text-[11px] rounded-full bg-[var(--panel-3)] px-1.5 py-0.5 font-medium text-[var(--text-2)]">
                {displayRole}
              </span>
            )}
            <span className="inline-flex items-center gap-1 text-[11px] rounded-full bg-violet-50 px-2 py-0.5 font-semibold text-violet-600">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-3 h-3"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
              1v1 私聊
            </span>
          </div>
          <span className="text-[11.5px] text-[var(--text-3)]">
            {[displayRole, waker?.enabled ? '在线' : '离线'].filter(Boolean).join(' · ')}
          </span>
        </div>
      </header>

      {/* ── Body ── */}
      <div className="flex flex-1 min-h-0">
        {/* ── Left panel ── */}
        <aside className="w-[288px] shrink-0 border-r border-[var(--border)] flex flex-col bg-[var(--bg)]">
          {/* Waker card */}
          <div className="p-4 border-b border-[var(--border)]">
            <div className="flex items-center gap-3">
              <Avatar name={displayName} size="default" />
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-semibold text-[var(--text)] truncate">{displayName}</div>
                <div className="text-[12px] text-[var(--text-3)] truncate">{displayRole}</div>
              </div>
            </div>
            {waker?.description && (
              <p className="mt-2 text-[12px] text-[var(--text-2)] leading-relaxed line-clamp-2">{waker.description}</p>
            )}
          </div>

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
              对话任务
            </button>
            <button
              type="button"
              onClick={() => setSideTab('auto')}
              className={`flex-1 py-2.5 text-[13px] font-medium transition-colors ${
                sideTab === 'auto'
                  ? 'text-[var(--primary)] border-b-2 border-[var(--primary)]'
                  : 'text-[var(--text-3)] hover:text-[var(--text-2)]'
              }`}
            >
              自动任务
            </button>
          </div>

          {/* Scrollable list */}
          <div className="flex-1 overflow-y-auto">
            {sideTab === 'tasks' ? (
              <div className="py-1">
                <div className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide">
                    {conversations.length} 个会话
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
                  <div className="px-3 py-6 text-center text-[12px] text-[var(--text-3)]">暂无对话记录</div>
                )}
              </div>
            ) : (
              <div className="py-1">
                <div className="px-3 py-1.5 text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide">
                  {autoTasks.length} 个自动任务
                </div>
                {autoTasks.map(at => (
                  <div key={at.id} className="px-3 py-2.5 border-b border-[var(--border)]/50">
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-medium text-[var(--text)]">{at.name}</span>
                      <StatusTag status={at.enabled ? 'enabled' : 'disabled_waker'} label={at.enabled ? '启用' : '停用'} />
                    </div>
                    <div className="text-[11.5px] text-[var(--text-3)] mt-1">
                      ⏰ {at.cron}{at.lastRun ? ` · ${at.lastRun}` : ''}
                    </div>
                  </div>
                ))}
                {autoTasks.length === 0 && (
                  <div className="px-3 py-6 text-center text-[12px] text-[var(--text-3)]">暂无自动任务</div>
                )}
              </div>
            )}
          </div>
        </aside>

        {/* ── Center area ── */}
        <section className="flex-1 flex flex-col min-w-0 bg-[var(--bg)]">
          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
            {messages.length === 0 && !typing && (
              <div className="flex h-full items-center justify-center">
                <div className="text-center">
                  <div className="mb-2 text-3xl opacity-40">💬</div>
                  <p className="text-[13px] text-[var(--text-3)]">开始和 {displayName} 的对话</p>
                </div>
              </div>
            )}
            {messages.map(msg => (
              <MessageBubble
                key={msg.id}
                message={msg}
                mode="direct"
                clarificationAnsweredIds={clarificationState.answeredIds}
                clarificationAnsweredValues={clarificationState.answeredValues}
                onClarificationSubmit={handleClarificationSubmit}
              />
            ))}
            {typing &&
              (progress ? (
                <ReplyProgress info={progress} />
              ) : (
                <TypingIndicator label={`${displayName} 正在思考…`} />
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

          {/* 多澄清待答提示：全部回答后统一处理 */}
          {totalClarifications > 1 && clarificationState.openRequestIds.length > 0 && (
            <div className="flex items-center gap-2 border-t border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-700">
              <span>📋</span>
              <span>
                还有 {clarificationState.openRequestIds.length} 个澄清问题待回答，全部回答后将统一处理
              </span>
            </div>
          )}

          {/* Composer */}
          <Composer onSend={handleSend} onStop={handleStop} running={running} />
        </section>
      </div>
    </div>
  )
}
