import { lazy, Suspense } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'

/* ─── Lazy page imports (code splitting) ─── */
const BoardPage = lazy(() => import('./pages/BoardPage'))
const WakersPage = lazy(() => import('./pages/WakersPage'))
const WakerDetailPage = lazy(() => import('./pages/WakerDetailPage'))
const GroupsPage = lazy(() => import('./pages/GroupsPage'))
const FlowsPage = lazy(() => import('./pages/FlowsPage'))
const FlowEditorPage = lazy(() => import('./pages/FlowEditorPage'))
const FlowRunPage = lazy(() => import('./pages/FlowRunPage'))
const SchedulesPage = lazy(() => import('./pages/SchedulesPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const DirectChatPage = lazy(() => import('./pages/DirectChatPage'))
const GroupChatPage = lazy(() => import('./pages/GroupChatPage'))
const GroupConfigPage = lazy(() => import('./pages/GroupConfigPage'))

/* ─── Loading fallback ─── */
function PageLoading() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-sm text-[var(--text-3)]">加载中...</div>
    </div>
  )
}

/* ─── Full-width routes (hide sidebar) ─── */
function isFullWidthRoute(pathname: string): boolean {
  return /^\/flows\/[^/]+/.test(pathname) || /^\/flow-runs\/[^/]+/.test(pathname)
}

/* ─── App ─── */
export default function App() {
  const location = useLocation()
  const fullWidth = isFullWidthRoute(location.pathname)

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: 'var(--bg)' }}>
      {/* Sidebar (hidden on full-width routes) */}
      {!fullWidth && <Sidebar />}

      {/* Main content */}
      <main className={`flex-1 overflow-auto ${fullWidth ? '' : 'min-w-0'}`}>
        <Suspense fallback={<PageLoading />}>
          <Routes>
            {/* Full-width flow pages */}
            <Route path="/flows/:id" element={<FlowEditorPage />} />
            <Route path="/flow-runs/:id" element={<FlowRunPage />} />

            {/* Chat routes */}
            <Route path="/chat/direct/:wakerName" element={<DirectChatPage />} />
            <Route path="/chat/group/:groupId" element={<GroupChatPage />} />

            {/* Standard pages */}
            <Route path="/" element={<BoardPage />} />
            <Route path="/wakers" element={<WakersPage />} />
            <Route path="/wakers/:name" element={<WakerDetailPage />} />
            <Route path="/groups" element={<GroupsPage />} />
            <Route path="/groups/:groupId/config" element={<GroupConfigPage />} />
            <Route path="/flows" element={<FlowsPage />} />
            <Route path="/schedules" element={<SchedulesPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  )
}
