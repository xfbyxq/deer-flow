import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { GroupActivityItem } from '../../types';
import Avatar from '../Avatar';

export interface RunningWakersBarProps {
  groupId: string;
  /** 点击头像：打开员工运行详情抽屉 */
  onOpenWaker: (item: GroupActivityItem) => void;
  /** 活动状态变化回调（父组件用于联动消息刷新轮询） */
  onActivityChange?: (active: boolean) => void;
}

const POLL_MS = 3000;

/**
 * 运行状态条：输入框上方展示「正在运行/排队」的 Waker 头像（多人并列）。
 * 数据来自 GET /groups/{id}/activity（3s 轮询）；页面隐藏时暂停轮询；
 * 无活动时不渲染。点击头像打开员工运行详情抽屉。
 */
const RunningWakersBar: React.FC<RunningWakersBarProps> = ({
  groupId,
  onOpenWaker,
  onActivityChange,
}) => {
  const [items, setItems] = useState<GroupActivityItem[]>([]);

  const refresh = useCallback(async () => {
    if (document.visibilityState === 'hidden') return;
    try {
      const data = await api.getGroupActivity(groupId);
      setItems(data.items ?? []);
      onActivityChange?.(Boolean(data.active));
    } catch {
      /* 轮询失败忽略，下次重试 */
    }
  }, [groupId, onActivityChange]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [refresh]);

  if (items.length === 0) return null;

  const runningCount = items.filter((i) => i.status === 'running').length;
  const queuedCount = items.length - runningCount;

  return (
    <div className="border-t border-[var(--border)] bg-emerald-50/70 px-4 py-2" data-testid="running-wakers-bar">
      <div className="flex items-center gap-2.5">
        {/* 头像堆叠（点击查看详情） */}
        <div className="flex -space-x-2">
          {items.slice(0, 5).map((item, idx) => (
            <button
              key={`${item.waker}-${item.task_id ?? item.conversation_id ?? idx}`}
              type="button"
              onClick={() => onOpenWaker(item)}
              className="relative rounded-full ring-2 ring-white transition-transform hover:scale-110 hover:z-10 focus:outline-none"
              title={`${item.waker} · ${item.status === 'running' ? '运行中' : '排队中'} · ${item.title}`}
            >
              <Avatar name={item.waker} size="sm" />
              <span
                className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full border-2 border-white ${
                  item.status === 'running' ? 'bg-emerald-500' : 'bg-amber-400'
                }`}
              />
            </button>
          ))}
          {items.length > 5 && (
            <span className="relative inline-flex h-[26px] w-[26px] items-center justify-center rounded-full bg-[var(--panel-3)] text-[10px] font-semibold text-[var(--text-2)] ring-2 ring-white">
              +{items.length - 5}
            </span>
          )}
        </div>
        <span className="text-[12px] font-medium text-emerald-700">
          {items.length} 个 Waker 正在运行或排队
        </span>
        {runningCount > 0 && queuedCount > 0 && (
          <span className="text-[11px] text-emerald-600/80">
            （运行 {runningCount} · 排队 {queuedCount}）
          </span>
        )}
        <span className="ml-auto text-[11px] text-emerald-600/80">点击头像查看详情</span>
      </div>
    </div>
  );
};

export default React.memo(RunningWakersBar);
