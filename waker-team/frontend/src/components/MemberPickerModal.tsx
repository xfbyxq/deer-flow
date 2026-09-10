import { useState } from 'react'
import Modal, { PickerItem } from './Modal'
import Avatar from './Avatar'
import type { Waker } from '../types'

export interface MemberPickerModalProps {
  open: boolean
  onClose: () => void
  wakers: Waker[]
  existingIds: string[]
  onConfirm: (selected: string[]) => void
}

export default function MemberPickerModal({ open, onClose, wakers, existingIds, onConfirm }: MemberPickerModalProps) {
  const [selected, setSelected] = useState<string[]>([])

  const candidates = wakers.filter((w) => !existingIds.includes(w.name))

  const toggle = (name: string) => {
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
      title="添加群成员"
      subtitle="选择要加入群组的 Waker 员工"
      footer={
        <>
          <span className="mr-auto text-xs text-[var(--text-3)]">
            已选 {selected.length} 人
          </span>
          <button
            onClick={handleClose}
            className="rounded-lg border border-[var(--border)] px-4 py-1.5 text-sm font-medium text-[var(--text-2)] transition-colors hover:bg-[var(--panel-2)]"
          >
            取消
          </button>
          <button
            onClick={handleConfirm}
            disabled={selected.length === 0}
            className="rounded-lg bg-[var(--primary)] px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-[var(--primary-deep)] disabled:opacity-50"
          >
            确认添加{selected.length ? `（${selected.length}）` : ''}
          </button>
        </>
      }
    >
      {candidates.length === 0 ? (
        <div className="py-8 text-center text-sm text-[var(--text-3)]">所有员工都已在群中</div>
      ) : (
        <div className="space-y-1">
          {candidates.map((w) => {
            const sel = selected.includes(w.name)
            return (
              <PickerItem key={w.name} selected={sel} onClick={() => toggle(w.name)}>
                <Avatar name={w.name} size="sm" presence={w.enabled ? 'online' : 'offline'} />
                <div className="min-w-0">
                  <span className="block truncate text-sm font-medium text-[var(--text)]">{w.name}</span>
                  <span className="block truncate text-xs text-[var(--text-3)]">
                    {w.description?.slice(0, 42) || '未设置职责'}
                  </span>
                </div>
              </PickerItem>
            )
          })}
        </div>
      )}
    </Modal>
  )
}
