import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'

import { api } from '../api/client'
import type { FlowDef, FlowRunTimeline, NodeTimelineItem } from '../types'
import FlowRunTimelineView from '../components/flow/FlowRunTimeline'

/* ─── Status display helpers ─── */
const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '待执行', running: '运行中', done: '已完成',
  failed: '失败', cancelled: '已取消', paused: '已暂停',
  waiting_review: '待审核',
}
const RUN_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-500',
  paused: 'bg-amber-100 text-amber-700',
  waiting_review: 'bg-orange-100 text-orange-700',
}
const NODE_STATUS_LABELS: Record<string, string> = {
  pending: '待执行', running: '运行中', done: '已完成',
  failed: '失败', waiting_review: '待审核', skipped: '已跳过',
}
const NODE_STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  waiting_review: 'bg-orange-100 text-orange-700',
  skipped: 'bg-gray-100 text-gray-400',
}
const TRIGGER_LABELS: Record<string, string> = {
  manual: '手动触发', schedule: '定时触发', webhook: 'Webhook', api: 'API 调用',
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  const hrs = Math.floor(mins / 60)
  return `${hrs}h ${mins % 60}m`
}

/* ─── Review Action Panel ─── */
function ReviewPanel({
  node,
  runId,
  onDone,
}: {
  node: NodeTimelineItem
  runId: string
  onDone: () => void
}) {
  const [comment, setComment] = useState('')
  const [loading, setLoading] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState('')

  const handleAction = async (action: 'approve' | 'reject') => {
    if (action === 'reject' && !comment.trim()) {
      setError('打回时 comment 为必填')
      return
    }
    setLoading(action)
    setError('')
    try {
      await api.reviewFlowRunNode(runId, { action, node_key: node.node_key, comment: comment.trim() || undefined })
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="rounded-lg border border-orange-200 bg-orange-50 p-4 space-y-3">
      <h4 className="text-sm font-semibold text-orange-800 flex items-center gap-1.5">
        <span>👤</span> 人工确认
      </h4>
      {error && (
        <div className="rounded-md bg-red-50 border border-red-200 px-3 py-2 text-xs text-red-700">{error}</div>
      )}
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">备注 {node.type === 'human_review' && <span className="text-red-500">*</span>}</label>
        <textarea
          value={comment}
          onChange={(e) => { setComment(e.target.value); setError('') }}
          rows={3}
          className="w-full resize-y rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-orange-400 focus:ring-2 focus:ring-orange-100"
          placeholder="输入审核意见或备注..."
        />
      </div>
      <div className="flex gap-2">
        <button
          onClick={() => handleAction('approve')}
          disabled={loading !== null}
          className="flex-1 rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50 transition-colors"
        >
          {loading === 'approve' ? '处理中...' : '✓ 通过'}
        </button>
        <button
          onClick={() => handleAction('reject')}
          disabled={loading !== null}
          className="flex-1 rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
        >
          {loading === 'reject' ? '处理中...' : '✗ 打回'}
        </button>
      </div>
    </div>
  )
}

/* ─── Node Detail Sidebar ─── */
function NodeDetailPanel({
  node,
  runId,
  onClose,
  onRefresh,
}: {
  node: NodeTimelineItem
  runId: string
  onClose: () => void
  onRefresh: () => void
}) {
  const [rerunLoading, setRerunLoading] = useState(false)

  const handleRerun = async () => {
    setRerunLoading(true)
    try {
      await api.rerunFlowRunNode(runId, node.node_key)
      onRefresh()
    } catch { /* ignore */ }
    setRerunLoading(false)
  }

  const canRerun = ['done', 'failed'].includes(node.status)
  const needsReview = node.type === 'human_review' && node.status === 'waiting_review'

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-gray-100 px-5 py-4">
        <div className="min-w-0">
          <h3 className="truncate text-base font-semibold text-gray-900">{node.node_key}</h3>
          <div className="mt-1 flex items-center gap-2">
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${NODE_STATUS_COLORS[node.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {NODE_STATUS_LABELS[node.status] ?? node.status}
            </span>
            <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">{node.type}</span>
          </div>
        </div>
        <button onClick={onClose} className="shrink-0 text-gray-400 hover:text-gray-600 text-sm">✕</button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {/* Waker */}
        {node.waker && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">执行 Waker</div>
            <div className="mt-1 text-sm font-medium text-gray-800">{node.waker}</div>
          </div>
        )}

        {/* Timing */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">开始时间</div>
            <div className="mt-1 text-xs text-gray-700">
              {node.started_at ? new Date(node.started_at).toLocaleString('zh-CN') : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">完成时间</div>
            <div className="mt-1 text-xs text-gray-700">
              {node.completed_at ? new Date(node.completed_at).toLocaleString('zh-CN') : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">耗时</div>
            <div className="mt-1 text-sm font-semibold text-gray-800">{formatDuration(node.duration_seconds)}</div>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">重试次数</div>
            <div className="mt-1 text-sm font-semibold text-gray-800">{node.retry_count}</div>
          </div>
        </div>

        {/* Error */}
        {node.error_message && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-red-400">错误信息</div>
            <div className="mt-1 rounded-md bg-red-50 border border-red-100 p-3 text-sm text-red-700 whitespace-pre-wrap break-words">
              {node.error_message}
            </div>
          </div>
        )}

        {/* Input */}
        {node.input && Object.keys(node.input).length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">输入</div>
            <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700 font-mono leading-relaxed">
              {JSON.stringify(node.input, null, 2)}
            </pre>
          </div>
        )}

        {/* Output */}
        {node.output && Object.keys(node.output).length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">输出</div>
            <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-gray-50 p-3 text-xs text-gray-700 font-mono leading-relaxed">
              {JSON.stringify(node.output, null, 2)}
            </pre>
          </div>
        )}

        {/* Outcome */}
        {node.outcome && Object.keys(node.outcome).length > 0 && (
          <div>
            <div className="text-xs font-medium uppercase tracking-wide text-gray-400">结果</div>
            <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-blue-50 p-3 text-xs text-blue-800 font-mono leading-relaxed">
              {JSON.stringify(node.outcome, null, 2)}
            </pre>
          </div>
        )}

        {/* Human review panel */}
        {needsReview && (
          <ReviewPanel node={node} runId={runId} onDone={onRefresh} />
        )}

        {/* Rerun button */}
        {canRerun && !needsReview && (
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
            <p className="mb-3 text-xs text-gray-500">
              {node.status === 'failed' ? '节点执行失败，可以重跑此节点。' : '节点已完成，可以重新执行。'}
            </p>
            <button
              onClick={handleRerun}
              disabled={rerunLoading}
              className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {rerunLoading ? '重跑中...' : '🔄 重跑此节点'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/* ─── Flow Run Info Card ─── */
function FlowRunInfoCard({
  timeline,
  flowDef,
}: {
  timeline: FlowRunTimeline
  flowDef: FlowDef | null
}) {
  const run = timeline.flow_run
  const doneCount = timeline.nodes.filter((n) => n.status === 'done').length
  const failedCount = timeline.nodes.filter((n) => n.status === 'failed').length
  const totalCount = timeline.nodes.length

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <Link
              to={`/flows/${run.flow_def_id}`}
              className="text-lg font-bold text-blue-600 hover:underline truncate"
            >
              {flowDef?.name ?? run.flow_def_id.slice(0, 8)}
            </Link>
            <span className={`rounded-md px-2.5 py-1 text-xs font-semibold ${RUN_STATUS_COLORS[run.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {RUN_STATUS_LABELS[run.status] ?? run.status}
            </span>
          </div>
          <p className="mt-1 text-xs text-gray-400">
            Run ID: {run.id.slice(0, 12)} · {TRIGGER_LABELS[run.trigger_type] ?? run.trigger_type}
            {run.created_by && ` · ${run.created_by}`}
          </p>
        </div>
        <div className="flex items-center gap-4 text-xs text-gray-500 shrink-0">
          <div className="text-center">
            <div className="text-lg font-bold text-gray-800">{formatDuration(timeline.total_duration_seconds)}</div>
            <div>总耗时</div>
          </div>
          <div className="h-8 w-px bg-gray-200" />
          <div className="text-center">
            <div className="text-lg font-bold text-gray-800">{doneCount}/{totalCount}</div>
            <div>已完成</div>
          </div>
          {failedCount > 0 && (
            <>
              <div className="h-8 w-px bg-gray-200" />
              <div className="text-center">
                <div className="text-lg font-bold text-red-600">{failedCount}</div>
                <div>失败</div>
              </div>
            </>
          )}
          {timeline.current_node && (
            <>
              <div className="h-8 w-px bg-gray-200" />
              <div className="text-center">
                <div className="text-sm font-semibold text-blue-600 truncate max-w-[120px]">{timeline.current_node}</div>
                <div>当前节点</div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Time range */}
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-gray-400 border-t border-gray-100 pt-3">
        <span>启动: {new Date(run.started_at).toLocaleString('zh-CN')}</span>
        {run.completed_at && <span>完成: {new Date(run.completed_at).toLocaleString('zh-CN')}</span>}
        {run.failure_reason && (
          <span className="text-red-500">失败原因: {run.failure_reason}</span>
        )}
      </div>
    </div>
  )
}

/* ─── Node list (compact) ─── */
function NodeList({
  nodes,
  selectedKey,
  onSelect,
}: {
  nodes: NodeTimelineItem[]
  selectedKey: string | null
  onSelect: (key: string) => void
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      <div className="border-b border-gray-100 bg-gray-50 px-4 py-2.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">节点列表</h3>
      </div>
      <div className="max-h-64 overflow-y-auto divide-y divide-gray-50">
        {nodes.map((n) => (
          <button
            key={n.node_key}
            onClick={() => onSelect(n.node_key)}
            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-gray-50 ${
              selectedKey === n.node_key ? 'bg-blue-50 border-l-2 border-blue-500' : ''
            }`}
          >
            <span className={`h-2 w-2 rounded-full shrink-0 ${
              n.status === 'pending' ? 'bg-gray-300' :
              n.status === 'running' ? 'bg-blue-500 animate-pulse' :
              n.status === 'done' ? 'bg-emerald-500' :
              n.status === 'failed' ? 'bg-red-500' :
              n.status === 'waiting_review' ? 'bg-orange-500' : 'bg-gray-300'
            }`} />
            <span className="text-sm text-gray-800 truncate flex-1">{n.node_key}</span>
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${NODE_STATUS_COLORS[n.status] ?? 'bg-gray-100 text-gray-500'}`}>
              {NODE_STATUS_LABELS[n.status] ?? n.status}
            </span>
            {n.duration_seconds != null && (
              <span className="shrink-0 text-[10px] text-gray-400 tabular-nums">{n.duration_seconds.toFixed(1)}s</span>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

/* ─── Main Page ─── */
export default function FlowRunPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [timeline, setTimeline] = useState<FlowRunTimeline | null>(null)
  const [flowDef, setFlowDef] = useState<FlowDef | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const tl = await api.getFlowRunTimeline(id)
      setTimeline(tl)
      // Also load the flow definition for name + node deps
      const fd = await api.getFlow(tl.flow_run.flow_def_id)
      setFlowDef(fd)
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { load() }, [load])

  const selectedNode = useMemo(
    () => timeline?.nodes.find((n) => n.node_key === selectedNodeKey) ?? null,
    [timeline, selectedNodeKey],
  )

  const defNodeDeps = useMemo(
    () => flowDef?.definition_json?.nodes?.map((n) => ({ key: n.key, depends_on: n.depends_on })) ?? [],
    [flowDef],
  )

  if (loading) {
    return <div className="py-20 text-center text-gray-400">加载中...</div>
  }

  if (error || !timeline) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-20 text-center">
        <p className="text-gray-400 mb-4">{error || 'Flow Run 不存在'}</p>
        <button
          onClick={() => navigate('/flows')}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
        >
          返回 Flow 列表
        </button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-2.5 shrink-0">
        <button
          onClick={() => navigate('/flows')}
          className="text-gray-400 hover:text-gray-700 transition-colors text-sm"
        >
          ← Flow 列表
        </button>
        <span className="text-gray-300">/</span>
        <span className="text-sm font-medium text-gray-700 truncate">
          {flowDef?.name ?? timeline.flow_run.flow_def_id.slice(0, 8)} — 运行详情
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={load}
            className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 transition-colors"
          >
            🔄 刷新
          </button>
          {['pending', 'running', 'waiting_review'].includes(timeline.flow_run.status) && (
            <button
              onClick={async () => {
                try {
                  await api.pauseFlowRun(timeline.flow_run.id)
                  load()
                } catch { /* ignore */ }
              }}
              className="rounded-md border border-amber-200 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-50 transition-colors"
            >
              ⏸ 暂停
            </button>
          )}
          {timeline.flow_run.status === 'paused' && (
            <button
              onClick={async () => {
                try {
                  await api.resumeFlowRun(timeline.flow_run.id)
                  load()
                } catch { /* ignore */ }
              }}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 transition-colors"
            >
              ▶ 恢复
            </button>
          )}
          {['pending', 'running', 'paused', 'waiting_review'].includes(timeline.flow_run.status) && (
            <button
              onClick={async () => {
                if (!confirm('确认取消此运行？')) return
                try {
                  await api.cancelFlowRun(timeline.flow_run.id)
                  load()
                } catch { /* ignore */ }
              }}
              className="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors"
            >
              ✗ 取消运行
            </button>
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Timeline + Info */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Info card */}
          <div className="shrink-0 border-b border-gray-100 bg-gray-50 p-4">
            <FlowRunInfoCard timeline={timeline} flowDef={flowDef} />
          </div>

          {/* React Flow timeline */}
          <div className="flex-1 relative bg-white">
            <FlowRunTimelineView
              key={timeline.flow_run.id}
              timeline={timeline}
              defNodes={defNodeDeps}
              selectedNodeKey={selectedNodeKey}
              onNodeClick={setSelectedNodeKey}
            />
          </div>

          {/* Bottom: node list */}
          <div className="shrink-0 border-t border-gray-100 bg-gray-50 p-4">
            <NodeList
              nodes={timeline.nodes}
              selectedKey={selectedNodeKey}
              onSelect={setSelectedNodeKey}
            />
          </div>
        </div>

        {/* Right: Detail sidebar */}
        {selectedNode && (
          <div className="w-80 shrink-0 border-l border-gray-200 bg-white overflow-hidden">
            <NodeDetailPanel
              node={selectedNode}
              runId={timeline.flow_run.id}
              onClose={() => setSelectedNodeKey(null)}
              onRefresh={load}
            />
          </div>
        )}
      </div>
    </div>
  )
}
