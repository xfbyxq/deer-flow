import { useState, useEffect } from 'react'
import type { FlowNodeDef } from '../../types'

interface NodeConfigPanelProps {
  nodeKey: string
  nodeType: FlowNodeDef['type']
  initialData: Partial<FlowNodeDef>
  allNodeKeys: string[]
  onChange: (key: string, data: Partial<FlowNodeDef>) => void
  onDelete: (key: string) => void
  onClose: () => void
  readOnly?: boolean
}

export default function NodeConfigPanel({
  nodeKey,
  nodeType,
  initialData,
  allNodeKeys,
  onChange,
  onDelete,
  onClose,
  readOnly = false,
}: NodeConfigPanelProps) {
  const [data, setData] = useState<Partial<FlowNodeDef>>(initialData)

  useEffect(() => {
    setData(initialData)
  }, [initialData, nodeKey])

  const update = (patch: Partial<FlowNodeDef>) => {
    const next = { ...data, ...patch }
    setData(next)
    onChange(nodeKey, next)
  }

  const inputClass = 'w-full rounded-md border border-gray-200 px-3 py-1.5 text-sm outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100'
  const labelClass = 'mb-1 block text-xs font-medium text-gray-600'

  const otherKeys = allNodeKeys.filter((k) => k !== nodeKey)

  return (
    <div className="flex h-full w-72 flex-col border-l border-gray-200 bg-white">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">{nodeKey}</h3>
          <span className="text-xs text-gray-400">{nodeType}</span>
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {/* Key */}
        <div>
          <label className={labelClass}>节点 Key</label>
          <input
            value={data.key ?? nodeKey}
            readOnly
            className={`${inputClass} bg-gray-50 text-gray-500`}
          />
        </div>

        {/* Type-specific fields */}
        {nodeType === 'waker_task' && (
          <>
            <div>
              <label className={labelClass}>Waker</label>
              <input
                value={data.waker ?? ''}
                onChange={(e) => update({ waker: e.target.value })}
                className={inputClass}
                placeholder="执行者名称"
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>指令</label>
              <textarea
                value={data.instruction ?? ''}
                onChange={(e) => update({ instruction: e.target.value })}
                rows={3}
                className={`${inputClass} resize-y`}
                placeholder="任务指令..."
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>超时（秒）</label>
              <input
                type="number"
                value={data.timeout_seconds ?? ''}
                onChange={(e) => update({ timeout_seconds: e.target.value ? Number(e.target.value) : undefined })}
                className={inputClass}
                readOnly={readOnly}
              />
            </div>
          </>
        )}

        {nodeType === 'leader_plan' && (
          <>
            <div>
              <label className={labelClass}>计划描述</label>
              <textarea
                value={data.instruction ?? ''}
                onChange={(e) => update({ instruction: e.target.value })}
                rows={3}
                className={`${inputClass} resize-y`}
                placeholder="计划内容..."
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>超时（小时）</label>
              <input
                type="number"
                value={data.timeout_hours ?? ''}
                onChange={(e) => update({ timeout_hours: e.target.value ? Number(e.target.value) : undefined })}
                className={inputClass}
                readOnly={readOnly}
              />
            </div>
          </>
        )}

        {nodeType === 'human_review' && (
          <>
            <div>
              <label className={labelClass}>审核说明</label>
              <textarea
                value={data.instruction ?? ''}
                onChange={(e) => update({ instruction: e.target.value })}
                rows={3}
                className={`${inputClass} resize-y`}
                placeholder="审核要求..."
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>Checklist（每行一项）</label>
              <textarea
                value={(data.checklist ?? []).join('\n')}
                onChange={(e) => update({ checklist: e.target.value.split('\n').filter(Boolean) })}
                rows={4}
                className={`${inputClass} resize-y font-mono text-xs`}
                placeholder="检查项1&#10;检查项2"
                readOnly={readOnly}
              />
            </div>
          </>
        )}

        {nodeType === 'condition' && (
          <>
            <div>
              <label className={labelClass}>条件表达式</label>
              <textarea
                value={data.expression ?? ''}
                onChange={(e) => update({ expression: e.target.value })}
                rows={3}
                className={`${inputClass} resize-y font-mono text-xs`}
                placeholder="status == 'approved'"
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>分支映射（JSON）</label>
              <textarea
                value={data.branches ? JSON.stringify(data.branches, null, 2) : ''}
                onChange={(e) => {
                  try { update({ branches: JSON.parse(e.target.value) }) } catch { /* ignore */ }
                }}
                rows={4}
                className={`${inputClass} resize-y font-mono text-xs`}
                placeholder='{"true": "next_a", "false": "next_b"}'
                readOnly={readOnly}
              />
            </div>
          </>
        )}

        {nodeType === 'notify' && (
          <>
            <div>
              <label className={labelClass}>通知渠道</label>
              <input
                value={data.channel ?? ''}
                onChange={(e) => update({ channel: e.target.value })}
                className={inputClass}
                placeholder="feishu / slack / email"
                readOnly={readOnly}
              />
            </div>
            <div>
              <label className={labelClass}>Payload（JSON）</label>
              <textarea
                value={data.payload ? JSON.stringify(data.payload, null, 2) : ''}
                onChange={(e) => {
                  try { update({ payload: JSON.parse(e.target.value) }) } catch { /* ignore */ }
                }}
                rows={4}
                className={`${inputClass} resize-y font-mono text-xs`}
                placeholder='{"message": "..."}'
                readOnly={readOnly}
              />
            </div>
          </>
        )}

        {/* Depends on */}
        <div>
          <label className={labelClass}>依赖节点</label>
          <select
            multiple
            value={data.depends_on ?? []}
            onChange={(e) => {
              const selected = Array.from(e.target.selectedOptions, (o) => o.value)
              update({ depends_on: selected })
            }}
            className={`${inputClass} h-24`}
            disabled={readOnly}
          >
            {otherKeys.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <p className="mt-1 text-[10px] text-gray-400">按住 Ctrl/Cmd 多选</p>
        </div>

        {/* Input from */}
        <div>
          <label className={labelClass}>输入来源</label>
          <select
            value={data.input_from ?? ''}
            onChange={(e) => update({ input_from: e.target.value || undefined })}
            className={inputClass}
            disabled={readOnly}
          >
            <option value="">无</option>
            {otherKeys.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Footer */}
      {!readOnly && (
        <div className="border-t border-gray-100 px-4 py-3">
          <button
            onClick={() => onDelete(nodeKey)}
            className="w-full rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
          >
            删除节点
          </button>
        </div>
      )}
    </div>
  )
}
