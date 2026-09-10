import React from 'react';

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  // Run / Node statuses
  pending:         { label: '待执行',   className: 'bg-[var(--panel-3)] text-[var(--text-2)]' },
  running:         { label: '运行中',   className: 'bg-blue-50 text-blue-700' },
  done:            { label: '已完成',   className: 'bg-[var(--green-soft)] text-[#0d8a53]' },
  failed:          { label: '失败',     className: 'bg-[var(--red-soft)] text-[#b91c1c]' },
  cancelled:       { label: '已取消',   className: 'bg-[var(--panel-3)] text-[var(--text-3)]' },
  paused:          { label: '已暂停',   className: 'bg-[var(--amber-soft)] text-[#b45309]' },
  waiting_review:  { label: '待审核',   className: 'bg-[var(--amber-soft)] text-[#ea580c]' },
  skipped:         { label: '已跳过',   className: 'bg-[var(--panel-3)] text-[var(--text-3)]' },

  // Flow definition status
  active:   { label: '启用', className: 'bg-[var(--green-soft)] text-[#0d8a53]' },
  disabled: { label: '禁用', className: 'bg-[var(--panel-3)] text-[var(--text-2)]' },
  archived: { label: '已归档', className: 'bg-[var(--amber-soft)] text-[#b45309]' },

  // Waker enabled toggle
  enabled:  { label: '已启用', className: 'bg-[var(--green-soft)] text-[#0d8a53]' },
  disabled_waker: { label: '已禁用', className: 'bg-[var(--panel-3)] text-[var(--text-2)]' },
};

const DEFAULT_STATUS = { label: '未知', className: 'bg-[var(--panel-3)] text-[var(--text-2)]' };

export interface StatusTagProps {
  status: string;
  /** Override the label from the default map */
  label?: string;
}

const StatusTag: React.FC<StatusTagProps> = ({ status, label }) => {
  const entry = STATUS_MAP[status] ?? DEFAULT_STATUS;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold ${entry.className}`}
    >
      {label ?? entry.label}
    </span>
  );
};

export default React.memo(StatusTag);
