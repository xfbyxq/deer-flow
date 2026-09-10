import { type ReactNode } from 'react'

interface ToolbarButtonProps {
  onClick: () => void
  disabled?: boolean
  children: ReactNode
  variant?: 'default' | 'primary' | 'danger'
  title?: string
}

function ToolbarButton({ onClick, disabled, children, variant = 'default', title }: ToolbarButtonProps) {
  const base = 'rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:opacity-40'
  const variants = {
    default: 'border border-gray-200 bg-white text-gray-700 hover:bg-gray-50',
    primary: 'bg-blue-600 text-white hover:bg-blue-700',
    danger: 'border border-red-200 text-red-600 hover:bg-red-50',
  }
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${variants[variant]}`} title={title}>
      {children}
    </button>
  )
}

interface FlowToolbarProps {
  flowName: string
  onSave: () => void
  onRun: () => void
  onImport: () => void
  onExport: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onFitView: () => void
  onBack: () => void
  readOnly?: boolean
  saving?: boolean
}

export default function FlowToolbar({
  flowName,
  onSave,
  onRun,
  onImport,
  onExport,
  onZoomIn,
  onZoomOut,
  onFitView,
  onBack,
  readOnly = false,
  saving = false,
}: FlowToolbarProps) {
  return (
    <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-4 py-2">
      <button
        onClick={onBack}
        className="mr-2 rounded-md p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700"
        title="返回"
      >
        ←
      </button>
      <h2 className="text-sm font-semibold text-gray-800 truncate max-w-[200px]">{flowName}</h2>
      {readOnly && (
        <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-500">只读</span>
      )}
      <div className="ml-auto flex items-center gap-1.5">
        <ToolbarButton onClick={onZoomIn} title="放大">+</ToolbarButton>
        <ToolbarButton onClick={onZoomOut} title="缩小">−</ToolbarButton>
        <ToolbarButton onClick={onFitView} title="适应视图">⊞</ToolbarButton>
        <div className="mx-1 h-5 w-px bg-gray-200" />
        <ToolbarButton onClick={onImport} title="导入 JSON">导入</ToolbarButton>
        <ToolbarButton onClick={onExport} title="导出 JSON">导出</ToolbarButton>
        {!readOnly && (
          <>
            <div className="mx-1 h-5 w-px bg-gray-200" />
            <ToolbarButton onClick={onSave} disabled={saving} variant="primary" title="保存">
              {saving ? '保存中...' : '保存'}
            </ToolbarButton>
            <ToolbarButton onClick={onRun} variant="primary" title="运行 Flow">▶ 运行</ToolbarButton>
          </>
        )}
      </div>
    </div>
  )
}
