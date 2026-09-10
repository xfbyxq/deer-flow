import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Group, GroupMember, Waker } from '../types'
import { notifyTeamDataChanged } from '../utils/teamEvents'

/* ─── Group Form Dialog ─── */
function GroupFormDialog({
  editing,
  wakers,
  onClose,
  onSaved,
}: {
  editing: Group | null
  wakers: Waker[]
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = editing !== null
  const [name, setName] = useState(editing?.name ?? '')
  const [leaderId, setLeaderId] = useState(editing?.leader_waker_id ?? '')
  const [project, setProject] = useState(editing?.project_id ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    setError('')
    setSaving(true)
    try {
      const payload: { name: string; leader_waker_id?: string; project_id?: string } = {
        name: name.trim(),
      }
      if (leaderId) payload.leader_waker_id = leaderId
      if (project.trim()) payload.project_id = project.trim()
      if (isEdit && editing) {
        await api.updateGroup(editing.id, payload)
      } else {
        await api.createGroup(payload)
      }
      onSaved()
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
        className="modal-panel w-full max-w-md rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEdit ? '编辑群组' : '创建群组'}
          </h2>
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
              placeholder="例如: 研发一组"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Leader</label>
            <select
              value={leaderId}
              onChange={(e) => setLeaderId(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            >
              <option value="">未指定</option>
              {wakers.filter((w) => w.enabled).map((w) => (
                <option key={w.name} value={w.name}>{w.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Project ID</label>
            <input
              value={project}
              onChange={(e) => setProject(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="可选，关联项目标识"
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

/* ─── Member Panel ─── */
function MemberPanel({
  group,
  wakers,
  onClose,
}: {
  group: Group
  wakers: Waker[]
  onClose: () => void
}) {
  const [members, setMembers] = useState<GroupMember[]>([])
  const [loading, setLoading] = useState(true)
  const [addWakerId, setAddWakerId] = useState('')
  const [addRole, setAddRole] = useState<'member' | 'leader'>('member')
  const [addLoading, setAddLoading] = useState(false)

  const loadMembers = useCallback(async () => {
    try {
      const m = await api.listGroupMembers(group.id)
      setMembers(m)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [group.id])

  useEffect(() => { loadMembers() }, [loadMembers])

  const handleAdd = async () => {
    if (!addWakerId) return
    setAddLoading(true)
    try {
      await api.addGroupMember(group.id, { waker_id: addWakerId, role: addRole })
      setAddWakerId('')
      loadMembers()
    } catch { /* ignore */ }
    finally { setAddLoading(false) }
  }

  const handleRemove = async (wakerId: string) => {
    try {
      await api.removeGroupMember(group.id, wakerId)
      loadMembers()
    } catch { /* ignore */ }
  }

  const existingIds = new Set(members.map((m) => m.waker_id))
  const availableWakers = wakers.filter((w) => !existingIds.has(w.name))

  return (
    <div className="drawer-overlay fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="drawer-panel flex h-full w-full max-w-md flex-col bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <div>
            <h3 className="text-base font-semibold text-gray-900">{group.name}</h3>
            <p className="text-xs text-gray-400">成员管理</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {/* Add member */}
          <div className="rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-2">
            <div className="text-sm font-medium text-gray-700">添加成员</div>
            <select
              value={addWakerId}
              onChange={(e) => setAddWakerId(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-1.5 text-sm outline-none focus:border-blue-400"
            >
              <option value="">选择员工...</option>
              {availableWakers.map((w) => (
                <option key={w.name} value={w.name}>{w.name}</option>
              ))}
            </select>
            <div className="flex items-center gap-2">
              <select
                value={addRole}
                onChange={(e) => setAddRole(e.target.value as 'member' | 'leader')}
                className="rounded-md border border-gray-200 px-3 py-1.5 text-sm outline-none focus:border-blue-400"
              >
                <option value="member">成员</option>
                <option value="leader">Leader</option>
              </select>
              <button
                onClick={handleAdd}
                disabled={!addWakerId || addLoading}
                className="rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {addLoading ? '添加中...' : '添加'}
              </button>
            </div>
          </div>

          {/* Member list */}
          {loading ? (
            <div className="py-8 text-center text-sm text-gray-400">加载中...</div>
          ) : members.length === 0 ? (
            <div className="py-8 text-center text-sm text-gray-400">暂无成员</div>
          ) : (
            <div className="space-y-1">
              {members.map((m) => (
                <div key={m.waker_id} className="flex items-center justify-between rounded-md border border-gray-100 bg-white px-3 py-2">
                  <div className="flex items-center gap-2">
                    <Link
                      to={`/wakers/${m.waker_id}`}
                      className="text-sm font-medium text-blue-600 hover:underline"
                    >
                      {m.waker_id}
                    </Link>
                    <span className={`rounded px-1.5 py-0.5 text-xs ${
                      m.role === 'leader' ? 'bg-amber-100 text-amber-700' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {m.role === 'leader' ? 'Leader' : '成员'}
                    </span>
                  </div>
                  <button
                    onClick={() => handleRemove(m.waker_id)}
                    className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/* ─── Groups Page ─── */
export default function GroupsPage() {
  const [groups, setGroups] = useState<Group[]>([])
  const [wakers, setWakers] = useState<Waker[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Group | null>(null)
  const [selectedGroup, setSelectedGroup] = useState<Group | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [g, w] = await Promise.all([api.listGroups(), api.listWakers()])
      setGroups(g)
      setWakers(w)
    } catch (err) { console.error('Load failed:', err); setError('服务连接失败，请检查后端是否正常运行') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = async (g: Group) => {
    if (!confirm(`确认删除群组「${g.name}」？`)) return
    try {
      await api.deleteGroup(g.id)
      notifyTeamDataChanged()
      load()
    } catch { /* ignore */ }
  }

  const openCreate = () => { setEditing(null); setFormOpen(true) }
  const openEdit = (g: Group) => { setEditing(g); setFormOpen(true) }
  const handleSaved = () => { setFormOpen(false); notifyTeamDataChanged(); load() }

  const wakerNameMap = new Map(wakers.map((w) => [w.name, w.description]))

  return (
    <div className="h-full overflow-auto">
      <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">群组管理</h1>
        <button
          onClick={openCreate}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
        >
          + 创建群组
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
      ) : groups.length === 0 ? (
        <div className="py-20 text-center text-gray-400">暂无群组，点击上方按钮创建</div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gray-100 bg-gray-50/60 text-xs font-medium uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">名称</th>
                <th className="hidden px-4 py-3 md:table-cell">Leader</th>
                <th className="hidden px-4 py-3 lg:table-cell">Project</th>
                <th className="px-4 py-3">创建时间</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {groups.map((g) => (
                <tr key={g.id} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3">
                    <button
                      onClick={() => setSelectedGroup(g)}
                      className="font-medium text-blue-600 hover:underline"
                    >
                      {g.name}
                    </button>
                  </td>
                  <td className="hidden px-4 py-3 text-gray-500 md:table-cell">
                    {g.leader_waker_id ? (
                      <span title={wakerNameMap.get(g.leader_waker_id) || ''}>{g.leader_waker_id}</span>
                    ) : '—'}
                  </td>
                  <td className="hidden px-4 py-3 text-gray-500 lg:table-cell">
                    {g.project_id || '—'}
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {g.created_at ? new Date(g.created_at).toLocaleDateString('zh-CN') : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => setSelectedGroup(g)}
                        className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
                      >
                        成员
                      </button>
                      <button
                        onClick={() => openEdit(g)}
                        className="rounded px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleDelete(g)}
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
        <GroupFormDialog
          editing={editing}
          wakers={wakers}
          onClose={() => setFormOpen(false)}
          onSaved={handleSaved}
        />
      )}

      {/* Member Panel */}
      {selectedGroup && (
        <MemberPanel
          group={selectedGroup}
          wakers={wakers}
          onClose={() => setSelectedGroup(null)}
        />
      )}
      </div>
    </div>
  )
}
