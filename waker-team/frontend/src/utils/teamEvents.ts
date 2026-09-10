/**
 * 团队数据变更事件总线.
 *
 * 侧边栏的团队面板（员工/群组列表）在挂载时拉取一次数据；当员工/群组
 * 在页面中被创建、编辑、删除后，需要主动通知侧边栏刷新，否则会一直显示
 * 过期数据（直到整页刷新）。发布方：WakersPage / GroupsPage / WakerDetailPage。
 */

const EVENT_NAME = 'waker-team:data-changed'

export function notifyTeamDataChanged(): void {
  window.dispatchEvent(new Event(EVENT_NAME))
}

export function onTeamDataChanged(handler: () => void): () => void {
  window.addEventListener(EVENT_NAME, handler)
  return () => window.removeEventListener(EVENT_NAME, handler)
}
