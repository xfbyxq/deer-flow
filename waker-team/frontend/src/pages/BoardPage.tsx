import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import type { Task, Board, Waker, BoardParams } from '../types'
import Avatar from '../components/Avatar'
import StatusTag from '../components/StatusTag'
import { deerflowThreadUrl } from '../utils/deerflow'

/* ─── Helpers ─── */
const STATUS_LABELS: Record<string, string> = {
  pending: '待处理', running: '运行中', done: '已完成',
  failed: '失败', cancelled: '已取消',
}
const KIND_LABELS: Record<string, string> = {
  manual: '手动', delegate: '同步委派', async_delegate: '委派', schedule: '定时',
}

function timeAgo(dateStr?: string) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const diff = Date.now() - d.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins}分钟前`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}小时前`
  return `${Math.floor(hrs / 24)}天前`
}

/* ─── Stat Card ─── */
function StatCard({ label, count, accent }: { label: string; count: number; accent: string }) {
  return (
    <div className="rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)] p-5">
      <div className={`mb-3 flex h-9 w-9 items-center justify-center rounded-lg ${accent}`}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
        </svg>
      </div>
      <div className="text-2xl font-bold text-[var(--text)]">{count}</div>
      <div className="text-xs text-[var(--text-3)]">{label}</div>
    </div>
  )
}

/* ─── Task Detail Drawer ─── */
function TaskDrawer({
  task,
  onClose,
  onRefresh,
}: {
  task: Task
  onClose: () => void
  onRefresh: () => void
}) {
  const [actionLoading, setActionLoading] = useState('')

  const handleRetry = async () => {
    setActionLoading('retry')
    try { await api.retryTask(task.id); onRefresh() } catch (err) { console.error('Retry task failed:', err) }
    setActionLoading('')
  }
  const handleCancel = async () => {
    setActionLoading('cancel')
    try { await api.cancelTask(task.id); onRefresh() } catch (err) { console.error('Cancel task failed:', err) }
    setActionLoading('')
  }

  // 运行详情链接指向 DeerFlow 界面（读取设置页保存的 deerflow_url，缺省 2026 网关）
  const deerflowUrl = task.thread_id ? deerflowThreadUrl(task.thread_id) : null

  return (
    <div className="drawer-overlay fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="drawer-panel flex h-full w-full max-w-md flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h3 className="text-base font-semibold text-gray-900">任务详情</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-5">
          <div className="flex items-center gap-2">
            <StatusTag status={task.status} />
            <span className="rounded bg-[var(--panel-3)] px-1.5 py-0.5 text-xs text-[var(--text-2)]">
              {KIND_LABELS[task.kind] || task.kind}
            </span>
            <span className="ml-auto text-xs text-[var(--text-3)]">ID: {task.id.slice(0, 8)}</span>
          </div>

          <div>
            <div className="text-xs font-medium text-[var(--text-3)]">执行者</div>
            <div className="mt-1 flex items-center gap-2">
              <Avatar name={task.executor} size="sm" />
              <span className="text-sm font-medium text-[var(--text)]">{task.executor}</span>
            </div>
          </div>

          <div>
            <div className="text-xs font-medium text-[var(--text-3)]">输入</div>
            <div className="mt-1 rounded-lg bg-[var(--panel-2)] p-3 text-sm whitespace-pre-wrap text-[var(--text-2)]">
              {task.input_text || '—'}
            </div>
          </div>

          <div>
            <div className="text-xs font-medium text-[var(--text-3)]">输出</div>
            <div className="mt-1 rounded-lg bg-[var(--panel-2)] p-3 text-sm whitespace-pre-wrap text-[var(--text-2)]">
              {task.result_summary || '—'}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 text-xs text-[var(--text-3)]">
            <div>
              <span className="font-medium text-[var(--text-2)]">创建者</span>
              <div className="mt-0.5">{task.created_by}</div>
            </div>
            <div>
              <span className="font-medium text-[var(--text-2)]">创建时间</span>
              <div className="mt-0.5">{task.created_at ? new Date(task.created_at).toLocaleString('zh-CN') : '—'}</div>
            </div>
          </div>

          {deerflowUrl && (
            <a href={deerflowUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-sm text-[var(--primary)] hover:underline">
              查看运行详情 →
            </a>
          )}
        </div>

        <div className="border-t border-gray-100 px-6 py-4">
          <div className="flex gap-2">
            {task.status === 'failed' && (
              <button onClick={handleRetry} disabled={!!actionLoading} className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--primary-deep)] disabled:opacity-50">
                {actionLoading === 'retry' ? '处理中...' : '重试'}
              </button>
            )}
            {task.status === 'running' && (
              <button onClick={handleCancel} disabled={!!actionLoading} className="rounded-lg border border-[var(--red)] px-4 py-2 text-sm font-medium text-[var(--red)] hover:bg-[var(--red-soft)] disabled:opacity-50">
                {actionLoading === 'cancel' ? '处理中...' : '取消'}
              </button>
            )}
            <button onClick={onClose} className="ml-auto rounded-lg border border-[var(--border)] px-4 py-2 text-sm font-medium text-[var(--text-2)] hover:bg-[var(--panel-2)]">
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── Dispatch Task Dialog ─── */
function DispatchDialog({
  wakers,
  onClose,
  onCreated,
}: {
  wakers: Waker[]
  onClose: () => void
  onCreated: () => void
}) {
  const [executor, setExecutor] = useState(
    wakers.find((w) => w.enabled)?.name ?? wakers[0]?.name ?? ''
  )
  const [inputText, setInputText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!executor || !inputText.trim()) return
    setLoading(true); setError('')
    try {
      await api.createTask({ executor, input_text: inputText.trim() })
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center" style={{ background: 'rgba(15,23,42,.42)' }} onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-[var(--panel)] shadow-[var(--shadow-md)]" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-[var(--border)] px-6 py-4">
          <h2 className="text-base font-semibold text-[var(--text)]">派活</h2>
          <button onClick={onClose} className="text-[var(--text-3)] hover:text-[var(--text-2)]">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4 px-6 py-4">
          {error && <div className="rounded-md bg-[var(--red-soft)] px-3 py-2 text-sm text-[#b91c1c]">{error}</div>}
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">执行者</label>
            <select
              required
              value={executor}
              onChange={(e) => setExecutor(e.target.value)}
              className="w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
            >
              {wakers.filter(w => w.enabled).map((w) => (
                <option key={w.name} value={w.name}>{w.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">任务内容</label>
            <textarea
              required
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              rows={4}
              className="w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm outline-none focus:border-[var(--primary)]"
              placeholder="输入任务描述..."
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm font-medium text-[var(--text-2)] hover:bg-[var(--panel-2)]">取消</button>
            <button type="submit" disabled={loading} className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--primary-deep)] disabled:opacity-50">
              {loading ? '提交中...' : '派活'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ─── Board Page ─── */
export default function BoardPage() {
  const [board, setBoard] = useState<Board | null>(null)
  const [wakers, setWakers] = useState<Waker[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [dispatchOpen, setDispatchOpen] = useState(false)
  const [filters, setFilters] = useState<BoardParams>({})

  const load = useCallback(async () => {
    setError(null)
    try {
      const [b, w] = await Promise.all([api.getBoard(filters), api.listWakers()])
      setBoard(b)
      setWakers(w)
    } catch (err) { console.error('Board load failed:', err); setError('看板数据加载失败') }
    finally { setLoading(false) }
  }, [filters])

  useEffect(() => { load() }, [load])

  const refreshTask = async () => {
    if (!selectedTask) return
    try {
      const t = await api.getTask(selectedTask.id)
      setSelectedTask(t)
      load()
    } catch (err) { console.error('Refresh task failed:', err) }
  }

  if (loading) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">加载中...</div>
  if (error) return (
    <div className="flex h-full flex-col items-center justify-center gap-3">
      <p className="text-sm text-red-500">{error}</p>
      <button onClick={load} className="text-sm text-[var(--primary)] hover:underline">重试</button>
    </div>
  )
  if (!board) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">加载失败</div>

  const counts = board.counts
  const totalCount = board.total
  const runningCount = counts['running'] || 0
  const pendingCount = counts['pending'] || 0
  const doneCount = counts['done'] || 0

  return (
    <div className="h-full overflow-auto">
      <div className="px-6 py-6">
        {/* Header */}
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold text-[var(--text)]">任务看板</h1>
          <button
            onClick={() => setDispatchOpen(true)}
            className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-[var(--primary-deep)]"
          >
            + 派活
          </button>
        </div>

        {/* Stat cards */}
        <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard label="总任务 (30d)" count={totalCount} accent="bg-blue-50 text-blue-600" />
          <StatCard label="进行中" count={runningCount} accent="bg-[var(--green-soft)] text-[var(--green)]" />
          <StatCard label="需处理" count={pendingCount} accent="bg-[var(--amber-soft)] text-[var(--amber)]" />
          <StatCard label="已完成" count={doneCount} accent="bg-[var(--panel-3)] text-[var(--text-3)]" />
        </div>

        {/* Notice banner */}
        {(runningCount + pendingCount > 0) && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50/50 px-4 py-2.5 text-sm text-blue-700">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
            当前有 <b>{runningCount + pendingCount}</b> 个任务需要关注
          </div>
        )}

        {/* Filters */}
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            value={filters.status || ''}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value || undefined }))}
            className="rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm outline-none focus:border-[var(--primary)]"
          >
            <option value="">全部状态</option>
            {Object.keys(STATUS_LABELS).map((s) => (
              <option key={s} value={s}>{STATUS_LABELS[s]}</option>
            ))}
          </select>
          <select
            value={filters.waker || ''}
            onChange={(e) => setFilters((f) => ({ ...f, waker: e.target.value || undefined }))}
            className="rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm outline-none focus:border-[var(--primary)]"
          >
            <option value="">全部员工</option>
            {wakers.map((w) => <option key={w.name} value={w.name}>{w.name}</option>)}
          </select>
          <select
            value={filters.kind || ''}
            onChange={(e) => setFilters((f) => ({ ...f, kind: e.target.value || undefined }))}
            className="rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm outline-none focus:border-[var(--primary)]"
          >
            <option value="">全部类型</option>
            {Object.entries(KIND_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          {(filters.status || filters.waker || filters.kind) && (
            <button onClick={() => setFilters({})} className="text-xs text-[var(--primary)] hover:underline">
              清除筛选
            </button>
          )}
        </div>

        {/* Task table */}
        <div className="overflow-hidden rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)]">
          <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
            <div className="flex items-baseline gap-2">
              <h3 className="text-[15px] font-semibold text-[var(--text)]">全部任务</h3>
              <span className="rounded-full bg-[var(--panel-3)] px-2 py-0.5 text-[11px] font-medium text-[var(--text-3)]">
                共 {board.total} 条
              </span>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-[var(--border)] bg-[var(--panel-2)] text-xs font-medium text-[var(--text-3)]">
                <tr>
                  <th className="px-5 py-3">任务名称</th>
                  <th className="px-5 py-3">执行者</th>
                  <th className="px-5 py-3">触发来源</th>
                  <th className="px-5 py-3">状态</th>
                  <th className="px-5 py-3">最后更新</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)]">
                {board.items.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-5 py-12 text-center text-sm text-[var(--text-3)]">
                      暂无任务
                    </td>
                  </tr>
                ) : (
                  board.items.map((t) => (
                    <tr
                      key={t.id}
                      className="cursor-pointer transition-colors hover:bg-[var(--panel-2)]"
                      onClick={() => setSelectedTask(t)}
                    >
                      <td className="px-5 py-3 font-medium text-[var(--text)]">
                        {t.input_text || '无描述'}
                      </td>
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-2">
                          <Avatar name={t.executor} size="xs" />
                          <span className="text-[var(--text-2)]">{t.executor}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3 text-[var(--text-2)]">
                        {KIND_LABELS[t.kind] || t.kind}
                      </td>
                      <td className="px-5 py-3">
                        <StatusTag status={t.status} />
                      </td>
                      <td className="px-5 py-3 text-[var(--text-3)]">
                        {timeAgo(t.updated_at || t.created_at)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Task detail drawer */}
      {selectedTask && (
        <TaskDrawer
          task={selectedTask}
          onClose={() => setSelectedTask(null)}
          onRefresh={refreshTask}
        />
      )}

      {/* Dispatch dialog */}
      {dispatchOpen && (
        <DispatchDialog
          wakers={wakers}
          onClose={() => setDispatchOpen(false)}
          onCreated={() => { setDispatchOpen(false); load() }}
        />
      )}
    </div>
  )
}
