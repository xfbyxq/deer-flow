import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Waker, EnumData } from '../types'
import Avatar from '../components/Avatar'
import StatusTag from '../components/StatusTag'
import SkillPickerModal from '../components/SkillPickerModal'
import McpPickerModal from '../components/McpPickerModal'

/* ─── Toggle Switch ─── */
function Toggle({ on, onChange, small }: { on: boolean; onChange: (v: boolean) => void; small?: boolean }) {
  return (
    <button
      onClick={() => onChange(!on)}
      className={`relative inline-flex shrink-0 cursor-pointer items-center rounded-full transition-colors ${
        small ? 'h-5 w-9' : 'h-6 w-11'
      } ${on ? 'bg-[var(--primary)]' : 'bg-[var(--border-strong)]'}`}
    >
      <span
        className={`inline-block rounded-full bg-white shadow-sm transition-transform ${
          small ? 'h-3.5 w-3.5' : 'h-4 w-4'
        } ${on ? (small ? 'translate-x-4' : 'translate-x-5.5') : 'translate-x-1'}`}
      />
    </button>
  )
}

/* ─── Section Card ─── */
function SectionCard({ title, hint, actions, children }: {
  title?: string
  hint?: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)]">
      {(title || actions) && (
        <div className="flex items-center justify-between px-6 pt-5 pb-3">
          <div className="flex items-baseline gap-2">
            {title && <h3 className="text-[15px] font-semibold text-[var(--text)]">{title}</h3>}
            {hint && <span className="text-xs text-[var(--text-3)]">{hint}</span>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className="px-6 pb-5">{children}</div>
    </div>
  )
}

/* ─── Waker Config Page ─── */
export default function WakerDetailPage() {
  const { name } = useParams<{ name: string }>()
  const [waker, setWaker] = useState<Waker | null>(null)
  const [loading, setLoading] = useState(true)
  const [skillPickerOpen, setSkillPickerOpen] = useState(false)
  const [mcpPickerOpen, setMcpPickerOpen] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [enumData, setEnumData] = useState<EnumData>({ models: [], skills: [], tool_groups: [] })
  const [saveError, setSaveError] = useState<string | null>(null)

  // Local state for editing
  const [model, setModel] = useState('')
  const [concurrency, setConcurrency] = useState(3)
  const [enabled, setEnabled] = useState(true)
  const [desc, setDesc] = useState('')
  const [skills, setSkills] = useState<string[]>([])
  const [mcps, setMcps] = useState<string[]>([])
  const [toolGroups, setToolGroups] = useState<string[]>([])

  const load = useCallback(async () => {
    if (!name) return
    try {
      const [w, e] = await Promise.all([
        api.getWaker(name),
        api.getEnumData(),
      ])
      setWaker(w)
      setEnumData(e)
      setModel(w.model || (e.models.length > 0 ? e.models[0].name : ''))
      setConcurrency(w.max_concurrent_tasks ?? 3)
      setEnabled(w.enabled)
      setDesc(w.description || '')
      setSkills(w.skills ?? [])
      // Use mcp_connectors keys if present; otherwise empty array
      setMcps(w.mcp_connectors ? Object.keys(w.mcp_connectors) : [])
      setToolGroups(w.tool_groups ?? [])
    } catch (err) { setSaveError(err instanceof Error ? err.message : '加载失败') }
    finally { setLoading(false) }
  }, [name])

  useEffect(() => { load() }, [load])

  const saveField = async (patch: Partial<Waker>) => {
    if (!name) return
    try {
      setSaveError(null)
      const updated = await api.updateWaker(name, patch)
      setWaker(updated)
    } catch (err) { setSaveError(err instanceof Error ? err.message : '保存失败') }
  }

  const handleToggleEnabled = (v: boolean) => {
    setEnabled(v)
    saveField({ enabled: v })
  }

  const handleModelChange = (v: string) => {
    setModel(v)
    saveField({ model: v } as Partial<Waker>)
  }

  const handleConcurrencyChange = (v: number) => {
    setConcurrency(v)
    saveField({ max_concurrent_tasks: v })
  }

  const handleDescChange = (v: string) => {
    setDesc(v)
  }

  const handleDescBlur = () => {
    if (desc !== waker?.description) saveField({ description: desc } as Partial<Waker>)
  }

  const handleRemoveSkill = (s: string) => {
    const next = skills.filter((x) => x !== s)
    setSkills(next)
    saveField({ skills: next } as Partial<Waker>)
  }

  const handleAddSkills = (selected: string[]) => {
    if (!selected.length) return
    const next = [...new Set([...skills, ...selected])]
    setSkills(next)
    saveField({ skills: next } as Partial<Waker>)
  }

  const handleRemoveMcp = (m: string) => {
    const next = mcps.filter((x) => x !== m)
    setMcps(next)
    saveField({ mcp_connectors: buildMcpConnectors(next) } as Partial<Waker>)
  }

  const handleAddMcps = (selected: string[]) => {
    if (!selected.length) return
    const next = [...new Set([...mcps, ...selected])]
    setMcps(next)
    saveField({ mcp_connectors: buildMcpConnectors(next) } as Partial<Waker>)
  }

  /** 以连接器名称列表构造 mcp_connectors，保留已有连接器的配置对象 */
  const buildMcpConnectors = (names: string[]): Record<string, Record<string, unknown>> => {
    const existing = waker?.mcp_connectors ?? {}
    const next: Record<string, Record<string, unknown>> = {}
    for (const n of names) next[n] = existing[n] ?? {}
    return next
  }

  const toggleToolGroup = (id: string) => {
    const next = toolGroups.includes(id)
      ? toolGroups.filter((g) => g !== id)
      : [...toolGroups, id]
    setToolGroups(next)
    saveField({ tool_groups: next } as Partial<Waker>)
  }

  const handleDelete = async () => {
    if (!name) return
    try {
      await api.deleteWaker(name)
      window.location.hash = ''
      window.history.back()
    } catch (err) { setSaveError(err instanceof Error ? err.message : '删除失败') }
  }

  if (loading) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">加载中...</div>
  if (!waker) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">员工不存在</div>

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-[860px] px-6 py-6 space-y-5">
        {/* Save error banner */}
        {saveError && (
          <div className="flex items-center justify-between rounded-lg bg-red-50 px-4 py-2.5 text-sm text-red-700 border border-red-200">
            <span>{saveError}</span>
            <button onClick={() => setSaveError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
          </div>
        )}
        {/* Topbar */}
        <div className="flex items-center justify-between">
          <Link to="/wakers" className="text-sm text-[var(--text-3)] transition-colors hover:text-[var(--primary)]">
            ← 返回员工管理
          </Link>
        </div>

        {/* Identity header */}
        <div className="flex items-center gap-4 rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)] px-6 py-5">
          <Avatar name={waker.name} size="default" presence={enabled ? 'online' : 'offline'} />
          <div className="min-w-0 flex-1">
            <div className="text-lg font-bold text-[var(--text)]">{waker.name}</div>
            <div className="text-xs text-[var(--text-3)]">
              {waker.description?.slice(0, 40) || '未设置职责'} · ID: {waker.name} · {enabled ? '在线' : '离线'}
            </div>
          </div>
          <div className="flex items-center gap-3">
            <StatusTag status={enabled ? 'enabled' : 'disabled_waker'} />
            <Link
              to={`/chat/direct/${waker.name}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
            >
              进入对话
            </Link>
          </div>
        </div>

        {/* Section: Basic config */}
        <SectionCard title="基础配置" hint="模型 / 并发 / 启用状态 / 职责">
          <div className="space-y-4">
            <div className="flex flex-wrap gap-4">
              <div className="min-w-[200px] flex-1">
                <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">执行模型</label>
                <select
                  value={model}
                  onChange={(e) => handleModelChange(e.target.value)}
                  className="w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
                >
                  {enumData.models.length === 0 && <option value="">加载中...</option>}
                  {enumData.models.map((m) => (
                    <option key={m.name} value={m.name}>{m.display_name || m.name}</option>
                  ))}
                </select>
              </div>
              <div style={{ maxWidth: 170 }}>
                <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">最大并发任务</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={concurrency}
                  onChange={(e) => handleConcurrencyChange(Number(e.target.value))}
                  className="w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
                />
              </div>
              <div style={{ maxWidth: 160 }}>
                <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">启用状态</label>
                <div className="flex items-center gap-2.5 pt-1">
                  <Toggle on={enabled} onChange={handleToggleEnabled} />
                  <span className="text-xs text-[var(--text-3)]">{enabled ? '启用' : '停用'}</span>
                </div>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">职责描述</label>
              <textarea
                value={desc}
                onChange={(e) => handleDescChange(e.target.value)}
                onBlur={handleDescBlur}
                rows={3}
                className="w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
                placeholder="描述该员工的职责..."
              />
            </div>
          </div>
        </SectionCard>

        {/* Section: Skills */}
        <SectionCard
          title={`技能 Skill`}
          hint={String(skills.length)}
          actions={
            <button
              onClick={() => setSkillPickerOpen(true)}
              className="text-sm font-medium text-[var(--primary)] transition-colors hover:text-[var(--primary-deep)]"
            >
              + 从技能市场添加
            </button>
          }
        >
          {skills.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {skills.map((s) => (
                <span
                  key={s}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--panel-2)] px-3 py-1 text-sm text-[var(--text)]"
                >
                  {s}
                  <button
                    onClick={() => handleRemoveSkill(s)}
                    className="flex h-4 w-4 items-center justify-center rounded-full text-[var(--text-3)] transition-colors hover:bg-[var(--red-soft)] hover:text-[var(--red)]"
                    title="移除"
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>
          ) : (
            <div className="text-sm text-[var(--text-3)]">暂未安装技能，点击右上角从技能市场添加</div>
          )}
        </SectionCard>

        {/* Section: MCP Connectors */}
        <SectionCard
          title="MCP 连接器"
          hint={String(mcps.length)}
          actions={
            <button
              onClick={() => setMcpPickerOpen(true)}
              className="text-sm font-medium text-[var(--primary)] transition-colors hover:text-[var(--primary-deep)]"
            >
              + 添加连接器
            </button>
          }
        >
          {mcps.length > 0 ? (
            <div className="space-y-2">
              {mcps.map((m) => (
                <div key={m} className="flex items-center gap-3 rounded-lg border border-[var(--border)] px-4 py-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--panel-3)] text-[var(--text-3)]">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                    </svg>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-[var(--text)]">{m}</div>
                  </div>
                  <StatusTag status="enabled" label="已连接" />
                  <button
                    onClick={() => handleRemoveMcp(m)}
                    className="rounded-md px-2 py-1 text-xs font-medium text-[var(--red)] transition-colors hover:bg-[var(--red-soft)]"
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-[var(--text-3)]">暂未连接 MCP 服务，点击右上角添加</div>
          )}
        </SectionCard>

        {/* Section: Tool Groups */}
        <SectionCard title="工具组" hint="点击切换启用；停用后该员工无法使用对应工具">
          <div className="flex flex-wrap gap-2">
            {enumData.tool_groups.map((g) => {
              const on = toolGroups.includes(g)
              return (
                <button
                  key={g}
                  onClick={() => toggleToolGroup(g)}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition-all ${
                    on
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--panel)] text-[var(--text-3)] hover:border-[var(--border-strong)]'
                  }`}
                >
                  {on && <span className="text-xs">✓</span>}
                  {g}
                </button>
              )
            })}
          </div>
        </SectionCard>

        {/* Section: Danger Zone */}
        <div className="rounded-xl border border-[var(--red)] bg-[var(--panel)] shadow-[var(--shadow-sm)]">
          <div className="flex items-center justify-between px-6 py-5">
            <div>
              <div className="text-sm font-semibold text-[var(--red)]">删除此 Waker</div>
              <div className="mt-0.5 text-xs text-[var(--text-3)]">删除后该员工的对话历史与配置将不可恢复</div>
            </div>
            <button
              onClick={() => setDeleteConfirmOpen(true)}
              className="rounded-lg bg-[var(--red)] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:opacity-90"
            >
              删除
            </button>
          </div>
        </div>
      </div>

      {/* Skill Picker Modal */}
      <SkillPickerModal
        open={skillPickerOpen}
        onClose={() => setSkillPickerOpen(false)}
        installed={skills}
        onConfirm={handleAddSkills}
      />

      {/* MCP Picker Modal */}
      <McpPickerModal
        open={mcpPickerOpen}
        onClose={() => setMcpPickerOpen(false)}
        installed={mcps}
        onConfirm={handleAddMcps}
      />

      {/* Delete Confirmation Modal */}
      {deleteConfirmOpen && (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center"
          style={{ background: 'rgba(15,23,42,.42)' }}
          onClick={() => setDeleteConfirmOpen(false)}
        >
          <div
            className="rounded-2xl bg-[var(--panel)] p-6 shadow-[var(--shadow-md)]"
            style={{ width: 400 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-[var(--text)]">确认删除</h3>
            <p className="mt-2 text-sm text-[var(--text-2)]">
              确认删除员工「{waker.name}」？此操作不可撤销，对话历史和配置将永久丢失。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setDeleteConfirmOpen(false)}
                className="rounded-lg border border-[var(--border)] px-4 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                className="rounded-lg bg-[var(--red)] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:opacity-90"
              >
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
