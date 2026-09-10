import React, { useState } from 'react';
import type { MessagePart } from '../../types';

/* ── tiny inline SVG icons ── */
const ChevronIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
    <path d="M9 18l6-6-6-6" />
  </svg>
);
const BrainIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
    <path d="M12 2a5 5 0 0 0-5 5 4 4 0 0 0-3 6.9V17a4 4 0 0 0 4 4h1" />
    <path d="M12 2a5 5 0 0 1 5 5 4 4 0 0 1 3 6.9V17a4 4 0 0 1-4 4h-1" />
    <path d="M12 2v19" />
  </svg>
);
const ToolIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
    <path d="M14.7 6.3a5 5 0 0 0 6 6l-9 9a2.4 2.4 0 0 1-3.4 0l-2.6-2.6a2.4 2.4 0 0 1 0-3.4l9-9z" />
    <path d="M14.7 6.3L17.5 3.5a5 5 0 0 1 3 3L17.7 9.3" />
  </svg>
);
const SparkIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
    <path d="M12 3l1.9 5.6L19 10l-5.1 1.4L12 17l-1.9-5.6L5 10l5.1-1.4L12 3z" />
  </svg>
);
const CheckIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="w-3 h-3">
    <path d="M20 6L9 17l-5-5" />
  </svg>
);

const ICON_MAP: Record<string, React.FC> = {
  think: BrainIcon,
  tool: ToolIcon,
  skill: SparkIcon,
};

const STATUS_LABEL: Record<string, { text: string; color: string }> = {
  running: { text: '运行中', color: 'text-blue-600' },
  done:    { text: '已完成', color: 'text-[var(--green)]' },
  error:   { text: '异常',   color: 'text-[var(--red)]' },
};

function formatText(text: string) {
  return text.split('\n').map((line, i) => (
    <React.Fragment key={i}>
      {line}
      {i < text.split('\n').length - 1 && <br />}
    </React.Fragment>
  ));
}

export interface CollapseBlockProps {
  part: MessagePart;
}

const CollapseBlock: React.FC<CollapseBlockProps> = ({ part }) => {
  const defaultOpen = part.t === 'text';
  const [open, setOpen] = useState(defaultOpen);

  const Icon = ICON_MAP[part.t] ?? ToolIcon;
  const label = part.label ?? (part.t === 'think' ? '深度思考' : part.t === 'tool' ? '工具调用' : '技能');
  const status = STATUS_LABEL[part.status ?? 'done'];

  return (
    <div className={`collapse-block rounded-lg border border-[var(--border)] mb-2 ${open ? 'open' : ''}`}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] hover:bg-[var(--panel-2)] transition-colors rounded-lg"
      >
        <span className={`shrink-0 transition-transform duration-200 text-[var(--text-3)] ${open ? 'rotate-90' : ''}`}>
          <ChevronIcon />
        </span>
        <span className="shrink-0 text-[var(--text-2)]"><Icon /></span>
        <span className="flex-1 truncate font-medium text-[var(--text)]">{label}</span>
        {part.status === 'running' && (
          <span className="shrink-0 flex items-center gap-1 text-[11px] text-blue-600">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
            {status.text}
          </span>
        )}
        {part.status === 'done' && (
          <span className={`shrink-0 flex items-center gap-1 text-[11px] ${status.color}`}>
            <CheckIcon />
            {status.text}
          </span>
        )}
        {part.status === 'error' && (
          <span className={`shrink-0 text-[11px] ${status.color}`}>{status.text}</span>
        )}
      </button>
      {open && (
        <div className="px-3 pb-3 pt-0">
          {/* args */}
          {part.args && (
            <div className="mb-2">
              <div className="text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide mb-1">参数</div>
              <pre className="rounded-md bg-[var(--panel-3)] p-2 text-[12px] font-mono text-[var(--text-2)] overflow-x-auto whitespace-pre-wrap break-all">
                {part.args}
              </pre>
            </div>
          )}
          {/* output */}
          {part.output && (
            <div className="mb-2">
              <div className="text-[11px] font-semibold text-[var(--text-3)] uppercase tracking-wide mb-1">响应</div>
              <pre className="rounded-md bg-[var(--green-soft)] p-2 text-[12px] font-mono text-[var(--text-2)] overflow-x-auto whitespace-pre-wrap break-all">
                {part.output}
              </pre>
            </div>
          )}
          {/* content (think / text) */}
          {part.content && !part.args && (
            <div className="text-[13px] text-[var(--text-2)] leading-relaxed whitespace-pre-wrap">
              {formatText(part.content)}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default React.memo(CollapseBlock);
