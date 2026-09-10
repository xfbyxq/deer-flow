import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Waker, EnumData, WakerTemplate } from '../types'
import { notifyTeamDataChanged } from '../utils/teamEvents'

/* ─── Status Badge ─── */
function StatusBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> 启用
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-500">
      <span className="h-1.5 w-1.5 rounded-full bg-gray-400" /> 停用
    </span>
  )
}

/* ─── Multi-select chip input ─── */
function MultiSelect({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: string[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  const toggle = (opt: string) =>
    onChange(value.includes(opt) ? value.filter((v) => v !== opt) : [...value, opt])

  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium text-gray-700">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.length === 0 && (
          <span className="text-xs text-gray-400">暂无可选</span>
        )}
        {options.map((opt) => {
          const selected = value.includes(opt)
          return (
            <button
              key={opt}
              type="button"
              onClick={() => toggle(opt)}
              className={`rounded-md border px-2.5 py-1 text-xs font-medium transition-colors ${
                selected
                  ? 'border-blue-300 bg-blue-50 text-blue-700'
                  : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
              }`}
            >
              {opt}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

/* ─── Free-form tag input ─── */
function TagInput({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string
  placeholder?: string
  value: string[]
  onChange: (v: string[]) => void
}) {
  const [draft, setDraft] = useState('')

  const add = () => {
    const tag = draft.trim()
    setDraft('')
    if (!tag || value.includes(tag)) return
    onChange([...value, tag])
  }

  return (
    <fieldset>
      <legend className="mb-1.5 text-sm font-medium text-gray-700">{label}</legend>
      {value.length > 0 && (
        <div className="mb-1.5 flex flex-wrap gap-1.5">
          {value.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center gap-1 rounded-md border border-blue-300 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700"
            >
              {tag}
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v !== tag))}
                className="text-blue-400 hover:text-blue-600"
              >
                ✕
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-1.5">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          placeholder={placeholder ?? '输入后回车添加'}
          className="w-56 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
        />
        <button
          type="button"
          onClick={add}
          className="rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
        >
          添加
        </button>
      </div>
    </fieldset>
  )
}

/* ─── Waker Form Dialog ─── */
function WakerFormDialog({
  editing,
  enumData,
  templates,
  onClose,
  onSaved,
}: {
  editing: Waker | null
  enumData: EnumData
  templates: WakerTemplate[]
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = editing !== null
  const [name, setName] = useState(editing?.name ?? '')
  const [description, setDescription] = useState(editing?.description ?? '')
  const [soul, setSoul] = useState(editing?.soul ?? '')
  const [model, setModel] = useState(editing?.model ?? '')
  const [toolGroups, setToolGroups] = useState<string[]>(editing?.tool_groups ?? [])
  const [skills, setSkills] = useState<string[]>(editing?.skills ?? [])
  const [maxConcurrent, setMaxConcurrent] = useState(
    String(editing?.max_concurrent_tasks ?? '1')
  )
  const [role, setRole] = useState(editing?.role ?? '')
  const [mcpServers, setMcpServers] = useState<string[]>(
    Object.keys(editing?.mcp_connectors ?? {})
  )
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const applyTemplate = (t: WakerTemplate) => {
    setSelectedTemplateId(t.id)
    setName(t.suggested_name)
    setDescription(t.description)
    setSoul(t.soul)
    setModel(t.model ?? '')
    setRole(t.role ?? '')
    setToolGroups(t.tool_groups)
    setSkills(t.skills)
    setMaxConcurrent(String(t.max_concurrent_tasks))
    setMcpServers(Object.keys(t.mcp_connectors ?? {}))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setSaving(true)
    try {
      const mcpConnectors: Record<string, Record<string, unknown>> = {}
      for (const n of mcpServers) mcpConnectors[n] = editing?.mcp_connectors?.[n] ?? {}
      const payload: Partial<Waker> = {
        name,
        description,
        soul,
        model: model || undefined,
        role,
        tool_groups: toolGroups,
        skills,
        max_concurrent_tasks: parseInt(maxConcurrent, 10) || 1,
        mcp_connectors: mcpConnectors,
      }
      if (isEdit && editing) {
        await api.updateWaker(editing.name, payload)
      } else {
        await api.createWaker(payload)
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
        className="modal-panel flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">
            {isEdit ? '编辑员工' : '创建员工'}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            ✕
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4 overflow-y-auto px-6 py-4">
          {error && (
            <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
          )}

          {/* Template picker (create mode only) */}
          {!isEdit && templates.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-baseline gap-2">
                <span className="text-sm font-medium text-gray-700">从模板创建</span>
                <span className="text-xs text-gray-400">选择模板自动填充，可继续修改</span>
              </div>
              <div className="grid max-h-48 grid-cols-2 gap-1.5 overflow-y-auto">
                {templates.map((t) => {
                  const selected = selectedTemplateId === t.id
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => applyTemplate(t)}
                      className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                        selected
                          ? 'border-blue-400 bg-blue-50 ring-2 ring-blue-100'
                          : 'border-gray-200 bg-white hover:border-gray-300'
                      }`}
                    >
                      <div className="text-sm font-medium text-gray-800">{t.title}</div>
                      <div className="mt-0.5 line-clamp-2 text-xs leading-snug text-gray-500">
                        {t.description}
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {/* Name */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">名称</label>
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              readOnly={isEdit}
              className={`w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100 ${
                isEdit ? 'bg-gray-50 text-gray-500' : ''
              }`}
              placeholder="例如: researcher"
            />
          </div>

          {/* Description */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">描述</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="简要描述该员工的职责"
            />
          </div>

          {/* Role */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">角色</label>
            <input
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="例如: 前端工程师"
            />
          </div>

          {/* Soul */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              Soul（系统提示）
            </label>
            <textarea
              value={soul}
              onChange={(e) => setSoul(e.target.value)}
              rows={5}
              className="w-full resize-y rounded-md border border-gray-200 px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
              placeholder="输入 SOUL 模板内容..."
            />
          </div>

          {/* Model */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">模型</label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            >
              <option value="">默认</option>
              {enumData.models.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.display_name || m.name}
                </option>
              ))}
            </select>
          </div>

          {/* Max concurrent tasks */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">
              最大并发任务数
            </label>
            <input
              type="number"
              min={1}
              max={10}
              value={maxConcurrent}
              onChange={(e) => setMaxConcurrent(e.target.value)}
              className="w-24 rounded-md border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            />
          </div>

          {/* Tool groups */}
          <MultiSelect
            label="工具组"
            options={enumData.tool_groups}
            value={toolGroups}
            onChange={setToolGroups}
          />

          {/* Skills */}
          <MultiSelect
            label="技能"
            options={enumData.skills.map((s) => s.name)}
            value={skills}
            onChange={setSkills}
          />

          {/* MCP connectors */}
          <TagInput
            label="MCP 连接器"
            placeholder="例如: mcp-atlassian"
            value={mcpServers}
            onChange={setMcpServers}
          />

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ─── Wakers Page ─── */
export default function WakersPage() {
  const [wakers, setWakers] = useState<Waker[]>([])
  const [enumData, setEnumData] = useState<EnumData>({ models: [], skills: [], tool_groups: [] })
  const [templates, setTemplates] = useState<WakerTemplate[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Waker | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [w, e, t] = await Promise.all([
        api.listWakers(),
        api.getEnumData(),
        api.listWakerTemplates().catch(() => [] as WakerTemplate[]),
      ])
      setWakers(w)
      setEnumData(e)
      setTemplates(t)
    } catch (err) {
      console.error('Load failed:', err)
      setError('服务连接失败，请检查后端是否正常运行')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleToggle = async (w: Waker) => {
    try {
      setActionError(null)
      await api.toggleWaker(w.name, !w.enabled)
      notifyTeamDataChanged()
      load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '操作失败')
    }
  }

  const handleDelete = async (w: Waker) => {
    if (!confirm(`确认删除员工「${w.name}」？`)) return
    try {
      setActionError(null)
      await api.deleteWaker(w.name)
      notifyTeamDataChanged()
      load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : '删除失败')
    }
  }

  const openCreate = () => { setEditing(null); setFormOpen(true) }
  const openEdit = (w: Waker) => { setEditing(w); setFormOpen(true) }
  const handleSaved = () => { setFormOpen(false); notifyTeamDataChanged(); load() }

  return (
    <div className="h-full overflow-auto">
      <div className="p-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">员工管理</h1>
        <button
          onClick={openCreate}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-blue-700"
        >
          + 创建员工
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
      ) : wakers.length === 0 ? (
        <div className="py-20 text-center text-gray-400">暂无员工，点击上方按钮创建</div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-gray-100 bg-gray-50/60 text-xs font-medium uppercase tracking-wider text-gray-500">
              <tr>
                <th className="px-4 py-3">名称</th>
                <th className="px-4 py-3">描述</th>
                <th className="hidden px-4 py-3 md:table-cell">模型</th>
                <th className="hidden px-4 py-3 lg:table-cell">并发</th>
                <th className="px-4 py-3">状态</th>
                <th className="px-4 py-3 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {wakers.map((w) => (
                <tr key={w.name} className="hover:bg-gray-50/50">
                  <td className="px-4 py-3">
                    <Link to={`/wakers/${w.name}`} className="font-medium text-blue-600 hover:underline">
                      {w.name}
                    </Link>
                  </td>
                  <td className="max-w-xs truncate px-4 py-3 text-gray-500">{w.description}</td>
                  <td className="hidden px-4 py-3 text-gray-500 md:table-cell">
                    {w.model || '—'}
                  </td>
                  <td className="hidden px-4 py-3 text-gray-500 lg:table-cell">
                    {w.max_concurrent_tasks ?? 1}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge enabled={w.enabled} />
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <button
                        onClick={() => openEdit(w)}
                        className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => handleToggle(w)}
                        className="rounded px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50"
                      >
                        {w.enabled ? '停用' : '启用'}
                      </button>
                      <button
                        onClick={() => handleDelete(w)}
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
        <WakerFormDialog
          editing={editing}
          enumData={enumData}
          templates={templates}
          onClose={() => setFormOpen(false)}
          onSaved={handleSaved}
        />
      )}
      </div>
    </div>
  )
}
