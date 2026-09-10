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

/** 群消息结构化标记（过程消息 partial=true，前端据此区分回复是否已完成） */
export interface ChatMessageMeta {
  kind?: 'dispatch' | 'report' | 'leader_post';
  partial?: boolean;
  target?: string;
  status?: string;
  mode?: string;
  mentions?: string[];
  /** 澄清请求（ask_clarification 结构化 payload，waker 消息携带） */
  clarification?: ClarificationRequest;
  /** 澄清回答标记（user 回答消息携带，用于简洁展示） */
  clarification_response?: ClarificationResponseMeta;
}

/** 澄清输入形态（对齐 DeerFlow human_input_request 协议） */
export type ClarificationInputMode = 'free_text' | 'single_choice' | 'choice_with_other' | 'form';

export type ClarificationFieldType =
  | 'text'
  | 'textarea'
  | 'number'
  | 'select'
  | 'multi_select'
  | 'checkbox'
  | 'date';

export interface ClarificationOption {
  id: string;
  label: string;
  value: string;
}

export interface ClarificationField {
  name: string;
  label: string;
  type: ClarificationFieldType;
  required: boolean;
  placeholder?: string;
  options?: ClarificationOption[];
}

/** 澄清请求结构化数据（DeerFlow ToolMessage.artifact.human_input 透传） */
export interface ClarificationRequest {
  version: number;
  kind: 'human_input_request';
  source: string;
  request_id: string;
  tool_call_id?: string;
  clarification_type?: string;
  question: string;
  context?: string | null;
  input_mode: ClarificationInputMode;
  options?: ClarificationOption[];
  fields?: ClarificationField[];
}

/** 澄清回答元数据（回答消息展示用） */
export interface ClarificationResponseMeta {
  request_id: string;
  kind: 'option' | 'text' | 'form';
  value: string;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'waker' | 'system';
  waker?: { name: string; role: string; avatar?: string };
  time: string;
  text?: string;
  parts?: MessagePart[];
  meta?: ChatMessageMeta;
}

/** 群内活动项（运行状态条数据源） */
export interface GroupActivityItem {
  waker: string;
  kind: 'member_task' | 'leader_run';
  status: 'running' | 'queued';
  title: string;
  elapsed: number;
  task_id?: string;
  conversation_id?: string;
  started_at?: string;
}

export interface GroupActivity {
  active: boolean;
  items: GroupActivityItem[];
}

/** 成员任务进度快照（详情抽屉数据源） */
export interface TaskProgress {
  active: boolean;
  task_id: string;
  executor: string;
  status: string;
  instruction: string;
  elapsed?: number;
  run_id?: string;
  steps?: Array<{ name: string; detail: string; done: boolean; result: string | null }>;
  current?: { kind: string; name?: string; detail?: string };
  latest_output?: string | null;
  result_summary?: string | null;
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
