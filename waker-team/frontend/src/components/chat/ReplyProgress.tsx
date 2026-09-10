import React from 'react';

export interface ReplyProgressStep {
  name: string;
  detail: string;
  done: boolean;
  result: string | null;
}

export interface ReplyProgressInfo {
  active: boolean;
  run_id?: string;
  target?: string;
  elapsed?: number;
  steps?: ReplyProgressStep[];
  current?: { kind: string; name?: string; detail?: string };
}

/** 工具名 → 中文动作（未知工具回退原文）。 */
const TOOL_LABELS: Record<string, string> = {
  web_search: '搜索资料',
  web_fetch: '阅读网页',
  image_search: '搜索图片',
  list_uploaded_files: '查看文件',
  read_file: '读取文件',
  ask_clarification: '准备提问',
  query_group: '查询团队成员',
  delegate_to_agent: '委派任务',
  delegate_submit: '提交委派',
  delegate_status: '查询委派状态',
  present_file: '整理交付文件',
};

function toolLabel(name?: string): string {
  if (!name) return '执行工具';
  return TOOL_LABELS[name] ?? name;
}

function formatElapsed(seconds?: number): string {
  if (!seconds || seconds < 1) return '';
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分钟`;
}

function currentLabel(info: ReplyProgressInfo): string {
  const c = info.current;
  if (!c) return '正在思考…';
  switch (c.kind) {
    case 'tool':
      return `正在${toolLabel(c.name)}…`;
    case 'thinking':
      return c.detail || '正在整理信息…';
    case 'starting':
      return '正在启动…';
    default:
      return c.detail || '正在执行…';
  }
}

/**
 * 回复进度展示：等待 waker 回复期间展示「当前动作 + 已完成工具步骤 + 已耗时」，
 * 数据来自会话进度快照接口（3s 轮询）。
 */
const ReplyProgress: React.FC<{ info: ReplyProgressInfo }> = ({ info }) => {
  const steps = info.steps ?? [];
  const visible = steps.slice(-4);
  const hiddenCount = steps.length - visible.length;
  const elapsed = formatElapsed(info.elapsed);

  return (
    <div className="py-2 px-1 mb-2">
      {/* 当前动作行 */}
      <div className="flex items-center gap-2.5">
        <div className="flex items-center gap-1">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--text-3)]"
              style={{
                animation: 'dot-bounce 1.2s ease-in-out infinite',
                animationDelay: `${i * 0.15}s`,
              }}
            />
          ))}
        </div>
        <span className="text-[12px] text-[var(--text-2)]">{currentLabel(info)}</span>
        {info.current?.kind === 'tool' && info.current.detail && (
          <span className="text-[11px] text-[var(--text-3)] truncate max-w-[320px]">
            {info.current.detail}
          </span>
        )}
        {elapsed && (
          <span className="text-[11px] text-[var(--text-3)] ml-auto shrink-0">已进行 {elapsed}</span>
        )}
      </div>

      {/* 已完成/进行中步骤 */}
      {visible.length > 0 && (
        <div className="mt-1.5 ml-[26px] space-y-0.5">
          {hiddenCount > 0 && (
            <div className="text-[11px] text-[var(--text-3)]">… 之前还有 {hiddenCount} 步</div>
          )}
          {visible.map((s, idx) => (
            <div key={idx} className="flex items-center gap-1.5 text-[11px] text-[var(--text-3)]">
              <span className={s.done ? 'text-emerald-600' : ''}>{s.done ? '✓' : '◌'}</span>
              <span className="text-[var(--text-2)] shrink-0">{toolLabel(s.name)}</span>
              {s.detail && <span className="truncate max-w-[280px]">{s.detail}</span>}
              {s.done && s.result && (
                <span className="truncate max-w-[220px] opacity-80">{s.result}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default React.memo(ReplyProgress);
