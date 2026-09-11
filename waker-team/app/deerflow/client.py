import asyncio
import json
import logging
import time
from typing import Any, NoReturn

import httpx

from app.config import get_settings
from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    AuthenticationError,
    DeerFlowError,
    DeerFlowUnavailableError,
    McpServerNotFoundError,
    TaskConflictError,
    ThreadNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# 连接类错误重试：网关重启/网络抖动会使连接池中的旧 keep-alive 连接失效，
# 表现为响应阶段 RemoteProtocolError/ConnectError 等；本服务所有写接口均幂等
# 设计（idempotency_key / 冲突处理），可安全重试。
_CONNECTION_RETRIES = 3
_RETRYABLE_REQUEST_ERRORS = (
    httpx.ConnectError,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.PoolTimeout,
)

# G2/G3 会话自愈：重登失败后的冷却时长（秒）。冷却期内自愈路径直接跳过上游
# 登录接口（防 DeerFlow 长时间不可达时每个请求都触发一次登录 → 风暴）。
_RELOGIN_COOLDOWN_SECONDS = 30.0


# 上游响应体只允许进入服务端日志（且截断），继不整体内插进异常消息：
# 异常消息会沿调用链冒泡到 HTTP detail / MCP 工具结果 / 前端展示，
# 透传上游 body 等于把网关内部细节（栈信息、配置片段、账号信息）泄露给客户端。
_BODY_LOG_LIMIT = 1000

# CF14：轮询 thread state / run 状态时 404是常态，对每个 ≥400 无条件记 1KB body
# 到 WARNING 会淹没日志；以下 4xx 属「正常控制流」，降为 DEBUG 一行且不含 body。
_QUIET_4XX = frozenset({401, 404, 409, 422})

# CF20：4xx 中需要向用户解释的状态码才提取上游「可操作原因」；
# 401（服务账号凭据问题）与 5xx（网关故障）不属于用户可自行处理的范畴。
_REASON_EXCLUDED_STATUSES = frozenset({401})
# 只接受上游 JSON 顶层的这两个字段（FastAPI / 常见网关的标准错误形状）
_REASON_KEYS = ("detail", "message")
_REASON_LIMIT = 200
# reason 内容黑名单：命中即丢弃（防上游把栈帧 / 内部 URL / 凭据塞进 detail）
_REASON_BLOCKLIST = (
    "traceback",
    "most recent call last",
    'file "',
    "http://",
    "https://",
    "sqlite://",
    "/app/",
    "password",
    "secret",
    "token",
)


def _log_upstream_body(context: str, status: int, path: str, body: str) -> None:
    """把上游响应体以 WARNING 写入服务端日志（截断至 ``_BODY_LOG_LIMIT``）.

    仅用于「真正异常」的场合（5xx / 未预期状态码 / 登录失败）；
    正常控制流的 4xx 请走 ``_log_upstream_status``（CF14 分级）。
    """
    logger.warning(
        "DeerFlow upstream error body [%s]: status=%s path=%s body=%s",
        context,
        status,
        path,
        (body or "")[:_BODY_LOG_LIMIT],
    )


def _log_upstream_status(context: str, status: int, path: str, body: str) -> None:
    """按状态码分级记录上游失败（CF14）.

    - 4xx 正常控制流（401/404/409/422）：DEBUG 一行、**不含 body**；
    - 5xx 与未预期状态码（400/403/418/429 …）：WARNING + 截断 body。
    """
    if status in _QUIET_4XX:
        logger.debug(
            "DeerFlow upstream 4xx (expected control flow) [%s]: status=%s path=%s",
            context,
            status,
            path,
        )
        return
    _log_upstream_body(context, status, path, body)


def _extract_upstream_reason(status: int, body: str) -> str | None:
    """从上游 4xx 响应体中**白名单**提取可操作原因（CF20）.

    只接受 JSON 对象顶层的 ``detail`` / ``message`` **字符串**字段，折叠空白后
    限长 ``_REASON_LIMIT``；命中 ``_REASON_BLOCKLIST``（栈帧 / URL / 凭据特征）
    或非字符串（如 FastAPI 422 的校验错误列表）一律丢弃。既不整体透传 body，
    又能把「该 waker 已有运行中任务」这类用户可操作原因带给前端。
    """
    if status >= 500 or status in _REASON_EXCLUDED_STATUSES or not body:
        return None
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    for key in _REASON_KEYS:
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        reason = " ".join(value.split())  # 折叠换行/多空白，防多行栈混入
        if not reason:
            continue
        lowered = reason.lower()
        if any(marker in lowered for marker in _REASON_BLOCKLIST):
            continue
        return reason[:_REASON_LIMIT]
    return None


def build_run_configuration(agent_name: str) -> dict[str, Any]:
    """构建 waker run 的 ``config``，携带请求级 ``waker_identity`` 凭据。

    共享 MCP server（如 waker-team）通过 ``headers_from_context`` 要求每次工具
    调用携带调用方身份（本服务发起的 run 以执行者自己的身份调用工具）；
    缺失时拦截器 fail-closed，所有工具调用被拒绝（表现为"agent 无法获取
    同事列表"）。凡由本服务发起的 waker run 都必须使用本函数构造 config。

    递归预算（``recursion_limit``）取自 ``settings.recursion_limit``（默认 1000，
    与 DeerFlow Web UI 一致）：以前硬编码 1000 会给所有 run（含轻量唤醒、
    短委派）统一放大成本上限，现可按部署预算调整。
    隐式依赖：生效值受 DeerFlow Gateway 的 ``max_recursion_limit`` clamp，
    配置值超过 Gateway 上限时会被服务端静默截断（不报错），调大前需
    确认 Gateway 侧配置。对外签名保持稳定（仅 ``agent_name``），调用方无需变更。
    """
    return {
        "configurable": {"agent_name": agent_name},
        "context": {"secrets": {"waker_identity": agent_name}},
        # 可配置的长任务递归预算（默认 100 在"搜索重试"类场景下易被耗尽）
        "recursion_limit": get_settings().recursion_limit,
    }


class DeerFlowClient:
    """封装对 DeerFlow Gateway 的所有 HTTP 调用。"""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=60,
            follow_redirects=True,
        )
        self._logged_in = False
        # 会话生命周期
        self._email: str | None = None
        self._password: str | None = None
        self._login_at: float | None = None
        self._session_max_age: float = 604800  # 7 days in seconds
        self._refresh_threshold: float = 518400  # 6 days — proactive refresh
        # 连接层故障自愈锁（并发请求同时耗尽重试时只重建一次）
        self._client_rebuild_lock = asyncio.Lock()
        # G2/G3 会话自愈：重登单飞锁（并发触发只登一次）+ 失败冷却时间戳
        self._relogin_lock = asyncio.Lock()
        self._last_login_failure_at: float | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> None:
        """POST /api/v1/auth/login/local (OAuth2 表单)."""
        # 缓存凭据，支持主动续期与凭据轮换
        self._email = email
        self._password = password
        try:
            resp = await self._client.post(
                "/api/v1/auth/login/local",
                data={
                    "username": email,
                    "password": password,
                    "remember_me": "true",
                },
            )
        except httpx.RequestError as exc:
            self._login_at = None
            # 连接类失败属"上游不可达"（瞬时语义，G2）：上层（sync 瞬时预算 /
            # API 502 映射 / 自愈冷却）据此处理，不应误判为鉴权失败。
            raise DeerFlowUnavailableError(
                f"Login request failed (upstream unreachable): {exc}"
            ) from exc

        if resp.status_code != 200:
            self._login_at = None
            # 上游 body 仅进服务端日志（截断）；异常消息只保留安全摘要（状态码）。
            _log_upstream_body(
                "login", resp.status_code, "/api/v1/auth/login/local", resp.text
            )
            raise AuthenticationError(f"Login failed: status={resp.status_code}")

        self._logged_in = True
        self._login_at = time.time()

    async def _ensure_logged_in(self) -> None:
        """懒登录 + 主动续期 + 自愈重登（G2）.

        - 会话接近过期 → 主动续期（统一走 _relogin，含锁/冷却）
        - 未登录但有缓存凭据（降级启动 / 401 清理后）→ 自愈重登，
          DeerFlow 恢复后无需重启进程即可自动接上
        - 无缓存凭据 → 保持 fail-fast（调用方必须先 login）
        """
        if self._login_at is not None and (
            time.time() - self._login_at
        ) > self._refresh_threshold:
            # 会话接近过期，主动刷新
            logger.info("Session nearing expiry, proactive refresh …")
            await self._relogin()
            return
        if not self._logged_in:
            if self._email is not None and self._password is not None:
                # G2：自愈重登（凭据在 login() 入口即缓存，即便首次登录失败）
                logger.info("Not logged in with cached credentials, self-healing …")
                await self._relogin()
                return
            raise AuthenticationError(
                "Not logged in. Call login() or use a client that has been "
                "authenticated via lifespan."
            )

    async def _relogin(self) -> None:
        """用缓存凭据重新登录（G2/G3 自愈路径的单一入口）.

        并发单飞（``_relogin_lock`` + 会话新鲜度双检：入口与锁内各检一次，
        等锁期间被其他协程刷新则直接复用）+ 失败冷却节流（冷却期内跳过
        上游，防登录风暴）。

        异常语义（调用方按类型分支）：
        - 无缓存凭据 → ``AuthenticationError``（fail-fast，需先 login）
        - 冷却中 / 连接类失败 → ``DeerFlowUnavailableError``（瞬时，可重试）
        - 凭据被拒 → ``AuthenticationError``（需人工修正配置）
        """
        if self._email is None or self._password is None:
            raise AuthenticationError(
                "Not logged in and no cached credentials for re-login. "
                "Call login() first."
            )
        # 快速路径：会话已新鲜（可能刚被并发协程刷新）→ 直接复用（单飞）
        if self._session_fresh():
            return
        self._raise_if_in_relogin_cooldown()
        async with self._relogin_lock:
            # 双检：等锁期间已被并发协程刷新 → 复用新会话（不重复登录）
            if self._session_fresh():
                return
            # 锁内复查冷却（等锁期间可能刚失败过）
            self._raise_if_in_relogin_cooldown()
            try:
                self._logged_in = False
                self._client.cookies.clear()
                self._login_at = None
                await self.login(self._email, self._password)
            except DeerFlowUnavailableError:
                # 上游不可达：记冷却 + 顺带重建连接池（覆盖"连接池中毒 +
                # 未登录"的边缘自愈；受冷却节流保护，最多 30s 一次）
                self._last_login_failure_at = time.time()
                await self._rebuild_http_client()
                raise
            except AuthenticationError:
                # 凭据被拒：记冷却（避免每个请求都打登录接口），保留原语义
                self._last_login_failure_at = time.time()
                raise
            self._last_login_failure_at = None
            logger.info("Re-login succeeded (self-heal)")

    def _session_fresh(self) -> bool:
        """当前是否持有新鲜（未接近过期）的登录会话（G2 并发复用判定）."""
        return (
            self._logged_in
            and self._login_at is not None
            and (time.time() - self._login_at) <= self._refresh_threshold
        )

    def _raise_if_in_relogin_cooldown(self) -> None:
        """冷却期内跳过重登（不触达上游），抛瞬时语义异常（G2 防风暴）."""
        if (
            self._last_login_failure_at is not None
            and time.time() - self._last_login_failure_at < _RELOGIN_COOLDOWN_SECONDS
        ):
            raise DeerFlowUnavailableError(
                "Re-login skipped: in cooldown after recent login failure"
            )

    # ------------------------------------------------------------------
    # CSRF
    # ------------------------------------------------------------------

    def _csrf_headers(self) -> dict[str, str]:
        """从 cookie jar 读 csrf_token → {X-CSRF-Token: value}."""
        csrf = self._client.cookies.get("csrf_token")
        if csrf is None:
            return {}
        return {"X-CSRF-Token": csrf}

    # ------------------------------------------------------------------
    # Unified request
    # ------------------------------------------------------------------

    async def _request(
        self, method: str, path: str, *, _is_retry: bool = False, **kwargs: Any
    ) -> httpx.Response:
        """统一请求包装：写请求注入 CSRF + 401 自动重登重试 + 连接故障自愈."""
        await self._ensure_logged_in()

        # 注入 CSRF 头（仅写请求）
        if method.upper() in _WRITE_METHODS:
            headers = dict(kwargs.pop("headers", {}))
            headers.update(self._csrf_headers())
            kwargs["headers"] = headers

        # 连接类错误重试：网关重启后连接池中的旧 keep-alive 连接会失效，
        # 首次请求可能在响应阶段断开；对幂等接口安全重试。
        # 固定本次调用使用的 client 引用：重试耗尽时据此判定是否已被其他
        # 并发请求重建（重建后新请求使用全新连接池）。
        client_ref = self._client
        last_exc: httpx.RequestError | None = None
        for attempt in range(_CONNECTION_RETRIES):
            try:
                resp = await client_ref.request(method, path, **kwargs)
                break
            except _RETRYABLE_REQUEST_ERRORS as exc:
                last_exc = exc
                if attempt + 1 < _CONNECTION_RETRIES:
                    logger.warning(
                        "Connection error on %s %s (attempt %d/%d): %s — retrying",
                        method,
                        path,
                        attempt + 1,
                        _CONNECTION_RETRIES,
                        exc,
                    )
                    await asyncio.sleep(0.3 * (attempt + 1))
        else:
            # 连接类错误重试耗尽：重建 HTTP 客户端自愈——长时间运行后连接池
            # 可能进入无法自愈的异常状态（表现为所有请求 ConnectError，
            # 只能靠重启进程恢复）；重建等效于「连接层重启」，下次请求使用
            # 全新连接（cookie 保留，登录态延续；失效时由 401 路径兜底重登）。
            await self._rebuild_http_client(client_ref)
            raise DeerFlowUnavailableError(f"Connection error: {last_exc}") from last_exc

        # 401 → 会话失效（如 DeerFlow 重启）→ 缓存凭据自愈重登后重试一次（G3）。
        # 401 表示请求未被服务端处理，重试安全；_is_retry 保证至多重试一次。
        if resp.status_code == 401 and not _is_retry:
            logger.info("Received 401, re-authenticating and retrying …")
            self._logged_in = False
            self._login_at = None
            # 清除旧 cookie 避免干扰
            self._client.cookies.clear()
            try:
                await self._relogin()
            except AuthenticationError as exc:
                # 无法自愈（无凭据 / 凭据被拒）：保留 "Session expired" 语义
                raise AuthenticationError(
                    f"Session expired (401) and re-login unavailable: {exc}"
                ) from exc
            # 重登成功：重试原请求（重试期间再遇 401 由 _is_retry 分支兜底）
            return await self._request(method, path, _is_retry=True, **kwargs)

        self._raise_for_status(resp, path)
        return resp

    async def request_with_relogin(
        self, method: str, path: str, email: str, password: str, **kwargs: Any
    ) -> httpx.Response:
        """带自动重登重试的请求（供内部使用）."""
        try:
            return await self._request(method, path, **kwargs)
        except AuthenticationError:
            await self.login(email, password)
            return await self._request(method, path, _is_retry=True, **kwargs)

    # ------------------------------------------------------------------
    # 连接层故障自愈
    # ------------------------------------------------------------------

    async def _rebuild_http_client(self, stale: httpx.AsyncClient | None = None) -> None:
        """重建 HTTP 客户端（保留会话 cookie）；并发场景下只重建一次.

        ``stale`` 为发起重建的调用所用的 client 引用：若已被其他请求重建则跳过。
        """
        async with self._client_rebuild_lock:
            if stale is not None and self._client is not stale:
                return
            old = self._client
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=60,
                follow_redirects=True,
                cookies=httpx.Cookies(old.cookies),
            )
            logger.warning(
                "DeerFlow HTTP client rebuilt after connection failures (stale pool/state)"
            )
        # 旧客户端异步关闭（不阻塞当前调用；在途请求自然结束）
        asyncio.create_task(self._close_client(old))

    @staticmethod
    async def _close_client(client: httpx.AsyncClient) -> None:
        try:
            await client.aclose()
        except Exception:
            logger.debug("close stale http client failed", exc_info=True)

    # ------------------------------------------------------------------
    # Error mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(resp: httpx.Response, path: str) -> None:
        """把上游失败响应映射为本服务的异常类型.

        契约（跨包「异常类型稳定」）：抛出的异常**类型**与状态码/路径的对应关系
        不得改变（包 A 的 wake / sync 引擎按类型分支处理）。本方法只做两件事：

        * CF14：按状态码分级记日志——4xx 正常控制流（401/404/409/422）记 DEBUG
          一行且不含 body，5xx 与未预期码记 WARNING + 截断 body；
        * CF20：对可向用户解释的 4xx，**白名单**提取上游 ``detail``/``message``
          字符串作为可操作原因，追加到异常消息的 ``reason=`` 段，并挂到异常实例的
          ``upstream_reason`` 属性上（供 ``app.api.errors`` 组织脱敏后的 detail）。

        上游响应体永不整体进入异常消息：调用方（REST 路由 / MCP 工具 / 前端）会
        把异常文本当作展示内容，透传 body 会泄露网关内部细节（栈、配置、账号）。
        """
        if resp.status_code < 400:
            return

        status = resp.status_code
        body = resp.text
        _log_upstream_status("raise_for_status", status, path, body)
        reason = _extract_upstream_reason(status, body)

        def _message(prefix: str) -> str:
            base = f"{prefix}: status={status} path={path}"
            return f"{base} reason={reason}" if reason else base

        def _raise(exc: DeerFlowError) -> NoReturn:
            # 用实例属性传递白名单原因：既不改变异常类型（契约要求），
            # 也不让调用方去解析异常消息文本。
            if reason is not None:
                exc.upstream_reason = reason  # type: ignore[attr-defined]
            raise exc

        if status >= 500:
            _raise(DeerFlowUnavailableError(_message("DeerFlow Gateway error")))
        if status == 404:
            # 根据路径判断资源类型
            if "/threads/" in path:
                _raise(ThreadNotFoundError(_message("Thread not found")))
            if "/mcp/" in path:
                _raise(McpServerNotFoundError(_message("MCP server not found")))
            _raise(AgentNotFoundError(_message("Agent not found")))
        if status == 409:
            if "/agents" in path:
                _raise(AgentConflictError(_message("Agent conflict")))
            _raise(TaskConflictError(_message("Task conflict")))
        if status == 422:
            _raise(ValidationError(_message("Validation error")))
        if status == 401:
            _raise(AuthenticationError(_message("Authentication failed")))

        # 其它 4xx
        _raise(DeerFlowError(_message("Unexpected DeerFlow error")))

    # ------------------------------------------------------------------
    # Agents API
    # ------------------------------------------------------------------

    async def list_agents(self) -> list[dict]:
        """GET /api/agents → {"agents": [...]} → 返回 [...]."""
        resp = await self._request("GET", "/api/agents")
        data = resp.json()
        return data.get("agents", data if isinstance(data, list) else [])

    async def get_agent(self, name: str) -> dict:
        """GET /api/agents/{name} → AgentResponse dict."""
        resp = await self._request("GET", f"/api/agents/{name}")
        return resp.json()

    async def create_agent(self, data: dict) -> dict:
        """POST /api/agents, body=data → 201 + AgentResponse."""
        resp = await self._request("POST", "/api/agents", json=data)
        return resp.json()

    async def update_agent(self, name: str, data: dict) -> dict:
        """PUT /api/agents/{name}, body=data → 200 + AgentResponse."""
        resp = await self._request("PUT", f"/api/agents/{name}", json=data)
        return resp.json()

    async def delete_agent(self, name: str) -> None:
        """DELETE /api/agents/{name} → 204."""
        await self._request("DELETE", f"/api/agents/{name}")

    async def check_agent_name(self, name: str) -> dict:
        """GET /api/agents/check?name={name} → {"available": bool, "name": str}."""
        resp = await self._request("GET", "/api/agents/check", params={"name": name})
        return resp.json()

    # ------------------------------------------------------------------
    # Models & Skills 枚举
    # ------------------------------------------------------------------

    async def list_models(self) -> list[dict]:
        """GET /api/models → {"models": [...]} → 返回 [...]."""
        resp = await self._request("GET", "/api/models")
        data = resp.json()
        return data.get("models", data if isinstance(data, list) else [])

    async def list_skills(self) -> list[dict]:
        """GET /api/skills → {"skills": [...]} → 返回 [...]."""
        resp = await self._request("GET", "/api/skills")
        data = resp.json()
        return data.get("skills", data if isinstance(data, list) else [])

    # ------------------------------------------------------------------
    # Threads API
    # ------------------------------------------------------------------

    async def create_thread(self, thread_id: str | None = None) -> dict:
        """POST /api/threads, body={"thread_id": ...} → ThreadResponse."""
        body: dict = {}
        if thread_id is not None:
            body["thread_id"] = thread_id
        resp = await self._request("POST", "/api/threads", json=body)
        return resp.json()

    async def delete_thread(self, thread_id: str) -> None:
        """DELETE /api/threads/{thread_id} → 200."""
        await self._request("DELETE", f"/api/threads/{thread_id}")

    # ------------------------------------------------------------------
    # Runs API
    # ------------------------------------------------------------------

    async def create_run(
        self,
        thread_id: str,
        body: dict,
        idempotency_key: str | None = None,
    ) -> dict:
        """POST /api/threads/{thread_id}/runs."""
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        resp = await self._request(
            "POST",
            f"/api/threads/{thread_id}/runs",
            json=body,
            headers=headers,
        )
        return resp.json()

    async def get_run(self, thread_id: str, run_id: str) -> dict:
        """GET /api/threads/{thread_id}/runs/{run_id} → RunResponse."""
        resp = await self._request(
            "GET", f"/api/threads/{thread_id}/runs/{run_id}"
        )
        return resp.json()

    async def get_thread_state(self, thread_id: str) -> dict:
        """GET /api/threads/{thread_id}/state → ThreadStateResponse（含 values.messages）."""
        resp = await self._request("GET", f"/api/threads/{thread_id}/state")
        return resp.json()

    async def cancel_run(
        self, thread_id: str, run_id: str, wait: bool = True
    ) -> httpx.Response:
        """POST /api/threads/{thread_id}/runs/{run_id}/cancel?wait={wait}."""
        return await self._request(
            "POST",
            f"/api/threads/{thread_id}/runs/{run_id}/cancel",
            params={"wait": str(wait).lower()},
        )

    # ------------------------------------------------------------------
    # MCP Config API（需管理员账号）
    # ------------------------------------------------------------------

    async def get_mcp_config(self) -> dict:
        """GET /api/mcp/config → {"mcp_servers": {...}} → 返回 mcp_servers 映射."""
        resp = await self._request("GET", "/api/mcp/config")
        return resp.json().get("mcp_servers", {})

    async def add_mcp_server(self, name: str, config: dict) -> None:
        """POST /api/mcp/config/servers — 新增单个 server（重名 409）."""
        await self._request(
            "POST", "/api/mcp/config/servers", json={"mcp_servers": {name: config}}
        )

    async def update_mcp_server(self, name: str, config: dict) -> None:
        """PUT /api/mcp/config/server — 替换单个 server（不存在 404）."""
        await self._request(
            "PUT",
            "/api/mcp/config/server",
            json={"server_name": name, "server": config},
        )

    async def delete_mcp_server(self, name: str) -> None:
        """DELETE /api/mcp/config/servers/{name} — 不存在 404（McpServerNotFoundError）."""
        await self._request("DELETE", f"/api/mcp/config/servers/{name}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def update_credentials(self, email: str, password: str) -> None:
        """凭据轮换: 更新内部凭据，下次请求自动使用新凭据."""
        self._email = email
        self._password = password

    @property
    def is_authenticated(self) -> bool:
        """当前是否已登录."""
        return self._login_at is not None

    @property
    def session_age_days(self) -> float:
        """会话已存在天数; 未登录返回 inf."""
        if self._login_at is None:
            return float("inf")
        return (time.time() - self._login_at) / 86400

    def logout(self) -> None:
        """清除会话状态 (cookie / CSRF / 登录时间)."""
        self._client.cookies.clear()
        self._logged_in = False
        self._login_at = None

    async def close(self) -> None:
        await self._client.aclose()
