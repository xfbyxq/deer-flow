import { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'
import type { EnumData } from '../types'
import { DEFAULT_DEERFLOW_URL } from '../utils/deerflow'

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

/* ─── Settings Card ─── */
function SettingsCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-[var(--panel)] shadow-[var(--shadow-sm)] border border-[var(--border)] mb-4">
      <div className="px-6 pt-4 pb-2">
        <h3 className="text-[15px] font-semibold text-[var(--text)]">{title}</h3>
      </div>
      {children}
    </div>
  )
}

/* ─── Settings Row ─── */
function SettingsRow({ name, desc, children }: { name: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 px-6 py-3">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-[var(--text)]">{name}</div>
        {desc && <div className="mt-0.5 text-[11.5px] text-[var(--text-3)]">{desc}</div>}
      </div>
      {children}
    </div>
  )
}

/* ─── Settings Page ─── */
export default function SettingsPage() {
  const [healthStatus, setHealthStatus] = useState<{ ok: boolean; message: string } | null>(null)
  const [checking, setChecking] = useState(false)
  const [deerflowUrl, setDeerflowUrl] = useState(
    () => localStorage.getItem('deerflow_url') || DEFAULT_DEERFLOW_URL
  )
  const [saved, setSaved] = useState(false)
  const [models, setModels] = useState<EnumData['models']>([])

  // Settings state
  const [defaultModel, setDefaultModel] = useState('')
  const [compactMode, setCompactMode] = useState(false)
  const [notifyTask, setNotifyTask] = useState(true)
  const [notifyMention, setNotifyMention] = useState(true)
  const [mcpConnected, setMcpConnected] = useState(true)

  // Track whether initial load is done to avoid PUT during mount
  const initialized = useRef(false)

  // Load settings and enum data from backend on mount
  useEffect(() => {
    api.getSettings()
      .then((s) => {
        if (s.default_model) setDefaultModel(s.default_model)
        if (s.density) {
          setCompactMode(s.density === 'compact')
          // 紧凑模式在加载时立即生效，而不仅是切换时
          document.body.classList.toggle('compact', s.density === 'compact')
        }
        if (s.notify_task != null) setNotifyTask(s.notify_task)
        if (s.notify_mention != null) setNotifyMention(s.notify_mention)
      })
      .catch(() => {/* use defaults */})
      .finally(() => { initialized.current = true })

    api.getEnumData()
      .then((e) => {
        setModels(e.models)
        // Set default model to first available if not already set
        if (e.models.length > 0) {
          setDefaultModel((prev) => prev || e.models[0].name)
        }
      })
      .catch(() => {/* use defaults */})
  }, [])

  // Persist settings to backend when they change (skip initial render)
  useEffect(() => {
    if (!initialized.current) return
    api.updateSettings({
      default_model: defaultModel,
      density: compactMode ? 'compact' : null,
      notify_task: notifyTask,
      notify_mention: notifyMention,
    }).catch(() => {/* silent */})
  }, [defaultModel, compactMode, notifyTask, notifyMention])

  const handleHealthCheck = async () => {
    setChecking(true)
    setHealthStatus(null)
    try {
      // /api/health 由 waker-team 后端代理并附带 DeerFlow 连通性探测
      const res = await fetch('/api/health')
      const data = res.ok ? await res.json().catch(() => null) : null
      if (data && data.deerflow === 'ok') {
        setHealthStatus({ ok: true, message: `DeerFlow 连接正常（DB: ${data.db}）` })
      } else if (data) {
        setHealthStatus({ ok: false, message: `DeerFlow 异常：${data.deerflow ?? '未知'}` })
      } else {
        setHealthStatus({ ok: false, message: `HTTP ${res.status}` })
      }
    } catch (err) {
      setHealthStatus({ ok: false, message: err instanceof Error ? err.message : '连接失败' })
    } finally {
      setChecking(false)
    }
  }

  const handleSaveUrl = () => {
    localStorage.setItem('deerflow_url', deerflowUrl)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  const handleCompactToggle = (v: boolean) => {
    setCompactMode(v)
    if (v) {
      document.body.classList.add('compact')
    } else {
      document.body.classList.remove('compact')
    }
  }

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-[800px] px-6 py-6">
        <h1 className="mb-1 text-xl font-bold text-[var(--text)]">系统设置</h1>
        <p className="mb-5 text-sm text-[var(--text-3)]">模型、数据源与通知偏好。</p>

        {/* Model */}
        <SettingsCard title="模型">
          <div className="px-6 pb-5 pt-2">
            <div className="max-w-md">
              <label className="mb-1 block text-xs font-medium text-[var(--text-2)]">
                默认模型（新员工默认使用，可在员工配置中覆盖）
              </label>
              <select
                value={defaultModel}
                onChange={(e) => setDefaultModel(e.target.value)}
                className="w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
              >
                {models.length === 0 && <option value="">加载中...</option>}
                {models.map((m) => (
                  <option key={m.name} value={m.name}>{m.display_name || m.name}</option>
                ))}
              </select>
            </div>
          </div>
        </SettingsCard>

        {/* Display */}
        <SettingsCard title="消息外观">
          <SettingsRow
            name="紧凑模式"
            desc="压缩消息与服务间距，单屏显示更多内容（立即生效）"
          >
            <Toggle on={compactMode} onChange={handleCompactToggle} small />
          </SettingsRow>
        </SettingsCard>

        {/* Notifications */}
        <SettingsCard title="通知">
          <SettingsRow
            name="任务完成提醒"
            desc="Waker 完成任务后推送提醒"
          >
            <Toggle on={notifyTask} onChange={setNotifyTask} small />
          </SettingsRow>
          <SettingsRow
            name="群对话 @ 提醒"
            desc="被 @ 时高亮并推送"
          >
            <Toggle on={notifyMention} onChange={setNotifyMention} small />
          </SettingsRow>
        </SettingsCard>

        {/* Data Sources */}
        <SettingsCard title="数据源">
          <SettingsRow
            name="mcp-atlassian"
            desc="Jira / Confluence 只读接入（全局注册）"
          >
            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${
              mcpConnected ? 'bg-[var(--green-soft)] text-[#0d8a53]' : 'bg-[var(--panel-3)] text-[var(--text-3)]'
            }`}>
              {mcpConnected ? '已连接' : '已断开'}
            </span>
            <button
              onClick={() => setMcpConnected(!mcpConnected)}
              className="ml-2 rounded-md px-2.5 py-1 text-xs font-medium text-[var(--primary)] transition-colors hover:bg-[var(--primary-soft)]"
            >
              {mcpConnected ? '断开' : '重连'}
            </button>
          </SettingsRow>
          <SettingsRow
            name="waker-team 服务"
            desc="伴生服务 API（127.0.0.1:8000）"
          >
            <span className="inline-flex items-center rounded-full bg-[var(--green-soft)] px-2 py-0.5 text-[11.5px] font-semibold text-[#0d8a53]">
              健康
            </span>
          </SettingsRow>
        </SettingsCard>

        {/* DeerFlow Connection */}
        <SettingsCard title="DeerFlow 连接配置">
          <div className="px-6 pb-5 pt-2">
            <div className="flex gap-2">
              <input
                value={deerflowUrl}
                onChange={(e) => setDeerflowUrl(e.target.value)}
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)]"
                placeholder={DEFAULT_DEERFLOW_URL}
              />
              <button
                onClick={handleSaveUrl}
                className="rounded-lg bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--primary-deep)]"
              >
                {saved ? '已保存' : '保存'}
              </button>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <button
                onClick={handleHealthCheck}
                disabled={checking}
                className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)] disabled:opacity-50"
              >
                {checking ? '检查中...' : '健康检查'}
              </button>
              {healthStatus && (
                <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  healthStatus.ok ? 'bg-[var(--green-soft)] text-[#0d8a53]' : 'bg-[var(--red-soft)] text-[#b91c1c]'
                }`}>
                  {healthStatus.message}
                </span>
              )}
            </div>
          </div>
        </SettingsCard>
      </div>
    </div>
  )
}
