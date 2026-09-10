import React, { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { GroupActivityItem } from '../../types';
import Avatar from '../Avatar';
import ReplyProgress, { type ReplyProgressInfo } from './ReplyProgress';

export interface WakerActivityDrawerProps {
  /** 被点击的活动项（null = 关闭） */
  item: GroupActivityItem | null;
  /** 员工角色（来自群成员 lookup，可选） */
  role?: string;
  onClose: () => void;
}

const POLL_MS = 3000;

/**
 * 员工运行详情抽屉：点击运行状态条头像打开。
 * - member_task：GET /tasks/{id}/progress（任务说明 + 实时步骤 + 最近输出）
 * - leader_run：GET /conversations/{id}/progress（实时步骤）
 * 3s 轮询；关闭或切换活动项时重建。
 */
const WakerActivityDrawer: React.FC<WakerActivityDrawerProps> = ({ item, role, onClose }) => {
  const [progress, setProgress] = useState<ReplyProgressInfo | null>(null);
  const [instruction, setInstruction] = useState('');
  const [latestOutput, setLatestOutput] = useState<string | null>(null);
  const [resultSummary, setResultSummary] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!item) return;
    let cancelled = false;
    setProgress(null);
    setInstruction('');
    setLatestOutput(null);
    setResultSummary(null);
    setError(null);

    const load = async () => {
      try {
        if (item.kind === 'member_task' && item.task_id) {
          const p = await api.getTaskProgress(item.task_id);
          if (cancelled) return;
          setInstruction(p.instruction || '');
          setLatestOutput(p.latest_output ?? null);
          setResultSummary(p.active ? null : (p.result_summary ?? null));
          setProgress({
            active: p.active,
            target: p.executor,
            elapsed: p.elapsed,
            steps: p.steps,
            current: p.current,
          });
          setError(null);
        } else if (item.kind === 'leader_run' && item.conversation_id) {
          const p = await api.getConversationProgress(item.conversation_id);
          if (cancelled) return;
          setInstruction(item.title || '');
          setProgress(p);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载失败');
      }
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [item]);

  if (!item) return null;

  const statusLabel = item.status === 'running' ? '运行中' : '排队中';

  return (
    <div
      className="fixed inset-0 z-40"
      onClick={onClose}
      data-testid="waker-activity-drawer"
      role="dialog"
      aria-label={`${item.waker} 运行详情`}
    >
      <div className="drawer-overlay absolute inset-0 bg-black/10" />
      <aside
        className="drawer-panel absolute right-0 top-0 flex h-full w-[380px] flex-col border-l border-[var(--border)] bg-[var(--panel)] shadow-[var(--shadow-md)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header：员工身份 + 状态 */}
        <header className="flex items-center gap-3 border-b border-[var(--border)] px-4 py-3">
          <Avatar name={item.waker} size="default" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-[14px] font-semibold text-[var(--text)]">
                {item.waker}
              </span>
              <span
                className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                  item.status === 'running'
                    ? 'bg-emerald-50 text-emerald-600'
                    : 'bg-amber-50 text-amber-600'
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    item.status === 'running' ? 'bg-emerald-500' : 'bg-amber-400'
                  }`}
                />
                {statusLabel}
              </span>
            </div>
            <span className="text-[11.5px] text-[var(--text-3)]">
              {item.kind === 'leader_run' ? 'Leader · 正在回复群消息' : role || '群成员'}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-[var(--text-3)] transition-colors hover:bg-[var(--panel-2)] hover:text-[var(--text-2)]"
            title="关闭"
          >
            ✕
          </button>
        </header>

        {/* 当前任务 */}
        <div className="border-b border-[var(--border)] px-4 py-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)]">
            当前任务
          </div>
          <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-[var(--text)]">
            {instruction || item.title || '（无任务说明）'}
          </div>
        </div>

        {/* 执行进度 */}
        <div className="flex-1 overflow-y-auto px-4 py-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)]">
            执行进度
          </div>
          {error ? (
            <div className="text-[12px] text-red-500">{error}</div>
          ) : progress ? (
            <ReplyProgress info={progress} />
          ) : (
            <div className="text-[12px] text-[var(--text-3)]">加载中…</div>
          )}

          {latestOutput && (
            <div className="mt-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)]">
                最近输出
              </div>
              <div className="whitespace-pre-wrap rounded-lg bg-[var(--panel-2)] p-3 text-[12.5px] leading-relaxed text-[var(--text-2)]">
                {latestOutput}
              </div>
            </div>
          )}

          {resultSummary && (
            <div className="mt-3">
              <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--text-3)]">
                执行结果
              </div>
              <div className="whitespace-pre-wrap rounded-lg bg-[var(--panel-2)] p-3 text-[12.5px] leading-relaxed text-[var(--text-2)]">
                {resultSummary}
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
};

export default WakerActivityDrawer;
