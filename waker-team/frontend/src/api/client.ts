import type { Waker, WakerTemplate, Task, Board, EnumData, BoardParams, Group, GroupMember, GroupSkill, FlowDef, FlowRun, FlowRunTimeline, ScheduleDef, ScheduleRun, GroupActivity, TaskProgress } from '../types';

const BASE = '/api';

async function fetchJSON<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers: extraHeaders, ...rest } = options ?? {};
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: { 'Content-Type': 'application/json', ...extraHeaders },
  });
  if (!res.ok) {
    // CONTRACT-ERROR：错误体截断 200 字符（防超长/敏感上游 body 进入错误信息），
    // 优先解析后端安全中文 detail 作为用户可见文案；非 JSON/无 detail 回退「请求失败（status）」。
    // 禁止把 res.url（绝对 URL，含内部主机名）写入用户可见错误。
    const raw = (await res.text().catch(() => '')).slice(0, 200);
    let message = `请求失败（${res.status}）`;
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as unknown;
        if (parsed && typeof parsed === 'object') {
          const detail = (parsed as Record<string, unknown>).detail;
          if (typeof detail === 'string' && detail) message = detail;
        }
      } catch (err) {
        // 原始解析错误仅记日志便于排障，不进入用户可见文案
        console.error('[api] non-JSON response', path, res.status, err);
      }
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  try {
    return await res.json();
  } catch {
    // 使用相对 path（非 res.url），避免泄露内部主机名
    throw new Error(`服务端返回了非 JSON 响应（${res.status} ${path}）`);
  }
}

export const api = {
  // Wakers
  listWakers: () => fetchJSON<Waker[]>('/wakers'),
  getWaker: (name: string) => fetchJSON<Waker>(`/wakers/${encodeURIComponent(name)}`),
  createWaker: (data: Partial<Waker>) =>
    fetchJSON<Waker>('/wakers', { method: 'POST', body: JSON.stringify(data) }),
  updateWaker: (name: string, data: Partial<Waker>) =>
    fetchJSON<Waker>(`/wakers/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
  toggleWaker: (name: string, enabled: boolean) =>
    fetchJSON<Waker>(`/wakers/${encodeURIComponent(name)}/toggle`, {
      method: 'PATCH',
      body: JSON.stringify({ enabled }),
    }),
  deleteWaker: (name: string) =>
    fetchJSON<void>(`/wakers/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  getEnumData: () => fetchJSON<EnumData>('/wakers/enum'),
  listWakerTemplates: () => fetchJSON<WakerTemplate[]>('/wakers/templates'),

  // Tasks
  createTask: (data: { executor: string; input_text: string; group_id?: string }) =>
    fetchJSON<Task>('/tasks', { method: 'POST', body: JSON.stringify(data) }),
  getTask: (id: string) => fetchJSON<Task>(`/tasks/${id}`),
  retryTask: (id: string) =>
    fetchJSON<Task>(`/tasks/${id}/retry`, { method: 'POST' }),
  cancelTask: (id: string) =>
    fetchJSON<Task>(`/tasks/${id}/cancel`, { method: 'POST' }),

  // Board
  getBoard: (params?: BoardParams) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.waker) qs.set('waker', params.waker);
    if (params?.kind) qs.set('kind', params.kind);
    if (params?.group_id) qs.set('group_id', params.group_id);
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.offset) qs.set('offset', String(params.offset));
    return fetchJSON<Board>(`/board?${qs}`);
  },

  // Groups
  listGroups: () => fetchJSON<Group[]>('/groups'),
  getGroup: (id: string) => fetchJSON<Group>(`/groups/${id}`),
  createGroup: (data: { name: string; leader_waker_id?: string; project_id?: string }) =>
    fetchJSON<Group>('/groups', { method: 'POST', body: JSON.stringify(data) }),
  updateGroup: (id: string, data: { name?: string; leader_waker_id?: string; project_id?: string; description?: string; sop_id?: string }) =>
    fetchJSON<Group>(`/groups/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteGroup: (id: string) =>
    fetchJSON<void>(`/groups/${id}`, { method: 'DELETE' }),
  listGroupMembers: (groupId: string) => fetchJSON<GroupMember[]>(`/groups/${groupId}/members`),
  addGroupMember: (groupId: string, data: { waker_id: string; role?: string }) =>
    fetchJSON<GroupMember>(`/groups/${groupId}/members`, { method: 'POST', body: JSON.stringify(data) }),
  removeGroupMember: (groupId: string, wakerId: string) =>
    fetchJSON<void>(`/groups/${groupId}/members/${encodeURIComponent(wakerId)}`, { method: 'DELETE' }),
  listGroupSkills: (groupId: string) => fetchJSON<GroupSkill[]>(`/groups/${groupId}/skills`),
  addGroupSkill: (groupId: string, skillName: string) =>
    fetchJSON<GroupSkill>(`/groups/${groupId}/skills`, { method: 'POST', body: JSON.stringify({ skill_name: skillName }) }),
  removeGroupSkill: (groupId: string, skillName: string) =>
    fetchJSON<void>(`/groups/${groupId}/skills/${encodeURIComponent(skillName)}`, { method: 'DELETE' }),
  transferGroupLeader: (groupId: string, targetWakerId: string) =>
    fetchJSON<Group>(`/groups/${groupId}/transfer-leader`, { method: 'POST', body: JSON.stringify({ target_waker_id: targetWakerId }) }),

  // Flow 定义
  listFlows: (groupId?: string) => {
    const qs = groupId ? `?group_id=${encodeURIComponent(groupId)}` : '';
    return fetchJSON<FlowDef[]>(`/flows${qs}`);
  },
  getFlow: (id: string) => fetchJSON<FlowDef>(`/flows/${id}`),
  createFlow: (data: Partial<FlowDef>) =>
    fetchJSON<FlowDef>('/flows', { method: 'POST', body: JSON.stringify(data) }),
  updateFlow: (id: string, data: Partial<FlowDef>) =>
    fetchJSON<FlowDef>(`/flows/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteFlow: (id: string) =>
    fetchJSON<void>(`/flows/${id}`, { method: 'DELETE' }),

  // Flow 运行
  listFlowRuns: (params?: { flow_id?: string; group_id?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.flow_id) qs.set('flow_id', params.flow_id);
    if (params?.group_id) qs.set('group_id', params.group_id);
    if (params?.status) qs.set('status', params.status);
    return fetchJSON<FlowRun[]>(`/flow-runs?${qs}`);
  },
  getFlowRun: (id: string) => fetchJSON<FlowRun>(`/flow-runs/${id}`),
  startFlowRun: (flowId: string) =>
    fetchJSON<FlowRun>(`/flows/${flowId}/run`, { method: 'POST' }),
  pauseFlowRun: (id: string) =>
    fetchJSON<FlowRun>(`/flow-runs/${id}/pause`, { method: 'POST' }),
  resumeFlowRun: (id: string) =>
    fetchJSON<FlowRun>(`/flow-runs/${id}/resume`, { method: 'POST' }),
  cancelFlowRun: (id: string) =>
    fetchJSON<FlowRun>(`/flow-runs/${id}/cancel`, { method: 'POST' }),
  getFlowRunTimeline: (id: string) =>
    fetchJSON<FlowRunTimeline>(`/flow-runs/${id}/timeline`),
  reviewFlowRunNode: (runId: string, data: { action: 'approve' | 'reject'; node_key: string; comment?: string }) =>
    fetchJSON<void>(`/flow-runs/${runId}/review`, { method: 'POST', body: JSON.stringify(data) }),
  rerunFlowRunNode: (runId: string, nodeKey: string) =>
    fetchJSON<void>(`/flow-runs/${runId}/nodes/${encodeURIComponent(nodeKey)}/rerun`, { method: 'POST' }),

  // Schedules
  listSchedules: (groupId?: string) => {
    const qs = groupId ? `?group_id=${encodeURIComponent(groupId)}` : '';
    return fetchJSON<ScheduleDef[]>(`/schedules${qs}`);
  },
  getSchedule: (id: string) => fetchJSON<ScheduleDef>(`/schedules/${id}`),
  createSchedule: (data: Partial<ScheduleDef>) =>
    fetchJSON<ScheduleDef>('/schedules', { method: 'POST', body: JSON.stringify(data) }),
  updateSchedule: (id: string, data: Partial<ScheduleDef>) =>
    fetchJSON<ScheduleDef>(`/schedules/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteSchedule: (id: string) =>
    fetchJSON<void>(`/schedules/${id}`, { method: 'DELETE' }),
  pauseSchedule: (id: string) =>
    fetchJSON<ScheduleDef>(`/schedules/${id}/pause`, { method: 'POST' }),
  resumeSchedule: (id: string) =>
    fetchJSON<ScheduleDef>(`/schedules/${id}/resume`, { method: 'POST' }),
  triggerSchedule: (id: string) =>
    fetchJSON<ScheduleRun>(`/schedules/${id}/trigger`, { method: 'POST' }),
  listScheduleRuns: (id: string) =>
    fetchJSON<ScheduleRun[]>(`/schedules/${id}/runs`),

  // Settings
  getSettings: () => fetchJSON<{ default_model?: string; density?: string; notify_task?: boolean; notify_mention?: boolean }>('/settings'),
  updateSettings: (data: Record<string, unknown>) =>
    fetchJSON<Record<string, unknown>>('/settings', { method: 'PUT', body: JSON.stringify(data) }),

  // Waker Flow runs
  listFlowRunsByWaker: (wakerName: string) => {
    const qs = new URLSearchParams({ waker: wakerName });
    return fetchJSON<FlowRun[]>(`/flow-runs?${qs}`);
  },

  // Conversations
  createWakerConversation: (wakerName: string, data?: { title?: string }) =>
    fetchJSON<{ id: string; scope: string; waker_id: string | null; group_id: string | null; title: string | null; status: string; thread_id: string | null; created_by: string | null; created_at: string | null; updated_at: string | null }>(`/wakers/${encodeURIComponent(wakerName)}/conversations`, { method: 'POST', body: JSON.stringify({ title: data?.title }) }),
  listWakerConversations: (wakerName: string) =>
    fetchJSON<Array<{ id: string; scope: string; waker_id: string | null; group_id: string | null; title: string | null; status: string; thread_id: string | null; created_by: string | null; created_at: string | null; updated_at: string | null }>>(`/wakers/${encodeURIComponent(wakerName)}/conversations`),
  getConversationMessages: (conversationId: string, limit = 200) =>
    fetchJSON<Array<{ id: string; conversation_id: string; role: string; waker_id: string | null; content_json: string | null; created_at: string | null }>>(`/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`),
  sendConversationMessage: (conversationId: string, data: { role: string; waker_id?: string; content_json?: Record<string, unknown> | unknown[]; defer_reply?: boolean }) =>
    fetchJSON<{ id: string; conversation_id: string; role: string; waker_id: string | null; content_json: string | null; created_at: string | null }>(`/conversations/${encodeURIComponent(conversationId)}/messages`, { method: 'POST', body: JSON.stringify(data) }),
  getConversationProgress: (conversationId: string) =>
    fetchJSON<{
      active: boolean;
      run_id?: string;
      target?: string;
      elapsed?: number;
      steps?: Array<{ name: string; detail: string; done: boolean; result: string | null }>;
      current?: { kind: string; name?: string; detail?: string };
    }>(`/conversations/${encodeURIComponent(conversationId)}/progress`),
  stopConversationReply: (conversationId: string) =>
    fetchJSON<{ stopped: boolean }>(`/conversations/${encodeURIComponent(conversationId)}/stop`, { method: 'POST' }),

  // Group conversations
  listGroupConversations: (groupId: string) =>
    fetchJSON<Array<{ id: string; scope: string; waker_id: string | null; group_id: string | null; title: string | null; status: string; thread_id: string | null; created_by: string | null; created_at: string | null; updated_at: string | null }>>(`/groups/${encodeURIComponent(groupId)}/conversations`),
  createGroupConversation: (groupId: string, data: { title?: string }) =>
    fetchJSON<{ id: string; scope: string; waker_id: string | null; group_id: string | null; title: string | null; status: string; thread_id: string | null; created_by: string | null; created_at: string | null; updated_at: string | null }>(`/groups/${encodeURIComponent(groupId)}/conversations`, { method: 'POST', body: JSON.stringify(data) }),

  // Group activity（运行状态条）与任务进度（员工详情抽屉）
  getGroupActivity: (groupId: string) =>
    fetchJSON<GroupActivity>(`/groups/${encodeURIComponent(groupId)}/activity`),
  getTaskProgress: (taskId: string) =>
    fetchJSON<TaskProgress>(`/tasks/${encodeURIComponent(taskId)}/progress`),
};
