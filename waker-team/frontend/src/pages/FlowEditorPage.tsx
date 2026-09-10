import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactFlow, {
  addEdge,
  useNodesState,
  useEdgesState,
  MiniMap,
  Controls,
  Background,
  type Connection,
  type Node,
  type Edge,
  type ReactFlowInstance,
} from 'reactflow'
import 'reactflow/dist/style.css'

import { api } from '../api/client'
import type { FlowDef, FlowNodeDef } from '../types'
import { nodeTypes, NODE_PALETTE } from '../components/flow/FlowNodes'
import FlowToolbar from '../components/flow/FlowToolbar'
import NodeConfigPanel from '../components/flow/NodeConfigPanel'

/* ─── Helpers ─── */
let nodeIdCounter = 0
function genNodeId(type: string) {
  return `${type}_${++nodeIdCounter}`
}

/** Simple topological layer assignment */
function layoutNodes(nodes: Node[], edges: Edge[]): Node[] {
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
  // BFS layers
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
  // Assign positions
  const layerNodes = new Map<number, string[]>()
  for (const [id, l] of layer) {
    if (!layerNodes.has(l)) layerNodes.set(l, [])
    layerNodes.get(l)!.push(id)
  }
  const xGap = 280
  const yGap = 180
  return nodes.map((n) => {
    const l = layer.get(n.id) ?? 0
    const siblings = layerNodes.get(l) ?? []
    const idx = siblings.indexOf(n.id)
    return {
      ...n,
      position: { x: l * xGap + 100, y: idx * yGap + 100 },
    }
  })
}

/** Convert FlowDefinitionV1 nodes to React Flow nodes + edges */
function defToFlow(def: FlowDef): { nodes: Node[]; edges: Edge[] } {
  const defNodes = def.definition_json?.nodes ?? []
  const rfNodes: Node[] = defNodes.map((dn) => ({
    id: dn.key,
    type: dn.type,
    position: { x: 0, y: 0 },
    data: {
      label: dn.key,
      waker: dn.waker,
      channel: dn.channel,
      expression: dn.expression,
    },
  }))
  const rfEdges: Edge[] = []
  const keySet = new Set(defNodes.map((n) => n.key))
  for (const dn of defNodes) {
    for (const dep of dn.depends_on) {
      if (keySet.has(dep)) {
        rfEdges.push({ id: `${dep}->${dn.key}`, source: dep, target: dn.key, animated: false })
      }
    }
  }
  return { nodes: layoutNodes(rfNodes, rfEdges), edges: rfEdges }
}

/** Convert React Flow nodes + edges back to FlowDefinitionV1 */
function flowToDef(
  flowDef: FlowDef,
  rfNodes: Node[],
  rfEdges: Edge[],
  nodeDataMap: Map<string, Partial<FlowNodeDef>>,
): FlowDef {
  const nodes: FlowNodeDef[] = rfNodes.map((n) => {
    const existing = nodeDataMap.get(n.id)
    return {
      key: n.id,
      type: n.type as FlowNodeDef['type'],
      waker: existing?.waker ?? n.data?.waker,
      instruction: existing?.instruction,
      depends_on: rfEdges.filter((e) => e.target === n.id).map((e) => e.source),
      input_from: existing?.input_from,
      timeout_seconds: existing?.timeout_seconds,
      timeout_hours: existing?.timeout_hours,
      checklist: existing?.checklist,
      expression: existing?.expression ?? n.data?.expression,
      branches: existing?.branches,
      channel: existing?.channel ?? n.data?.channel,
      payload: existing?.payload,
    }
  })
  return {
    ...flowDef,
    definition_json: {
      ...flowDef.definition_json,
      version: flowDef.definition_json?.version ?? 1,
      nodes,
    },
  }
}

/* ─── JSON Import Dialog ─── */
function ImportDialog({
  onImport,
  onClose,
}: {
  onImport: (json: string) => void
  onClose: () => void
}) {
  const [text, setText] = useState('')
  const [error, setError] = useState('')

  const handleImport = () => {
    try {
      JSON.parse(text)
      onImport(text)
    } catch {
      setError('JSON 格式错误')
    }
  }

  return (
    <div className="drawer-overlay fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="modal-panel w-full max-w-lg rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <h2 className="text-lg font-semibold text-gray-900">导入 JSON</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        <div className="space-y-4 px-6 py-4">
          {error && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
          <textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setError('') }}
            rows={12}
            className="w-full resize-y rounded-md border border-gray-200 px-3 py-2 font-mono text-xs leading-relaxed outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
            placeholder='粘贴 FlowDefinitionV1 JSON...'
          />
          <div className="flex justify-end gap-2">
            <button onClick={onClose} className="rounded-md border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50">取消</button>
            <button onClick={handleImport} className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">导入</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── Flow Editor Page ─── */
export default function FlowEditorPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [flowDef, setFlowDef] = useState<FlowDef | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [readOnly] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [runError, setRunError] = useState('')

  const [rfNodes, setRfNodes, onNodesChange] = useNodesState([])
  const [rfEdges, setRfEdges, onEdgesChange] = useEdgesState([])
  const [nodeDataMap, setNodeDataMap] = useState<Map<string, Partial<FlowNodeDef>>>(new Map())
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const [rfInstance, setRfInstance] = useState<ReactFlowInstance | null>(null)

  // Load flow definition
  useEffect(() => {
    if (!id) return
    api.getFlow(id)
      .then((f) => {
        setFlowDef(f)
        const { nodes, edges } = defToFlow(f)
        setRfNodes(nodes)
        setRfEdges(edges)
        // Populate data map
        const map = new Map<string, Partial<FlowNodeDef>>()
        for (const dn of f.definition_json?.nodes ?? []) {
          map.set(dn.key, dn)
        }
        setNodeDataMap(map)
      })
      .catch(() => { /* ignore */ })
      .finally(() => setLoading(false))
  }, [id, setRfNodes, setRfEdges])

  // Connection handler
  const onConnect = useCallback(
    (params: Connection) => setRfEdges((eds) => addEdge({ ...params, animated: false }, eds)),
    [setRfEdges],
  )

  // Node drag & drop
  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const type = e.dataTransfer.getData('application/reactflow-type')
      if (!type || !rfInstance || !reactFlowWrapper.current) return

      const bounds = reactFlowWrapper.current.getBoundingClientRect()
      const position = rfInstance.project({
        x: e.clientX - bounds.left,
        y: e.clientY - bounds.top,
      })

      const newNode: Node = {
        id: genNodeId(type),
        type,
        position,
        data: { label: genNodeId(type) },
      }
      setRfNodes((nds) => [...nds, newNode])
      setNodeDataMap((m) => {
        const next = new Map(m)
        next.set(newNode.id, { key: newNode.id, type: type as FlowNodeDef['type'], depends_on: [] })
        return next
      })
    },
    [rfInstance, setRfNodes],
  )

  // Node click → show config panel
  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNodeId(node.id)
  }, [])

  // Update node data from config panel
  const handleNodeDataChange = useCallback((key: string, data: Partial<FlowNodeDef>) => {
    setNodeDataMap((m) => {
      const next = new Map(m)
      next.set(key, data)
      return next
    })
  }, [])

  // Delete node
  const handleNodeDelete = useCallback((key: string) => {
    setRfNodes((nds) => nds.filter((n) => n.id !== key))
    setRfEdges((eds) => eds.filter((e) => e.source !== key && e.target !== key))
    setNodeDataMap((m) => {
      const next = new Map(m)
      next.delete(key)
      return next
    })
    setSelectedNodeId(null)
  }, [setRfNodes, setRfEdges])

  // Save
  const handleSave = useCallback(async () => {
    if (!flowDef || !id) return
    setSaving(true)
    try {
      const updated = flowToDef(flowDef, rfNodes, rfEdges, nodeDataMap)
      await api.updateFlow(id, { definition_json: updated.definition_json })
      setFlowDef(updated)
    } catch { /* ignore */ }
    finally { setSaving(false) }
  }, [flowDef, id, rfNodes, rfEdges, nodeDataMap])

  // Run：启动成功后直接跳转运行详情页
  const handleRun = useCallback(async () => {
    if (!id) return
    setRunError('')
    try {
      const run = await api.startFlowRun(id)
      navigate(`/flow-runs/${run.id}`)
    } catch (err) {
      setRunError(err instanceof Error ? err.message : '运行失败')
    }
  }, [id, navigate])

  // Export JSON
  const handleExport = useCallback(() => {
    if (!flowDef) return
    const exported = flowToDef(flowDef, rfNodes, rfEdges, nodeDataMap)
    const json = JSON.stringify(exported.definition_json, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${flowDef.name || 'flow'}.json`
    a.click()
    URL.revokeObjectURL(url)
  }, [flowDef, rfNodes, rfEdges, nodeDataMap])

  // Import JSON
  const handleImport = useCallback((json: string) => {
    try {
      const def: FlowDef['definition_json'] = JSON.parse(json)
      if (!def.nodes || !Array.isArray(def.nodes)) {
        alert('无效的 Flow 定义')
        return
      }
      const tempDef: FlowDef = {
        ...(flowDef as FlowDef),
        definition_json: def,
      }
      const { nodes, edges } = defToFlow(tempDef)
      setRfNodes(nodes)
      setRfEdges(edges)
      const map = new Map<string, Partial<FlowNodeDef>>()
      for (const dn of def.nodes) {
        map.set(dn.key, dn)
      }
      setNodeDataMap(map)
      setImportOpen(false)
    } catch {
      alert('JSON 解析失败')
    }
  }, [flowDef, setRfNodes, setRfEdges])

  // Zoom controls
  const handleZoomIn = useCallback(() => rfInstance?.zoomIn(), [rfInstance])
  const handleZoomOut = useCallback(() => rfInstance?.zoomOut(), [rfInstance])
  const handleFitView = useCallback(() => rfInstance?.fitView(), [rfInstance])

  // Selected node data
  const selectedNode = useMemo(() => {
    if (!selectedNodeId) return null
    return rfNodes.find((n) => n.id === selectedNodeId) ?? null
  }, [selectedNodeId, rfNodes])

  if (loading) return <div className="py-20 text-center text-gray-400">加载中...</div>
  if (!flowDef) return <div className="py-20 text-center text-gray-400">Flow 不存在</div>

  return (
    <div className="flex h-full flex-col">
      <FlowToolbar
        flowName={flowDef.name}
        onSave={handleSave}
        onRun={handleRun}
        onImport={() => setImportOpen(true)}
        onExport={handleExport}
        onZoomIn={handleZoomIn}
        onZoomOut={handleZoomOut}
        onFitView={handleFitView}
        onBack={() => navigate('/flows')}
        readOnly={readOnly}
        saving={saving}
      />

      {runError && (
        <div className="flex items-center justify-between border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          <span>运行失败：{runError}</span>
          <button onClick={() => setRunError('')} className="ml-2 text-red-400 hover:text-red-600">✕</button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* Left: Node Palette */}
        {!readOnly && (
          <div className="w-48 border-r border-gray-200 bg-gray-50 p-3">
            <h3 className="mb-2 text-xs font-semibold uppercase text-gray-500">节点类型</h3>
            <div className="space-y-1.5">
              {NODE_PALETTE.map((item) => (
                <div
                  key={item.type}
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/reactflow-type', item.type)
                    e.dataTransfer.effectAllowed = 'move'
                  }}
                  className="flex cursor-grab items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm shadow-sm transition-shadow hover:shadow-md active:cursor-grabbing"
                >
                  <span>{item.icon}</span>
                  <span className="font-medium" style={{ color: item.color }}>{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Center: React Flow Canvas */}
        <div ref={reactFlowWrapper} className="flex-1">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onInit={setRfInstance}
            onDrop={onDrop}
            onDragOver={onDragOver}
            onNodeClick={onNodeClick}
            onPaneClick={() => setSelectedNodeId(null)}
            nodeTypes={nodeTypes}
            fitView
            proOptions={{ hideAttribution: true }}
          >
            <Controls />
            <MiniMap
              nodeStrokeWidth={3}
              zoomable
              pannable
              className="!bg-white"
            />
            <Background gap={16} size={1} />
          </ReactFlow>
        </div>

        {/* Right: Config Panel */}
        {selectedNode && (
          <NodeConfigPanel
            nodeKey={selectedNode.id}
            nodeType={selectedNode.type as FlowNodeDef['type']}
            initialData={nodeDataMap.get(selectedNode.id) ?? { key: selectedNode.id, type: selectedNode.type as FlowNodeDef['type'], depends_on: [] }}
            allNodeKeys={rfNodes.map((n) => n.id)}
            onChange={handleNodeDataChange}
            onDelete={handleNodeDelete}
            onClose={() => setSelectedNodeId(null)}
            readOnly={readOnly}
          />
        )}
      </div>

      {/* Import Dialog */}
      {importOpen && (
        <ImportDialog
          onImport={handleImport}
          onClose={() => setImportOpen(false)}
        />
      )}
    </div>
  )
}
