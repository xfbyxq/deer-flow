"""Pydantic 请求/响应模型 — Waker 管理 API + 任务 API + Flow 定义 API."""

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


_WAKER_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff\u3400-\u4dbf\uff00-\uffef-]+$")


class WakerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="员工名，支持中英文、数字、连字符、下划线")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空")
        if len(v) > 64:
            raise ValueError("name 最长 64 字符")
        if not _WAKER_NAME_RE.match(v):
            raise ValueError(
                "name 只能包含中英文、数字、连字符、下划线，不能含空格或特殊符号"
            )
        return v
    description: str = ""
    soul: str = ""  # 用户自定义 SOUL 内容（会与协作规则模板拼接）
    model: Optional[str] = None
    tool_groups: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    allowed_subagents: Optional[list[str]] = None
    model_settings: Optional[dict] = None
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = Field(None, pattern=r"^(low|medium|high)$")
    role: Optional[str] = None
    max_concurrent_tasks: Optional[int] = None
    mcp_connectors: Optional[dict] = None


class WakerUpdateRequest(BaseModel):
    description: Optional[str] = None
    soul: Optional[str] = None
    model: Optional[str] = None
    tool_groups: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    allowed_subagents: Optional[list[str]] = None
    model_settings: Optional[dict] = None
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = Field(None, pattern=r"^(low|medium|high)$")
    presence: Optional[str] = Field(None, pattern=r"^(online|offline|busy)$")
    role: Optional[str] = None
    max_concurrent_tasks: Optional[int] = None
    mcp_connectors: Optional[dict] = None


class WakerResponse(BaseModel):
    name: str
    description: str = ""
    soul: str = ""
    model: Optional[str] = None
    tool_groups: Optional[list[str]] = None
    skills: Optional[list[str]] = None
    allowed_subagents: Optional[list[str]] = None
    model_settings: Optional[dict] = None
    thinking_enabled: Optional[bool] = None
    reasoning_effort: Optional[str] = None
    enabled: bool = True
    deer_user: str = ""
    home_thread_id: Optional[str] = None
    presence: str = "offline"
    role: Optional[str] = None
    max_concurrent_tasks: int = 3
    mcp_connectors: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class EnumDataResponse(BaseModel):
    """枚举数据源（模型/技能/工具组列表）."""

    models: list[dict] = []
    skills: list[dict] = []
    tool_groups: list[str] = []


class WakerTemplateResponse(BaseModel):
    """员工模板（静态预定义，新建员工时选择后预填表单）."""

    id: str
    title: str
    description: str = ""
    suggested_name: str
    role: Optional[str] = None
    soul: str = ""
    model: Optional[str] = None
    tool_groups: list[str] = []
    skills: list[str] = []
    max_concurrent_tasks: int = 3
    mcp_connectors: Optional[dict] = None


# ------------------------------------------------------------------
# Task schemas
# ------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    """任务创建请求."""

    executor: str
    input_text: str
    group_id: str = "default"


class TaskResponse(BaseModel):
    """任务响应."""

    id: str
    kind: str
    ticket_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    group_id: Optional[str] = None
    executor: str
    status: str
    input_text: str
    result_summary: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_by: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表响应."""

    items: list[TaskResponse]
    total: int


class BoardResponse(BaseModel):
    """看板响应."""

    counts: dict[str, int]
    items: list[TaskResponse]
    total: int
    needs_action: int = 0


# ------------------------------------------------------------------
# Group schemas
# ------------------------------------------------------------------


class GroupCreate(BaseModel):
    """群组创建请求."""

    name: str = Field(..., min_length=1, max_length=128)
    leader_waker_id: Optional[str] = None
    project_id: Optional[str] = None
    description: Optional[str] = None
    sop_id: Optional[str] = None


class GroupUpdate(BaseModel):
    """群组更新请求."""

    name: Optional[str] = Field(None, min_length=1, max_length=128)
    leader_waker_id: Optional[str] = None
    project_id: Optional[str] = None
    description: Optional[str] = None
    sop_id: Optional[str] = None


class GroupMemberAdd(BaseModel):
    """添加群成员请求."""

    waker_id: str
    role: str = "member"


class GroupMemberResponse(BaseModel):
    """群成员响应."""

    group_id: str
    waker_id: str
    role: str
    joined_at: Optional[str] = None


class GroupResponse(BaseModel):
    """群组响应."""

    id: str
    name: str
    leader_waker_id: Optional[str] = None
    project_id: Optional[str] = None
    description: Optional[str] = None
    sop_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GroupSkillResponse(BaseModel):
    """群组技能响应."""

    id: str
    group_id: str
    skill_name: str
    created_at: Optional[str] = None


class TransferLeaderRequest(BaseModel):
    """转移群主请求."""

    target_waker_id: str = Field(..., description="目标 waker name，必须是群成员")


# ------------------------------------------------------------------
# Conversation schemas
# ------------------------------------------------------------------


class ConversationCreate(BaseModel):
    """会话创建请求."""

    scope: str = Field("direct", pattern=r"^(direct|group)$", description="direct|group")
    waker_id: Optional[str] = None
    title: Optional[str] = None
    thread_id: Optional[str] = None


class ConversationResponse(BaseModel):
    """会话响应."""

    id: str
    scope: str
    waker_id: Optional[str] = None
    group_id: Optional[str] = None
    title: Optional[str] = None
    status: str
    thread_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ConversationMessageCreate(BaseModel):
    """消息创建请求."""

    role: str = Field(..., pattern=r"^(user|waker|system)$", description="user|waker|system")
    waker_id: Optional[str] = None
    content_json: Optional[dict | list] = Field(None, description="结构化内容")


class ConversationMessageResponse(BaseModel):
    """消息响应."""

    id: str
    conversation_id: str
    role: str
    waker_id: Optional[str] = None
    content_json: Optional[str] = None
    created_at: Optional[str] = None


# ------------------------------------------------------------------
# User settings schemas
# ------------------------------------------------------------------


class UserSettingsResponse(BaseModel):
    """用户设置响应."""

    user_id: str
    default_model: Optional[str] = None
    density: Optional[str] = None
    notify_task: bool = True
    notify_mention: bool = True
    updated_at: Optional[str] = None


class UserSettingsUpdate(BaseModel):
    """用户设置更新请求."""

    default_model: Optional[str] = None
    density: Optional[str] = None
    notify_task: Optional[bool] = None
    notify_mention: Optional[bool] = None


# ------------------------------------------------------------------
# Flow Definition schemas
# ------------------------------------------------------------------


_VALID_NODE_TYPES = {"waker_task", "leader_plan", "human_review", "condition", "notify"}


class FlowNodeDef(BaseModel):
    """Flow 节点定义."""

    key: str
    type: str  # waker_task | leader_plan | human_review | condition | notify
    waker: Optional[str] = None
    instruction: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    input_from: Optional[str] = None
    timeout_seconds: Optional[int] = None
    timeout_hours: Optional[int] = None  # human_review
    checklist: Optional[list[str]] = None  # human_review
    expression: Optional[str] = None  # condition
    branches: Optional[dict[str, str]] = None  # condition
    channel: Optional[str] = None  # notify
    payload: Optional[dict] = None  # notify


class FlowSettings(BaseModel):
    """Flow 全局设置."""

    max_concurrent_nodes: int = 5
    on_failure: str = "pause"  # pause | fail_flow | skip


class FlowDefinitionV1(BaseModel):
    """Flow 定义 JSON schema (v1)."""

    version: int = 1
    nodes: list[FlowNodeDef]
    settings: Optional[FlowSettings] = None


class FlowDefCreate(BaseModel):
    """Flow 定义创建请求."""

    name: str
    group_id: str
    description: Optional[str] = None
    definition_json: FlowDefinitionV1


class FlowDefUpdate(BaseModel):
    """Flow 定义更新请求."""

    name: Optional[str] = None
    description: Optional[str] = None
    definition_json: Optional[FlowDefinitionV1] = None


class FlowDefResponse(BaseModel):
    """Flow 定义响应."""

    id: str
    name: str
    group_id: Optional[str] = None
    description: Optional[str] = None
    version: int
    definition_json: dict
    status: str
    created_at: str
    updated_at: str
    created_by: Optional[str] = None


# ------------------------------------------------------------------
# Schedule schemas
# ------------------------------------------------------------------


class ScheduleCreate(BaseModel):
    """调度创建请求."""

    name: str
    group_id: str
    description: Optional[str] = None
    cron_expression: str
    target_type: str  # "flow" | "task"
    target_id: str
    target_input: Optional[dict] = None
    max_run_count: Optional[int] = None
    end_date: Optional[str] = None  # ISO format


class ScheduleUpdate(BaseModel):
    """调度更新请求."""

    name: Optional[str] = None
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    max_run_count: Optional[int] = None
    end_date: Optional[str] = None


class ScheduleResponse(BaseModel):
    """调度响应."""

    id: str
    name: str
    group_id: str
    description: Optional[str] = None
    cron_expression: str
    target_type: str
    target_id: str
    target_input: Optional[dict] = None
    status: str
    max_run_count: Optional[int] = None
    end_date: Optional[str] = None
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    run_count: int
    executor: Optional[str] = None
    created_at: str
    updated_at: str


class ScheduleRunResponse(BaseModel):
    """调度执行历史响应."""

    id: str
    schedule_def_id: str
    trigger_type: str
    triggered_at: str
    status: str
    flow_run_id: Optional[str] = None
    task_id: Optional[str] = None
    error_message: Optional[str] = None
    completed_at: Optional[str] = None


# ------------------------------------------------------------------
# Review / Rerun schemas
# ------------------------------------------------------------------


class ReviewAction(BaseModel):
    """人工确认操作请求."""

    action: str = Field(..., pattern=r"^(approve|reject)$", description="approve | reject")
    node_key: str
    comment: Optional[str] = None


# ------------------------------------------------------------------
# Timeline schemas
# ------------------------------------------------------------------


class NodeTaskDetail(BaseModel):
    """节点关联的 Task 详情."""

    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    result_summary: Optional[str] = None


class NodeTimelineItem(BaseModel):
    """时间线节点项."""

    node_key: Optional[str] = None
    type: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    input: Optional[dict] = None
    output: Optional[dict] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    task: Optional[NodeTaskDetail] = None


class FlowRunTimeline(BaseModel):
    """Flow 运行时间线."""

    flow_run: dict
    nodes: list[NodeTimelineItem]
    total_duration_seconds: Optional[float] = None
    current_node: Optional[str] = None
