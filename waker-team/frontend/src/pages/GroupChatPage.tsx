import { useState, useEffect, useRef, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Group, GroupMember, ChatMessage, Waker } from '../types'
import Avatar from '../components/Avatar'
import StatusTag from '../components/StatusTag'
import MessageBubble from '../components/chat/MessageBubble'
import TypingIndicator from '../components/chat/TypingIndicator'
import Composer from '../components/chat/Composer'
import type { MentionMember } from '../components/chat/Composer'

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
  let text: string | undefined
  let parts: ChatMessage['parts']

  if (msg.content_json) {
    if (typeof msg.content_json === 'string') {
      try {
        const parsed = JSON.parse(msg.content_json)
        if (Array.isArray(parsed)) {
          parts = parsed
        } else if (parsed.text) {
          text = parsed.text
        } else {
          text = msg.content_json
        }
      } catch {
        text = msg.content_json
      }
    } else {
      text = JSON.stringify(msg.content_json)
    }
  }

  const wakerInfo = msg.waker_id ? wakerLookup[msg.waker_id] : undefined
  return {
    id: msg.id,
    role: msg.role as ChatMessage['role'],
    waker: msg.role === 'waker' && wakerInfo ? wakerInfo : undefined,
    time,
    text,
    parts,
  }
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
      // 超时保护：5 分钟后不再等待（后端回复超时上限 10 分钟）
      if (replyWaitStartRef.current && Date.now() - replyWaitStartRef.current > 5 * 60_000) {
        setTyping(false)
        return
      }
      try {
        const msgs = await api.getConversationMessages(activeConversationId)
        setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
        const last = msgs[msgs.length - 1]
        if (last && last.role === 'waker') setTyping(false)
      } catch {
        /* 保持轮询，下次重试 */
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [typing, activeConversationId, wakerLookup])

  // Send message
  const handleSend = useCallback(async (text: string) => {
    setSendError(null)

    // 1. Add user message optimistically
    const now = new Date()
    const timeStr = `${now.getHours()}:${String(now.getMinutes()).padStart(2, '0')}`
    const userMsg: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      time: timeStr,
      text,
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
      setTyping(true)
      await api.sendConversationMessage(convId, {
        role: 'user',
        content_json: { text },
      })

      // 4. Reload messages to get the full conversation including any waker response
      const msgs = await api.getConversationMessages(convId)
      setMessages(msgs.map(m => toChatMessage(m, wakerLookup)))
      // 若回复已到达（极快响应）则结束等待，否则保持 typing 由轮询接管
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'waker') setTyping(false)
    } catch (err) {
      setSendError(err instanceof Error ? err.message : '发送消息失败')
      setTyping(false)
      // Keep the optimistic message in place
    }
  }, [activeConversationId, wakerLookup, gid])

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
        {/* 任务/设置面板开关（面板默认隐藏） */}
        <button
          type="button"
          onClick={() => setSidePanelOpen(v => !v)}
          className={`ml-auto flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
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
            {messages.map(msg => (
              <MessageBubble key={msg.id} message={msg} mode="group" />
            ))}
            {typing && <TypingIndicator label="正在思考…" />}
            <div ref={bottomRef} />
          </div>

          {/* Error banner */}
          {sendError && (
            <div className="flex items-center justify-between bg-red-50 px-6 py-2 text-sm text-red-700">
              <span>{sendError}</span>
              <button onClick={() => setSendError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
            </div>
          )}

          {/* Composer with @mention support */}
          <Composer onSend={handleSend} mentionMembers={mentionMembers} />
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
                <div className="px-3 py-1.5 text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide">
                  {conversations.length} 个群任务
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
                    <div className="flex items-center gap-1.5 mt-1">
                      <StatusTag
                        status={conv.status === 'active' ? 'active' : conv.status === 'closed' ? 'done' : 'running'}
                        label={conv.status === 'active' ? '进行中' : conv.status === 'closed' ? '已结束' : conv.status}
                      />
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
    </div>
  )
}
