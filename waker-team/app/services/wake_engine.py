"""唤醒 run 构造与投递（后台队列 + worker 池）.

当异步委派完成后，在源 agent 的 thread 上发起唤醒 run，
注入目标 agent 的结果摘要，让源 agent 感知委派结果。

护栏（S18）与后台投递（CF3）
-----------------------------
``SyncEngine`` 发现 async_delegate run 终态后会自动触发本模块投递唤醒 run，
批量任务同时终态时会瞬时拉起大量 run，而唤醒 run 中的 agent 还可能继续委派，
形成递归放大。旧实现把限速等待嵌在 SyncEngine 的串行轮询循环内，队头阻塞
最长 ``throttle_wait_timeout`` × N 个任务。现改为**后台投递**：

1. ``wake()`` **非阻塞入队**后立即返回（有界队列满时短暂有界等待，仍满则抛
   ``WakeQueueFullError``，由调用方延后处理，绝不阻塞 60s 级限速窗口）。
2. ``start()`` 拉起 ``max_concurrency`` 个 worker 协程（惰性：``wake()`` 在
   ``start()`` 之前被调用时自动拉起，兼容 ``app.mcp.server`` 的构造点），
   ``stop()`` 幂等关闭。**并发上限由 worker 池规模实现**：每个 worker 串行
   消费队列，同时在途的 ``create_run`` 数天然 <= worker 数。
3. **速率上限**：滑动窗口限速（``max_per_minute`` / ``rate_window_seconds``）
   跨所有 worker 统一（共享 ``_rate_lock`` + ``_recent``）。窗口内超额时有界
   等待；等待超过 ``throttle_wait_timeout_seconds`` 抛 ``WakeThrottledError``，
   worker 将请求**重新入队延后重试**（背压只延后投递，不改写任务终态）；
   重试预算（``_throttle_retry_budget``）耗尽后通过 ``on_failed`` 回调上抛，
   由调用方决定落库语义。
4. 投递结果通过调用方传入的 ``on_delivered`` / ``on_failed`` 回调**在 worker
   协程内**回写（回调自行开独立短 session），``SyncEngine`` 同步循环绝不被
   限速阻塞。

异常语义分离（CF4）
--------------------
- ``WakeThrottledError``：**本地背压/限速超时**。任务本身已完成，仅投递被
  延后；调用方不得据此修改 ``task.status`` 或覆盖 ``result_summary``。
- ``WakeDeliveryError``：**真实上游投递失败**（create_run 报错 / 无 run_id /
  限速重试预算耗尽）。调用方可据此判失败。
- ``WakeQueueFullError``：本地队列满且有界等待超时（背压信号），调用方应
  保持待投递状态、延后重试。

阈值按进程计（CF18）
---------------------
``wake_max_concurrency`` / ``wake_max_per_minute`` /
``wake_throttle_wait_timeout_seconds`` 均为 **per-process** 语义：Gateway
（``app.main``）与 MCP Server（``app.mcp.server``）各自持有独立的
``WakeEngine`` 实例与独立计数，全局上界 = 阈值 × 进程数。CF3 之后并发由
worker 池实现（每个进程 ``wake_max_concurrency`` 个 worker）。

**委派链深度上界**：唤醒 run 不改变委派链深度控制，深度/扇出上界仍由
``app.services.delegation_guard.DelegationGuard`` 强制约束：
``MAX_DEPTH = 3``（委派链最多 3 层）、``MAX_FANOUT_PER_RUN = 5``（单 run 最多
5 个在途子委派）、``MAX_FANOUT_PER_GROUP = 10``（单群最多 10 个在途委派）。
即：本模块的并发/速率护栏只限制"拉起 wake run 的瞬时压力"，
而"wake 后 agent 再次委派"的递归放大由 DelegationGuard.MAX_DEPTH 收口。
"""

import asyncio
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import get_settings
from app.deerflow.client import DeerFlowClient, build_run_configuration

logger = logging.getLogger(__name__)

# 滑动窗口默认长度：60s（对应配置项名 wake_max_per_minute 的语义）。
_DEFAULT_RATE_WINDOW_SECONDS = 60.0

# 有界队列默认容量：可容纳批量终态时的瞬时唤醒洪峰，超出走背压路径。
_DEFAULT_QUEUE_SIZE = 128

# 队列满时 put 的有界等待（秒）：短暂等待 worker 腾位，仍满则抛
# WakeQueueFullError——绝不退化为在调用方线程内等限速窗口（60s 级）。
_DEFAULT_QUEUE_PUT_TIMEOUT_SECONDS = 2.0

# 限速超时后的延后重试预算（次）：每次重试都重新排队，由 worker 在限速
# 窗口滑出后投递；预算耗尽才通过 on_failed(WakeThrottledError) 上报。
_THROTTLE_RETRY_BUDGET = 5

# 限速重试的重新入队延迟（秒）：与滑动窗口同量级，避免空转。
_THROTTLE_RETRY_DELAY_SECONDS = 1.0


class WakeDeliveryError(Exception):
    """唤醒 run 真实投递失败（上游 create_run 报错 / 无 run_id / 重试预算耗尽）."""


class WakeThrottledError(WakeDeliveryError):
    """本地背压/限速超时：仅投递被延后，**不代表任务失败**.

    CF4：调用方不得据此修改 ``task.status`` 或覆盖 ``result_summary``——
    任务本身可能已完成且已在群里汇报成功。子类化 ``WakeDeliveryError``
    仅为兼容旧 ``except WakeDeliveryError`` 调用点；新代码应优先匹配
    ``WakeThrottledError`` 并走"保持待投递/延后重试"路径。
    """


class WakeQueueFullError(WakeDeliveryError):
    """本地投递队列满（有界等待后仍无空位）：调用方应延后重试入队."""


# 投递结果回调：worker 在自身协程内调用，回调自行开独立短 session 回写。
OnDelivered = Callable[[str], Awaitable[None]]
OnFailed = Callable[[Exception], Awaitable[None]]


class _WakeRequest:
    """一条待投递的唤醒请求（队列元素）."""

    __slots__ = (
        "delivery_id",
        "source_thread_id",
        "source_agent_name",
        "result_summary",
        "ticket_id",
        "target_waker",
        "on_delivered",
        "on_failed",
        "attempts",
        "enqueued_at",
    )

    def __init__(
        self,
        *,
        source_thread_id: str,
        source_agent_name: str,
        result_summary: str,
        ticket_id: str,
        target_waker: str,
        on_delivered: OnDelivered | None,
        on_failed: OnFailed | None,
    ) -> None:
        self.delivery_id = uuid.uuid4().hex
        self.source_thread_id = source_thread_id
        self.source_agent_name = source_agent_name
        self.result_summary = result_summary
        self.ticket_id = ticket_id
        self.target_waker = target_waker
        self.on_delivered = on_delivered
        self.on_failed = on_failed
        self.attempts = 0
        self.enqueued_at = time.monotonic()


class WakeEngine:
    """构造并投递唤醒 run（后台队列 + worker 池 + 并发/速率护栏）.

    护栏阈值默认取自 ``settings``，也可在构造时以 keyword-only 参数覆盖（测试/
    特殊部署用）。第一个位置参数仍是 ``deerflow_client``，既有调用点
    （``app.main`` / ``app.mcp.server`` 的 ``WakeEngine(df)``）无需改动。

    生命周期：``await start()`` 显式启动 worker 池（``app.main`` lifespan）；
    未 start 时首次 ``wake()`` **惰性启动**（兼容 MCP Server 等不管理生命
    周期的构造点）；``await stop()`` 幂等关闭。

    阈值为 per-process 语义（见模块 docstring「阈值按进程计」）。
    委派链深度上界见模块 docstring：由 ``DelegationGuard.MAX_DEPTH`` 约束。
    """

    def __init__(
        self,
        deerflow_client: DeerFlowClient,
        *,
        max_concurrency: int | None = None,
        max_per_minute: int | None = None,
        rate_window_seconds: float = _DEFAULT_RATE_WINDOW_SECONDS,
        throttle_wait_timeout_seconds: float | None = None,
        queue_maxsize: int = _DEFAULT_QUEUE_SIZE,
        queue_put_timeout_seconds: float = _DEFAULT_QUEUE_PUT_TIMEOUT_SECONDS,
        throttle_retry_budget: int = _THROTTLE_RETRY_BUDGET,
        throttle_retry_delay_seconds: float = _THROTTLE_RETRY_DELAY_SECONDS,
    ) -> None:
        settings = get_settings()
        self._client = deerflow_client
        # worker 池规模（= 同时在途 create_run 上限），至少为 1。
        self._max_concurrency = max(
            1,
            max_concurrency
            if max_concurrency is not None
            else settings.wake_max_concurrency,
        )
        # max_per_minute <= 0 视为"不限速"（仅保留并发护栏）。
        self._max_per_minute = (
            max_per_minute if max_per_minute is not None else settings.wake_max_per_minute
        )
        self._rate_window = float(rate_window_seconds)
        self._throttle_wait_timeout = (
            throttle_wait_timeout_seconds
            if throttle_wait_timeout_seconds is not None
            else settings.wake_throttle_wait_timeout_seconds
        )
        self._queue: asyncio.Queue[_WakeRequest | None] = asyncio.Queue(
            maxsize=max(1, queue_maxsize)
        )
        self._queue_put_timeout = float(queue_put_timeout_seconds)
        self._throttle_retry_budget = max(0, throttle_retry_budget)
        self._throttle_retry_delay = float(throttle_retry_delay_seconds)
        self._workers: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False
        # 滑动窗口限速状态：跨所有 worker 统一（同一把锁 + 同一窗口）。
        self._rate_lock = asyncio.Lock()
        self._recent: deque[float] = deque()

    # ------------------------------------------------------------------
    # 属性（护栏阈值可观测）
    # ------------------------------------------------------------------

    @property
    def max_concurrency(self) -> int:
        """worker 池规模 = 同时在途 wake run 创建数上限（per-process）."""
        return self._max_concurrency

    @property
    def max_per_minute(self) -> int:
        """滑动窗口内 wake run 创建数上限（<=0 表示不限速，per-process）."""
        return self._max_per_minute

    @property
    def rate_window_seconds(self) -> float:
        """速率滑动窗口长度（秒）."""
        return self._rate_window

    @property
    def throttle_wait_timeout_seconds(self) -> float:
        """限速时有界等待的上限（秒），超过则抛 WakeThrottledError 并重排."""
        return self._throttle_wait_timeout

    @property
    def queue_size(self) -> int:
        """当前待投递请求数（含限速重排的请求）."""
        return self._queue.qsize()

    @property
    def worker_count(self) -> int:
        """当前存活的 worker 协程数."""
        return sum(1 for w in self._workers if not w.done())

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台投递 worker 池（幂等）."""
        if self._started and not self._stopping:
            return
        self._started = True
        self._stopping = False
        if not self._workers:
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"wake-worker-{index}")
                for index in range(self._max_concurrency)
            ]
            logger.info(
                "WakeEngine started: %d workers, queue_maxsize=%d, "
                "rate=%d/%gs (per-process)",
                self._max_concurrency,
                self._queue.maxsize,
                self._max_per_minute,
                self._rate_window,
            )

    async def stop(self) -> None:
        """关闭 worker 池（幂等）：先请求排空，再兜底 cancel."""
        if not self._started and not self._workers:
            return
        self._stopping = True
        self._started = False
        workers, self._workers = self._workers, []
        if not workers:
            return
        # 每个 worker 一个 sentinel，令阻塞在 get() 的 worker 正常退出
        for _ in workers:
            try:
                self._queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        _done, pending = await asyncio.wait(workers, timeout=5.0)
        for worker in pending:
            worker.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        dropped = self._queue.qsize()
        self._stopping = False
        logger.info("WakeEngine stopped (dropped_pending=%d)", dropped)

    def _ensure_workers(self) -> None:
        """惰性启动：wake() 在 start() 之前被调用时兜底拉起 worker.

        兼容 ``app.mcp.server`` 等只构造 ``WakeEngine(df)``、不显式管理
        生命周期的调用点。要求当前处于运行中的事件循环内。
        """
        if self._stopping or (self._started and self._workers):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning(
                "WakeEngine.wake() called without a running event loop; "
                "cannot start delivery workers lazily"
            )
            return
        self._started = True
        self._workers = [
            asyncio.create_task(self._worker(index), name=f"wake-worker-{index}")
            for index in range(self._max_concurrency)
        ]
        logger.info(
            "WakeEngine workers lazily started (%d workers)", self._max_concurrency
        )

    # ------------------------------------------------------------------
    # 投递入口（非阻塞）
    # ------------------------------------------------------------------

    async def wake(
        self,
        source_thread_id: str,
        source_agent_name: str,
        result_summary: str,
        ticket_id: str = "",
        target_waker: str = "",
        *,
        on_delivered: OnDelivered | None = None,
        on_failed: OnFailed | None = None,
    ) -> str:
        """把唤醒请求**入队**后立即返回（CF3：非阻塞，不等限速窗口）。

        Parameters
        ----------
        source_thread_id:
            源 agent（发起委派的 agent）的 DeerFlow thread id。
        source_agent_name:
            源 agent 名称（用于 configurable.agent_name）。
        result_summary:
            目标 agent 的执行结果摘要。
        ticket_id:
            异步委派 ticket id（用于追溯）。
        target_waker:
            目标 waker 名（用于消息内容）。
        on_delivered:
            投递成功回调（worker 协程内调用，入参为唤醒 run_id）。
            回调应自行开独立短 session 回写 ledger/task。
        on_failed:
            投递失败回调（worker 协程内调用，入参为 ``WakeThrottledError``
            或 ``WakeDeliveryError``）。``WakeThrottledError`` 表示限速重试
            预算耗尽——任务本身未失败，调用方不得改写 task 终态。

        Returns
        -------
        本次投递的 delivery_id（本地追踪用）。真实 run_id 通过
        ``on_delivered`` 回调送达。

        Raises
        ------
        WakeQueueFullError
            队列满且短暂有界等待（``queue_put_timeout_seconds``）后仍无空位。
            调用方应保持待投递状态、延后重试（绝不阻塞限速窗口时长）。
        """
        self._ensure_workers()
        request = _WakeRequest(
            source_thread_id=source_thread_id,
            source_agent_name=source_agent_name,
            result_summary=result_summary,
            ticket_id=ticket_id,
            target_waker=target_waker,
            on_delivered=on_delivered,
            on_failed=on_failed,
        )
        try:
            await asyncio.wait_for(self._queue.put(request), timeout=self._queue_put_timeout)
        except (asyncio.QueueFull, asyncio.TimeoutError) as exc:
            logger.error(
                "Wake delivery queue full, deferring: ticket=%s queue_size=%d",
                ticket_id,
                self._queue.qsize(),
            )
            raise WakeQueueFullError(
                f"Wake delivery queue full for ticket {ticket_id}: "
                f"queue_size={self._queue.maxsize} "
                f"put_timeout={self._queue_put_timeout:g}s"
            ) from exc
        logger.info(
            "Wake delivery enqueued: ticket=%s delivery=%s queue_size=%d",
            ticket_id,
            request.delivery_id,
            self._queue.qsize(),
        )
        return request.delivery_id

    # ------------------------------------------------------------------
    # worker 池
    # ------------------------------------------------------------------

    async def _worker(self, index: int) -> None:
        """后台投递 worker：消费队列 → 限速 → create_run → 回调回写."""
        logger.debug("Wake worker %d started", index)
        try:
            while True:
                request = await self._queue.get()
                if request is None:  # stop() 的退出信号
                    return
                await self._deliver(request)
        except asyncio.CancelledError:
            logger.debug("Wake worker %d cancelled", index)
            raise
        except Exception:
            logger.exception("Wake worker %d crashed", index)

    async def _deliver(self, request: _WakeRequest) -> None:
        """投递单个请求：限速 → create_run；限速超时重排，失败走回调."""
        request.attempts += 1
        try:
            run_id = await self._deliver_once(request)
        except WakeThrottledError as exc:
            if request.attempts <= self._throttle_retry_budget:
                # 背压重试：重新入队延后投递（绝不改写任务终态，CF4）
                logger.warning(
                    "Wake delivery throttled, requeueing: ticket=%s attempt=%d/%d",
                    request.ticket_id,
                    request.attempts,
                    self._throttle_retry_budget,
                )
                await asyncio.sleep(self._throttle_retry_delay)
                try:
                    self._queue.put_nowait(request)
                    return
                except asyncio.QueueFull:
                    logger.error(
                        "Wake delivery queue full on throttle-requeue: ticket=%s",
                        request.ticket_id,
                    )
                    await self._finish(request, WakeQueueFullError(str(exc)))
                    return
            await self._finish(request, exc)
            return
        except WakeDeliveryError as exc:
            await self._finish(request, exc)
            return
        except Exception as exc:
            await self._finish(
                request,
                WakeDeliveryError(
                    f"Failed to deliver wake run for ticket {request.ticket_id}: {exc}"
                ),
            )
            return
        await self._finish(request, None, run_id)

    async def _deliver_once(self, request: _WakeRequest) -> str:
        """单次投递尝试：取限速额度 + create_run，返回 run_id.

        Raises
        ------
        WakeThrottledError
            限速窗口内超额且有界等待超时（本地背压，非上游失败）。
        WakeDeliveryError
            上游 create_run 失败或未返回 run_id（真实投递失败）。
        """
        wake_input = self.build_wake_input(
            result_summary=request.result_summary,
            ticket_id=request.ticket_id,
            target_waker=request.target_waker,
        )
        run_body = {
            "input": wake_input,
            "config": build_run_configuration(request.source_agent_name),
        }
        # 速率护栏：滑动窗口跨所有 worker 统一；超额时有界等待。
        await self._acquire_rate_slot(request.ticket_id)
        try:
            run_resp = await self._client.create_run(
                thread_id=request.source_thread_id,
                body=run_body,
                idempotency_key=f"wake-{request.ticket_id}-{uuid.uuid4().hex[:8]}",
            )
        except Exception as exc:
            raise WakeDeliveryError(
                f"Failed to deliver wake run for ticket {request.ticket_id}: {exc}"
            ) from exc
        run_id = run_resp.get("run_id")
        if not run_id:
            raise WakeDeliveryError(
                f"DeerFlow create_run returned no run_id for wake-{request.ticket_id}"
            )
        return run_id

    async def _finish(
        self, request: _WakeRequest, error: Exception | None, run_id: str = ""
    ) -> None:
        """把投递结果交给调用方回调（回调内部自开独立短 session 回写）."""
        if error is None:
            logger.info(
                "Wake run delivered: thread=%s run=%s ticket=%s attempts=%d",
                request.source_thread_id,
                run_id,
                request.ticket_id,
                request.attempts,
            )
            if request.on_delivered is not None:
                try:
                    await request.on_delivered(run_id)
                except Exception:
                    logger.exception(
                        "on_delivered callback failed for ticket %s", request.ticket_id
                    )
            return

        if isinstance(error, WakeThrottledError):
            # CF4：本地背压耗尽重试预算——任务本身未失败，仅告警级日志。
            logger.warning(
                "Wake delivery throttled beyond retry budget: ticket=%s attempts=%d: %s",
                request.ticket_id,
                request.attempts,
                error,
            )
        else:
            logger.error(
                "Wake delivery failed: ticket=%s attempts=%d: %s",
                request.ticket_id,
                request.attempts,
                error,
            )
        if request.on_failed is not None:
            try:
                await request.on_failed(error)
            except Exception:
                logger.exception(
                    "on_failed callback failed for ticket %s", request.ticket_id
                )

    # ------------------------------------------------------------------
    # 限速（滑动窗口，跨 worker 统一）
    # ------------------------------------------------------------------

    async def _acquire_rate_slot(self, ticket_id: str) -> None:
        """取得一个速率额度；窗口内超额时有界等待.

        策略：滑动窗口（``time.monotonic()`` + ``deque``）。窗口内已投递数达
        ``max_per_minute`` 时，等到最早一笔滑出窗口再继续；累计等待超过
        ``throttle_wait_timeout_seconds`` 则抛 ``WakeThrottledError``（本地
        背压信号，由 ``_deliver`` 重新入队延后重试）。

        ``max_per_minute <= 0`` 时直接放行（不限速）。
        """
        limit = self._max_per_minute
        if limit <= 0:
            return

        waited = 0.0
        while True:
            async with self._rate_lock:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= self._rate_window:
                    self._recent.popleft()
                if len(self._recent) < limit:
                    self._recent.append(now)
                    return
                wait = self._rate_window - (now - self._recent[0])
                # CF16：防御性下限。浮点竞态下 wait 可能 <= 0（此时窗口其实
                # 已滑出），钳到 1ms 让出事件循环后重查，杜绝忙等烧 CPU。
                wait = max(wait, 0.001)

            if waited + wait > self._throttle_wait_timeout:
                raise WakeThrottledError(
                    f"Wake delivery throttled for ticket {ticket_id}: "
                    f"limit={limit}/{self._rate_window:g}s "
                    f"worker_pool={self._max_concurrency} "
                    f"wait_timeout={self._throttle_wait_timeout:g}s"
                )
            logger.warning(
                "Wake delivery rate-limited: ticket=%s wait=%.3fs limit=%d/%gs",
                ticket_id, wait, limit, self._rate_window,
            )
            await asyncio.sleep(wait)
            waited += wait

    # ------------------------------------------------------------------
    # 唤醒 input 构造
    # ------------------------------------------------------------------

    def build_wake_input(
        self,
        result_summary: str,
        ticket_id: str = "",
        target_waker: str = "",
    ) -> dict[str, Any]:
        """构造唤醒 run 的 input payload.

        格式参考 Team Briefing 注入方式：以 user message 注入委派结果通知。
        """
        content = (
            f"[异步委派结果通知]\n"
            f"ticket_id: {ticket_id}\n"
            f"执行者: {target_waker}\n"
            f"结果摘要:\n{result_summary}\n"
            f"---\n"
            f"请根据以上结果继续你的工作。"
        )
        return {
            "messages": [
                {"role": "user", "content": content},
            ]
        }
