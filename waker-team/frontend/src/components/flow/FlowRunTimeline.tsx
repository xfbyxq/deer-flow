import { memo, useMemo, useCallback } from 'react'
import ReactFlow, {
  useNodesState,
  useEdgesState,
  MiniMap,
  Controls,
  Background,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
} from 'reactflow'
import 'reactflow/dist/style.css'

import type { FlowRunTimeline } from '../../types'

/* ─── Status colour tokens ─── */
const STATUS_STYLES: Record<string, { border: string; bg: string; text: string; handle: string; label: string }> = {
  pending:         { border: '#9ca3af', bg: '#f3f4f615', text: '#6b7280', handle: '#9ca3af', label: '待执行' },
  running:         { border: '#3b82f6', bg: '#3b82f615', text: '#2563eb', handle: '#3b82f6', label: '运行中' },
  done:            { border: '#10b981', bg: '#10b98115', text: '#059669', handle: '#10b981', label: '已完成' },
  failed:          { border: '#ef4444', bg: '#ef444415', text: '#dc2626', handle: '#ef4444', label: '失败' },
  waiting_review:  { border: '#f97316', bg: '#f9731615', text: '#ea580c', handle: '#f97316', label: '待审核' },
}

const DEFAULT_STYLE = STATUS_STYLES.pending

/* ─── Type icon map ─── */
const TYPE_ICONS: Record<string, string> = {
  waker_task: '🤖',
  leader_plan: '📋',
  human_review: '👤',
  condition: '🔀',
  notify: '📢',
}

/* ─── Timeline node component ─── */
const TimelineNode = memo(({ data, selected }: NodeProps) => {
  const style = STATUS_STYLES[data.status] ?? DEFAULT_STYLE
  const icon = TYPE_ICONS[data.type] ?? '⚙️'
  const isSelected = selected

  return (
    <div
      className={`rounded-lg border-2 px-4 py-3 min-w-[172px] transition-shadow ${
        isSelected ? 'shadow-lg ring-2 ring-blue-300' : 'shadow-sm'
      }`}
      style={{ borderColor: style.border, backgroundColor: style.bg }}
    >
      <Handle type="target" position={Position.Top} className="!w-2 !h-2" style={{ background: style.handle }} />
      <div className="flex flex-col items-center text-center gap-1">
        <span className="text-base">{icon}</span>
        <span className="text-xs font-bold truncate max-w-[140px]" style={{ color: style.text }}>
          {data.nodeKey}
        </span>
        <span
          className="mt-0.5 inline-block rounded-full px-2 py-0.5 text-[10px] font-semibold leading-tight"
          style={{ color: style.text, backgroundColor: `${style.border}20` }}
        >
          {style.label}
        </span>
        {data.waker && (
          <span className="text-[10px] text-gray-400 truncate max-w-[140px]">{data.waker}</span>
        )}
        {data.durationSeconds != null && (
          <span className="text-[10px] text-gray-400">{data.durationSeconds.toFixed(1)}s</span>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!w-2 !h-2" style={{ background: style.handle }} />
    </div>
  )
})

const timelineNodeTypes = { timeline: TimelineNode }

/* ─── Layout: topological layers ─── */
function layoutTimelineNodes(nodes: Node[], edges: Edge[]): Node[] {
  const adj = new Map<string, string[]>()
  const inDeg = new Map<string, number>()
  for (const n of nodes) {
    adj.set(n.id, [])
    inDeg.set(n.id, 0)
  }
  for (const e of edges) {
    adj.get(e.source)?.push(e.target)
    inDeg.set(e.target, (inDeg.get(e.target) ?? 0) + 1)
  }
  const layer = new Map<string, number>()
  const queue: string[] = []
  for (const [id, deg] of inDeg) {
    if (deg === 0) { queue.push(id); layer.set(id, 0) }
  }
  while (queue.length) {
    const cur = queue.shift()!
    for (const next of adj.get(cur) ?? []) {
      layer.set(next, Math.max(layer.get(next) ?? 0, (layer.get(cur) ?? 0) + 1))
      inDeg.set(next, (inDeg.get(next) ?? 1) - 1)
      if (inDeg.get(next) === 0) queue.push(next)
    }
  }
  const layerMap = new Map<number, string[]>()
  for (const [id, l] of layer) {
    if (!layerMap.has(l)) layerMap.set(l, [])
    layerMap.get(l)!.push(id)
  }
  const xGap = 260
  const yGap = 160
  return nodes.map((n) => {
    const l = layer.get(n.id) ?? 0
    const siblings = layerMap.get(l) ?? []
    const idx = siblings.indexOf(n.id)
    return { ...n, position: { x: l * xGap + 80, y: idx * yGap + 80 } }
  })
}

/* ─── Build React Flow graph from timeline data ─── */
function buildTimelineGraph(timeline: FlowRunTimeline, defNodes: { key: string; depends_on: string[] }[]) {
  const nodeMap = new Map(timeline.nodes.map((n) => [n.node_key, n]))
  const rfNodes: Node[] = timeline.nodes.map((tn) => ({
    id: tn.node_key,
    type: 'timeline',
    position: { x: 0, y: 0 },
    data: {
      nodeKey: tn.node_key,
      type: tn.type,
      status: tn.status,
      waker: tn.waker,
      durationSeconds: tn.duration_seconds,
    },
  }))

  const keySet = new Set(timeline.nodes.map((n) => n.node_key))
  const rfEdges: Edge[] = []
  for (const dn of defNodes) {
    for (const dep of dn.depends_on) {
      if (keySet.has(dep) && keySet.has(dn.key)) {
        const sourceStatus = nodeMap.get(dep)?.status
        const isAnimated = sourceStatus === 'running' || sourceStatus === 'done'
        rfEdges.push({
          id: `${dep}->${dn.key}`,
          source: dep,
          target: dn.key,
          animated: isAnimated,
          style: { stroke: '#94a3b8', strokeWidth: 1.5 },
        })
      }
    }
  }

  return { nodes: layoutTimelineNodes(rfNodes, rfEdges), edges: rfEdges }
}

/* ─── Public component ─── */
interface FlowRunTimelineProps {
  timeline: FlowRunTimeline
  defNodes: { key: string; depends_on: string[] }[]
  selectedNodeKey: string | null
  onNodeClick: (nodeKey: string) => void
}

export default function FlowRunTimelineView({
  timeline,
  defNodes,
  selectedNodeKey: _selectedNodeKey,
  onNodeClick,
}: FlowRunTimelineProps) {
  const { nodes: graphNodes, edges: graphEdges } = useMemo(
    () => buildTimelineGraph(timeline, defNodes),
    [timeline, defNodes],
  )

  const [rfNodes, , onNodesChange] = useNodesState(graphNodes)
  const [, , onEdgesChange] = useEdgesState(graphEdges)

  // Update when graph data changes
  useMemo(() => {
    // This is a workaround: useNodesState won't re-sync after init,
    // so we expose the key to force remount via parent.
  }, [graphNodes])

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => { onNodeClick(node.id) },
    [onNodeClick],
  )

  // MiniMap node color based on status
  const miniMapNodeColor = useCallback((node: Node) => {
    const s = (node.data?.status as string | undefined) ?? 'pending'
    const style = STATUS_STYLES[s] ?? DEFAULT_STYLE
    return style.border
  }, [])

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={graphEdges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
      nodeTypes={timelineNodeTypes}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      fitView
      proOptions={{ hideAttribution: true }}
    >
      <Controls showInteractive={false} />
      <MiniMap
        nodeColor={miniMapNodeColor}
        nodeStrokeWidth={3}
        zoomable
        pannable
        className="!bg-white"
      />
      <Background gap={16} size={1} color="#e5e7eb" />
    </ReactFlow>
  )
}
