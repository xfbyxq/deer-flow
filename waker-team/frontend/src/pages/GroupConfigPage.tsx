import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api/client'
import type { Group, GroupMember, GroupSkill, Waker } from '../types'
import Avatar from '../components/Avatar'
import StatusTag from '../components/StatusTag'
import MemberPickerModal from '../components/MemberPickerModal'
import SkillPickerModal from '../components/SkillPickerModal'

const SOP_POOL = [
  { id: 'standard', name: '标准工作流', desc: '需求 → 计划 → 开发 → 质量 → 发布 五段协作' },
  { id: 'data-analysis', name: '数据分析流程', desc: '数据采集 → 清洗 → 分析 → 报告 → 复盘' },
  { id: 'report-gen', name: '报告生成流程', desc: '信息收集 → 草稿 → 审核 → 格式化 → 发布' },
]

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

export default function GroupConfigPage() {
  const { groupId } = useParams<{ groupId: string }>()
  const [group, setGroup] = useState<Group | null>(null)
  const [members, setMembers] = useState<GroupMember[]>([])
  const [wakers, setWakers] = useState<Waker[]>([])
  const [loading, setLoading] = useState(true)
  const [memberPickerOpen, setMemberPickerOpen] = useState(false)
  const [skillPickerOpen, setSkillPickerOpen] = useState(false)
  const [transferTarget, setTransferTarget] = useState<string | null>(null)

  // Local state
  const [groupName, setGroupName] = useState('')
  const [groupDesc, setGroupDesc] = useState('')
  const [groupSkills, setGroupSkills] = useState<string[]>([])
  const [sop, setSop] = useState('standard')

  const load = useCallback(async () => {
    if (!groupId) return
    try {
      const [g, m, w, skills] = await Promise.all([
        api.getGroup(groupId),
        api.listGroupMembers(groupId),
        api.listWakers(),
        api.listGroupSkills(groupId).catch(() => [] as GroupSkill[]),
      ])
      setGroup(g)
      setMembers(m)
      setWakers(w)
      setGroupName(g.name)
      setGroupDesc(g.description ?? '')
      setSop(g.sop_id ?? 'standard')
      setGroupSkills(skills.map((s) => s.skill_name))
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [groupId])

  useEffect(() => { load() }, [load])

  const reloadSkills = useCallback(async () => {
    if (!groupId) return
    try {
      const skills = await api.listGroupSkills(groupId)
      setGroupSkills(skills.map((s) => s.skill_name))
    } catch { /* ignore */ }
  }, [groupId])

  const wakerMap = new Map(wakers.map((w) => [w.name, w]))
  const leader = group?.leader_waker_id ? wakerMap.get(group.leader_waker_id) : null

  const handleRemoveMember = async (wakerId: string) => {
    try {
      await api.removeGroupMember(groupId!, wakerId)
      load()
    } catch { /* ignore */ }
  }

  const handleTransferLeader = async (targetId: string) => {
    if (!groupId) return
    try {
      await api.transferGroupLeader(groupId, targetId)
      setTransferTarget(null)
      load()
    } catch { /* ignore */ }
  }

  const handleAddMembers = async (selected: string[]) => {
    if (!groupId || !selected.length) return
    for (const id of selected) {
      try { await api.addGroupMember(groupId, { waker_id: id }) } catch { /* ignore */ }
    }
    load()
  }

  const handleAddSkills = async (selected: string[]) => {
    if (!groupId || !selected.length) return
    for (const name of selected) {
      try { await api.addGroupSkill(groupId, name) } catch { /* ignore */ }
    }
    await reloadSkills()
  }

  const handleRemoveSkill = async (name: string) => {
    if (!groupId) return
    try { await api.removeGroupSkill(groupId, name) } catch { /* ignore */ }
    await reloadSkills()
  }

  const handleSaveName = () => {
    if (!groupId || !groupName.trim()) return
    api.updateGroup(groupId, { name: groupName.trim() }).then(load).catch(() => {})
  }

  const handleSaveDesc = () => {
    if (!groupId) return
    if (groupDesc === (group?.description ?? '')) return
    api.updateGroup(groupId, { description: groupDesc }).then(load).catch(() => {})
  }

  const handleSelectSop = (id: string) => {
    setSop(id)
    if (!groupId) return
    api.updateGroup(groupId, { sop_id: id }).catch(() => {})
  }

  if (loading) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">加载中...</div>
  if (!group) return <div className="flex h-full items-center justify-center text-sm text-[var(--text-3)]">群组不存在</div>

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-[860px] px-6 py-6 space-y-5">
        {/* Topbar */}
        <div className="flex items-center justify-between">
          <Link to="/groups" className="text-sm text-[var(--text-3)] transition-colors hover:text-[var(--primary)]">
            ← 返回群组管理
          </Link>
        </div>

        {/* Identity header */}
        <div className="flex items-center gap-4 rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)] px-6 py-5">
          <div className="flex -space-x-2">
            {members.slice(0, 4).map((m) => {
              const w = wakerMap.get(m.waker_id)
              return w ? <Avatar key={m.waker_id} name={w.name} size="default" /> : null
            })}
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-lg font-bold text-[var(--text)]">{group.name}</div>
            <div className="text-xs text-[var(--text-3)]">
              {members.length} 名成员 · Leader {leader?.name || group.leader_waker_id || '—'} · {groupSkills.length} 个群技能
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link
              to={`/chat/group/${group.id}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
            >
              进入群对话
            </Link>
          </div>
        </div>

        {/* Section: Basic config */}
        <SectionCard title="基础配置">
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">群名称</label>
              <input
                value={groupName}
                onChange={(e) => setGroupName(e.target.value)}
                onBlur={handleSaveName}
                className="w-full max-w-md rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">群描述</label>
              <textarea
                rows={3}
                value={groupDesc}
                onChange={(e) => setGroupDesc(e.target.value)}
                onBlur={handleSaveDesc}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
                placeholder="描述该群组的目标..."
              />
            </div>
          </div>
        </SectionCard>

        {/* Section: Members */}
        <SectionCard
          title="成员管理"
          hint={String(members.length)}
          actions={
            <button
              onClick={() => setMemberPickerOpen(true)}
              className="text-sm font-medium text-[var(--primary)] transition-colors hover:text-[var(--primary-deep)]"
            >
              + 添加成员
            </button>
          }
        >
          <div className="space-y-1">
            {members.map((m) => {
              const w = wakerMap.get(m.waker_id)
              if (!w) return null
              const isLeader = m.waker_id === group.leader_waker_id
              return (
                <div key={m.waker_id} className="flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-[var(--panel-2)]">
                  <Avatar name={w.name} size="sm" presence={w.enabled ? 'online' : 'offline'} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-[var(--text)]">{w.name}</span>
                      {isLeader && (
                        <span className="rounded-full bg-[var(--primary-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--primary)]">
                          LEADER
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-[var(--text-3)]">
                      {w.description?.slice(0, 30) || '未设置'} · {w.enabled ? '在线' : '离线'}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {isLeader ? (
                      <span className="text-xs text-[var(--text-3)]">群负责人不可移除</span>
                    ) : (
                      <>
                        <button
                          onClick={() => setTransferTarget(m.waker_id)}
                          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--primary)] transition-colors hover:bg-[var(--primary-soft)]"
                        >
                          转让负责人
                        </button>
                        <button
                          onClick={() => handleRemoveMember(m.waker_id)}
                          className="rounded-md px-2 py-1 text-xs font-medium text-[var(--red)] transition-colors hover:bg-[var(--red-soft)]"
                        >
                          移除
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </SectionCard>

        {/* Section: Group Skills */}
        <SectionCard
          title="群技能"
          hint={String(groupSkills.length)}
          actions={
            <button
              onClick={() => setSkillPickerOpen(true)}
              className="text-sm font-medium text-[var(--primary)] transition-colors hover:text-[var(--primary-deep)]"
            >
              + 添加群技能
            </button>
          }
        >
          {groupSkills.length > 0 ? (
            <div className="space-y-2">
              {groupSkills.map((s) => (
                <div key={s} className="flex items-center gap-3 rounded-lg border border-[var(--border)] px-4 py-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--violet-soft)] text-[var(--violet)]">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                    </svg>
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-[var(--text)]">{s}</div>
                  </div>
                  <button
                    onClick={() => handleRemoveSkill(s)}
                    className="rounded-md px-2 py-1 text-xs font-medium text-[var(--red)] transition-colors hover:bg-[var(--red-soft)]"
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-[var(--text-3)]">暂未配置群技能</div>
          )}
          <div className="mt-3 text-xs text-[var(--text-3)]">群技能可供群内所有 Waker 共享使用，成员执行任务时自动加载。</div>
        </SectionCard>

        {/* Section: SOP Selection */}
        <SectionCard title="成员协作 SOP" hint="约定群内成员的协作流程与分工方式">
          <div className="space-y-2">
            {SOP_POOL.map((s) => (
              <button
                key={s.id}
                onClick={() => handleSelectSop(s.id)}
                className={`flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition-all ${
                  sop === s.id
                    ? 'border-[var(--primary)] bg-[var(--primary-soft)]'
                    : 'border-[var(--border)] hover:bg-[var(--panel-2)]'
                }`}
              >
                <span className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border-2 ${
                  sop === s.id ? 'border-[var(--primary)]' : 'border-[var(--border-strong)]'
                }`}>
                  {sop === s.id && <span className="h-2 w-2 rounded-full bg-[var(--primary)]" />}
                </span>
                <div className="flex-1">
                  <div className="text-sm font-medium text-[var(--text)]">{s.name}</div>
                  <div className="text-xs text-[var(--text-3)]">{s.desc}</div>
                </div>
                {sop === s.id && (
                  <StatusTag status="running" label="当前生效" />
                )}
              </button>
            ))}
          </div>
        </SectionCard>
      </div>

      {/* Member Picker Modal */}
      <MemberPickerModal
        open={memberPickerOpen}
        onClose={() => setMemberPickerOpen(false)}
        wakers={wakers}
        existingIds={members.map((m) => m.waker_id)}
        onConfirm={handleAddMembers}
      />

      {/* Skill Picker Modal */}
      <SkillPickerModal
        open={skillPickerOpen}
        onClose={() => setSkillPickerOpen(false)}
        installed={groupSkills}
        onConfirm={handleAddSkills}
      />

      {/* Transfer Leader Confirmation */}
      {transferTarget && (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center"
          style={{ background: 'rgba(15,23,42,.42)' }}
          onClick={() => setTransferTarget(null)}
        >
          <div
            className="rounded-2xl bg-[var(--panel)] p-6 shadow-[var(--shadow-md)]"
            style={{ width: 420 }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-[var(--text)]">转让群负责人</h3>
            <p className="mt-2 text-sm text-[var(--text-2)]">
              群负责人只能有一位 · {group.name}
            </p>
            <div className="mt-4 rounded-xl bg-[var(--panel-2)] p-4">
              <div className="flex items-center gap-2.5">
                <Avatar name={transferTarget} size="sm" />
                <span className="text-sm font-medium">{transferTarget}</span>
                <StatusTag status="running" label="新负责人" />
              </div>
              {leader && (
                <div className="mt-3 flex items-center gap-2.5 opacity-50">
                  <Avatar name={leader.name} size="sm" />
                  <span className="text-sm text-[var(--text-3)]">{leader.name}</span>
                  <StatusTag status="disabled_waker" label="转为普通成员" />
                </div>
              )}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setTransferTarget(null)}
                className="rounded-lg border border-[var(--border)] px-4 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
              >
                取消
              </button>
              <button
                onClick={() => handleTransferLeader(transferTarget)}
                className="rounded-lg bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-[var(--primary-deep)]"
              >
                确认转让
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
