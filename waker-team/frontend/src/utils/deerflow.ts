/**
 * DeerFlow 连接配置工具.
 *
 * `deerflow_url` 由设置页写入 localStorage；未配置时回退到本地默认网关地址。
 * 所有需要跳转到 DeerFlow（如任务运行详情）的地方都应使用本模块，
 * 禁止使用 window.location.origin（那是 WakerTeam 自身，会导致链接无效）。
 */

export const DEFAULT_DEERFLOW_URL = 'http://localhost:2026'

export function getDeerflowBaseUrl(): string {
  const stored = localStorage.getItem('deerflow_url')
  return (stored && stored.trim()) || DEFAULT_DEERFLOW_URL
}

/** 构造 DeerFlow 线程（运行详情）页 URL */
export function deerflowThreadUrl(threadId: string): string {
  const base = getDeerflowBaseUrl().replace(/\/+$/, '')
  return `${base}/?thread=${encodeURIComponent(threadId)}`
}
