import { useState, useMemo } from 'react'
import Modal, { CategoryChip, PickerItem } from './Modal'

export interface SkillDef {
  name: string
  desc: string
  cat: string
}

const SKILL_POOL: SkillDef[] = [
  { name: 'waker-team-assistant', desc: '团队管理助手，自动化列表与配置查询', cat: '数据' },
  { name: 'jira-analyzer', desc: 'Jira 数据分析与趋势报告生成', cat: '数据' },
  { name: 'web-search', desc: '联网搜索与信息聚合', cat: '搜索' },
  { name: 'doc-summarizer', desc: '文档摘要与关键信息提取', cat: '报告' },
  { name: 'code-reviewer', desc: '代码审查与质量建议', cat: '开发' },
  { name: 'test-case-gen', desc: '测试用例自动生成', cat: '开发' },
  { name: 'weekly-report', desc: '周报自动生成与格式化', cat: '报告' },
  { name: 'data-pipeline-check', desc: '数据管道健康检查', cat: '数据' },
  { name: 'api-docs-gen', desc: 'API 文档自动生成', cat: '开发' },
  { name: 'translate-helper', desc: '多语言翻译辅助', cat: '搜索' },
  { name: 'meeting-notes', desc: '会议纪要整理与行动项提取', cat: '报告' },
  { name: 'bug-classifier', desc: 'Bug 严重程度自动分类', cat: '数据' },
]

const SKILL_CATS = ['全部', '数据', '搜索', '报告', '开发']

export interface SkillPickerModalProps {
  open: boolean
  onClose: () => void
  installed: string[]
  onConfirm: (selected: string[]) => void
}

export default function SkillPickerModal({ open, onClose, installed, onConfirm }: SkillPickerModalProps) {
  const [cat, setCat] = useState('全部')
  const [selected, setSelected] = useState<string[]>([])

  const filtered = useMemo(
    () => (cat === '全部' ? SKILL_POOL : SKILL_POOL.filter((s) => s.cat === cat)),
    [cat],
  )

  const toggle = (name: string) => {
    if (installed.includes(name)) return
    setSelected((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    )
  }

  const handleConfirm = () => {
    onConfirm(selected)
    setSelected([])
    setCat('全部')
    onClose()
  }

  const handleClose = () => {
    setSelected([])
    setCat('全部')
    onClose()
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="从技能市场添加"
      subtitle="安装到该员工，执行任务时自动加载"
      footer={
        <>
          <span className="mr-auto text-xs text-[var(--text-3)]">
            已选 {selected.length}/{SKILL_POOL.length} 项
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
      <div className="space-y-3">
        {/* Category filter */}
        <div className="flex flex-wrap gap-1.5">
          {SKILL_CATS.map((c) => (
            <CategoryChip key={c} label={c} active={cat === c} onClick={() => setCat(c)} />
          ))}
        </div>

        {/* Skill list */}
        <div className="space-y-1">
          {filtered.map((s) => {
            const has = installed.includes(s.name)
            const sel = selected.includes(s.name)
            return (
              <PickerItem key={s.name} selected={sel} disabled={has} onClick={() => toggle(s.name)}>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-[var(--text)]">{s.name}</span>
                    {has && (
                      <span className="shrink-0 rounded-full bg-[var(--green-soft)] px-1.5 py-0.5 text-[10px] font-semibold text-[#0d8a53]">
                        已添加
                      </span>
                    )}
                    {!has && (
                      <span className="shrink-0 rounded-full bg-[var(--panel-3)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--text-3)]">
                        {s.cat}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-xs text-[var(--text-3)]">{s.desc}</div>
                </div>
              </PickerItem>
            )
          })}
        </div>
      </div>
    </Modal>
  )
}
