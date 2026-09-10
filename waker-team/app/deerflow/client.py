import asyncio
import logging
import time
from typing import Any

import httpx

from app.deerflow.errors import (
    AgentConflictError,
    AgentNotFoundError,
    AuthenticationError,
    DeerFlowError,
    DeerFlowUnavailableError,
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


# Lead agent 的 LangGraph 递归预算：与 DeerFlow Web UI 的 recursion_limit=1000 一致
# （Gateway 默认仅 100，长任务中模型多次重试工具容易撞到上限导致 run 报
# Recursion limit reached 而丢失回复；服务端会 clamp 到 max_recursion_limit）。
_RUN_RECURSION_LIMIT = 1000


def build_run_configuration(agent_name: str) -> dict[str, Any]:
    """构建 waker run 的 ``config``，携带请求级 ``waker_identity`` 凭据。

    共享 MCP server（如 waker-team）通过 ``headers_from_context`` 要求每次工具
    调用携带调用方身份（本服务发起的 run 以执行者自己的身份调用工具）；
    缺失时拦截器 fail-closed，所有工具调用被拒绝（表现为"agent 无法获取
    同事列表"）。凡由本服务发起的 waker run 都必须使用本函数构造 config。
    """
    return {
        "configurable": {"agent_name": agent_name},
        "context": {"secrets": {"waker_identity": agent_name}},
        # 与主 UI 一致的长任务递归预算（默认 100 在"搜索重试"类场景下易被耗尽）
        "recursion_limit": _RUN_RECURSION_LIMIT,
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
            raise AuthenticationError(f"Login request failed: {exc}") from exc

        if resp.status_code != 200:
            self._login_at = None
            raise AuthenticationError(
                f"Login failed: status={resp.status_code} body={resp.text!r}"
            )

        self._logged_in = True
        self._login_at = time.time()

    async def _ensure_logged_in(self) -> None:
        """懒登录 + 主动续期: 未登录→登录; 接近过期→自动刷新."""
        if self._login_at is not None and (
            time.time() - self._login_at
        ) > self._refresh_threshold:
            # 会话接近过期，主动刷新
            if self._email is not None and self._password is not None:
                logger.info("Session nearing expiry, proactive refresh …")
                self._logged_in = False
                self._client.cookies.clear()
                self._login_at = None
                await self.login(self._email, self._password)
                return
        if not self._logged_in:
            raise AuthenticationError(
                "Not logged in. Call login() or use a client that has been "
                "authenticated via lifespan."
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

        # 401 → 自动重登一次后重试
        if resp.status_code == 401 and not _is_retry:
            logger.info("Received 401, re-authenticating …")
            self._logged_in = False
            self._login_at = None
            # 清除旧 cookie 避免干扰
            self._client.cookies.clear()
            # 需要外部重新 login；这里尝试用缓存凭据
            # 由于 client 不持有凭据，交由上层 lifespan 注入；
            # 此处仅标记未登录，由 _retry_after_login 处理。
            raise AuthenticationError(
                "Session expired (401). Caller must invoke login() again."
            )

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
        if resp.status_code < 400:
            return

        status = resp.status_code
        body = resp.text

        if status >= 500:
            raise DeerFlowUnavailableError(
                f"DeerFlow Gateway error: {status} {body!r}"
            )
        if status == 404:
            # 根据路径判断是 Agent 还是 Thread
            if "/threads/" in path:
                raise ThreadNotFoundError(f"Thread not found: {path}")
            raise AgentNotFoundError(f"Agent not found: {path}")
        if status == 409:
            if "/agents" in path:
                raise AgentConflictError(f"Agent conflict: {body!r}")
            raise TaskConflictError(f"Task conflict: {body!r}")
        if status == 422:
            raise ValidationError(f"Validation error: {body!r}")
        if status == 401:
            raise AuthenticationError(f"Authentication failed: {body!r}")

        # 其它 4xx
        raise DeerFlowError(f"Unexpected {status}: {body!r}")

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
