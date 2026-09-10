import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'

/* ─── Base wrapper ─── */
function NodeShell({
  children,
  color,
  shape = 'rect',
  label,
  sublabel,
}: {
  children?: React.ReactNode
  color: string
  shape?: 'rect' | 'diamond'
  label: string
  sublabel?: string
}) {
  if (shape === 'diamond') {
    return (
      <div className="relative flex flex-col items-center justify-center" style={{ width: 160, height: 160 }}>
        <div
          className="absolute inset-0 rotate-45 rounded-lg border-2"
          style={{ borderColor: color, backgroundColor: `${color}15` }}
        />
        <div className="relative z-10 flex flex-col items-center text-center">
          <span className="text-xs font-bold" style={{ color }}>{label}</span>
          {sublabel && <span className="mt-0.5 text-[10px] text-gray-500 max-w-[100px] truncate">{sublabel}</span>}
          {children}
        </div>
      </div>
    )
  }
  return (
    <div
      className="rounded-lg border-2 px-4 py-3 shadow-sm min-w-[160px]"
      style={{ borderColor: color, backgroundColor: `${color}10` }}
    >
      <div className="flex flex-col items-center text-center">
        <span className="text-xs font-bold" style={{ color }}>{label}</span>
        {sublabel && <span className="mt-0.5 text-[10px] text-gray-500 max-w-[140px] truncate">{sublabel}</span>}
        {children}
      </div>
    </div>
  )
}

/* ─── waker_task ─── */
export const WakerTaskNode = memo(({ data }: NodeProps) => (
  <NodeShell color="#3b82f6" label="Waker Task" sublabel={data.waker || data.label}>
    <Handle type="target" position={Position.Top} className="!bg-blue-500" />
    <Handle type="source" position={Position.Bottom} className="!bg-blue-500" />
  </NodeShell>
))

/* ─── leader_plan ─── */
export const LeaderPlanNode = memo(({ data }: NodeProps) => (
  <NodeShell color="#8b5cf6" label="Plan" sublabel={data.label}>
    <Handle type="target" position={Position.Top} className="!bg-violet-500" />
    <Handle type="source" position={Position.Bottom} className="!bg-violet-500" />
  </NodeShell>
))

/* ─── human_review ─── */
export const HumanReviewNode = memo(({ data }: NodeProps) => (
  <NodeShell color="#f97316" shape="diamond" label="Review" sublabel={data.label}>
    <Handle type="target" position={Position.Top} className="!bg-orange-500" />
    <Handle type="source" position={Position.Bottom} className="!bg-orange-500" />
  </NodeShell>
))

/* ─── condition ─── */
export const ConditionNode = memo(({ data }: NodeProps) => (
  <NodeShell color="#eab308" shape="diamond" label="Condition" sublabel={data.expression || data.label}>
    <Handle type="target" position={Position.Top} className="!bg-yellow-500" />
    <Handle type="source" position={Position.Bottom} id="default" className="!bg-yellow-500" />
    <Handle type="source" position={Position.Right} id="branch" className="!bg-yellow-500" />
  </NodeShell>
))

/* ─── notify ─── */
export const NotifyNode = memo(({ data }: NodeProps) => (
  <NodeShell color="#22c55e" label="Notify" sublabel={data.channel || data.label}>
    <Handle type="target" position={Position.Top} className="!bg-green-500" />
    <Handle type="source" position={Position.Bottom} className="!bg-green-500" />
  </NodeShell>
))

/* ─── Node type map for React Flow ─── */
export const nodeTypes = {
  waker_task: WakerTaskNode,
  leader_plan: LeaderPlanNode,
  human_review: HumanReviewNode,
  condition: ConditionNode,
  notify: NotifyNode,
}

/* ─── Node palette items ─── */
export const NODE_PALETTE = [
  { type: 'waker_task', label: 'Waker Task', color: '#3b82f6', icon: '🤖' },
  { type: 'leader_plan', label: 'Leader Plan', color: '#8b5cf6', icon: '📋' },
  { type: 'human_review', label: 'Human Review', color: '#f97316', icon: '👤' },
  { type: 'condition', label: 'Condition', color: '#eab308', icon: '🔀' },
  { type: 'notify', label: 'Notify', color: '#22c55e', icon: '📢' },
] as const
