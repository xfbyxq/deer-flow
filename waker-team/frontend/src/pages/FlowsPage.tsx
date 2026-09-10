import { useState, useEffect, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type { FlowDef, Group, FlowDefinitionV1, FlowRun } from '../types'

const STATUS_COLORS: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-600',
  active: 'bg-emerald-100 text-emerald-700',
  archived: 'bg-amber-100 text-amber-700',
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
const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '待执行', running: '运行中', done: '已完成',
  failed: '失败', cancelled: '已取消', paused: '已暂停',
  waiting_review: '待审核',
}

/* ─── Create / Edit Flow Dialog ─── */
function FlowFormDialog({
  editing,
  groups,
  onClose,
  onSaved,
}: {
  editing: FlowDef | null
  groups: Group[]
  onClose: () => void
  onSaved: (id: string) => void
}) {
  const isEdit = editing !== null
  const [name, setName] = useState(editing?.name ?? '')
  const [groupId, setGroupId] = useState(editing?.group_id ?? (groups[0]?.id ?? ''))
  const [description, setDescription] = useState(editing?.description ?? '')
  const [jsonText, setJsonText] = useState(
    editing ? JSON.stringify(editing.definition_json, null, 2) : JSON.stringify(
      { version: 1, nodes: [], settings: { max_concurrent_nodes: 3, on_failure: 'pause' } },
      null,
      2,
    )
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !groupId) return
    setError('')
    setSaving(true)
    try {
      let defJson: FlowDefinitionV1
      try {
        defJson = JSON.parse(jsonText)
      } catch {
        setError('JSON 格式错误')
        setSaving(false)
        return
      }
      const payload: Partial<FlowDef> = {
        name: name.trim(),
        group_id: groupId,
        description: description.trim() || null,
        definition_json: defJson,
      }
      if (isEdit && editing) {
        await api.updateFlow(editing.id, payload)
        onSaved(editing.id)
      } else {
        const created = await api.createFlow(payload)
        onSaved(created.id)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="drawer-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4"
      onClick={onClose}
    >
      <div
        className="modal-panel w-full max-w-2xl rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEdit ? '编辑 Flow' : '创建 Flow'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4 px-6 py-4">
          {error && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700">名称</label>
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                placeholder="例如: 日报审批流程"
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
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>{g.name}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">描述</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="可选描述"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Definition JSON</label>
            <textarea
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              rows={10}
              className="w-full resize-y rounded-md border border-gray-200 px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder='{"version": 1, "nodes": [...]}'
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-md border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50">取消</button>
            <button type="submit" disabled={saving} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ─── Flows Page ─── */
export default function FlowsPage() {
  const [flows, setFlows] = useState<FlowDef[]>([])
  const [groups, setGroups] = useState<Group[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<FlowDef | null>(null)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setError(null)
    try {
      const [f, g] = await Promise.all([api.listFlows(), api.listGroups()])
      setFlows(f)
      setGroups(g)
    } catch (err) { console.error('Load failed:', err); setError('服务连接失败，请检查后端是否正常运行') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = async (f: FlowDef) => {
    if (!confirm(`确认删除 Flow「${f.name}」？`)) return
    try {
      await api.deleteFlow(f.id)
      load()
    } catch { /* ignore */ }
  }

  const openCreate = () => { setEditing(null); setFormOpen(true) }
  const openEdit = (f: FlowDef) => { setEditing(f); setFormOpen(true) }
  const handleSaved = (id: string) => { setFormOpen(false); load(); navigate(`/flows/${id}`) }

  const groupMap = new Map(groups.map((g) => [g.id, g.name]))

  // Flow runs drawer state
  const [runsFlowId, setRunsFlowId] = useState<string | null>(null)
  const [runs, setRuns] = useState<FlowRun[]>([])
  const [runsLoading, setRunsLoading] = useState(false)

  const openRuns = useCallback(async (flowId: string) => {
    setRunsFlowId(flowId)
    setRunsLoading(true)
    try {
      const r = await api.listFlowRuns({ flow_id: flowId })
      setRuns(r)
    } catch { /* ignore */ }
    setRunsLoading(false)
  }, [])

  return (
    <div className="h-full overflow-auto">
      <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Flow 编排</h1>
        <button
          onClick={openCreate}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
        >
          + 创建 Flow
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <div className="py-20 text-center text-gray-400">加载中...</div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <p className="text-sm text-red-500">{error}</p>
          <button onClick={load} className="text-sm text-blue-600 hover:underline">重试</button>
        </div>
      ) : flows.length === 0 ? (
        <div className="py-20 text-center text-gray-400">暂无 Flow，点击上方按钮创建</div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gray-100 bg-gray-50/60 text-xs font-medium uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">名称</th>
                <th className="hidden px-4 py-3 md:table-cell">群组</th>
                <th className="px-4 py-3">状态</th>
                <th className="hidden px-4 py-3 lg:table-cell">节点数</th>
                <th className="px-4 py-3">更新时间</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {flows.map((f) => (
                <tr key={f.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3">
                    <Link to={`/flows/${f.id}`} className="font-medium text-blue-600 hover:underline">
                      {f.name}
                    </Link>
                    {f.description && (
                      <p className="mt-0.5 text-xs text-gray-400 truncate max-w-xs">{f.description}</p>
                    )}
                  </td>
                  <td className="hidden px-4 py-3 text-gray-500 md:table-cell">
                    {groupMap.get(f.group_id) || f.group_id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[f.status] || 'bg-gray-100 text-gray-600'}`}>
                      {f.status}
                    </span>
                  </td>
                  <td className="hidden px-4 py-3 text-gray-500 lg:table-cell">
                    {f.definition_json?.nodes?.length ?? 0}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {f.updated_at ? new Date(f.updated_at).toLocaleDateString('zh-CN') : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <Link
                        to={`/flows/${f.id}`}
                        className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
                      >
                        编辑
                      </Link>
                      <button
                        onClick={() => openRuns(f.id)}
                        className="rounded px-2 py-1 text-xs font-medium text-emerald-600 hover:bg-emerald-50"
                      >
                        运行记录
                      </button>
                      <button
                        onClick={() => openEdit(f)}
                        className="rounded px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50"
                      >
                        配置
                      </button>
                      <button
                        onClick={() => handleDelete(f)}
                        className="rounded px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Form Dialog */}
      {formOpen && (
        <FlowFormDialog
          editing={editing}
          groups={groups}
          onClose={() => setFormOpen(false)}
          onSaved={handleSaved}
        />
      )}

      {/* Flow Runs Drawer */}
      {runsFlowId && (
        <div
          className="drawer-overlay fixed inset-0 z-50 flex justify-end bg-black/30"
          onClick={() => setRunsFlowId(null)}
        >
          <div
            className="drawer-panel flex h-full w-full max-w-md flex-col bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <h3 className="text-base font-semibold text-gray-900">运行记录</h3>
              <button onClick={() => setRunsFlowId(null)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div className="flex-1 overflow-y-auto px-6 py-4">
              {runsLoading ? (
                <div className="py-8 text-center text-sm text-gray-400">加载中...</div>
              ) : runs.length === 0 ? (
                <div className="py-8 text-center text-sm text-gray-400">暂无运行记录</div>
              ) : (
                <div className="space-y-2">
                  {runs.map((r) => (
                    <Link
                      key={r.id}
                      to={`/flow-runs/${r.id}`}
                      className="block rounded-lg border border-gray-200 bg-white p-3 shadow-sm transition-shadow hover:shadow-md"
                    >
                      <div className="flex items-center gap-2 mb-1.5">
                        <span className={`rounded px-2 py-0.5 text-xs font-medium ${RUN_STATUS_COLORS[r.status] ?? 'bg-gray-100 text-gray-600'}`}>
                          {RUN_STATUS_LABELS[r.status] ?? r.status}
                        </span>
                        <span className="ml-auto text-xs text-gray-400">
                          {r.started_at ? new Date(r.started_at).toLocaleString('zh-CN') : '—'}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 text-xs text-gray-500">
                        <span>ID: {r.id.slice(0, 8)}</span>
                        <span>·</span>
                        <span>{r.trigger_type === 'manual' ? '手动' : r.trigger_type === 'schedule' ? '定时' : r.trigger_type}</span>
                        {r.created_by && <><span>·</span><span>{r.created_by}</span></>}
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
      </div>
    </div>
  )
}
