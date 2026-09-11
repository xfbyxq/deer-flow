class DeerFlowError(Exception):
    """Base exception for DeerFlow client errors."""


class DeerFlowUnavailableError(DeerFlowError):
    """DeerFlow Gateway 不可达或返回 5xx."""


class AuthenticationError(DeerFlowError):
    """认证失败（登录失败或会话过期）."""


class AgentNotFoundError(DeerFlowError):
    """Agent 不存在 (404)."""


class AgentConflictError(DeerFlowError):
    """Agent 名称冲突 (409)."""


class ThreadNotFoundError(DeerFlowError):
    """Thread 不存在 (404)."""


class TaskConflictError(DeerFlowError):
    """Task 状态冲突 (409)."""


class TaskNotFoundError(DeerFlowError):
    """Task 不存在 (404)."""


class McpServerNotFoundError(DeerFlowError):
    """MCP server 不存在 (404)."""


class ValidationError(DeerFlowError):
    """请求参数校验失败 (422)."""
