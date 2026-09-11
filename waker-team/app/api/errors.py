"""DeerFlow 异常 → HTTP 异常的**共享**映射（所有 REST 路由统一错误语义）.

历史问题（CF9）：``api/tasks.py`` 用一个 ``isinstance(exc, DeerFlowError)`` catch-all
把上游所有 4xx 压成 502「请稍后重试」，而 ``api/wakers.py`` 自己维护另一套映射
（422/409/502/500 且直接回传 ``str(exc)``）——同类故障在两条路由上语义分叉，
前端无法据状态码决策（该重试？该刷新？该找管理员？）。

本模块是**唯一**映射源，两条路由都复用它。契约（跨包 CONTRACT-ERROR）：

============================  =====  ==========================================
异常类型                       状态码  detail（安全中文文案）
============================  =====  ==========================================
``ValidationError``            422   请求参数校验未通过，请联系管理员
``AuthenticationError``        503   服务账号鉴权失败，请检查凭据配置
``ThreadNotFoundError``        404   关联会话已不存在
``McpServerNotFoundError``     404   关联的 MCP 服务配置已不存在
``AgentNotFoundError``         404   员工不存在
``TaskNotFoundError``          404   任务不存在
``AgentConflictError``         409   资源状态冲突，请刷新后重试
``TaskConflictError``          409   资源状态冲突，请刷新后重试
``DeerFlowUnavailableError``   502   上游服务暂时不可用，请稍后重试
``DeerFlowError``（兜底）        502   上游服务暂时不可用，请稍后重试
其它未预期异常                   500   服务内部错误，请稍后重试
============================  =====  ==========================================

设计要点：

* **detail 一律脱敏**：不含上游响应体、内部路径（``path=/api/...``）、主机名、
  状态码技术串或栈信息——完整原因只进服务端日志。
* **AuthenticationError 映射 503 而非透传 401**：这是**服务账号**（Gateway 侧
  系统账号）鉴权失败，属服务端配置故障；若回传 401，前端会误判为「用户登录态
  失效」而触发登出，把运维问题变成用户被踢。故记 ``logger.critical`` 告警。
* **ValidationError 记 ``logger.exception``**：请求体由本服务自己拼装，上游 422
  意味着本服务缺陷（而非用户输入错误），需要栈信息告警。
* **可操作原因（CF20）**：``deerflow.client`` 会对 4xx 白名单提取上游
  ``detail``/``message`` 字符串并挂到异常的 ``upstream_reason`` 属性上；映射时
  以「安全文案：上游原因」形式组织 detail，前端既能展示可操作原因，又拿不到
  原始 body。
"""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import HTTPException

from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    AuthenticationError,
    DeerFlowError,
    DeerFlowUnavailableError,
    McpServerNotFoundError,
    TaskConflictError,
    TaskNotFoundError,
    ThreadNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 安全文案（与前端约定一致；不含上游 body / 内部路径 / 主机名）
# ---------------------------------------------------------------------------

UPSTREAM_UNAVAILABLE_DETAIL = "上游服务暂时不可用，请稍后重试"
INTERNAL_ERROR_DETAIL = "服务内部错误，请稍后重试"
VALIDATION_DETAIL = "请求参数校验未通过，请联系管理员"
SERVICE_AUTH_DETAIL = "服务账号鉴权失败，请检查凭据配置"
THREAD_NOT_FOUND_DETAIL = "关联会话已不存在"
MCP_NOT_FOUND_DETAIL = "关联的 MCP 服务配置已不存在"
AGENT_NOT_FOUND_DETAIL = "员工不存在"
TASK_NOT_FOUND_DETAIL = "任务不存在"
CONFLICT_DETAIL = "资源状态冲突，请刷新后重试"

# 上游白名单 reason 并入 detail 时的二次限长（client 侧已限长，这里兜底）
_REASON_LIMIT = 200


def _compose(base: str, exc: BaseException) -> str:
    """把 client 白名单提取的上游原因并入安全文案.

    只有 ``deerflow.client._raise_for_status`` 会设置 ``upstream_reason``
    （来源限于上游 JSON 顶层 ``detail``/``message`` 字符串、已限长与内容过滤），
    本服务自行构造的异常没有该属性，detail 保持纯安全文案。
    """
    reason = getattr(exc, "upstream_reason", None)
    if isinstance(reason, str):
        reason = " ".join(reason.split())
        if reason:
            return f"{base}：{reason[:_REASON_LIMIT]}"
    return base


def map_deerflow_error(exc: Exception) -> NoReturn:
    """把异常映射为 ``HTTPException`` 并抛出（**永不返回**）.

    Parameters
    ----------
    exc:
        路由 ``except`` 块捕获的异常。``HTTPException`` 原样透传；DeerFlow
        异常按上表映射；其它未预期异常 → 500 + 安全文案。
    """
    # 路由内主动抛出的 HTTPException 不改写（如资源 id 不存在的 404）
    if isinstance(exc, HTTPException):
        raise exc

    # 422：请求体由本服务拼装 → 上游校验失败属本服务缺陷，需栈告警
    if isinstance(exc, ValidationError):
        logger.exception(
            "Upstream rejected our request payload (service defect)", exc_info=exc
        )
        raise HTTPException(status_code=422, detail=_compose(VALIDATION_DETAIL, exc)) from exc

    # 503：服务账号鉴权失败（**不**透传 401，避免前端误判用户登录态失效而登出）
    if isinstance(exc, AuthenticationError):
        logger.critical(
            "DeerFlow service-account authentication failed — check credentials config: %s",
            exc,
        )
        raise HTTPException(status_code=503, detail=SERVICE_AUTH_DETAIL) from exc

    # 404：资源态（前端可提示刷新）
    if isinstance(exc, ThreadNotFoundError):
        raise HTTPException(
            status_code=404, detail=_compose(THREAD_NOT_FOUND_DETAIL, exc)
        ) from exc
    if isinstance(exc, McpServerNotFoundError):
        raise HTTPException(
            status_code=404, detail=_compose(MCP_NOT_FOUND_DETAIL, exc)
        ) from exc
    if isinstance(exc, AgentNotFoundError):
        raise HTTPException(
            status_code=404, detail=_compose(AGENT_NOT_FOUND_DETAIL, exc)
        ) from exc
    if isinstance(exc, TaskNotFoundError):
        raise HTTPException(
            status_code=404, detail=_compose(TASK_NOT_FOUND_DETAIL, exc)
        ) from exc

    # 409：状态冲突（前端可刷新后重试）
    if isinstance(exc, (AgentConflictError, TaskConflictError)):
        raise HTTPException(
            status_code=409, detail=_compose(CONFLICT_DETAIL, exc)
        ) from exc

    # 502：上游不可用/未预期上游错误（前端可稍后重试）
    if isinstance(exc, DeerFlowUnavailableError):
        logger.warning("Upstream DeerFlow unavailable: %s", exc)
        raise HTTPException(status_code=502, detail=UPSTREAM_UNAVAILABLE_DETAIL) from exc
    if isinstance(exc, DeerFlowError):
        logger.warning("Upstream DeerFlow call failed: %s", exc)
        raise HTTPException(status_code=502, detail=UPSTREAM_UNAVAILABLE_DETAIL) from exc

    # 其它未预期异常：不回传 str(exc)（可能内含上游片段/内部细节）
    logger.exception("Unexpected error in API route", exc_info=exc)
    raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc
