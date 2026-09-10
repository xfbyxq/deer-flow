import { useState } from 'react'
import Modal, { PickerItem } from './Modal'

export interface McpDef {
  name: string
  desc: string
}

const MCP_POOL: McpDef[] = [
  { name: 'browser-use', desc: '浏览器自动化操控与网页数据提取' },
  { name: 'mcp-atlassian', desc: 'Jira / Confluence 只读接入' },
  { name: 'microsoft-project', desc: 'Microsoft Project 项目数据读取' },
  { name: 'drawio-mcp', desc: 'Draw.io 图表绘制与编辑' },
  { name: 'github-mcp', desc: 'GitHub 仓库、Issue、PR 操作' },
  { name: 'slack-mcp', desc: 'Slack 消息发送与频道管理' },
  { name: 'filesystem-mcp', desc: '本地文件系统读写操作' },
  { name: 'postgres-mcp', desc: 'PostgreSQL 数据库查询' },
]

export interface McpPickerModalProps {
  open: boolean
  onClose: () => void
  installed: string[]
  onConfirm: (selected: string[]) => void
}

export default function McpPickerModal({ open, onClose, installed, onConfirm }: McpPickerModalProps) {
  const [selected, setSelected] = useState<string[]>([])

  const toggle = (name: string) => {
    if (installed.includes(name)) return
    setSelected((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }

  const handleConfirm = () => {
    onConfirm(selected)
    setSelected([])
    onClose()
  }

  const handleClose = () => {
    setSelected([])
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="添加 MCP 连接器"
      subtitle="为该员工接入 MCP 服务，扩展可用工具范围"
      footer={
        <>
          <span className="mr-auto text-xs text-[var(--text-3)]">
            已选 {selected.length}/{MCP_POOL.length} 项
          </span>
          <button
            onClick={handleClose}
            className="rounded-lg border border-[var(--border)] px-4 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            className="rounded-lg bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-[var(--primary-deep)]"
          >
            确认添加{selected.length ? `（${selected.length}）` : ''}
          </button>
        </>
      }
    >
      <div className="space-y-1">
        {MCP_POOL.map((m) => {
          const has = installed.includes(m.name)
          const sel = selected.includes(m.name)
          return (
            <PickerItem key={m.name} selected={sel} disabled={has} onClick={() => toggle(m.name)}>
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--panel-3)] text-[var(--text-3)]">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
                </svg>
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-[var(--text)]">{m.name}</span>
                  {has && (
                    <span className="shrink-0 rounded-full bg-[var(--green-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[#0d8a53]">
                      已连接
                    </span>
                  )}
                </div>
                <div className="mt-0.5 truncate text-xs text-[var(--text-3)]">{m.desc}</div>
              </div>
            </PickerItem>
          )
        })}
      </div>
    </Modal>
  )
}
