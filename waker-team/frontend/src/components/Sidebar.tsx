import { useState, useEffect, useMemo } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import { api } from '../api/client'
import type { Waker, Group } from '../types'
import Avatar from './Avatar'
import { onTeamDataChanged } from '../utils/teamEvents'

/* ─── Icons (inline SVG) ─── */
const IconBoard = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1.5" y="1.5" width="4" height="13" rx="1" />
    <rect x="6.5" y="1.5" width="4" height="8" rx="1" />
    <rect x="11.5" y="1.5" width="3" height="5" rx="1" />
  </svg>
)
const IconFlow = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="3" cy="4" r="1.5" />
    <circle cx="13" cy="4" r="1.5" />
    <circle cx="8" cy="12" r="1.5" />
    <path d="M4.2 4.8L6.8 10.8M11.8 4.8L9.2 10.8" />
  </svg>
)
const IconSchedule = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="6.5" />
    <path d="M8 4v4l2.5 1.5" />
  </svg>
)
const IconUsers = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="6" cy="5" r="2.5" />
    <path d="M1.5 13.5c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4" />
    <circle cx="12" cy="5.5" r="2" />
    <path d="M12 9.5c1.5 0 2.5 1 2.5 2.5" />
  </svg>
)
const IconGroup = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="5" cy="5.5" r="2" />
    <circle cx="11" cy="5.5" r="2" />
    <circle cx="8" cy="11" r="2" />
    <path d="M3.5 9c-1 .5-1.5 1.5-1.5 2.5M12.5 9c1 .5 1.5 1.5 1.5 2.5M6 13h4" />
  </svg>
)
const IconCollapse = ({ collapsed }: { collapsed: boolean }) => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    {collapsed ? (
      <path d="M6 3l5 5-5 5" />
    ) : (
      <path d="M10 3L5 8l5 5" />
    )}
  </svg>
)

/* ─── Nav item component ─── */
function NavItem({
  to,
  icon,
  label,
  collapsed,
  end,
}: {
  to: string
  icon: React.ReactNode
  label: string
  collapsed: boolean
  end?: boolean
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `nav-item ${isActive ? 'active' : ''}`
      }
      title={collapsed ? label : undefined}
    >
      {icon}
      <span className="nav-label">{label}</span>
    </NavLink>
  )
}

/* ─── Team Panel ─── */
function TeamPanel({
  wakers,
  groups,
  onNavigate,
}: {
  wakers: Waker[]
  groups: Group[]
  onNavigate: (path: string) => void
}) {
  const [tab, setTab] = useState<'wakers' | 'groups'>('wakers')
  const [search, setSearch] = useState('')

  const filteredWakers = useMemo(() => {
    if (!search) return wakers
    const q = search.toLowerCase()
    return wakers.filter((w) => w.name.toLowerCase().includes(q) || w.description?.toLowerCase().includes(q))
  }, [wakers, search])

  const filteredGroups = useMemo(() => {
    if (!search) return groups
    const q = search.toLowerCase()
    return groups.filter((g) => g.name.toLowerCase().includes(q))
  }, [groups, search])

  return (
    <div className="team-panel">
      {/* Tabs */}
      <div className="team-tabs">
        <button
          className={`team-tab ${tab === 'wakers' ? 'active' : ''}`}
          onClick={() => { setTab('wakers'); setSearch('') }}
        >
          员工 <span className="tab-count">{wakers.length}</span>
        </button>
        <button
          className={`team-tab ${tab === 'groups' ? 'active' : ''}`}
          onClick={() => { setTab('groups'); setSearch('') }}
        >
          群组 <span className="tab-count">{groups.length}</span>
        </button>
      </div>

      {/* Search */}
      <div className="team-search">
        <input
          placeholder={tab === 'wakers' ? '搜索员工...' : '搜索群组...'}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* List */}
      <div className="team-list">
        {tab === 'wakers'
          ? filteredWakers.map((w) => (
              <button
                key={w.name}
                className="team-item"
                onClick={() => onNavigate(`/chat/direct/${encodeURIComponent(w.name)}`)}
              >
                <Avatar
                  name={w.name}
                  size="sm"
                  presence={w.enabled ? 'online' : 'offline'}
                />
                <div className="team-item-main">
                  <div className="team-item-name">{w.name}</div>
                  <div className="team-item-desc">{w.description || '未配置'}</div>
                </div>
              </button>
            ))
          : filteredGroups.map((g) => (
              <button
                key={g.id}
                className="team-item"
                onClick={() => onNavigate(`/chat/group/${encodeURIComponent(g.id)}`)}
              >
                <div className="avatar-stack">
                  <Avatar name={g.name} size="sm" />
                </div>
                <div className="team-item-main">
                  <div className="team-item-name">{g.name}</div>
                  <div className="team-item-desc">
                    {g.leader_waker_id ? `Leader: ${g.leader_waker_id}` : '未指定 Leader'}
                  </div>
                </div>
              </button>
            ))}
        {tab === 'wakers' && filteredWakers.length === 0 && (
          <div className="py-6 text-center text-xs text-[var(--text-3)]">无匹配员工</div>
        )}
        {tab === 'groups' && filteredGroups.length === 0 && (
          <div className="py-6 text-center text-xs text-[var(--text-3)]">无匹配群组</div>
        )}
      </div>
    </div>
  )
}

/* ─── Main Sidebar ─── */
export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(() => {
    return localStorage.getItem('sidebar_collapsed') === 'true'
  })
  const navigate = useNavigate()
  const location = useLocation()
  const [wakers, setWakers] = useState<Waker[]>([])
  const [groups, setGroups] = useState<Group[]>([])

  // Persist collapse state
  useEffect(() => {
    localStorage.setItem('sidebar_collapsed', String(collapsed))
  }, [collapsed])

  // Load team data（挂载时、路由变化时、页面内增删改后由事件触发刷新）
  useEffect(() => {
    const reload = () => {
      api.listWakers().then(setWakers).catch(() => {})
      api.listGroups().then(setGroups).catch(() => {})
    }
    reload()
    const off = onTeamDataChanged(reload)
    return off
  }, [location.pathname])

  return (
    <aside className={`sidebar ${collapsed ? 'collapsed' : ''}`}>
      {/* Brand header */}
      <div className="sidebar-head">
        <div className="brand">
          <div className="brand-logo">W</div>
          <span className="brand-name">WakerTeam</span>
        </div>
        <button
          className="icon-btn"
          onClick={() => setCollapsed((c) => !c)}
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          <IconCollapse collapsed={collapsed} />
        </button>
      </div>

      {/* Navigation */}
      <nav className="nav">
        <div className="nav-label">工作管理</div>
        <NavItem to="/" icon={<IconBoard />} label="看板" collapsed={collapsed} end />
        <NavItem to="/flows" icon={<IconFlow />} label="流程" collapsed={collapsed} />
        <NavItem to="/schedules" icon={<IconSchedule />} label="调度" collapsed={collapsed} />

        <div className="nav-label">员工资源</div>
        <NavItem to="/wakers" icon={<IconUsers />} label="员工" collapsed={collapsed} />
        <NavItem to="/groups" icon={<IconGroup />} label="群组" collapsed={collapsed} />
      </nav>

      {/* Team panel (hidden when collapsed) */}
      {!collapsed && (
        <TeamPanel
          wakers={wakers}
          groups={groups}
          onNavigate={(path) => navigate(path)}
        />
      )}

      {/* User footer */}
      <div className="sidebar-foot">
        <button className="user-chip" onClick={() => navigate('/settings')}>
          <Avatar name="Admin" size="sm" />
          <div className="user-meta">
            <b>Admin</b>
          </div>
          <span className="foot-icon">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="8" cy="3" r="1" />
              <circle cx="8" cy="8" r="1" />
              <circle cx="8" cy="13" r="1" />
            </svg>
          </span>
        </button>
      </div>
    </aside>
  )
}
