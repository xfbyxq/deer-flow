import { useState, useEffect, useCallback, Fragment } from 'react'
import { api } from '../api/client'
import type { ScheduleDef, ScheduleRun, Group } from '../types'

/* ─── Status helpers ─── */
const STATUS_LABELS: Record<string, string> = {
  active: '启用', paused: '暂停', disabled: '停用',
}
const STATUS_COLORS: Record<string, string> = {
  active: 'bg-emerald-100 text-emerald-700',
  paused: 'bg-amber-100 text-amber-700',
  disabled: 'bg-gray-100 text-gray-500',
}
const RUN_STATUS_COLORS: Record<string, string> = {
  success: 'bg-emerald-100 text-emerald-700',
  running: 'bg-blue-100 text-blue-700',
  failed: 'bg-red-100 text-red-700',
  pending: 'bg-gray-100 text-gray-600',
}

/* ─── Create Schedule Dialog ─── */
function ScheduleFormDialog({
  groups,
  onClose,
  onSaved,
}: {
  groups: Group[]
  onClose: () => void
  onSaved: () => void
}) {
  const [name, setName] = useState('')
  // 后端 group_id 为必填字段，默认选中第一个群组（默认“不指定”会 422）
  const [groupId, setGroupId] = useState(groups[0]?.id ?? '')
  const [cron, setCron] = useState('')
  const [targetType, setTargetType] = useState('task')
  const [targetId, setTargetId] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !cron.trim() || !targetId.trim()) return
    setError('')
    setSaving(true)
    try {
      await api.createSchedule({
        name: name.trim(),
        group_id: groupId,
        cron_expression: cron.trim(),
        target_type: targetType,
        target_id: targetId.trim(),
        description: description.trim() || null,
      } as Partial<ScheduleDef>)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">创建调度</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4 px-6 py-4">
          {error && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
          )}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">名称</label>
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="例如: 每日报告"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">群组</label>
            <select
              required
              value={groupId}
              onChange={(e) => setGroupId(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            >
              {groups.length === 0 && <option value="">（无群组）</option>}
              {groups.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Cron 表达式</label>
            <input
              required
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 font-mono text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="0 9 * * *"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">目标类型</label>
              <select
                value={targetType}
                onChange={(e) => setTargetType(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              >
                <option value="task">员工任务</option>
                <option value="flow">流程</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">目标 ID</label>
              <input
                required
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                placeholder={targetType === 'flow' ? 'Flow ID' : '员工名称（如 zww）'}
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">描述</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="可选"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-md border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50">取消</button>
            <button type="submit" disabled={saving} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
              {saving ? '创建中...' : '创建'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ─── Run History Row ─── */
function RunHistory({ scheduleId }: { scheduleId: string }) {
  const [runs, setRuns] = useState<ScheduleRun[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listScheduleRuns(scheduleId)
      .then(setRuns)
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [scheduleId])

  if (loading) return <div className="py-4 text-center text-sm text-gray-400">加载执行历史...</div>
  if (runs.length === 0) return <div className="py-4 text-center text-sm text-gray-400">暂无执行记录</div>

  return (
    <div className="space-y-1">
      {runs.map((r) => (
        <div key={r.id} className="flex items-center gap-2 rounded-md border border-gray-100 px-3 py-1.5">
          <span className={`rounded px-1.5 py-0.5 text-xs ${RUN_STATUS_COLORS[r.status] || 'bg-gray-100 text-gray-600'}`}>
            {r.status}
          </span>
          <span className="text-xs text-gray-500">{r.trigger_type}</span>
          {r.error_message && (
            <span className="flex-1 truncate text-xs text-red-500" title={r.error_message}>{r.error_message}</span>
          )}
          <span className="flex-1" />
          <span className="text-xs text-gray-400">
            {r.triggered_at ? new Date(r.triggered_at).toLocaleString('zh-CN') : ''}
          </span>
        </div>
      ))}
    </div>
  )
}

/* ─── Schedules Page ─── */
export default function SchedulesPage() {
  const [schedules, setSchedules] = useState<ScheduleDef[]>([])
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [s, g] = await Promise.all([api.listSchedules(), api.listGroups()])
      setSchedules(s)
      setGroups(g)
    } catch (err) { console.error('Load failed:', err); setError('服务连接失败，请检查后端是否正常运行') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handlePause = async (id: string) => {
    try { setActionError(null); await api.pauseSchedule(id); load() } catch (err) { setActionError(err instanceof Error ? err.message : '暂停失败') }
  }
  const handleResume = async (id: string) => {
    try { setActionError(null); await api.resumeSchedule(id); load() } catch (err) { setActionError(err instanceof Error ? err.message : '恢复失败') }
  }
  const handleTrigger = async (id: string) => {
    try { setActionError(null); await api.triggerSchedule(id); load() } catch (err) { setActionError(err instanceof Error ? err.message : '触发失败') }
  }
  const handleDelete = async (s: ScheduleDef) => {
    if (!confirm(`确认删除调度「${s.name}」？`)) return
    try { setActionError(null); await api.deleteSchedule(s.id); load() } catch (err) { setActionError(err instanceof Error ? err.message : '删除失败') }
  }

  const groupNameMap = new Map(groups.map((g) => [g.id, g.name]))

  return (
    <div className="h-full overflow-auto">
      <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">调度管理</h1>
        <button
          onClick={() => setFormOpen(true)}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
        >
          + 创建调度
        </button>
      </div>

      {/* Action error banner */}
      {actionError && (
        <div className="mb-4 flex items-center justify-between rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-700 border border-red-200">
          <span>{actionError}</span>
          <button onClick={() => setActionError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="py-20 text-center text-gray-400">加载中...</div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <p className="text-sm text-red-500">{error}</p>
          <button onClick={load} className="text-sm text-blue-600 hover:underline">重试</button>
        </div>
      ) : schedules.length === 0 ? (
        <div className="py-20 text-center text-gray-400">暂无调度，点击上方按钮创建</div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gray-100 bg-gray-50/60 text-xs font-medium uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">名称</th>
                <th className="hidden px-4 py-3 md:table-cell">群组</th>
                <th className="px-4 py-3">Cron</th>
                <th className="hidden px-4 py-3 lg:table-cell">目标</th>
                <th className="px-4 py-3">状态</th>
                <th className="hidden px-4 py-3 lg:table-cell">上次运行</th>
                <th className="hidden px-4 py-3 lg:table-cell">下次运行</th>
                <th className="px-4 py-3">次数</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {schedules.map((s) => (
                <Fragment key={s.id}>
                  <tr
                    className="cursor-pointer hover:bg-gray-50/50"
                    onClick={() => setExpandedId(expandedId === s.id ? null : s.id)}
                  >
                    <td className="px-4 py-3 font-medium text-gray-800">{s.name}</td>
                    <td className="hidden px-4 py-3 text-gray-500 md:table-cell">
                      {groupNameMap.get(s.group_id) || s.group_id || '—'}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{s.cron_expression}</td>
                    <td className="hidden px-4 py-3 text-gray-500 lg:table-cell">
                      <span className="rounded bg-gray-50 px-1.5 py-0.5 text-xs">{s.target_type}</span>
                      <span className="ml-1 text-xs">{s.target_id}</span>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_COLORS[s.status] || 'bg-gray-100 text-gray-600'}`}>
                        {STATUS_LABELS[s.status] || s.status}
                      </span>
                    </td>
                    <td className="hidden px-4 py-3 text-xs text-gray-500 lg:table-cell">
                      {s.last_run_at ? new Date(s.last_run_at).toLocaleString('zh-CN') : '—'}
                    </td>
                    <td className="hidden px-4 py-3 text-xs text-gray-500 lg:table-cell">
                      {s.next_run_at ? new Date(s.next_run_at).toLocaleString('zh-CN') : '—'}
                    </td>
                    <td className="px-4 py-3 text-gray-600">{s.run_count}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                        {s.status === 'active' ? (
                          <button onClick={() => handlePause(s.id)} className="rounded px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50">暂停</button>
                        ) : s.status === 'paused' ? (
                          <button onClick={() => handleResume(s.id)} className="rounded px-2 py-1 text-xs font-medium text-emerald-600 hover:bg-emerald-50">恢复</button>
                        ) : null}
                        <button onClick={() => handleTrigger(s.id)} className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50">触发</button>
                        <button onClick={() => handleDelete(s)} className="rounded px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50">删除</button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === s.id && (
                    <tr>
                      <td colSpan={9} className="bg-gray-50/50 px-4 py-3">
                        <div className="text-xs font-medium text-gray-500 mb-2">执行历史</div>
                        <RunHistory scheduleId={s.id} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {formOpen && (
        <ScheduleFormDialog
          groups={groups}
          onClose={() => setFormOpen(false)}
          onSaved={() => { setFormOpen(false); load() }}
        />
      )}
      </div>
    </div>
  )
}
