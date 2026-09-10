export interface Waker {
  name: string;
  description: string;
  soul: string;
  model?: string;
  tool_groups?: string[];
  skills?: string[];
  role?: string;
  max_concurrent_tasks?: number;
  mcp_connectors?: Record<string, Record<string, unknown>>;
  enabled: boolean;
  deer_user: string;
  home_thread_id?: string;
  created_at?: string;
  updated_at?: string;
}

/** 员工模板（静态预定义，新建员工时选择后预填表单） */
export interface WakerTemplate {
  id: string;
  title: string;
  description: string;
  suggested_name: string;
  role?: string;
  soul: string;
  model?: string | null;
  tool_groups: string[];
  skills: string[];
  max_concurrent_tasks: number;
  mcp_connectors?: Record<string, Record<string, unknown>>;
}

export interface Task {
  id: string;
  kind: string;
  group_id: string;
  ticket_id: string | null;
  parent_task_id: string | null;
  executor: string;
  status: string;
  input_text: string;
  result_summary?: string;
  thread_id?: string;
  run_id?: string;
  created_by: string;
  created_at?: string;
  updated_at?: string;
}

export interface Board {
  counts: Record<string, number>;
  items: Task[];
  total: number;
}

export interface EnumData {
  models: { name: string; display_name?: string }[];
  skills: { name: string }[];
  tool_groups: string[];
}

export interface BoardParams {
  status?: string;
  waker?: string;
  kind?: string;
  group_id?: string;
  limit?: number;
  offset?: number;
}

export interface Group {
  id: string;
  name: string;
  leader_waker_id: string | null;
  project_id: string | null;
  description: string | null;
  sop_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface GroupMember {
  group_id: string;
  waker_id: string;
  role: 'member' | 'leader';
  joined_at: string;
}

export interface GroupSkill {
  id: string;
  group_id: string;
  skill_name: string;
  created_at: string | null;
}

// Flow 相关
export interface FlowNodeDef {
  key: string;
  type: 'waker_task' | 'leader_plan' | 'human_review' | 'condition' | 'notify';
  waker?: string;
  instruction?: string;
  depends_on: string[];
  input_from?: string;
  timeout_seconds?: number;
  timeout_hours?: number;
  checklist?: string[];
  expression?: string;
  branches?: Record<string, string>;
  channel?: string;
  payload?: Record<string, unknown>;
}

export interface FlowSettings {
  max_concurrent_nodes: number;
  on_failure: 'pause' | 'fail_flow' | 'skip';
}

export interface FlowDefinitionV1 {
  version: number;
  nodes: FlowNodeDef[];
  settings?: FlowSettings;
}

export interface FlowDef {
  id: string;
  name: string;
  group_id: string;
  description: string | null;
  version: number;
  definition_json: FlowDefinitionV1;
  status: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

// Chat message part types
export type MessagePartType = 'think' | 'skill' | 'tool' | 'text';

export interface MessagePart {
  t: MessagePartType;
  label?: string;
  content: string;
  status?: 'running' | 'done' | 'error';
  args?: string;
  output?: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'waker' | 'system';
  waker?: { name: string; role: string; avatar?: string };
  time: string;
  text?: string;
  parts?: MessagePart[];
}

export interface ConversationTask {
  id: string;
  title: string;
  time: string;
  status: 'running' | 'done' | 'pending' | 'failed';
  lastMessage?: string;
  messages?: ChatMessage[];
}

export interface AutoTask {
  id: string;
  name: string;
  cron: string;
  lastRun?: string;
  enabled: boolean;
  executor?: string;
}

export interface FlowRun {
  id: string;
  flow_def_id: string;
  group_id: string;
  status: string;
  current_node_key: string | null;
  trigger_type: string;
  created_by: string | null;
  failure_reason: string | null;
  started_at: string;
  completed_at: string | null;
  paused_at: string | null;
}

export interface NodeTimelineItem {
  node_key: string;
  type: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  outcome: Record<string, unknown> | null;
  error_message: string | null;
  retry_count: number;
  waker: string | null;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
}

export interface FlowRunTimeline {
  flow_run: FlowRun;
  nodes: NodeTimelineItem[];
  total_duration_seconds: number | null;
  current_node: string | null;
}

// Schedule 相关
export interface ScheduleDef {
  id: string;
  name: string;
  group_id: string;
  description: string | null;
  cron_expression: string;
  target_type: string;
  target_id: string;
  status: string;
  last_run_at: string | null;
  next_run_at: string | null;
  run_count: number;
  created_at: string;
  updated_at: string;
}

export interface ScheduleRun {
  id: string;
  schedule_def_id: string;
  trigger_type: string;
  triggered_at: string;
  status: string;
  flow_run_id: string | null;
  task_id: string | null;
  error_message: string | null;
  completed_at: string | null;
}
